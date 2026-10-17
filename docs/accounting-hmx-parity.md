# HMX Accounting 1:1 Parity Matrix

Source baseline:

- HMX worktree: `/home/yusufmjt/hmx-human-resources-latest`
- PRD audit: `/home/prd/prd_acc.doc` (extracted during implementation)
- GS Shop surface: `/apps/accounting/`

Status meaning:

- `MATCH`: the GS Shop flow has a dedicated data model, UI surface, permission boundary, and transactional service comparable to HMX.
- `PARTIAL`: the feature is usable, but an HMX sub-flow, source linkage, preview, retry, or country-specific behavior remains.
- `GAP`: no equivalent operational flow exists yet.

This document tracks functional parity, not framework class-name parity. HMX uses its own ORM and dynamic UI framework; GS Shop uses Django models, services, and generic workspace screens.

## Core accounting

| HMX feature | GS Shop implementation | Status | Remaining parity work |
|---|---|---:|---|
| Journal entry and lines | `JournalEntry`, `JournalLine`; draft/post/reverse services | MATCH | Final runtime/test verification |
| Multi-currency journals | Currency fields/rates, revaluation records and lines | MATCH | Final scenario verification |
| Cost-center allocation | Accounting cost centers plus percentage allocations per journal line | MATCH | Final scenario verification |
| Journal templates | Template/header/lines and instantiate service | MATCH | Final scenario verification |
| Recurring journals | Schedule, execution log, due runner | MATCH | Scheduler wiring/runtime verification |
| Closing/opening balance | Closing entry and opening balance lines | MATCH | Final scenario verification |
| Chart of accounts/types/tags | Dedicated masters and company constraints | MATCH | Final runtime verification |
| Product/category/variant accounts | Batched resolver with explicit mapping, variant, product, then category precedence | MATCH | Final scenario verification |
| Customer invoices/vendor bills | Invoice bridge and balanced posting | MATCH | Final scenario verification |
| Customer receipts/vendor payments | Payment, allocation and posting flows | MATCH | Final scenario verification |
| Multi receipt/payment | Batch and line models with posting | MATCH | Final scenario verification |
| Customer/vendor deposits | Typed deposits, lines, applications, conversion and history | MATCH | Final scenario verification |
| Payment voucher | Voucher/lines and posting | MATCH | Final scenario verification |
| Other income/expense | Header/lines and posting | MATCH | Final scenario verification |
| Payment unreconcile log | Payment lines, reconciliation history and immutable log | MATCH | Final scenario verification |
| Source-document cancellation | Request/approval/reversal service plus explicit source adapters and source-state synchronization | MATCH | Final scenario verification |
| Bank statements | Header/lines, matching and reconciliation history | MATCH | Final scenario verification |
| Bank import and OCR/parser | Import config, upload/mapping UI, bounded built-in CSV/OFX/text parser and authenticated HTTPS OCR provider | MATCH | Final provider/runtime verification |
| Reconciliation rules | Rules, conditions, counterpart lines, and test/suggestion runs | MATCH | Final scenario verification |
| Internal bank transfer | Transfer posting and FX fields | MATCH | Final scenario verification |
| Receipt/payment cheque | Cheque allocations plus issue/receive, clear, bounce, cancel, and reversal events | MATCH | Final scenario verification |
| Bank/cash book | Report surface/export | MATCH | Final scenario verification |
| Bank/cash multicurrency valuation | Valuation header/lines and posting | MATCH | Final scenario verification |
| Tax distribution | Normal and credit-note distributions | MATCH | Final scenario verification |
| Withholding tax | Transactions, lines, validate, reverse and history | MATCH | Final scenario verification |
| Fiscal year/period/position | Masters, account/tax mappings and posting lock | MATCH | Final scenario verification |
| Financial statements/reports | GL, TB, partner ledger, BS, P&L, cash flow, equity, ratios and exports | MATCH | Pixel/report-value comparison |
| Accounting Vision/intelligence | Configurable thresholds/scoring/governance/routing, health metrics, evidence-backed risk narratives, action ownership, data-quality disclosure and baseline forecast | MATCH | Final dashboard presentation and scenario verification |
| Prepayment | Header, applications and reversal | MATCH | Final scenario verification |
| AR reconciliation | Bounded FIFO suggestions, partial debit-credit allocations, residual enforcement, reconcile and non-destructive reverse | MATCH | Final concurrency/scenario verification |
| Currency rate/revaluation | Rate logs, average/closing types, FX posting and bounded provider synchronization | MATCH | Final provider/runtime verification |
| Company provisioning | Registry-driven core/extension account, journal, settings and tax provisioning | MATCH | Country-template depth comparison |

## Dependent accounting modules

| HMX module/feature | GS Shop implementation | Status | Remaining parity work |
|---|---|---:|---|
| Asset depreciation | Schedule lines and posting/reversal | MATCH | Final scenario verification |
| Asset revaluation | Approval/post/reversal and gain/loss | MATCH | Retry/error detail |
| Asset sale/disposal | Sold/scrapped workflows, linked customer invoice, combined disposal journal, retry/error and reversal | MATCH | Final scenario verification |
| Asset accounting settings | Company defaults plus per-category disposal journal/gain/loss/clearing overrides and foreign-currency sale rates | MATCH | Final scenario verification |
| Asset capitalization (CIP) | CIP sources, approval, multi-asset allocation, journal, and asset creation | MATCH | Final scenario verification |
| POS close accounting | Session close/reopen, sales, payments, COGS, consumption and variance | MATCH | Opening-entry/closing-summary presentation |
| POS payment mapping | Per-method settlement and fee accounts | MATCH | Final scenario verification |
| Payroll configuration | Company journal/payable/bank defaults | MATCH | Final scenario verification |
| Salary component mapping | Per-component account override | MATCH | Final scenario verification |
| Payroll accrual/payment | Accounting event posting with component resolver | MATCH | Payslip smart-link presentation |
| Inventory valuation | Automatic stock-move accounting | MATCH | Product/category precedence verification |
| Landed cost | Dedicated document with equal/quantity/value allocation, approval, post, and reverse | MATCH | Final scenario verification |
| Product usage | Usage type, accounting document, approval, cost center, post, and reverse | MATCH | Final scenario verification |
| Stock count/block | Control documents, difference/block journal behavior, approval and reversal | MATCH | Final scenario verification |
| Transfer accounting | Paired transfer accounting document and audit journal | MATCH | Final scenario verification |
| Inventory approval workflow | Waiting/approved/posted/reversed posting gate | MATCH | Final scenario verification |
| Purchase accounting | Vendor document bridge and product account codes | MATCH | Partner-default presentation |
| Sales accounting | Invoice bridge plus direct sale-order revenue/COGS posting | MATCH | Final scenario verification |
| Advance disbursement | Approval creates audited accounting event | MATCH | Journal preview before approval |
| Advance recognition/return | Expense settlement calculates applied/return values and posts return | MATCH | Confirmation preview presentation |
| Expense reimbursement/close | Recognition, reimbursement, close and reversal with product/variant/category expense-account resolver | MATCH | Final scenario verification |
| Budget allocation/change/transfer/carry-over | Dedicated documents, lines, approvals and audit | MATCH | Final scenario verification |
| Budget versus actual | Budget, committed, actual, Other Expense consumption/reversal, and integration-event budget adapter contract | MATCH | Final cross-module scenario verification |
| Petty cash | Fund, categories, voucher, replenishment, reconciliation and reversal | MATCH | Final scenario verification |

## Localization

| Country/HMX feature | GS Shop implementation | Status | Remaining parity work |
|---|---|---:|---|
| Indonesia VAT/PPh | Coretax VAT-in/out, e-Bupot/BPPU tax snapshots, identity validation and deterministic authority payload/export | MATCH | Final fixture comparison |
| Indonesia middleware/Pajak Express | Country backend, exact operation routing, authenticated transport, signed callback endpoint, queue/retry/response and audit | MATCH | Credential/runtime verification |
| Indonesia BI rate | Regulatory exchange-rate records plus bounded provider synchronization | MATCH | Provider/runtime verification |
| Malaysia SST/IRBM | SST/IRBM calculations, TIN lookup, send queue, response audit and idempotent received-invoice cursor sync | MATCH | Final fixture/provider verification |
| Philippines BIR forms/SAWT/RELIEF | Form-specific totals/detail rows, TIN/branch validation and deterministic DAT export | MATCH | Final fixture comparison |
| Singapore GST F5/F7/IAF/Peppol | GST box calculations, IAF/Peppol document payload, transport and received-invoice sync | MATCH | Final fixture/provider verification |
| Thailand VAT/PP30 | Tax catalog, PP30 run calculations, adjustments, carry-forward and filing payload validation | MATCH | Final fixture comparison |
| Australia BAS/GST | BAS labels G1/G2/G3/G10/G11/1A/1B, net GST calculation and profile validation | MATCH | Final fixture comparison |

## PRD direct dependencies (33-module check)

The source adapter registry is an extension contract, not evidence that a source-specific HMX behavior matches. A consumer only reaches `MATCH` when its named accounting behavior is implemented.

| HMX direct dependency | GS Shop coverage | Status | Remaining parity work |
|---|---|---:|---|
| `core_accounting_au` | BAS profile/template/tax/report engine and authority-shaped output | MATCH | Final fixture verification |
| `core_accounting_id` | VAT/PPh, Coretax/e-Bupot/BPPU catalogs, BI rates, connector/callback lifecycle | MATCH | Final fixture/provider verification |
| `core_accounting_intelligence` | Configurable health scoring and source coverage with AR/AP, margin, budget, reconciliation, duplicate, ledger-integrity signals, narratives, action routing, and forecast snapshot | MATCH | Final dashboard presentation and scenario verification |
| `core_accounting_my` | SST/IRBM templates, TIN lookup, queue/send/response and received-invoice cursor sync | MATCH | Final fixture/provider verification |
| `core_accounting_ph` | BIR catalogs, form-aware report lines, validation and DAT export | MATCH | Final fixture verification |
| `core_accounting_sg` | GST F5/F7, IAF/Peppol catalogs, transport, callbacks and received-invoice sync | MATCH | Final fixture/provider verification |
| `core_accounting_th` | VAT/PP30 templates, run calculations and scheduled operational service | MATCH | Final fixture verification |
| `core_asset_accounting` | Depreciation/revaluation plus sold/scrapped disposal, category override resolution, foreign-currency proceeds/rate validation, due-date posting gate, combined invoice journal, retry/error, reversal and scheduled due-date finalizer | MATCH | Final scenarios |
| `core_asset_capitalization` | CIP source, approval, allocation, journal and asset creation | MATCH | Final scenario verification |
| `core_budget_accounting` | Accounts/groups, allocation/change/transfer/carry-over, cost center, actuals | MATCH | Final scenario verification |
| `core_construction_accounting` | Dedicated construction posting model and service bridge | MATCH | Final scenario verification |
| `core_expense_advance_accounting` | Disbursement, recognition, return, reimbursement, close/reverse | MATCH | Final scenario verification |
| `core_hmx_hm3_accounting` | Pluggable outbound connector, HM3 multi-db session/OAuth1, customer/currency sync, invoice push, retry and status callback contract | MATCH | Endpoint credential/runtime verification |
| `core_hr_accounting` | Payroll settings, component mapping, accrual/payment events | MATCH | Final scenario verification |
| `core_inventory_accounting` | Valuation, landed cost, usage, count/block, transfer, approvals | MATCH | Final scenario verification |
| `core_localization_accounting` | Discoverable country packs, profiles, templates, tax codes and audit | MATCH | Final scenario verification |
| `core_localization_sales` | Sale-order localization header/lines, country-scoped tax selection, snapshots and balanced tax posting | MATCH | Final scenario verification |
| `core_marketplace` | Provider-neutral order, receivable, fee, refund and wallet settlement adapter with idempotent post/reversal | MATCH | Final provider callback/scenario verification |
| `core_petty_cash` | Fund, category, voucher, replenishment, reconciliation, journal/audit | MATCH | Final scenario verification |
| `core_pos` | Full GS POS domain is present | MATCH | Final scenario verification |
| `core_pos_accounting` | Close/reopen, sales/payment/COGS/consumption/variance accounting | MATCH | Final scenario verification |
| `core_pos_inventory` | POS usage, demand, transfer and stock accounting | MATCH | Final scenario verification |
| `core_pos_invoicing` | Idempotent per-order invoice/deposit/MDR journal, deposit history, reversal and session-close exclusion | MATCH | Final scenario verification |
| `core_purchase_accounting` | Vendor document, settings/mappings, approvals and posting bridge | MATCH | Final scenario verification |
| `core_rental_accounting` | Deposit eligibility/hold/release, immutable liability setting, down-payment/settlement provenance, invoice creation/post/reversal | MATCH | Final scenario verification |
| `core_sale_accounting` | Direct sale-order revenue, COGS, journal selection and bridge | MATCH | CRM smart-link presentation |
| `core_school` | School/student/course/library masters plus company-scoped billing-contact and accounting references | MATCH | Final cross-workspace scenario verification |
| `core_school_invoicing` | Fee types/structures/items, snapshot schedules, batch due-invoice generation, billing-run audit, invoice provenance, and installment approval/reschedule lifecycle | MATCH | Final schedule/installment scenario verification |
| `core_school_library` | Institution-scoped late-return calculation, posted fine invoice, immutable revenue provenance, retry/error and cancellation reset | MATCH | Final return/fine scenario verification |
| `core_service_operation` | Approved service-work-order vendor-bill adapter, line transfer, posting lifecycle and retry | MATCH | Final scenario verification |
| `master_sale_accounting` | Sales down-payment/settlement invoices with same-order line provenance, immutable promotion expense snapshots, and gross-revenue/promotion-expense posting | MATCH | Final promotion/down-payment scenario verification |
| `master_sale_anglo_saxon` | Product mapping plus COGS/inventory posting | MATCH | Final scenario verification |
| `master_sale_portal` | Discoverable document-type registry, stable external UUID references, user-to-customer mapping, company/customer/status guards, and read-only order/invoice list/detail portal | MATCH | Final authorization, pagination, and presentation scenarios |

## Active implementation order

1. Complete static/system validation of the generated migration and application registry.
2. Run Scale Guard review against the final implementation snapshot.
3. Run the full automated/scenario suite and HMX fixture comparisons last, as requested.
