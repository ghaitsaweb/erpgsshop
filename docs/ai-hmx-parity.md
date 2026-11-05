# HMX AI Functional Parity Matrix

Source baseline: `/home/yusufmjt/hmx-human-resources-port8000-latest/hmx`, reviewed on 5 November 2026.

This is a functional comparison. HMX is not imported into ERP GS Shop: the Django add-ons retain their own models, access controls, and deployment lifecycle. A row is not `MATCH` merely because it has a similar menu or model name; it needs equivalent access, data, action, and output evidence.

| HMX module / behavior | ERP GS Shop add-on | Current state | Evidence / remaining work |
| --- | --- | --- | --- |
| `ai`: provider-backed Hashy chat, sessions, attachments, feedback, proactive greeting | `erpshop_moduleai_assistant` | PARTIAL | Company-scoped sessions support HMX-aligned rename/archive/restore, HTTPS provider policy, bounded history/files, feedback and auditable work episodes. Streaming and Hashy OS launch-token verification remain separate integrations. |
| `ai`: local/base knowledge supplied to chat | `erpshop_moduleai_assistant` | PARTIAL | Company profile, reusable category vocabulary, structured company business-flow references, document chunks, request-relevant lexical retrieval, source labels, and a bounded local knowledge graph are implemented. Documents and record-model mappings mirror HMX `division_id` through company-wide-or-Branch scope; graph projections carry the same scope. HMX-shaped flow-map, generation-prerequisite, asynchronous draft-generation/job, and non-chain-of-thought reason endpoints are available. Semantic/vector retrieval, HMX graph inference, and live provider acceptance still require separate verification. |
| `ai`: digest and work-profile intelligence | `erpshop_moduleai_assistant` | PARTIAL | Company-scoped digest rules, records, work episodes, aggregate signals, and bounded refresh jobs exist. HMX runtime scenario comparison remains required. |
| `ai_coworker`: provider/model/pricing, credits, sessions, memory, subagents, cron and heartbeat | `erpshop_ai_coworker` | PARTIAL | Equivalent Django-owned model families and governed background runtime exist. A HMX-style connection check is queued in the worker and retains only state, selected model, and latency—never credentials or response text. HMX runtime fixtures and multi-channel delivery scenarios remain unverified. |
| `ai_coworker`: channels, inbound events, webhook endpoints and secret references | `erpshop_ai_coworker` | PARTIAL | Channel configuration, inbound event audit, signed webhooks, secret reference, approvals, and provider-tool controls are separate Coworker models. Real provider/channel acceptance verification remains required. |
| `hashy_bridge`: authenticated read-only ERP data access | `erpshop_hashy_bridge` | PARTIAL | Scoped bearer authentication, schema, keyset read, grouping, batch access and audit logging are implemented. A live Hashy endpoint acceptance test remains required. |

## Boundary rule

- Assistant owns chat, company knowledge, citations, feedback, digest and work-intelligence evidence.
- Coworker owns autonomous or scheduled execution, provider/model catalogues, credits, agent memory, channels, webhooks and approval-gated tools.
- Hashy Bridge owns external read-only ERP access and its audit trail.

No add-on may bypass another add-on's ownership boundary or place provider credentials in database rows. Each new parity slice needs isolated tests, company-scope checks, bounded data access, and an HMX scenario comparison before its status becomes `MATCH`.
