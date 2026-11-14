# HMX Purchase full-parity audit

## Evidence boundary

- Designated HMX source: `/home/yusufmjt/hmx-human-resources-port8000-latest`
  on `192.168.0.108`.
- Resolved module root: `hmx/module`.
- Git commit: `b59d2b9d743799729ca969e559ae992fea8b19f0`, resolved from
  the designated read-only worktree on 14 November 2026.
- Runtime: the HMX process listening on server port `8000` resolves its working
  directory to the designated `hmx` directory and returned HTTP 200 during the
  13 November 2026 audit.
- HMX owner: `basic/core_purchase` version `1.0.9`.
- Directly inspected extension owners: `core_purchase_accounting`,
  `core_purchase_inventory`, `core_inventory_purchase`,
  `core_purchase_budget`, `core_construction_purchase`,
  `core_construction_purchase_inventory`, `core_manuf_purchase`,
  `intercomp_sale_purchase`, `core_localization_purchase`,
  `core_purchase_ph`, `core_purchase_sg`, and `core_purchase_mcp`.

The HMX snapshot is a read-only reference. No HMX source, runtime, or database
was changed by this audit.

## Current verification matrix

| Concern | HMX owner | ERP GS Shop owner | Status | Evidence / remaining work |
| --- | --- | --- | --- | --- |
| Approval engine integration | `core_purchase` + `approval_workflow` | `modules.purchase` + `modules.approval_workflow` | ADAPTED | State, authorization, frozen approvers, rollback, and material domain effects are covered by the approval parity suites. |
| Blanket Order approval | `core_purchase.blanketorder` | `modules.purchase.BlanketOrder` | MATCHED | Draft, waiting approval, confirmed, rejected, and domain revalidation are covered. |
| Blanket Order release lifecycle | `core_purchase.blanketorder` and RFQ wizard | `modules.purchase.services`, form, and view | ADAPTED | Quantity-and-period versus period-only policy, exclusive validity period, partial release, explicit selected lines, manual close, automatic close, cancellation reopen, and reopen guards are covered. ERP uses bounded batch services rather than HMX recordset loops. |
| Blanket pricing and tax interaction | `core_purchase.blanketorderline` | `modules.purchase.BlanketOrderLine`, quotation-line trace fields, and `PurchaseOrderLinePricingProvenance` | ADAPTED | Tier pricing, draft price refresh, manual-price preservation, discount-formula traceability, generated free-product synchronization, tax-inclusive RFQ/PO propagation, and immutable approval/confirmation tax snapshots are covered. ERP adapts HMX's multi-tax relation to the local percentage-tax model; exact multi-tax, global-discount, and client-side onchange behavior remains deferred. |
| Purchase Request conversion | `core_purchase.purchaserequest` + conversion wizards | `modules.purchase.services`, forms, and views | ADAPTED | RFQ, Blanket, Tender, Direct Purchase, line decisions, re-request paths, header/line timeline defaults, and structured cost-center propagation exist with bounded line handling. PR estimated cost takes precedence during RFQ creation; zero estimated cost falls back to the vendor tier with price trace and free-product benefit. The complete HMX client-side onchange matrix is not yet verified. |
| RFQ and Purchase Order lifecycle | `core_purchase.purchaseorder` | `PurchaseQuotation`, core `PurchaseOrder`, and `PurchaseOrderControl` | ADAPTED | Draft RFQ through selected PO, approval, revision, receiving, vendor limits, cancellation effects, bounded multi-vendor RFQ comparison, direct-PO price refresh, rental controls, header/line timeline propagation, commercial-term snapshots, cost-center accounting propagation, editable Standard/Urgent/Follow-up RFQ mail, immutable delivery evidence, PDF attachment, and scoped Purchase prints are covered. |
| Tender and award | `core_purchase.tender` / `tenderaward` | `PurchaseTender`, bids, allocations, and responses | ADAPTED | The 50/20/15/5/10 evaluation criteria, persisted component scores and vendor rank, detailed scoring UI, separate Apply Award Strategy action, single-rank allocation, 60/30/10 split allocation with capacity fill, manual-allocation preservation, response creation, rollback, and bounded fixed-query scoring now match the HMX contract. HMX's complete bid-comparison workspace, every client-side onchange, audit-log detail, and all extension-module Tender behavior remain to be verified. |
| Vendor onboarding and legal documents | `core_purchase` vendor models | Purchase vendor profile/legal/prequalification/change-proposal models | ADAPTED | Blocking documents, expiry, review, prequalification, blacklist restoration, company-safe similar-vendor detection, parent-vendor validation, and approval-gated data changes are covered. HMX address subrecords and bank-account master records remain adapted to local Contact/Profile fields. |
| Vendor pricelist | `core_purchase.vendorpricelist*` | Purchase pricelist, tier, amendment, import, and eligibility services | ADAPTED | Approval, tier lookup, eligibility, import, amendment, initial Blanket free-product synchronization, and bounded price traceability exist. The complete HMX edit-time/onchange behavior remains deferred. |
| Vendor performance and transaction history | `core_purchase.vendorperformance*`, `vendortransactionhistory` | Purchase performance models/services/tasks and materialized transaction history | ADAPTED | Template validation, qualitative approval, pure scoring formulas, fully-received expected-versus-actual delivery evidence, immutable HMX-shaped score history, latest-vendor score, read-only history UI, daily bounded recalculation, and Vendor-scoped PO/Tender/receipt/return history are covered. HMX transient collection is adapted to idempotent materialization with immediate 100-row pages and keyset background backfill. HMX quality-control evidence remains adapted to local Goods Receipt quantities; additional extension-owned sources remain to be audited. |
| Purchase Team and auto-routing | `core_purchase.purchaseteam*`, PR/PO `_auto_route_to_buyer` | Purchase team models, bounded routing services, and document action extensions | ADAPTED | Product-category, order-type, and vendor-category matching; active/company scope; newest-first conflict handling; one-team-per-batch; existing-team preservation; Team Lead membership; manual PR/PO actions; and automatic PO-confirm routing are covered. ERP stores PO metadata in `PurchaseOrderControl` and uses bounded bulk writes. HMX branch-specific team behavior, dashboard tiles, and multi-record list-action UI remain deferred. |
| Purchase settings and document configuration | `core_purchase.purchasesettings` | `PurchaseSetting`, order type/template/remark/milestone models | ADAPTED | Major configuration records exist. PO Template duplication, immutable Remark Template snapshots, vendor-default payment-term toggle, vendor/billing-address snapshots, module-owned PO control fields, and required structured cost-center guards are covered. Every HMX UI dependency is not yet verified. |
| Purchase Intelligence dashboards and analysis | `core_purchase` intelligence/report services | Purchase dashboard, AppHub dashboards, and `purchase_analysis` | ADAPTED | Bounded dashboards plus HMX-shaped PO and PR product-by-month analysis contracts exist. Confirmed/approved filters, real quantity/discount/tax/received measures, organization scope, and report endpoints are covered; every spend/risk/vendor insight visualization is not yet verified. |
| Accounting and inventory extensions | `core_purchase_accounting`, `core_purchase_inventory`, `core_inventory_purchase` | Purchase accounting, receiving, transfer, return, and three-way-match services | ADAPTED | Core bill/receipt/return effects are covered. Every HMX extension view, consignment, procurement-planning, and stock-demand workflow is not verified. |
| Budget, construction, manufacturing, intercompany, localization, PH/SG, MCP | extension modules listed above | Multiple ERP modules | NOT VERIFIED | Presence of similarly named records is not behavioral parity. Each extension requires its own owner, workflow, security, side-effect, UI, and rollback audit. |

## Tender evaluation and award-strategy slice completed in this audit

- Added the HMX default evaluation weights: Pricing 50%, Delivery 20%,
  Compliance 15%, Terms 5%, and History 10%, with an exact 100% validation
  before Tender submission and evaluation.
- Scores submitted bids in bounded batches, persists every component plus the
  weighted total and rank at both vendor-average and product-line level, and
  exposes the detailed vendor and line comparison in the Tender workspace.
- Matches HMX price and delivery normalization, adapts compliance to the local
  compliant-bid and approved-prequalification records, and reuses the latest
  bounded Vendor Performance score for history instead of scanning purchase
  history per vendor.
- Keeps Award and Apply Award Strategy as separate workflow actions. Single
  Rank fills each product line in its own score order; Split Ratio applies
  60/30/10 before filling remaining capacity in per-line rank order; Manual
  preserves user allocations.
- Creates award responses only for actual winners and wraps automatic
  reallocation in one transaction so insufficient capacity leaves allocations,
  bid statuses, and responses unchanged.
- Keeps Purchase-specific action visibility, context, and summary UI in the
  module-owned document extension registry; core now exposes generic action
  filtering, context-builder, and action-audit extension points. Applying a
  strategy records the strategy, allocation count, and winning vendors in the
  Tender audit details.
- Bounds evaluation at 500 Tender lines, 50 submitted vendors, and 5,000 bid
  lines/allocations. The 2-versus-12-vendor fixture confirms that query count
  does not grow with vendor count.
- Verification: 9 focused Tender scoring/strategy tests, 11 existing Tender
  workflow/document-completion tests, the Tender approval-cycle regression,
  and 3 generic document-registry tests passed on an isolated reusable test
  database. Native Scale Guard reported zero new SG001-SG004 findings.

## Vendor Performance scoring slice completed in this audit

- Extracted the HMX delivery, rejection, quantitative, and final-score formulas
  into an ORM-independent service so the scoring contract runs as true unit
  tests without Django or a database.
- Replaced the manually selected Goods Receipt delivery-status label as the
  scoring source. Delivery accuracy now compares the immutable PO-line expected
  date (with header fallback) to the actual completed receipt date and excludes
  partially received lines, matching HMX behavior.
- Retained local-native Goods Receipt quantities for rejection scoring and
  records the source explicitly as `goods_receipt` rather than claiming the HMX
  quality-control model exists locally.
- Stores the HMX-shaped nested delivery/rejection evidence, transaction count,
  and template snapshot in immutable score history; the Purchase registry
  exposes final score and status while keeping history create/edit/delete
  actions disabled.
- Verified the existing daily scheduler and its keyset fan-out: only companies
  with scoring and automatic recalculation enabled participate, at most 100
  vendors are queued per task, and the next page continues after the last
  scheduled primary key.
- Keeps template evaluation at 500 lines and transaction evidence at 5,000 PO
  lines. The 1-versus-20-receipt-line fixture confirms non-growing query count.
- Verification: 6 ORM-free formula/evidence unit tests and 7 focused Django
  integration tests pass on the reusable isolated test database.

## Vendor Transaction History slice completed in this audit

- The Vendor `Transactions` smart action is now always visible and prepares
  history only after the user opens the scoped action. The generic smart-action
  layer exposes a preparer extension point while all Purchase behavior remains
  registered and implemented inside `modules.purchase`.
- Materializes HMX-shaped Purchase Order line, Tender participation, Goods
  Receipt, and Vendor Return rows with product, value, ordered/received/
  remaining quantities, expected/actual dates, delivery variance, rejection
  evidence, company, and branch snapshots.
- Keeps the history list and details read-only, company/vendor scoped, and
  idempotent through the existing vendor/type/reference uniqueness contract.
  Reopening the action refreshes changed source values without duplicating
  history.
- The request path reads at most 100 recent rows from each fixed source. Older
  rows are completed with retry-safe keyset tasks at 100 rows per invocation,
  preserving complete history without an unbounded interactive query.
- The list uses joined Product/Company/Branch reads and a vendor/date ordering
  index. An estimated `EXPLAIN` on the empty reusable test table selected a
  sequential scan because the table had no retained fixture rows; a
  representative production-scale plan remains `NOT RUN`.
- Verification: 6 ORM-free delivery-contract unit tests and 6 Django
  integration tests pass, including all four sources, smart-action behavior,
  idempotency, company isolation, 100-row pagination/continuation, and a
  non-growing 1-versus-21-line query-count fixture. Native Scale Guard reports
  zero SG001-SG004 findings for the changed history path.

## Blanket Order slice completed in this audit

- Added `quantity_period` and `period_only` agreement policy.
- Enforced HMX's exclusive End Date and active-period release gate.
- Allowed over-agreement release only for period-only agreements.
- Prevented explicit wizard selections from silently releasing omitted lines.
- Kept period-only agreements running after full release/PO selection.
- Cancelled open RFQs and restored quantities on manual close using bounded
  aggregate reads and bulk writes.
- Reopened auto-closed agreements only when the period and quantity policy
  allow it; Purchase Order cancellation now propagates to the source RFQ.
- Hid Remaining Quantity in the period-only RFQ wizard.

## Purchase Team auto-routing slice completed in this audit

- Added the Purchase Team relation to Purchase Requests and exposed it through
  the module-owned document extension registry.
- Matched HMX routing by product category, order type, and vendor category.
- Matched active-team filtering, company isolation, newest-team priority,
  one-team-per-batch conflict handling, and preservation of existing routing.
- Added manual PR/PO workflow actions without changing document state and
  automatic routing during Purchase Order confirmation.
- Kept Purchase Order routing metadata in `PurchaseOrderControl`, preserving
  ERP GS Shop module ownership while exposing the team in the Purchase UI.
- Ensured the Team Lead is a member for new and existing teams, enforced
  primary-buyer category requirements, and allowed the HMX-supported category
  overlap between primary and non-primary buyers.
- Bounded each routing batch and candidate-team pool at 500 records and used
  prefetched matching plus bulk writes instead of ORM calls per document.

## Blanket pricing and free-product slice completed in this audit

- Added stored price-source, applied-pricelist, applied-tier, source-note, and
  discount-formula traceability to Blanket and RFQ lines.
- Preserved the ERP adaptation that stores the tier's effective discounted
  unit price while retaining the original formula as trace evidence.
- Marked Purchase Request estimated-cost fallback as a manual source instead
  of falsely attributing it to a vendor pricelist.
- Generated one zero-price free-product benefit line per matched tier, linked
  it to the paid line, defaulted a missing/non-positive benefit quantity to
  one, and kept it out of Purchase Request consumption and agreement value.
- Kept generated benefit lines out of the release wizard selection, then
  propagated them automatically to RFQ and Purchase Order as zero-price lines.
- Added a Purchase-owned PO-line provenance model so traceability remains
  available without adding Purchase fields to the core PO-line model.
- Excluded free-product lines from quantity-policy auto-close decisions and
  vendor-pricelist eligibility blocking.
- Added a draft-only Update Prices document action and line form behavior that
  reprices changed quantities/products, preserves explicit manual prices, and
  removes stale tier benefits.
- Bounded paid plus benefit lines at 500 and retained fixed-query batch reads
  and bulk writes.

## Blanket tax-snapshot slice completed in this audit

- Added editable percentage tax to Blanket lines and untaxed, tax, and total
  amounts to the Blanket header while preserving `agreement_value` as the
  untaxed compatibility value.
- Captured immutable HMX-shaped tax snapshots on Blanket approval, including
  tax metadata and untaxed, tax, discount, and total amount keys.
- Propagated the tax percentage to partial Blanket RFQ releases and calculated
  the RFQ amount from the released quantity instead of the full agreement
  snapshot.
- Froze the selected RFQ tax amounts and copied percentage-tax and discount
  provenance into the Purchase-owned PO-line provenance record.
- Captured the PO provenance snapshot only on actual PO confirmation; a PO
  moved to Waiting Approval remains live and unsnapshotted.
- Kept generated free-product lines at zero taxable, tax, and total amounts.
- Added a module-owned line-field extension so the Blanket tax field is visible
  without hard-coding Purchase behavior into the core document configuration.
- Bounded every snapshot and total recalculation at 500 lines and used batch
  reads and bulk snapshot writes.

## Compare RFQ slice completed in this audit

- Added a module-owned bulk Compare RFQs action to the Purchase Quotation list.
- Enforced HMX's minimum two-RFQ rule, allowed only active RFQ lifecycle
  states, required at least one product quoted by two vendors, and isolated
  comparisons to one company and the user's scoped records.
- Preserved products quoted by only some vendors while comparing matching
  products side by side and marking unique lowest/highest normalized unit
  prices as Best and Highest.
- Included vendor contact, delivery-on-schedule, fulfillment, performance
  rating, expected/expiry dates, discount, tax-inclusive totals, summary rows,
  target budget, and bid deadline evidence.
- Normalized mixed-currency quotes to company currency using the latest local
  Finance Currency Rate on or before the comparison date, while retaining the
  source amount and conversion rate.
- Routed a comparison winner through RFQ selection, PO provenance creation,
  approval/confirmation, routing, tax snapshot, and vendor-limit services
  instead of introducing a shortcut state change.
- Bounded one comparison at 25 RFQs and 5,000 total lines; the 2-versus-12 RFQ
  fixture confirmed a non-growing query count.

## Purchase Request to RFQ pricing slice completed in this audit

- Preserved a positive Purchase Request estimated cost as the authoritative RFQ
  unit price with an explicit `purchase_request` source, no false pricelist
  attribution, and no generated tier benefit.
- Fell back to the active vendor pricelist when estimated cost is zero,
  retaining applied pricelist, tier, discount formula, and source-note trace.
- Generated a linked zero-price free-product RFQ line for a matching tier and
  kept the generated line outside Purchase Request quantity consumption.
- Applied the same behavior for every selected vendor through fixed-query batch
  resolution rather than one pricelist query chain per vendor.
- Integrated RFQ repricing into the module-owned document line saver: quantity
  or product changes refresh the tier, replace or remove the linked benefit,
  and recalculate the quote total when the user saves the document.
- Preserved unmatched manual RFQ prices across quantity changes, while a manual
  value equal to the current effective tier price is recognized and traced as
  pricelist-derived.
- Bounded each RFQ at 500 paid-plus-benefit lines and used bulk update/create/
  delete operations for price and benefit synchronization.

## Direct Purchase Order pricing and MOQ slice completed in this audit

- Added bounded draft/revised PO price refresh using the same PR-cost-first,
  vendor-pricelist fallback, manual-price preservation, and generated
  free-product semantics as RFQ pricing.
- Retained price source, pricelist, tier, formula, tax, and free-benefit
  provenance for direct/manual PO lines without requiring an RFQ source line.
- Matched HMX MOQ behavior: below-first-tier remains no-match when enforcement
  is disabled; enabled MOQ warns in draft, preserves manual prices, uses the
  first tier by sequence, and blocks every confirmation path below MOQ.
- Excluded generated benefit lines from eligibility and MOQ confirmation
  checks and kept refresh queries constant in the 2-versus-12-line fixture.

## Purchase rental and timeline slice completed in this audit

- Detects rental orders from the trimmed, case-insensitive Order Type name,
  without adding an HMX-only boolean to the shared core PO model.
- Added a Purchase-owned Product rental profile and immutable PO-line rental
  snapshots; rental orders reject non-rental paid products.
- Supports global and per-line rental duration, day/week/month/year periods,
  global or per-line expected dates, first-line-to-header defaults, persistent
  line overrides, and calendar-correct month-end/leap-year return dates.
- Multiplies untaxed, discount, tax, and total snapshots by rental duration and
  carries rental/timeline provenance into PO revisions while reopening the
  draft tax snapshot.
- Added module-owned header and line form fields, expected/expiry propagation
  that updates inherited lines but preserves overrides, and selected-RFQ
  timeline propagation into the generated PO.
- Confirmation requires rental eligibility, expected date, and complete
  global/per-line duration data. Generated free benefits stay outside rental
  eligibility and duration totals.
- Rental synchronization remains bounded at 500 lines and the 2-versus-12
  fixture confirms non-growing query count.

## Purchase configuration UI slice completed in this audit

- The generic document registry now accepts module-owned header and line form
  classes, allowing Purchase fields to be shown and persisted without adding
  them to core PO/PO-line models.
- Added PO expiry, expected date, cost center, contract terms, rental controls,
  and Remark Template fields to the Purchase-owned document forms.
- Remark Templates now create immutable code/name/content snapshots on PR and
  PO; later template edits do not rewrite existing transaction remarks, and
  inactive, cross-company, or wrong-document templates are rejected.
- PO Templates can be duplicated from a scoped list action. Header terms and
  all bounded line data are copied to new records with deterministic unique
  copy names.
- Vendor Categories now retain the HMX description field and generate a local
  company-scoped code when callers omit one.

## Purchase Request timeline and commercial-default slice completed in this audit

- Added nullable PR header defaults plus per-line expected date, destination
  warehouse, and expiry date. Header changes update inherited lines while
  preserving explicit overrides; the first populated line can initialize an
  empty header.
- Propagated the per-line timeline into RFQs and selected Purchase Orders,
  rather than collapsing every line back to one header date.
- Added the HMX `Use Vendor Default Payment Terms` setting and Vendor payment
  terms. PO creation snapshots payment terms plus vendor/billing addresses so
  later Vendor edits do not rewrite historical orders.
- PO Template payment, delivery, and legal terms are immutable transaction
  snapshots and all commercial fields survive Purchase Order revision.
- Applied the same commercial-default behavior to manual, RFQ, Tender, Direct
  Purchase, and Template-generated Purchase Orders with a fixed-query batch
  helper.

## Purchase cost-center distribution slice completed in this audit

- Added HMX-shaped cost-center mode, distribution IDs/data, and analytic-tag
  IDs to PR, Tender, and Blanket headers/lines and to Purchase-owned PO
  control/provenance records.
- Validates company ownership, unique rows, bounded relation counts, single
  versus distribution mode, and an exact 100% distribution total.
- Preserves line overrides with header fallback and carries assignments through
  PR-to-Blanket, PR-to-Tender, RFQ selection, Tender award, Direct Purchase,
  and PO revision paths.
- Materializes Purchase assignments as Finance
  `JournalLineCostCenterAllocation` rows and immutable Purchase journal-line
  snapshots, including analytic tags. Full down-payment settlement uses the
  same path.
- The journal materialization fixture confirms a fixed query shape for 2 versus
  12 lines; assignment and journal batches are explicitly bounded.

## Purchase analysis slice completed in this audit

- Added PO and PR analysis contracts with the HMX product row and expected
  month column defaults.
- PO analysis exposes quantity, discount, untaxed, tax, total, and gross
  subtotal measures and excludes Draft/RFQ records.
- PR analysis exposes requested, approved, RFQ, PO, and received quantities and
  excludes unapproved headers and lines.
- Both reports are organization-scoped, bounded to 366 inclusive days and 500
  product-month groups, and exposed through Purchase Report UI endpoints.

## Vendor similarity and controlled-change slice completed in this audit

- Similar-vendor detection checks normalized email, phone, tax identifiers,
  mobile number, and bank account in one bounded company-scoped query and keeps
  blacklisted matches visible.
- Added parent-vendor company validation and a single-open-proposal constraint.
- Vendor change proposals snapshot the current values and keep live Contact and
  Vendor Profile data unchanged until approval.
- Approval uses row locks and optimistic snapshot checks so an out-of-band live
  edit cannot be overwritten; rejection/cancellation leave live data intact.

## RFQ mail and Purchase print slice completed in this audit

- Added company-scoped Standard RFQ, Urgent RFQ, and Follow-up RFQ templates
  matching the three HMX template identities. Missing defaults are provisioned
  idempotently and later customer edits are never overwritten.
- Added an RFQ composer with template onchange, editable recipients, CC,
  subject, and HTML body. Vendor/company eligibility and bounded recipient
  validation run before delivery.
- Every attempt retains immutable template, sender, recipient, subject, body,
  actor, timestamp, and failure evidence. A Draft RFQ advances to Sent only
  after the email backend confirms delivery.
- RFQ delivery attaches the existing scoped server-side RFQ PDF. Purchase
  Request, Tender, Blanket, RFQ, and Purchase Order PDF reports are available
  from their document workspaces.
- Purchase Order prints now include immutable vendor/billing addresses plus
  payment, delivery, legal, and contract terms; Blanket and PR prints include
  agreement terms and approval justification.
- Mail Templates and RFQ Communication History are exposed in the Purchase
  workspace, while communication history remains read-only.

## Verification snapshot

- Django system check: PASS.
- Migration drift check: PASS.
- Purchase sourcing tests: 50/50 PASS, including Blanket price traceability,
  draft refresh/manual preservation, free-product propagation, tax snapshot
  propagation, PR-to-RFQ price precedence, RFQ form-save tier refresh,
  immutability, validation, UI exposure, and query growth.
- Purchase sourcing plus Purchase line-merge integration: 55/55 PASS in 69.197
  seconds.
- Direct approval, eligibility, line-merge, and receiving integration tests:
  36/36 PASS.
- Combined current-source rerun: 69/69 PASS in 101.337 seconds.
- Purchase Team routing suite: 7/7 PASS.
- Compare RFQ suite: 5/5 PASS, including payload, UI, winner confirmation,
  mixed currency, and query growth.
- Current focused regression rerun across registry, routing, sourcing, RFQ
  comparison, KPI, approval, line merge, receiving, and vendor credit: 108/108 PASS in 152.841
  seconds.
- Current broad affected regression across registry, sourcing, receiving,
  eligibility, approval, RFQ comparison, routing, rental, direct PO pricing,
  line merge, and configuration: 125/125 PASS in 172.504 seconds.
- Current sourcing, configuration, rental, and PO timeline rerun after the
  latest migrations and form wiring: 74/74 PASS in 91.650 seconds.
- Purchase mail/print suite: 7/7 PASS, including no-update template behavior,
  composer UI, PDF delivery, SMTP failure evidence, company/recipient guards,
  four scoped Purchase print types, and fixed 2-versus-12-line query shape.
- Focused registry, navigation, analytics, and mail/print correction rerun:
  10/10 PASS.
- Purchase revision plus PO timeline regression: 10/10 PASS, including rental
  and price/timeline provenance copy into a revision.
- Query-growth fixtures: 2 versus 12 lines for Blanket creation and manual-close
  cancellation; PASS with non-growing query assertions.
- Purchase Team query-growth fixture: 2 versus 12 Purchase Requests; PASS with
  a non-growing query-count assertion.
- Blanket free-product creation fixture: 2 versus 12 paid/benefit lines; PASS
  with a non-growing query-count assertion.
- Blanket draft price-refresh fixture: 2 versus 12 paid/benefit lines; PASS
  with a non-growing query-count assertion.
- Blanket approval tax-snapshot fixture: 2 versus 12 lines; PASS with a
  non-growing query-count assertion.
- Compare RFQ fixture: 2 versus 12 RFQs; PASS with a non-growing query-count
  assertion.
- Multi-vendor PR-to-RFQ pricing fixture: 2 versus 12 vendors; PASS with a
  non-growing query-count assertion.
- Purchase Order line merge fixture: 2 versus 12 lines; 17 queries for both
  fixture sizes after adding the fixed provenance relation checks.
- Native Scale Guard SG001-SG004: PASS with zero new findings. The only reported
  item is an explicitly allowed SG003 for provisioning exactly three fixed mail
  template identities per company.
- Pre-commit wrapper: NOT RUN because the host has no `pre-commit` executable;
  the configured local hook is the native Scale Guard that was run directly.
- Million-row benchmark and representative PostgreSQL EXPLAIN: NOT RUN.

## Honest completion state

The complete Purchase application is **not yet 1:1**. Only rows marked MATCHED
or ADAPTED have current evidence. Rows marked DEFERRED or NOT VERIFIED must be
implemented and tested before a full 1:1 claim is allowed.
