# Approval Workflow parity

ERP GSSHOP implements approval as the standalone `modules.approval_workflow` application. Transactional modules register a domain adapter and use this module for rule selection, approver resolution, action history, delegation, timeout processing, mass actions, and notifications.

## Implemented behavior

| Capability | ERP GSSHOP implementation |
|---|---|
| Workflow registry | One active workflow per Django content type, status field validation, primary and optional perimeter definitions |
| Rule resolution | Company-aware ordered rules, bounded candidate scan, nested trigger conditions, associated/unassociated/archive lifecycle |
| Approval levels | Sequential frozen levels, direct users, roles/groups, record fields, direct supervisor, minimum approval quorum |
| Runtime snapshot | Rule, approvers, perimeter values, notifications, delegation timing, and timeout policy are frozen when submitted |
| Authority | Normal approver, master approver, company scope, active-user checks, duplicate-vote protection |
| Delegation | Immediate while-away delegation, rule-configured user/group delegates, delayed escalation, validity dates, company scope, and actor/effective-approver attribution |
| Decisions | Approve, reject, master approve/reject, automatic approve/reject, mandatory rejection reason, configurable approval reason |
| History | Immutable submit and decision actions with level, actor, effective approver, delegated source, reason, metadata, and timestamp |
| Timeout safety | Automatic timeout runs only for content types explicitly present in the timeout whitelist; indexed due-time scan dispatches bounded per-record tasks |
| Mass action | Opt-in per workflow, maximum 100 selections, asynchronous per-record processing through the same locked approval engine |
| Notifications | Durable outbox, bounded `skip_locked` batch drain, idempotent in-app notification per recipient, approval inbox display |
| Operations | Celery beat scans due runtime work every minute and drains the outbox every 30 seconds |

## Current domain adapters

- Employee Change Request: submit freezes scalar and employee-relation snapshots; final approval applies them atomically; rejection preserves employee data.

Additional ERP document types can adopt the module by creating a workflow/rule seed, submitting through `submit_for_approval`, and registering their final approve/reject behavior in `modules/approval_workflow/adapters.py`.

## Verification

- Approval Workflow: 16 PostgreSQL tests pass.
- Human Resources: 52 PostgreSQL tests pass.
- Query growth: outbox drain uses the same query count for 2 and 12 rows; Employee Change Request submission and approved relation replacement have fixed query-growth tests.
- Django system check and migration drift check pass.

The HMX comparison is behavioral and clean-room. Visual identity, source structure, and HMX framework internals are not copied. Runtime side-by-side evidence is still required before marking the overall HMX approval comparison complete.
