# HMX AI Functional Parity Matrix

Source baseline: `/home/yusufmjt/hmx-human-resources-port8000-latest/hmx`, rechecked on 6 November 2026 at source commit `0cbc0c2266`.

This is a functional comparison. HMX is not imported into ERP GS Shop: the Django add-ons retain their own models, access controls, and deployment lifecycle. A row is not `MATCH` merely because it has a similar menu or model name; it needs equivalent access, data, action, and output evidence.

| HMX module / behavior | ERP GS Shop add-on | Current state | Evidence / remaining work |
| --- | --- | --- | --- |
| `ai`: provider-backed Hashy chat, sessions, attachments, feedback, proactive greeting | `erpshop_moduleai_assistant` | PARTIAL | Company-scoped sessions support HMX-aligned rename/archive/restore, HTTPS provider policy, bounded history/files, feedback and auditable work episodes. Streaming and Hashy OS launch-token verification remain separate integrations. |
| `ai`: local/base knowledge supplied to chat | `erpshop_moduleai_assistant` | PARTIAL | Company profile, reusable category vocabulary, structured company business-flow references, document chunks, request-relevant PostgreSQL full-text retrieval, source labels, and a bounded local knowledge graph are implemented. Documents and record-model mappings mirror HMX `division_id` through company-wide-or-Branch scope; graph projections carry the same scope. HMX-shaped flow-map, generation-prerequisite, asynchronous draft-generation/job, and non-chain-of-thought reason endpoints are available. Semantic/vector retrieval, live graph-scenario comparison, and live provider acceptance still require separate verification. |
| `ai`: digest and work-profile intelligence | `erpshop_moduleai_assistant` | PARTIAL | Company-scoped digest rules, records, work episodes, aggregate signals, and bounded refresh jobs exist. HMX runtime scenario comparison remains required. |
| `ai_coworker`: provider/model/pricing, credits, sessions, memory, subagents, cron and heartbeat | `erpshop_ai_coworker` | PARTIAL | Equivalent Django-owned model families and governed background runtime exist. A HMX-style connection check is queued in the worker and retains only state, selected model, and latency—never credentials or response text. HMX runtime fixtures and multi-channel delivery scenarios remain unverified. |
| `ai_coworker`: channels, inbound events, webhook endpoints and secret references | `erpshop_ai_coworker` | PARTIAL | Channel configuration, inbound event audit, signed webhooks, secret reference, approvals, and provider-tool controls are separate Coworker models. Real provider/channel acceptance verification remains required. |
| `hashy_bridge`: authenticated read-only ERP data access | `erpshop_hashy_bridge` | PARTIAL | Scoped bearer authentication, schema, keyset read, grouping, batch access and audit logging are implemented. A live Hashy endpoint acceptance test remains required. |

## Boundary rule

- Assistant owns chat, company knowledge, citations, feedback, digest and work-intelligence evidence.
- Coworker owns autonomous or scheduled execution, provider/model catalogues, credits, agent memory, channels, webhooks and approval-gated tools.
- Hashy Bridge owns external read-only ERP access and its audit trail.

No add-on may bypass another add-on's ownership boundary or place provider credentials in database rows. Each new parity slice needs isolated tests, company-scope checks, bounded data access, and an HMX scenario comparison before its status becomes `MATCH`.

## Knowledge workflow acceptance — 6 November 2026

Rechecked HMX's `ai/api.py`, `ai/services/hashy_api_service.py`, and
`ai/tests/test_ai_base_knowledge_contract.py` for prerequisites, draft generation,
job polling, flow maps, and reasoning. These HMX routes delegate to the Hashy
gateway. The ERP tests below verify the equivalent local workflow with simulated
provider transport; they do not establish live gateway parity.

Changes:

- Chat and flow reasoning now rank active company flows by the question before
  applying the candidate limit. Older relevant flows are no longer displaced by
  newer unrelated entries. Matching steps beyond the first five can supply
  evidence, and the bounded prompt retains all three cited flow references.
- The worker rechecks company ownership, provider readiness, and approved
  references when a queued generation job starts. Removed references or disabled
  providers fail the job before a provider request is sent.
- Added 13 acceptance/regression tests covering both supported provider response
  formats, document ingestion, generation, polling, maps, draft exclusion,
  activation, chat citations, archival, company/branch isolation, task redelivery,
  failed requests, overwrite behavior, and retrieval growth.
- Repaired three existing test defects: a missing Coworker participant import,
  the approval test's outdated pre-execution state expectation, and an outbox
  privacy check that expected one event after three conversations. The revised
  checks inspect the execution result/audit trail and all three event payloads.

Validation:

| Check | Result | Evidence |
| --- | --- | --- |
| AI unit and acceptance suite | PASS | `python manage.py test addons.ai --keepdb --noinput --verbosity 1`: 82 tests, 106.773 seconds. |
| Query growth | PASS | 1 versus 126 active company flows: 1 SQL query in both cases; the older relevant flow remains first. Candidate limit 100, chat result limit 3, reason result limit 5. |
| PostgreSQL execution plan | PASS | `EXPLAIN ANALYZE` on the isolated fixture returned 100 bounded candidates. This is not a production-volume benchmark. |
| Bundled SG001–SG004 scan | PASS | No high or medium findings in the changed backend files. |
| pre-commit | NOT CONFIGURED | No repository pre-commit configuration. |
| Native Scale Guard | NOT AVAILABLE | No `tools/scale_guard.py`. |
| Live provider/channel/Hashy acceptance | NOT RUN | Provider selection explicitly deferred; local Assistant configuration remains disabled. |

Tests used a dedicated temporary PostgreSQL container. Its database was prepared
from the local deployment's schema and Django migration metadata, with no business
rows, then checked through Django's normal test runner. The initial fresh-migration
test setup was stopped because historical migrations were slow; a complete replay
of historical migrations is not part of the passing evidence above. Broker and
result backend were in-memory, and HTTP provider responses were mocked at the
transport boundary. That workflow slice introduced no model or schema migration. Parity remains
`PARTIAL` pending live acceptance and the other gaps in the matrix.

## Indexed knowledge retrieval — 6 November 2026

Document chunk retrieval now uses PostgreSQL full-text search with the `simple`
configuration. This keeps multilingual business terms unstemmed, ranks matching
chunks in the database, and preserves the existing maximum of 240 candidates,
five returned chunks, and two chunks per document. A concurrent GIN expression
index (`ai_knowledge_chunk_fts`) avoids blocking normal writes while the index is
created. Legacy ready documents without chunks keep the existing bounded fallback
until they are rebuilt.

The isolated scale fixture contained 1,203 indexed documents, including foreign
company and inactive-branch matches. Retrieval issued two queries (indexed chunks
and legacy fallback), returned only the one company/branch-visible candidate, and
`EXPLAIN ANALYZE` selected the GIN index. The complete `addons.ai` suite passed 83
tests in 98.942 seconds. Migration 0016 was also applied, reversed to 0015, and
applied again in the temporary PostgreSQL database. This fixture verifies query
shape, limits, and index selection; it is not a production-volume benchmark or
semantic/vector retrieval evidence.

## Targeted knowledge-graph lookup — 6 November 2026

Graph search now queries each company-owned source before applying the result
limit. Exact node detail and one-hop neighbors resolve their requested category,
document, flow, step, or record-model mapping directly instead of depending on
the first bounded graph projection. This keeps search-result selection useful
when the node is older than the graph canvas window while preserving company and
active-branch boundaries.

The isolated fixture placed the relevant document after 150 earlier rows. The
bounded canvas correctly omitted it, while search still returned it with five
queries before and after growth. Exact detail used one query and its category
neighborhood used two. Foreign-company and inactive-branch matches remained
hidden. The existing graph contract test and the complete `addons.ai` suite
passed.

Arbitrary endpoint paths now resolve both requested nodes directly and build only
their bounded ancestry: document to category to profile, step to flow to profile,
and record-model mapping to category or flow. A second isolated fixture placed
both endpoints beyond a canvas containing 150 earlier documents and 80 earlier
flows. The same document-to-flow path remained available with five queries before
and after growth, an insufficient `max_length` returned an empty targeted result,
and an inactive-branch endpoint remained hidden.

Neighbor expansion at depth two through four now uses a type-batched BFS from
the exact requested node instead of the canvas projection. Connector nodes tied
directly to the target are prioritized before general company-profile children,
and every level retains the global result limit. A depth-two document fixture
remained complete after 150 earlier documents with five queries before and after
growth. A depth-four document-to-category-to-mapping/profile-to-flow-to-step
fixture remained complete after adding 150 documents, 20 categories, and 20
flows, with 11 queries before and after growth. Company and active-branch scope
was preserved. The complete `addons.ai` suite now contains 86 tests and completed
in 99.985 seconds.

One-hop lookup, depth-two-through-four neighbor expansion, and arbitrary endpoint
paths are now target-aware and independently bounded. Live HMX graph behavior
remains explicit follow-up work before this parity slice can be marked `MATCH`.
