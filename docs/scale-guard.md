# ERP GS Shop Scale Guard

This checker is adapted from HMX Scale Guard at
`/home/yusufmjt/hmx-human-resources-port8000-latest/tools/scale_guard.py`.
It preserves the HMX rule IDs, CLI shape, fingerprints, baselines, and
reviewable allow-comment contract, but its AST engine targets Django ORM.

The HMX baseline is intentionally not copied: its fingerprints describe Odoo
source paths and would hide unrelated findings. ERP GS Shop starts with an empty
baseline and scans tracked changes plus untracked Python source files.

## Rules

| Rule | Django risk | Expected correction |
| --- | --- | --- |
| SG001 | ORM query while looping over a QuerySet/relation | Batch the read and map results in memory. |
| SG002 | Evaluating a QuerySet without a page, cursor, slice, aggregate, or explicit iterator | Bound interactive output or chunk complete-data work. |
| SG003 | Per-record create/update/save/delete in a growing loop | Use a bulk or grouped write with explicit transaction semantics. |
| SG004 | Calling `_compute_*`/`_recompute_*` explicitly | Use normal dependency/invalidation flow or recompute one batch. |

The checker is deliberately syntactic. It complements query-growth tests and
database execution plans; it does not replace them. A helper can hide a query,
and a static warning cannot prove production latency.

An exact primary-key filter or an explicit `__in` collection is treated as
input-bounded. The producer of that collection must still apply its own page,
batch, or document-size policy; the checker does not prove the collection is
small.

## Usage

```bash
# Current tracked and untracked source changes
python tools/scale_guard.py --root "$PWD" --changed HEAD \
  --baseline tools/scale_guard_baseline.json --check --list

# Explicit paths, all severities
python tools/scale_guard.py addons/ai core modules --root "$PWD" --list
```

## Pre-commit

The repository ships a local-only `.pre-commit-config.yaml`. It has no remote
hook dependencies: it runs Scale Guard only for selected Python files and runs
the checker unit tests when checker/config files change. Install the developer
tool from `requirements-dev.txt`, then invoke it normally:

```bash
python -m pip install -r requirements-dev.txt
pre-commit run --files <changed-files>
```

Agents use the repository runner for the exact working or staged scope:

```bash
python .agents/skills/precommit-scale-guard/scripts/run_checks.py \
  --repo . --scope working
```

Query-growth tests and execution-plan review are still required when the changed
path has scale risk; a fast static hook cannot prove million-record performance.
See `docs/performance-at-scale.md` and the repo-local
`$large-scale-feature-performance` skill.

The commit hook blocks new `high` and `medium` findings in selected changed
files. Fix them or add a rule-specific, concrete false-positive annotation.
The repository-wide audit currently reports zero new high or medium findings.
The former 49 legacy medium candidates were resolved without adding them to the
empty baseline; five reviewed medium allowances remain for explicitly bounded
runtime or fixed-configuration loops.

Do not regenerate or expand the baseline to make a change pass. Prefer fixing
the query shape. A deliberate exception must be immediately above the finding,
name its rule, and contain a concrete reason of at least 12 characters:

```python
# scale-guard: allow SG001 - bounded admin preview with at most ten records
```

No `.git/hooks` entry or CI job is installed by this addition. The tracked
pre-commit configuration is available to developer environments without
modifying repository metadata automatically.
