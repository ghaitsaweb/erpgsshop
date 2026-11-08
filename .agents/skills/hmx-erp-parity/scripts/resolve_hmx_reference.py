#!/usr/bin/env python3
"""Resolve a user-designated HMX reference worktree without mutating it."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def git_value(path, *args):
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def main():
    parser = argparse.ArgumentParser(
        description="Resolve and validate the HMX worktree used as the parity reference."
    )
    parser.add_argument("--hmx-worktree", help="Exact HMX worktree supplied by the user.")
    args = parser.parse_args()

    raw_path = args.hmx_worktree or os.environ.get("HMX_REFERENCE_WORKTREE", "")
    source = "argument" if args.hmx_worktree else "HMX_REFERENCE_WORKTREE"
    if not raw_path:
        parser.error("provide --hmx-worktree or set HMX_REFERENCE_WORKTREE")

    candidate = Path(raw_path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        parser.error(f"HMX worktree cannot be resolved: {exc}")
    if not resolved.is_dir() or not os.access(resolved, os.R_OK | os.X_OK):
        parser.error("HMX worktree must be a readable directory")

    markers = [resolved / "hmx" / "module", resolved / "module"]
    marker = next((item for item in markers if item.is_dir()), None)
    if marker is None:
        parser.error("the directory does not contain hmx/module or module")

    git_root = git_value(resolved, "rev-parse", "--show-toplevel")
    commit = git_value(resolved, "rev-parse", "HEAD")
    output = {
        "source": source,
        "worktree": str(resolved),
        "module_root": str(marker.resolve()),
        "git_root": git_root or None,
        "commit": commit or None,
        "readable": True,
        "mode": "reference-read-only",
    }
    json.dump(output, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
