#!/usr/bin/env python3
"""Run configured pre-commit plus native and bundled Scale Guard checks."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def run(command: list[str], cwd: Path, *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=capture, check=False)


def git(repo: Path, *args: str) -> list[str]:
    result = run(["git", *args], repo, capture=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return [line for line in result.stdout.splitlines() if line]


def changed_files(repo: Path, scope: str) -> tuple[str, list[str]]:
    staged = git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR")
    if scope == "auto":
        scope = "staged" if staged else "working"
    if scope == "staged":
        names = staged
    else:
        names = git(repo, "diff", "HEAD", "--name-only", "--diff-filter=ACMR")
        names += git(repo, "ls-files", "--others", "--exclude-standard")
    names = sorted({name for name in names if (repo / name).is_file()})
    return scope, names


def precommit_binary(repo: Path) -> str | None:
    candidates = [
        shutil.which("pre-commit"),
        str(repo / ".venv" / "bin" / "pre-commit"),
        str(repo / "venv" / "bin" / "pre-commit"),
    ]
    return next((item for item in candidates if item and Path(item).is_file()), None)


def run_precommit(repo: Path, files: list[str]) -> tuple[str, int]:
    config = next((repo / name for name in (".pre-commit-config.yaml", ".pre-commit-config.yml") if (repo / name).is_file()), None)
    if config is None:
        print("pre-commit: NOT CONFIGURED — no .pre-commit-config.yaml or .yml")
        return "NOT CONFIGURED", 0
    executable = precommit_binary(repo)
    if executable is None:
        print("pre-commit: NOT RUN — configuration exists but executable is unavailable")
        return "NOT RUN", 0
    if not files:
        print("pre-commit: NOT APPLICABLE — selected scope has no files")
        return "NOT APPLICABLE", 0
    result = run([executable, "run", "--files", *files], repo)
    status = "PASS" if result.returncode == 0 else "FAIL"
    print(f"pre-commit: {status}")
    return status, result.returncode


def run_native_scale_guard(repo: Path, merge_base: str | None) -> tuple[str, int]:
    checker = repo / "tools" / "scale_guard.py"
    if not checker.is_file():
        print("native Scale Guard: NOT AVAILABLE — tools/scale_guard.py is absent")
        return "NOT AVAILABLE", 0
    if not merge_base:
        print("native Scale Guard: NOT RUN — pass a verified --merge-base")
        return "NOT RUN", 0
    command = [sys.executable, str(checker), "--root", str(repo), "--changed", merge_base]
    baseline = repo / "tools" / "scale_guard_baseline.json"
    if baseline.is_file():
        command.extend(["--baseline", str(baseline)])
    command.extend(["--severity", "high", "--check", "--list"])
    result = run(command, repo)
    status = "PASS" if result.returncode == 0 else "FAIL"
    print(f"native Scale Guard: {status}")
    return status, result.returncode


def run_bundled_scale_guard(repo: Path, files: list[str]) -> tuple[str, int]:
    python_files = [name for name in files if name.endswith(".py")]
    if not python_files:
        print("bundled SG001-SG004 scan: NOT APPLICABLE — no changed Python files")
        return "NOT APPLICABLE", 0
    scanner = Path(__file__).with_name("scale_guard_scan.py")
    result = run([sys.executable, str(scanner), "--repo", str(repo), "--check", *python_files], repo)
    status = "PASS" if result.returncode == 0 else "FAIL"
    print(f"bundled SG001-SG004 scan: {status}")
    return status, result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--scope", choices=("auto", "staged", "working"), default="auto")
    parser.add_argument("--merge-base", help="verified target merge-base SHA for the native checker")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    try:
        root = Path(git(repo, "rev-parse", "--show-toplevel")[0]).resolve()
        scope, files = changed_files(root, args.scope)
    except (IndexError, RuntimeError) as exc:
        print(f"quality gate: ERROR — {exc}", file=sys.stderr)
        return 2

    print(f"repository: {root}")
    print(f"scope: {scope}; files: {len(files)}")
    _, precommit_code = run_precommit(root, files)
    _, native_code = run_native_scale_guard(root, args.merge_base)
    _, bundled_code = run_bundled_scale_guard(root, files)
    print("unit tests: NOT RUN — select the repository-supported isolated test command for changed behavior")
    print("scale tests: NOT RUN — run query-growth or plan checks when the changed path has scale risk")
    return 1 if any((precommit_code, native_code, bundled_code)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
