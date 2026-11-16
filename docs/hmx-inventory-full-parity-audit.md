# HMX Inventory parity audit

## Evidence boundary

- Designated HMX source: `/home/yusufmjt/hmx-human-resources-port8000-latest`
  on `192.168.0.108`.
- Read-only HMX commit re-verified for the Stock Count continuation on 16
  November 2026: `af5184484b17fd08b4e201e0c66065254a6b379c`.
  The earlier foundation inspection recorded
  `eed14195cc2b8710bb49e6a4a02cff2315d09742`; the reference advanced between
  the two read-only inspections.
- Base owner inspected: `core_inventory` version `1.0.1.0.83`.
- The base manifest, security groups, menu tree, location, lot/serial, stock
  balance, principal views, and related tests were inspected. The source has
  107 model/service Python files, 63 XML view files, 19 wizard Python files,
  and 228 tests under `core_inventory`.
- Fifteen additional HMX add-ons whose path or manifest identifies Inventory
  integration were discovered. Their behavior is **DEFERRED** until each
  extension is separately inspected for ownership, workflow, security,
  side-effects, rollback, UI, and scale behavior.

The HMX worktree is a read-only reference. No HMX source, runtime, or database
was modified.

## Ownership and adaptation

`modules.inventory` is the ERP GS Shop owner for new Inventory capability.
Existing `core.Product`, `core.Warehouse`, and immutable `core.StockMove` are
retained as shared master/ledger contracts so Purchase, Construction, POS, and
Manufacturing do not fork stock truth. The older
`modules.purchase.PurchaseInventoryTransfer` remains the current transfer
document and is surfaced in the Inventory workspace as an explicit adapter;
it is migration input for a future Inventory-owned transfer document, not a
second parallel transfer implementation.

## Current verification matrix

| Concern | HMX reference | ERP GS Shop owner | Status | Evidence / remaining work |
| --- | --- | --- | --- | --- |
| Location hierarchy | `core_inventory.stocklocation` | `InventoryLocation` | ADAPTED | Company/branch/warehouse scope, bounded ancestry, View/Internal/Virtual usage, barcodes, packing/delivery/expired flags, sequence, and recursive-parent guards exist. Virtual locations are model-supported but intentionally hidden from the operator form. |
| Capacity | Location capacity fields and states | `InventoryLocation` | ADAPTED | Length/width/height, volume, occupied/available volume, fulfilment percentage, and Not Configured/Incomplete/Empty/Available/Full/Over Capacity states exist. Automatic occupied-volume refresh from lot dimensions remains deferred. |
| Removal strategy | FIFO / FEFO | `InventoryLocation` | ADAPTED | Policy is stored and validated. Automated reservation/picking allocation by FIFO/FEFO is deferred. |
| Lot and serial identity | HMX stock lot/serial | `InventoryLotSerial` | ADAPTED | Product/company identity, unique name, barcode, lot/serial type, expiry/alert date, active/out state, dimensional volume, and expiry flags exist. Stock-move-level lot capture and serial scanning remain deferred. |
| Stock balances | HMX on-hand/reserved/available positions | `InventoryStockBalance` | ADAPTED | Product, warehouse, internal location, optional lot, company/vendor ownership, cost, incoming date, and last movement are represented. Core movements currently populate company-owned no-lot positions; lot/vendor allocation and reservation services remain deferred. |
| Immutable movement integration | HMX inventory transactions | `core.StockMove` + `InventoryBalanceEvent` | ADAPTED | One idempotency event per immutable movement updates warehouse/location balances. Single saves use a signal; every known bulk writer in Purchase and Construction explicitly invokes the batch synchronizer. A bounded replay command migrates legacy ledger rows. |
| Internal transfer | HMX transfer workflow | `PurchaseInventoryTransfer`, exposed by Inventory | ADAPTED | Ready, dispatch, complete, cancel, two immutable stock moves, stock validation, and Inventory balance synchronization exist. Location-to-location lines, picking, and a native Inventory owner remain deferred. |
| Reordering rules | HMX replenishment policy | `InventoryReorderRule` | ADAPTED | Per-product/warehouse minimum, maximum, fixed-or-up-to-max quantity, Buy/Internal route, source warehouse, vendor, lead time, branch scope, and active state exist. |
| Replenishment review | HMX replenishment planning | `InventoryReplenishmentSnapshot` | ADAPTED | Bounded evaluation materializes Due/Healthy/Inactive, available quantity, suggested quantity, route, source/vendor, and need-by date. Purchase Request or transfer generation from a suggestion remains deferred. |
| Inventory roles | HMX Administrator/Manager/Supervisor/User | `modules.inventory.security` | ADAPTED | Four corresponding groups are provisioned after migration with Inventory, shared ledger/master, and adapted transfer permissions. Exact HMX record-rule semantics remain deferred. |
| Workspace and mobile shell | HMX menu/views | module-owned entity/workspace registries | ADAPTED | Stock Balance, Stock Counts, paginated Counting Lines, Replenishment Review, Reordering Rules, Internal Transfers, Locations, and Lot/Serial are exposed without central menu hardcoding and inherit the responsive generic ERP shell. Specialized barcode/mobile picking screens remain deferred. |
| Counts and adjustments | HMX `stock_count`, `stock_count_line`, and warehouse freeze | `InventoryStockCount`, `InventoryStockCountLine`, `InventoryStockFreeze` | ADAPTED | Draft → Confirm → In Progress → Complete → Validate and In Progress/Completed → Cancel are implemented. Confirm freezes the warehouse and creates a bounded balance snapshot; Validate posts exact location/lot gains or losses as immutable Stock Moves, updates product/balance/lot snapshots, records idempotency events, and releases the freeze atomically. Other stock writes fail while frozen. The local freeze conservatively locks the whole warehouse instead of permitting non-overlapping scopes. HMX approval, background generation above 1,000 lines, valuation review/accounting, CSV/barcode count, and reversal remain deferred. |
| Reservations, picking, packing, delivery | HMX operational flows | — | DEFERRED | No reservation allocator, picking batches/waves, package hierarchy, or delivery execution yet. |
| Routes and put-away | HMX routes/rules | — | DEFERRED | Location policy is present, but automated put-away, pull/push routes, cross-dock, and multi-step receipt/delivery are not implemented. |
| Valuation and accounting | Inventory accounting extensions | existing Finance inventory accounting records | NOT VERIFIED | Similar records are not parity evidence. Cost layers, landed cost, valuation posting, reconciliation, rollback, and extension ownership need a separate audit. |
| Barcode, dashboards, reports, imports, schedulers | Base and extension UI/services | partial AppHub dashboard and generic exports | DEFERRED | Existing generic pages are not claimed as HMX behavior parity. |
| Fifteen Inventory extension add-ons | HMX extension manifests | multiple ERP modules | NOT VERIFIED | Each extension requires its own clean-room mapping and tests. |

## Scale and migration contract

- Movement synchronization accepts at most 1,000 saved movements per
  transaction, locks immutable ledger rows, loads locations and balances in
  fixed queries, aggregates deltas in memory, and uses bulk create/update.
- `sync_inventory_balances` performs a complete legacy replay by bounded
  primary-key cursor batches. `InventoryBalanceEvent` makes retries idempotent.
- Replenishment evaluation accepts at most 250 rules per transaction, builds
  exact product/warehouse predicates, aggregates available stock in one query,
  and bulk-creates or bulk-updates the latest snapshots.
- `evaluate_inventory_replenishment` performs complete evaluation by bounded
  keyset batches.
- Stock Count confirmation and validation accept at most 1,000 lines, 100
  selected locations, and 1,000 selected products. Snapshot and adjustment
  writes are bulk operations. Larger counts fail explicitly and remain
  complete-data background-workflow work, rather than silently dropping lines.
- Counting Lines use the shared 25-row paginated entity list and exact-record
  edits instead of rendering an unbounded inline formset.
- Interactive lists continue through the shared scoped, searched, paginated
  entity view; no Inventory service returns an unbounded result set.

## Verification recorded for the current foundation

- Five no-database Inventory registry/domain tests pass.
- Seven PostgreSQL Inventory integration tests pass, covering idempotent replay,
  single-movement synchronization, scope validation, reorder policy, Due to
  Healthy replenishment changes, and query-growth fixtures.
- The 2-versus-12 movement fixture proves balance-sync query count does not grow
  with movement count.
- The 2-versus-12 rule fixture proves replenishment-evaluation query count does
  not grow with rule count.
- Six PostgreSQL Stock Count tests pass: full variance lifecycle, freeze and
  cancel behavior, stale-snapshot rollback, exact manual-line editing, generic
  entity action dispatch, and 2-versus-12 line query-growth checks for both
  Confirm and Validate.
- Six directly affected Purchase receipt, return, and transfer regressions pass,
  including their existing query-growth fixtures.
- Python compilation, Django system check, migration drift (`No changes
  detected`), and `git diff --check` pass.
- Native Scale Guard passes with zero new SG001-SG004 findings. It reports the
  replenishment aggregate as one allowed SG002 because its grouped output is
  bounded by the validated 250-rule transaction limit, and the two Stock Count
  exact-position reads as allowed SG002 findings bounded by the validated
  1,000-line limit. The pre-commit executable is unavailable, so configured
  pre-commit is **NOT RUN**.

## Deployment state

Purchase commit `c5eaab4` is deployed. Inventory foundation commit `e253611`
and migrations `0001` through `0003` are in the same repository/worktree;
Inventory migrations have only been applied to the isolated reusable test database. Production
Inventory migration, legacy replay, replenishment evaluation, and smoke tests
remain pending; this document does not claim Inventory is deployed or 1:1.
