# HMX parity contract

Use this contract before implementing or reviewing an ERP GS Shop feature that
must follow HMX.

## 1. Evidence boundary

Record:

- canonical HMX worktree and Git commit;
- HMX owning module and manifest dependencies;
- source models/services, views, access rules, migrations or seed data, and
  relevant tests;
- ERP GS Shop destination modules and current behavior.

Read only the files needed for the requested domain. Do not search or copy
credentials, environment files, database dumps, or unrelated modules. Never
write to the reference worktree during an ERP port.

## 2. Module mapping

Build a compact mapping before editing:

| Concern | HMX owner | ERP GS Shop owner | Decision |
|---|---|---|---|
| Domain model | module and symbol | model/module | match, adapt, or defer |
| Workflow | service/action/view | service/action/view | state sequence |
| Integration | extension/hook | extension/hook | side effects |
| UI | view/widget | template/JS/form | interaction |
| Security | group/rule/company scope | permission/scope | access boundary |

Keep accounting behavior in Finance, Sales behavior in Sales, and shared
framework behavior in an explicit shared extension point. A convenient import
path is not evidence of correct ownership.

## 3. Behavioral comparison

Trace the HMX operation from user action to completion:

1. preconditions and visible action;
2. state and lock checks;
3. records created or updated;
4. intermediate states and redirects;
5. accounting, inventory, notification, audit, or job side effects;
6. final state and linked-record visibility;
7. validation failure and transaction rollback;
8. repeated invocation and concurrency behavior.

Preserve separate HMX steps as separate ERP steps. For example, if HMX creates
a draft invoice and posts its journal only from the invoice's Post action, do
not combine both into one Sales action.

Configuration and seed behavior are part of parity. If HMX expects a journal,
account, tax, or company configuration to be provisioned explicitly, use an
ERP provisioning workflow or migration with separate authorization. Do not
hide missing setup by creating financial configuration inside a transaction
unless HMX does so.

## 4. Data and performance

Classify each path as exact record, bounded interactive page, complete-data
batch, or database aggregate. Preserve HMX business results while adapting to
ERP GS Shop's ORM.

- no ORM/network calls inside loops growing with lines or records;
- bound interactive searches with deterministic ordering;
- batch related reads and writes;
- retain company/branch filters and access checks;
- test query count for representative small and larger related sets.

Apply the repository Scale Guard workflow. A behavior match that introduces
N+1 queries or unbounded reads is not accepted parity.

## 5. Verification matrix

For each material behavior, classify the result:

- `MATCHED`: same externally observable behavior and side effects;
- `ADAPTED`: framework differs but the observable contract is preserved;
- `DEFERRED`: outside the authorized scope or blocked by missing dependency;
- `NOT VERIFIED`: reference, access, or tests were unavailable.

Test the success state sequence, validation rollback, permissions and tenant
scope, idempotent retry, and growing line counts where applicable. Do not use
screenshots or field-name similarity as the sole evidence of parity.
