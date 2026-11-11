---
name: precommit-scale-guard
description: Run repository verification before any commit, push, or merge request, and when Python/Django/HMX changes affect ORM queries or growing datasets. Detect configured pre-commit and native Scale Guard tooling, run the bundled SG001-SG004 fallback scanner when native tooling is absent, require applicable unit and query-growth tests, and report exact PASS, FAIL, NOT RUN, or NOT APPLICABLE results.
---

# Pre-commit and Scale Guard

Use this skill before every agent-created commit and before an authorized push
or merge request. Also use it while implementing or reviewing backend paths
whose cost can grow with records. It does not authorize a commit, push,
deployment, hook installation, CI edit, baseline expansion, or bypass.

## Run the deterministic checks

Resolve the repository root and inspect staged and unstaged changes. Preserve
partial staging and unrelated user work.

For a proposed commit, run:

```bash
python .agents/skills/precommit-scale-guard/scripts/run_checks.py --repo . --scope staged
```

For uncommitted work that has not been staged, use `--scope working`. Before a
push or merge request, also pass the verified target merge base with
`--merge-base <sha>` so a repository-native Scale Guard checker can compare the
correct range. Never guess a target branch merely to produce a result.

The runner:

- runs repository-configured pre-commit on the selected files when both config
  and executable exist;
- runs `tools/scale_guard.py` with its baseline on selected working/staged files
  when available, or against a verified merge base for push/MR review;
- uses the bundled SG001-SG004 scanner only when native tooling is unavailable;
  the repository-native checker already includes untracked working files;
- prints separate statuses so the fallback is never presented as the native
  repository gate.

If pre-commit formats files, inspect the resulting diff, stage only intended
changes, and rerun against the final content. Do not use `--no-verify`, `SKIP`,
`SCALE_GUARD_SKIP`, or baseline growth to make checks pass.

## Review and tests

Inspect every bundled finding. Fix new high findings. Fix medium findings or,
when review proves a genuine false positive, suppress them with a specific
comment immediately above the finding:

```python
# scale-guard: allow SG001 — bounded by the 25-row validated request limit
```

The reason must be concrete and at least 12 characters. Preserve access rules,
company isolation, validation, transaction behavior, and complete business
results while batching.

### Use the smallest meaningful test scope

Derive test labels from the files and behavior actually changed. During
iteration, run the affected test class or method first. Before handoff, run the
owning module's focused suite plus only its direct integration points. Do not
select a repository-wide suite, a broad app label, or every installed module
merely because that command is convenient.

Expand the scope when the change affects shared/core contracts, routing,
permissions, middleware, migrations, or another module's public integration;
also expand it when a focused failure shows a wider dependency. Report the
exact labels run and why they cover the change. Focused scope must not be used
to hide a known related failure.

For Django repositories with a verified isolated test database, prefer
`--keepdb` during repeated runs so migrations are reused. Confirm that the
database name is a test database and that the command cannot target normal or
production data. A first-time database may still require all project
migrations; do not start that expensive setup silently when a narrower existing
test environment or a targeted smoke check can provide useful feedback first.
If a focused test label still triggers a first-time all-application schema
bootstrap, stop before waiting for that broad setup unless the repository gate
or user explicitly requires it. Run the narrow static/runtime checks that are
available and report the unit test as `NOT RUN — isolated test database not
ready`; never describe an all-application bootstrap as focused execution.
Never trade isolation, authorization coverage, or query-growth evidence for
speed, and never use the application database as a test database.

Run the repository's supported unit tests for changed behavior in an isolated
test database. For N+1 or query-growth risk, compare a small and larger fixture
with the repository query-count helper when available. For new hot queries,
inspect an execution plan in a safe test environment when practical. A small
unit test does not prove million-record performance; report fixture sizes,
query counts or plans, and limits of the evidence.

## Report

Report these independently:

- pre-commit: `PASS`, `FAIL`, `NOT RUN`, or `NOT CONFIGURED`;
- native Scale Guard: `PASS`, `FAIL`, `NOT RUN`, `NOT AVAILABLE`, or
  `NOT APPLICABLE`;
- bundled SG001-SG004 scan: `PASS`, `FAIL`, or `NOT APPLICABLE`;
- unit tests and scale tests: `PASS`, `FAIL`, `NOT RUN`, or `NOT APPLICABLE`,
  with the exact scope.

Do not claim that missing repository tooling passed. Do not claim production
scale from static analysis or small fixtures.
