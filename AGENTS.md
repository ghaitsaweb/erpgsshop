# Repository agent instructions

## Mandatory skill usage

- Use `$hmx-erp-parity` whenever ERP GS Shop development is requested as HMX
  parity, 1:1 behavior, copying from HMX, or continued work based on HMX. The
  user-designated HMX worktree is the behavioral source; resolve it per server,
  keep it read-only, and do not claim 1:1 when it cannot be inspected.
- Use `$precommit-scale-guard` before every agent-created commit and before an
  authorized push or merge request.
- Also use `$precommit-scale-guard` when Python, Django, or HMX changes affect
  ORM queries, data loading, reports, exports, jobs, or growing datasets.
- Use `$large-scale-feature-performance` for backend paths whose reads, writes,
  reports, exports, jobs, or UI data loading grow with tenant records.
- HMX parity covers module ownership, workflow states and action ordering,
  permissions, transactions, side effects, UI behavior, and failure rollback;
  matching only fields or appearance is insufficient.
- Treat millions of rows as the design scale. Interactive reads must be bounded;
  complete-data work must be chunked or streamed without dropping results; and
  aggregates must remain in the database.
- Run applicable unit tests and query-growth or execution-plan checks in an
  isolated test environment. Report unavailable checks accurately; do not use
  bypass flags or expand a baseline to hide findings.
- These checks do not authorize commit, push, deployment, hook installation,
  CI changes, migrations against live data, or destructive operations.
