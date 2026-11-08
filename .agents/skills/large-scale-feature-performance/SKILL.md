---
name: large-scale-feature-performance
description: Design, implement, or review ERP GS Shop backend features whose reads, writes, reports, exports, jobs, or UI data loading can grow to millions of records. Skip purely cosmetic and static-content changes.
---

# ERP GS Shop large-scale feature performance

Treat millions of records as the design scale, not as a claim that the current
database has already been benchmarked at that size. Preserve business results,
authorization, company and branch isolation, transactions, and retry behavior.
This skill does not authorize production data changes, infrastructure changes,
new indexes, migrations, deployment, commits, or pushes.

Before changing a growing data path, read `docs/performance-at-scale.md` and
classify the path as an exact-record operation, bounded interactive page,
complete-data batch, or database aggregate. Record the likely cardinality,
scope, ordering, output bound, and most expensive query. Unknown values are
assumptions and must lead to bounded resource use, not invented benchmarks.

Apply these invariants:

- Never put database, network, or file-service calls in loops that grow with
  stored records. Batch or prefetch once and map in memory.
- Bound interactive reads by authorization plus a real business bound such as
  a key range, cursor, period, state, or validated page size. Company scope
  alone is not a size bound. Prefer keyset pagination for deep histories.
- Stream or chunk complete-data work with bounded memory and resumable progress;
  never use a silent limit that drops business results.
- Aggregate counts and totals in the database. Preserve bulk writes and define
  idempotency, conflicts, transaction boundaries, and retry safety.
- Move legitimate long-running work out of request handlers. Give background
  work progress, backpressure, and bounded batches.
- Match hot filters and deterministic ordering to existing indexes. Propose a
  focused index only with query-plan evidence and write-cost analysis.

For every applicable change, also use `$precommit-scale-guard`. Run focused
business and authorization tests, then compare SQL query counts for equivalent
small and larger fixtures. Inspect `EXPLAIN` for a new or changed hot query when
a safe representative database is available. Exercise pagination boundaries,
empty results, retry behavior, and bounded memory where relevant.

Report fixture sizes, query counts or plans, and limitations. Static analysis
and small fixtures do not prove production-scale performance; unavailable
representative tests must be reported as `NOT RUN`, never `PASS`.
