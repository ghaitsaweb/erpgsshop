# ERPShop AI Coworker

AI Coworker is the governed task runtime under `addons/ai/erpshop_ai_coworker`. It upgrades the earlier
`modules/ai_workspace` CRUD scaffold while preserving the existing Django app label (`gsshop_ai`), migration
history, and database tables.

## Current execution contract

- Coworker profiles belong to a company workspace.
- A task is owned by one company, optional active branch, requesting user, and coworker.
- `plan` runs without ERP data. `summarize` and `analyze` use Hashy Bridge's permission-aware read path.
- ERP snapshots use exposed fields only, at most 12 requested fields, 20 filters, 100 rows, and 128 KiB of
  serialized context. PostgreSQL reads have a five-second statement timeout.
- Provider execution is one bounded background turn using the company Hashy Assistant configuration.
- There are no write, delete, approval, messaging, webhook, channel, MCP, shell, or sub-agent tools.
- ERP values are labelled as untrusted data and cannot supply instructions to the agent.
- Queue, start, completion, failure, and cancellation are recorded as task events. Provider keys stay in
  environment variables and are never copied into task records.

## Scale characteristics

- Recent task pages use `company_id, id DESC` and a fixed 50-row limit.
- Worker claiming uses a row lock on the task only; nullable relationships are not locked.
- Queue, agent/status, requester, conversation, and event-stream indexes support operational lookups.
- ERP reads use keyset pagination and fixed limits. No query is issued per returned record.
- Usage counters use atomic `F()` updates so concurrent workers do not lose increments.
- The migration backfills task company ownership with one correlated database update rather than row-by-row ORM
  writes.

The targeted query-growth test compares small and larger ERP snapshots and requires the SQL query count to stay
constant. This is a structural scale test, not a claim that a million-row production benchmark was run.

## Deliberately deferred

HMX channel adapters, persistent memory, dream jobs, webhooks, MCP, sub-agents, credit ledgers, and autonomous ERP
writes are not implemented in this phase. They require separate modules and security/performance reviews; the UI
must not imply that they are available.
