# ERP GS Shop performance at scale

ERP GS Shop backend work must remain bounded as tenant data grows toward
millions of rows. This is a design target, not evidence that every path has been
load-tested with a million-row fixture.

## Classify every growing data path

| Path | Required shape |
| --- | --- |
| Exact record | Scoped authorization and an exact indexed key. |
| Interactive list | Selective filters, deterministic order, validated page size, and preferably keyset pagination for deep history. |
| Complete-data work | Explicit chunks or streaming, bounded memory, resumable progress, retry safety, and no dropped business results. |
| Count, total, dashboard | Database aggregation over the required business scope. |

Company or branch isolation is mandatory, but it is not by itself a cardinality
bound. Add an appropriate cursor, key range, period, state, or page size.

## Implementation invariants

- Do not execute ORM, network, or file-service calls inside loops that grow with
  records. Batch reads and writes, then map results in memory.
- Avoid large offsets for unbounded history; use deterministic keyset cursors.
- Keep request handlers bounded. Long exports, imports, recomputations, and
  fan-out belong in background jobs with bounded batches and progress.
- Preserve complete results: an arbitrary `limit` is not a valid fix for a
  complete report or financial operation.
- Define transaction boundaries, idempotency, conflict behavior, and retry
  behavior for bulk jobs.
- Check hot predicates and ordering against existing indexes. Add an index only
  with a reviewed migration, query-plan evidence, and write-cost analysis.
- Scope caches by tenant and authorization, and define expiry/invalidation.
  Caching must not hide an unbounded query.

## Required evidence

Run the repository pre-commit and Scale Guard checks. For an ORM path whose cost
can grow per row, compare query counts on equivalent small and larger fixtures,
such as 2 versus 12 related rows. A fixed query count is the objective; do not
inflate an allowance just to pass. For a new hot query, inspect PostgreSQL
`EXPLAIN` in an isolated environment when practical and record whether the plan
is estimated or measured.

Also test authorization and company/branch isolation, pagination boundaries,
empty results, duplicate/retry behavior, and bounded memory for complete-data
work. Report unavailable representative testing as `NOT RUN`; static checks and
small fixtures alone do not prove production-scale latency.

## Current verification snapshot

On 8 November 2026, the full-repository high/medium Scale Guard gate passed
with an empty baseline and no new findings. Six high allowances cover bounded
deployment/configuration catalogues. Five medium allowances cover a hard-capped
12-iteration agent trace, two bounded 500-row Finance paths, and two operations
across the fixed five employee-relation model types. The checker and fallback
scanner have 24 passing unit tests.

The school-accounting provisioner uses 500-row iterator batches; its
query-growth test measured 3 SQL queries for both 2 and 12 schools. School
invoice generation processes at most 500 due lines per run and leaves the
remainder pending. Recurring journals process at most 100 schedules per beat.
POS accounting rejects orders above 1,000 lines or 100 payments. Asset disposal
selection measured 1 SQL query for both 2 and 12 candidates after removing its
per-record reread. Work-intelligence aggregation measured 10 SQL queries for
both 2 and 12 selected events after replacing per-event aggregate writes with
batched reads and updates. The combined Finance and AppHub suite passed 31
tests; the repository-wide affected-module suite passed 328 tests in 104.518
seconds.

The former 49 legacy medium candidates in Construction, HR, Finance,
Organization, Approval, POS, Purchase, and AI were resolved without expanding
the baseline. Complete-data paths now use chunked iterators and batched writes;
the contract workspace caps each interactive related-data list at 100 recent
rows; role provisioning creates missing groups in one bulk operation; pricing
overlap validation uses one selective `exists()` query. This evidence still
does not claim a million-row load test or measured production latency.
