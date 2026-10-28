# Repository agent instructions

## Mandatory skill usage

- Use `$precommit-scale-guard` before every agent-created commit and before an
  authorized push or merge request.
- Also use `$precommit-scale-guard` when Python, Django, or HMX changes affect
  ORM queries, data loading, reports, exports, jobs, or growing datasets.
- Run applicable unit tests and query-growth or execution-plan checks in an
  isolated test environment. Report unavailable checks accurately; do not use
  bypass flags or expand a baseline to hide findings.
- These checks do not authorize commit, push, deployment, hook installation,
  CI changes, migrations against live data, or destructive operations.
