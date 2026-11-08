#!/usr/bin/env python3
"""ERP GS Shop Scale Guard, adapted from the HMX static gate.

The HMX checker targets Odoo-style recordsets. This port preserves its CLI,
rule identifiers, fingerprints, baseline contract, and reviewable annotation,
while recognizing Django QuerySet evaluation and per-row writes.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass


RULES = {
    "SG001": "ORM query inside a loop whose size grows with stored data",
    "SG002": "evaluated QuerySet without a bounded page, cursor, or iterator",
    "SG003": "per-record ORM write inside a growing loop",
    "SG004": "explicit compute/recompute helper call",
}

FIXES = {
    "SG001": "Read once for the whole scope, map results in memory, then loop.",
    "SG002": "Add keyset/page bounds, aggregate in SQL, or use an explicit chunked iterator.",
    "SG003": "Use bulk_create/bulk_update or one grouped update outside the growing loop.",
    "SG004": "Use normal dependency/invalidation flow, or recompute the complete batch once.",
}

SOURCE_ROOTS = ("addons/", "core/", "erp_gsshop/", "modules/")
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "demo",
    "demos",
    "management",
    "migrations",
    "node_modules",
    "test",
    "tests",
    "versions",
}

QUERY_METHODS = {
    "aggregate",
    "aaggregate",
    "acount",
    "aexists",
    "afirst",
    "aget",
    "alast",
    "count",
    "earliest",
    "exists",
    "first",
    "get",
    "in_bulk",
    "latest",
    "last",
    "refresh_from_db",
}
WRITE_METHODS = {
    "acreate",
    "adelete",
    "aget_or_create",
    "aupdate",
    "aupdate_or_create",
    "asave",
    "create",
    "delete",
    "get_or_create",
    "save",
    "update",
    "update_or_create",
}
QUERYSET_BUILDERS = {
    "alias",
    "all",
    "annotate",
    "defer",
    "distinct",
    "exclude",
    "filter",
    "none",
    "only",
    "order_by",
    "prefetch_related",
    "select_for_update",
    "select_related",
    "union",
    "values",
    "values_list",
}
EVALUATORS = {"list", "set", "tuple"}
TENANT_ONLY_FILTERS = {
    "active",
    "archived",
    "branch",
    "branch_id",
    "company",
    "company_id",
    "enabled",
    "is_active",
    "state",
    "status",
}

ALLOW_RE = re.compile(
    r"#\s*scale-guard:\s*allow\s+(SG\d{3})\s*[-—–:]\s*(?P<reason>.+)$"
)
MIN_REASON_CHARS = 12


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    path: str
    line: int
    function: str
    detail: str
    code: str
    suppressed: bool = False
    suppression_reason: str = ""

    @property
    def fingerprint(self) -> str:
        key = f"{self.rule}|{self.path}|{self.function}|{self.code.strip()}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "function": self.function,
            "detail": self.detail,
            "code": self.code,
            "fingerprint": self.fingerprint,
            "suppressed": self.suppressed,
            "suppression_reason": self.suppression_reason,
        }


def _attribute_name(node: ast.AST) -> str:
    return node.attr if isinstance(node, ast.Attribute) else ""


def _root_names(node: ast.AST) -> set[str]:
    names = set()
    current = node
    while isinstance(current, (ast.Attribute, ast.Call, ast.Subscript)):
        if isinstance(current, ast.Attribute):
            names.add(current.attr)
            current = current.value
        elif isinstance(current, ast.Call):
            current = current.func
        else:
            current = current.value
    if isinstance(current, ast.Name):
        names.add(current.id)
    return names


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        result = set()
        for item in node.elts:
            result.update(_target_names(item))
        return result
    return set()


class DjangoScaleVisitor(ast.NodeVisitor):
    def __init__(self, path: str, source: str):
        self.path = path
        self.source = source
        self.lines = source.splitlines()
        self.findings: list[Finding] = []
        self.queryset_vars: dict[str, str] = {}
        self.loop_stack: list[tuple[str, set[str]]] = []
        self.function_stack: list[str] = []

    @property
    def function(self) -> str:
        return self.function_stack[-1] if self.function_stack else "<module>"

    @property
    def loop_kind(self) -> str:
        return self.loop_stack[-1][0] if self.loop_stack else ""

    @property
    def loop_vars(self) -> set[str]:
        result = set()
        for _kind, names in self.loop_stack:
            result.update(names)
        return result

    @property
    def orm_loop_vars(self) -> set[str]:
        result = set()
        for kind, names in self.loop_stack:
            if kind in {"queryset", "relation"}:
                result.update(names)
        return result

    def _line(self, node: ast.AST) -> str:
        lineno = getattr(node, "lineno", 0)
        return self.lines[lineno - 1].strip() if 0 < lineno <= len(self.lines) else ""

    def _allow(self, node: ast.AST, rule: str) -> tuple[bool, str]:
        lineno = getattr(node, "lineno", 0)
        if lineno < 2:
            return False, ""
        match = ALLOW_RE.search(self.lines[lineno - 2])
        if not match:
            return False, ""
        annotated_rule = match.group(1)
        reason = match.group("reason").strip()
        if annotated_rule and annotated_rule != rule:
            return False, ""
        if len(reason) < MIN_REASON_CHARS:
            return False, ""
        return True, reason

    def add(self, node: ast.AST, rule: str, detail: str, *, severity: str | None = None):
        severity = severity or ("high" if self.loop_kind in {"queryset", "relation"} else "medium")
        suppressed, reason = self._allow(node, rule)
        self.findings.append(
            Finding(
                rule=rule,
                severity=severity,
                path=self.path,
                line=getattr(node, "lineno", 0),
                function=self.function,
                detail=detail,
                code=self._line(node),
                suppressed=suppressed,
                suppression_reason=reason,
            )
        )

    def _is_queryset(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Subscript):
            return self._is_queryset(node.value)
        if isinstance(node, ast.Name):
            return node.id in self.queryset_vars
        if isinstance(node, ast.Attribute):
            return node.attr in {"objects", "related_manager"} or self._is_queryset(node.value)
        if not isinstance(node, ast.Call):
            return False
        if isinstance(node.func, ast.Name):
            return node.func.id in self.queryset_vars
        method = _attribute_name(node.func)
        receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
        if method in QUERYSET_BUILDERS or method in {"iterator", "aiterator"}:
            return bool(
                receiver is not None
                and (
                    self._is_queryset(receiver)
                    or "objects" in _root_names(receiver)
                )
            )
        return False

    def _filter_is_meaningfully_scoped(self, node: ast.Call) -> bool:
        if node.args:
            return True
        keys = {keyword.arg for keyword in node.keywords if keyword.arg}
        if not keys:
            return False
        base_keys = {key.split("__", 1)[0] for key in keys}
        return bool(base_keys - TENANT_ONLY_FILTERS)

    def _filter_has_explicit_input_bound(self, node: ast.Call) -> bool:
        if node.args:
            return False
        keys = {keyword.arg for keyword in node.keywords if keyword.arg}
        return bool(
            keys & {"id", "pk"}
            or any(key.endswith("__in") for key in keys)
        )

    def _queryset_scope(self, node: ast.AST) -> str:
        if isinstance(node, ast.Subscript):
            return "bounded"
        if isinstance(node, ast.Name):
            return self.queryset_vars.get(node.id, "unbounded")
        if isinstance(node, ast.Attribute):
            return "unbounded" if node.attr == "objects" else "scoped"
        if not isinstance(node, ast.Call):
            return "unbounded"
        method = _attribute_name(node.func)
        receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
        if method in {"iterator", "aiterator", "none"}:
            return "bounded"
        inherited = self._queryset_scope(receiver) if receiver is not None else "unbounded"
        if method == "filter" and self._filter_has_explicit_input_bound(node):
            return "bounded"
        if method == "filter" and self._filter_is_meaningfully_scoped(node):
            return "scoped" if inherited != "bounded" else "bounded"
        return inherited

    def _is_bounded_queryset(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Subscript):
            return isinstance(node.slice, (ast.Slice, ast.Constant))
        if isinstance(node, ast.Call) and _attribute_name(node.func) in {"iterator", "aiterator", "none"}:
            return True
        return False

    def _iter_kind(self, node: ast.AST) -> str:
        if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Constant)):
            return "constant"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "range":
            return "constant" if all(isinstance(arg, ast.Constant) for arg in node.args) else "unknown"
        if self._is_queryset(node):
            return "queryset"
        if isinstance(node, ast.Attribute) and (node.attr.endswith("_set") or node.attr.endswith("_ids")):
            return "relation"
        return "unknown"

    def _receiver_is_orm(self, call: ast.Call) -> bool:
        if not isinstance(call.func, ast.Attribute):
            return False
        receiver = call.func.value
        roots = _root_names(receiver)
        return self._is_queryset(receiver) or "objects" in roots or bool(roots & self.orm_loop_vars)

    def _evaluates_unbounded_queryset(self, node: ast.AST):
        if self._is_queryset(node) and not self._is_bounded_queryset(node):
            scope = self._queryset_scope(node)
            if scope == "bounded":
                return
            severity = "medium" if scope == "scoped" else "high"
            detail = (
                "QuerySet has a business scope but no hard page/cursor/chunk bound; review complete-data behavior."
                if scope == "scoped"
                else "QuerySet is evaluated without a selective business scope or hard page/cursor/chunk bound."
            )
            self.add(
                node,
                "SG002",
                detail,
                severity=severity,
            )

    def visit_FunctionDef(self, node: ast.FunctionDef):
        previous_queryset_vars = self.queryset_vars
        self.queryset_vars = {}
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()
        self.queryset_vars = previous_queryset_vars

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign):
        self.visit(node.value)
        if self._is_queryset(node.value) and not self._is_bounded_queryset(node.value):
            for target in node.targets:
                for name in _target_names(target):
                    self.queryset_vars[name] = self._queryset_scope(node.value)
        for target in node.targets:
            self.visit(target)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        if node.value is not None:
            self.visit(node.value)
            if self._is_queryset(node.value) and not self._is_bounded_queryset(node.value):
                for name in _target_names(node.target):
                    self.queryset_vars[name] = self._queryset_scope(node.value)

    def visit_For(self, node: ast.For):
        self.visit(node.iter)
        self._evaluates_unbounded_queryset(node.iter)
        kind = self._iter_kind(node.iter)
        self.loop_stack.append((kind, _target_names(node.target)))
        for statement in node.body:
            self.visit(statement)
        self.loop_stack.pop()
        for statement in node.orelse:
            self.visit(statement)

    visit_AsyncFor = visit_For

    def _visit_comprehension(self, node):
        pushed = 0
        for generator in node.generators:
            self.visit(generator.iter)
            self._evaluates_unbounded_queryset(generator.iter)
            self.loop_stack.append((self._iter_kind(generator.iter), _target_names(generator.target)))
            pushed += 1
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        for _ in range(pushed):
            self.loop_stack.pop()

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension
    visit_DictComp = _visit_comprehension

    def visit_Call(self, node: ast.Call):
        method = _attribute_name(node.func)
        if method.startswith("_compute_") or method.startswith("_recompute_"):
            self.add(
                node,
                "SG004",
                f"Explicit {method}() call bypasses normal dependency/invalidation flow.",
                severity="high" if self.loop_stack else "medium",
            )
        if self.loop_stack and method in QUERY_METHODS and self._receiver_is_orm(node):
            self.add(node, "SG001", f"{method}() executes an ORM read inside a growing loop.")
        if self.loop_stack and method in WRITE_METHODS and self._receiver_is_orm(node):
            self.add(node, "SG003", f"{method}() executes an ORM write inside a growing loop.")
        if isinstance(node.func, ast.Name) and node.func.id in EVALUATORS and node.args:
            argument = node.args[0]
            if self.loop_stack and self._is_queryset(argument):
                self.add(node, "SG001", f"{node.func.id}() evaluates a QuerySet inside a growing loop.")
            elif self._is_queryset(argument):
                self._evaluates_unbounded_queryset(argument)
        self.generic_visit(node)


def is_scannable(path: pathlib.Path) -> bool:
    is_test_module = (
        path.name in {"test.py", "tests.py"}
        or path.name.startswith("test_")
        or path.name.startswith("tests_")
        or path.name.endswith("_test.py")
    )
    return (
        path.suffix == ".py"
        and not is_test_module
        and not any(part in EXCLUDED_PARTS for part in path.parts)
    )


def scan_source(source: str, *, path: str = "<memory>") -> list[Finding]:
    tree = ast.parse(source, filename=path)
    visitor = DjangoScaleVisitor(path, source)
    visitor.visit(tree)
    unique = {}
    for finding in visitor.findings:
        unique[(finding.rule, finding.line, finding.code)] = finding
    return sorted(unique.values(), key=lambda item: (item.path, item.line, item.rule))


def iter_python_files(targets: list[pathlib.Path], root: pathlib.Path):
    seen = set()
    for target in targets:
        resolved = target if target.is_absolute() else root / target
        if resolved.is_file():
            candidates = [resolved]
        elif resolved.is_dir():
            candidates = resolved.rglob("*.py")
        else:
            continue
        for candidate in candidates:
            relative = candidate.resolve().relative_to(root)
            if relative in seen or not is_scannable(relative):
                continue
            seen.add(relative)
            yield candidate, relative


def scan_paths(targets: list[pathlib.Path], *, root: pathlib.Path) -> tuple[list[Finding], list[str]]:
    findings = []
    errors = []
    for absolute, relative in iter_python_files(targets, root):
        try:
            source = absolute.read_text(encoding="utf-8")
            findings.extend(scan_source(source, path=relative.as_posix()))
        except (OSError, UnicodeError, SyntaxError) as exc:
            errors.append(f"{relative}: {exc}")
    return findings, errors


def _git_lines(root: pathlib.Path, args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    return [line for line in result.stdout.splitlines() if line]


def changed_files(ref: str, root: pathlib.Path) -> list[pathlib.Path]:
    tracked = _git_lines(root, ["diff", "--name-only", "--diff-filter=ACMR", ref])
    untracked = _git_lines(root, ["ls-files", "--others", "--exclude-standard"])
    paths = []
    for raw in dict.fromkeys([*tracked, *untracked]):
        if raw.endswith(".py") and raw.startswith(SOURCE_ROOTS):
            path = pathlib.Path(raw)
            if is_scannable(path):
                paths.append(path)
    return paths


def _baseline(path: str | None, root: pathlib.Path) -> set[str]:
    if not path:
        return set()
    candidate = pathlib.Path(path)
    candidate = candidate if candidate.is_absolute() else root / candidate
    if not candidate.exists():
        return set()
    return set(json.loads(candidate.read_text()).get("fingerprints", []))


def render(findings: list[Finding], known: set[str], *, show: bool, limit: int) -> str:
    active = [finding for finding in findings if not finding.suppressed]
    new = [finding for finding in active if finding.fingerprint not in known]
    known_rows = [finding for finding in active if finding.fingerprint in known]
    suppressed = [finding for finding in findings if finding.suppressed]
    lines = ["=" * 74, "ERP GS Shop Scale Guard", "=" * 74]
    lines.append(
        f"Findings: {len(findings)} total, {len(new)} new, {len(known_rows)} baseline, {len(suppressed)} allowed."
    )
    for rule in RULES:
        count = sum(finding.rule == rule for finding in new)
        lines.append(f"  {rule}: {count} new - {RULES[rule]}")
    if show and findings:
        lines.extend(["", "Findings", "-" * 74])
        for finding in findings[:limit]:
            state = "allowed" if finding.suppressed else "baseline" if finding.fingerprint in known else "NEW"
            lines.append(
                f"{finding.path}:{finding.line} [{finding.rule}/{finding.severity}/{state}] "
                f"{finding.function}() - {finding.detail}"
            )
            lines.append(f"    {finding.code}")
            lines.append(f"    Fix: {FIXES[finding.rule]}")
            if finding.suppressed:
                lines.append(f"    Allowed because: {finding.suppression_reason}")
        if len(findings) > limit:
            lines.append(f"... {len(findings) - limit} more finding(s) not shown.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ERP GS Shop Django Scale Guard")
    parser.add_argument("paths", nargs="*", default=["addons", "core", "modules"])
    parser.add_argument("--root", default=".")
    parser.add_argument("--changed", metavar="REF")
    parser.add_argument("--severity", choices=["high", "medium"])
    parser.add_argument("--baseline")
    parser.add_argument("--write-baseline")
    parser.add_argument("--json")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--max-list", type=int, default=40)
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    targets = changed_files(args.changed, root) if args.changed else [pathlib.Path(path) for path in args.paths]
    findings, errors = scan_paths(targets, root=root)
    if args.severity:
        findings = [finding for finding in findings if finding.severity == args.severity]
    known = _baseline(args.baseline, root)

    if args.write_baseline:
        destination = pathlib.Path(args.write_baseline)
        destination = destination if destination.is_absolute() else root / destination
        destination.write_text(
            json.dumps(
                {
                    "count": len([finding for finding in findings if not finding.suppressed]),
                    "fingerprints": sorted(
                        finding.fingerprint for finding in findings if not finding.suppressed
                    ),
                },
                indent=2,
            )
            + "\n"
        )
    if args.json:
        destination = pathlib.Path(args.json)
        destination = destination if destination.is_absolute() else root / destination
        destination.write_text(json.dumps([finding.to_dict() for finding in findings], indent=2) + "\n")

    print(render(findings, known, show=args.list, limit=max(1, args.max_list)))
    if errors:
        print("\nScan errors:", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 2
    new = [
        finding
        for finding in findings
        if not finding.suppressed and finding.fingerprint not in known
    ]
    if args.check and new:
        print(f"\nFAILED: {len(new)} new finding(s) outside the baseline.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
