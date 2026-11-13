# HMX AI Functional Parity Matrix

Source baseline: `/home/yusufmjt/hmx-human-resources-port8000-latest/hmx`. The launcher contract was rechecked read-only on 13 November 2026 at checkout commit `b59d2b9d743799729ca969e559ae992fea8b19f0`; the remaining matrix evidence retains its dated baselines below.

This is a functional comparison. HMX is not imported into ERP GS Shop: the Django add-ons retain their own models, access controls, and deployment lifecycle. A row is not `MATCH` merely because it has a similar menu or model name; it needs equivalent access, data, action, and output evidence.

| HMX module / behavior | ERP GS Shop add-on | Current state | Evidence / remaining work |
| --- | --- | --- | --- |
| `ai`: provider-backed Hashy chat, sessions, attachments, feedback, proactive greeting | `erpshop_moduleai_assistant` | PARTIAL | Company-scoped sessions support HMX-aligned rename/archive/restore, HTTPS provider policy, bounded history/files, feedback and auditable work episodes. Streaming and Hashy OS launch-token verification remain separate integrations. |
| `ai`: movable bottom-right assistant launcher and session panel | `erpshop_moduleai_assistant` | MATCH | A module-owned global shell extension provides a viewport-clamped, pointer-draggable launcher with relative-position persistence, keyboard/focus support, bounded lazy session/message loading, new-chat/send/poll behavior, and a full-workspace link. The visual identity is intentionally an original modern ERP GSSHOP topi SVG and the local product name is GashyAI, rather than a copy of the HMX Hashy artwork. |
| `ai`: local/base knowledge supplied to chat | `erpshop_moduleai_assistant` | PARTIAL | Company profile, reusable category vocabulary, structured company business-flow references, document chunks, request-relevant PostgreSQL full-text retrieval, source labels, and a bounded local knowledge graph are implemented. Every observed HMX Base Knowledge proxy path now has a local `/hmx_api/ai/knowledge/...` counterpart, including method aliases, upload polling, flow generation/reasoning, mappings, and graph lookup. Documents and mappings mirror HMX `division_id` through company-wide-or-Branch scope. Semantic/vector retrieval, response-fixture comparison against the live Hashy service, and live provider acceptance still require separate verification. |
| `ai`: digest and work-profile intelligence | `erpshop_moduleai_assistant` | PARTIAL | Company-scoped digest rules, records, work episodes, aggregate signals, and bounded refresh jobs exist. HMX runtime scenario comparison remains required. |
| `ai_coworker`: provider/model/pricing, credits, sessions, memory, subagents, cron and heartbeat | `erpshop_ai_coworker` | PARTIAL | Equivalent Django-owned model families and governed background runtime exist. A HMX-style connection check is queued in the worker and retains only state, selected model, and latency—never credentials or response text. HMX runtime fixtures and multi-channel delivery scenarios remain unverified. |
| `ai_coworker`: channels, inbound events, webhook endpoints and secret references | `erpshop_ai_coworker` | PARTIAL | Channel configuration, inbound event audit, signed webhooks, secret reference, approvals, and provider-tool controls are separate Coworker models. Real provider/channel acceptance verification remains required. |
| `hashy_bridge`: authenticated ERP data access | `erpshop_hashy_bridge` | PARTIAL | Scoped bearer authentication, schema, keyset read, grouping, batch access, one-record PDF reports, and audit logging are implemented. HMX `form_create` and `form_write` paths exist but deliberately fail closed because this deployment is read-only. Launch SSO, enabled write-mode behavior, and live Hashy acceptance remain gaps. |

## Boundary rule

- Assistant owns chat, company knowledge, citations, feedback, digest and work-intelligence evidence.
- Coworker owns autonomous or scheduled execution, provider/model catalogues, credits, agent memory, channels, webhooks and approval-gated tools.
- Hashy Bridge owns external read-only ERP access and its audit trail.

No add-on may bypass another add-on's ownership boundary or place provider credentials in database rows. Each new parity slice needs isolated tests, company-scope checks, bounded data access, and an HMX scenario comparison before its status becomes `MATCH`.

## GashyAI floating launcher — 13 November 2026

HMX evidence was taken from `hashy-session-icons.vue`,
`hashy-session-icons.js`, `hashy-session-management.css`, and the corresponding
launcher UI/E2E contract tests. ERP GSSHOP preserves the functional interaction
contract while using its own product identity: the add-on registers a global UI
fragment through the generic AppHub shell registry, and the shared base template
contains no assistant-specific branch.

The launcher is shown only when the AI application is enabled, the signed-in
user can access it, and an active company exists. Opening it performs the first
bounded data request; normal page rendering does not fetch chat sessions. The
panel returns at most 12 active sessions and 50 recent messages, retains the
existing company/user scope, and reuses the governed new-session, message queue,
and Celery processing flow. The user can drag the 68-pixel launcher in both axes;
its relative viewport position is persisted under
`gashyai_launcher_position_ratio` and re-clamped after viewport changes.

Validation: 5 focused launcher tests and the combined 37-test Assistant
regression run passed; the 2-versus-12 session query-growth check remained flat.
System check and migration drift passed, and working-scope Scale Guard reported
zero new findings. Migration `0017_gashyai_brand` updates the default local
display name without renaming Hashy OS compatibility identifiers. Deployment on
port 8010 completed with healthy web, worker, and beat services; an authenticated
live smoke test returned dashboard 200, rendered the launcher, and returned a
successful scoped launcher-state response. Provider runtime remains disabled
until an administrator selects a provider and mounts its secret environment
variable, so this slice does not change the whole Assistant row from `PARTIAL`.

## HMX Base Knowledge compatibility API — 8 November 2026

Rechecked the HMX route and forwarding contract in `ai/api.py`,
`ai/services/hashy_api_service.py`, and
`ai/tests/test_ai_base_knowledge_contract.py`. The current HMX checkout contains
unrelated Human Resources worktree changes, but its `ai`, `ai_coworker`, and
`hashy_bridge` trees are clean and have no diff from AI baseline `0cbc0c2266`.

The local adapter now covers all observed Base Knowledge route families under
the same `/hmx_api/ai/knowledge/...` prefix:

- company profile and its update alias;
- division and category list/create/update/archive aliases;
- document list/create/upload/update/retry/archive and upload-job polling;
- business-flow list/detail/create/update/archive, prerequisites, queued
  generation, job polling, flow maps, and evidence-based reasoning;
- record-model mapping list/upsert/archive; and
- graph status, overview, bounded view/search, exact node, neighbors, path, and
  trace.

All reads and writes require an authenticated Assistant administrator and an
active company. Branch-specific records are restricted to the active
organization scope; division mutations additionally require Organization
permissions. HMX delete routes are implemented as reversible archive operations.
The main branch cannot be archived through this API. No knowledge source row or
stored upload is hard-deleted.

Growing data paths are bounded: document pages allow at most 100 rows and 100
pages, non-paginated vocabulary/reference lists cap at 500, full-text document
search uses the existing PostgreSQL GIN-indexed chunk expression, graph limits
remain capped by the graph service, and mapping archival is a single batched
update. There are no ORM queries inside data-growing loops, per-row write loops,
or explicit recomputation calls in the adapter.

Validation:

| Check | Result | Evidence |
| --- | --- | --- |
| HMX Base Knowledge compatibility acceptance | PASS | 7 tests in 0.713 seconds: authentication/authorization, company isolation, reversible archive/restore, exact route aliases, upload and poll, bounded pagination, fixed query growth, flow/mapping, reasoning, and graph. |
| Complete AI add-on suite | PASS | The affected-module run included 94 AI tests within 328 passing tests; the new 2-versus-12 work-intelligence query-growth test then passed separately. |
| Django system and schema drift | PASS | `manage.py check`: no issues; `makemigrations --check --dry-run`: no changes detected. |
| SG001–SG004 manual review | PASS | No query-in-growing-loop, unbounded tenant read, de-batched write, or explicit compute finding in the compatibility adapter. |
| Native Scale Guard | PASS | Django-aware `tools/scale_guard.py`: 24 checker/fallback tests passed; the full-repository high/medium gate reported 0 new findings. The baseline remains empty. |
| pre-commit | PASS | Local-only configuration ran on the complete working scope together with native and bundled Scale Guard; no commit or push was created. |
| Live Hashy response-fixture comparison | NOT RUN | No live Hashy account/provider was selected. The local contract is tested offline; whole-module parity therefore remains `PARTIAL`. |

The compatibility slice adds no model or schema migration. Upload acceptance
uses a completed local job reference (`local-<document id>`) so the HMX polling
path remains usable without pretending that local ingestion is asynchronous.

## Hashy Bridge report and fail-closed write contract — 8 November 2026

The HMX `hashy_bridge` 1.4.2 routes were compared directly with the ERP bridge.
ERP GS Shop now exposes the missing `/hashy/v1/report`, `/hashy/v1/form_create`,
and `/hashy/v1/form_write` paths. Report rendering is functional and reuses the
native ERP document PDF definitions after bearer authentication, entity
permission checks, company/branch scoping, and exact record resolution. It
accepts exactly one record and rejects reports above 1,000 lines instead of
silently truncating business output. The normal 2 MB Bridge response ceiling
still applies to the base64 envelope.

Write paths are present for contract discovery but return a structured
`writes_disabled` 403 and make no mutation. This preserves the deployment's
documented read-only boundary while matching HMX's default-disabled write
posture. Health output advertises report support and explicitly reports both
form-write capabilities as unavailable.

The Bridge suite passed 8 tests in 2.873 seconds, including scoped PDF output,
fixed query count after report-line growth, cross-company denial, and proof that both write routes create no rows. The final
complete `addons.ai` suite passed 94 tests in 27.386 seconds. Launch-token SSO
and enabled write mode are not implemented because no Hashy tenant/embed/launch
configuration or authorization to enable external ERP writes was provided;
Bridge parity therefore remains `PARTIAL`.

## Remaining gates to whole-module 1:1

The following gaps cannot be truthfully marked `MATCH` from local offline work
alone:

- HMX agent chat streaming/SSE, contextual actions, remote session transport,
  and attachment round trips require a selected live Hashy provider contract;
- Hashy login/database discovery, wizard provisioning, token subscriptions,
  ledgers, boosters, and user limits require tenant credentials and live service
  response fixtures;
- DemoHMX generation creates broad business data and needs separate exact-scope
  authorization, recovery planning, and isolated acceptance fixtures;
- Coworker still lacks safe adapters for several HMX tools and channels (for
  example WhatsApp/Baileys, Telegram, MCP runtime reload, shell, SQL/ORM write,
  and external media search); enabling these changes the security boundary and
  needs an explicit allow-list and credentials; and
- Bridge launch SSO needs the Hashy tenant id, HTTPS embed base, launch signing
  secret, and access-gate choice. Enabled `form_create`/`form_write` behavior
  additionally needs explicit authorization to permit external ERP mutations.

Until those inputs and permissions exist, implementing fake success responses or
silently enabling external writes would reduce correctness and security rather
than achieve parity.

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
| pre-commit | PASS | A later repository-local configuration verified the complete working scope without remote hook dependencies. |
| Native Scale Guard | PASS | The later Django-aware repository checker passed 24 tests and the current working-scope high/medium gate with 0 new findings. |
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
