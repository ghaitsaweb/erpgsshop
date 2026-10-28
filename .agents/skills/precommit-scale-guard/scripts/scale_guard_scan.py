#!/usr/bin/env python3
"""Small cross-framework SG001-SG004 scanner for changed Python files."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "demo",
    "demos",
    "management",
    "node_modules",
    "test",
    "tests",
    "vendor",
    "versions",
    "venv",
}
READ_METHODS = {
    "aggregate",
    "all",
    "annotate",
    "count",
    "exclude",
    "exists",
    "filter",
    "first",
    "get",
    "last",
    "read_group",
    "search",
    "search_count",
    "search_read",
}
WRITE_METHODS = {
    "create",
    "delete",
    "save",
    "unlink",
    "update",
    "write",
}
HMX_METHODS = {"read_group", "search", "search_count", "search_read", "unlink", "write"}
SUPPRESSION_RE = re.compile(r"^\s*#\s*scale-guard:\s*allow\s+(SG00[1-4])\s+[—-]\s+(.+?)\s*$")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    path: str
    line: int
    message: str


def _call_name(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return "<call>"


def _method(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else ""


def _empty_domain(call: ast.Call) -> bool:
    if not call.args:
        return True
    first = call.args[0]
    return isinstance(first, (ast.List, ast.Tuple, ast.Set)) and not first.elts


def _has_limit(call: ast.Call) -> bool:
    return any(keyword.arg in {"limit", "page_size", "batch_size"} for keyword in call.keywords)


class ScaleVisitor(ast.NodeVisitor):
    def __init__(self, path: str, lines: list[str]):
        self.path = path
        self.lines = lines
        self.loop_depth = 0
        self.loop_nodes: list[ast.AST] = []
        self.findings: list[Finding] = []

    def _suppressed(self, rule: str, line: int) -> bool:
        if line < 2:
            return False
        for index in range(line - 2, max(-1, line - 6), -1):
            candidate = self.lines[index]
            match = SUPPRESSION_RE.match(candidate)
            if match and match.group(1) == rule and len(match.group(2).strip()) >= 12:
                return True
            if not candidate.lstrip().startswith("#"):
                break
        return False

    def _add(self, rule: str, severity: str, node: ast.AST, message: str) -> None:
        line = getattr(node, "lineno", 1)
        if not self._suppressed(rule, line):
            self.findings.append(Finding(rule, severity, self.path, line, message))

    def _visit_loop_body(self, node: ast.For | ast.AsyncFor) -> None:
        self.visit(node.iter)
        self.visit(node.target)
        self.loop_depth += 1
        self.loop_nodes.append(node)
        for child in node.body:
            self.visit(child)
        for child in node.orelse:
            self.visit(child)
        self.loop_nodes.pop()
        self.loop_depth -= 1

    def visit_For(self, node: ast.For) -> None:
        if isinstance(node.iter, ast.Call) and _method(node.iter) in {"all", "filter", "search"} and not _has_limit(node.iter):
            self._add("SG002", "medium", node.iter, "Loop iterates an ORM result without an explicit business bound or chunking.")
        self._visit_loop_body(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop_body(node)

    def _visit_comprehension(self, node: ast.AST, generators: list[ast.comprehension]) -> None:
        for generator in generators:
            self.visit(generator.iter)
        self.loop_depth += 1
        self.loop_nodes.append(node)
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.comprehension):
                self.visit(child)
        self.loop_nodes.pop()
        self.loop_depth -= 1

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, node.generators)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node, node.generators)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node, node.generators)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node, node.generators)

    def visit_Call(self, node: ast.Call) -> None:
        method = _method(node)
        call_name = _call_name(node.func)
        finding_node = self.loop_nodes[-1] if self.loop_nodes else node
        if method == "search" and _empty_domain(node) and not _has_limit(node):
            self._add("SG002", "high", node, "Unbounded search with an empty domain; add a real scope or process in chunks.")
        if self.loop_depth and method in WRITE_METHODS:
            strong_orm_signal = method in HMX_METHODS or ".objects" in call_name or "queryset" in call_name or ".qs" in call_name
            if strong_orm_signal or method in {"save", "create", "delete"}:
                severity = "high" if strong_orm_signal or method == "delete" else "medium"
                self._add("SG003", severity, finding_node, f"Possible per-record write `{call_name}` inside a growing loop.")
        elif self.loop_depth and method in READ_METHODS:
            orm_signal = method in HMX_METHODS or ".objects" in call_name or method not in {"get", "count"}
            if orm_signal:
                severity = "high" if method in HMX_METHODS or ".objects" in call_name else "medium"
                self._add("SG001", severity, finding_node, f"Possible ORM read `{call_name}` inside a growing loop.")
        if method.startswith("_compute_"):
            severity = "high" if self.loop_depth else "medium"
            self._add("SG004", severity, finding_node, f"Explicit compute call `{call_name}` bypasses normal dependency invalidation.")
        if isinstance(node.func, ast.Name) and node.func.id in {"list", "tuple", "set"} and node.args:
            inner = node.args[0]
            if isinstance(inner, ast.Call) and _method(inner) in {"all", "filter", "search"} and not _has_limit(inner):
                self._add("SG002", "medium", inner, "Materializes a potentially unbounded ORM result in memory.")
        self.generic_visit(node)


def is_scannable(path: Path, repo: Path) -> bool:
    try:
        relative = path.resolve().relative_to(repo.resolve())
    except ValueError:
        return False
    lowered = {part.lower() for part in relative.parts}
    return (
        path.suffix == ".py"
        and path.is_file()
        and not (lowered & EXCLUDED_PARTS)
        and path.name.lower() not in {"test.py", "tests.py"}
        and not path.name.lower().startswith("test_")
        and not path.name.lower().startswith("tests_")
        and not path.name.lower().endswith("_test.py")
    )


def scan_file(path: Path, repo: Path) -> tuple[list[Finding], str | None]:
    relative = str(path.resolve().relative_to(repo.resolve()))
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return [], f"{relative}: cannot scan: {exc}"
    visitor = ScaleVisitor(relative, source.splitlines())
    visitor.visit(tree)
    unique = sorted(set(visitor.findings), key=lambda item: (item.path, item.line, item.rule, item.message))
    return unique, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--check", action="store_true", help="exit nonzero for high findings or scan errors")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    paths = [Path(item) if Path(item).is_absolute() else repo / item for item in args.files]
    scannable = [path for path in paths if is_scannable(path, repo)]
    findings: list[Finding] = []
    errors: list[str] = []
    for path in scannable:
        file_findings, error = scan_file(path, repo)
        findings.extend(file_findings)
        if error:
            errors.append(error)

    if args.json:
        print(json.dumps({"files_scanned": len(scannable), "findings": [asdict(item) for item in findings], "errors": errors}, indent=2))
    else:
        for finding in findings:
            print(f"{finding.path}:{finding.line}: {finding.severity.upper()} {finding.rule} {finding.message}")
        for error in errors:
            print(f"ERROR {error}")
        print(f"Bundled Scale Guard: scanned={len(scannable)} high={sum(f.severity == 'high' for f in findings)} medium={sum(f.severity == 'medium' for f in findings)} errors={len(errors)}")

    has_high = any(item.severity == "high" for item in findings)
    return 1 if args.check and (has_high or errors) else 0


if __name__ == "__main__":
    raise SystemExit(main())
