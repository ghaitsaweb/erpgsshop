# HMX Purchase Approval Workflow Parity

Baseline: HMX commit `f616d3ad55f8e3fe5a353c8700e235b941d9a7ab` in
`hmx/module/basic/core_purchase`.

## Contract

| Workflow | State and authorization | Domain side effects | Result |
| --- | --- | --- | --- |
| Purchase Vendor | Draft → Waiting For Approval → Confirmed/Rejected; Team Purchase, minimum one approver | Blocking legal documents revalidated; approver and approval time recorded; rejection reason retained | MATCHED |
| RFQ / Purchase Order | Draft/Sent → Waiting Approval → Confirmed/Rejected; company-scoped Purchase Manager | Line eligibility and vendor purchase limit revalidated; totals and control confirmation metadata updated | MATCHED |
| Vendor Pricelist | Draft → Waiting Approval → Active/Rejected | Requires a product line and total price of at least 1; activation state retained | MATCHED |
| Vendor Pricelist Amendment | Draft/Submitted → Waiting For Approval → Approved/Rejected | Bounded item/tier changes apply atomically only after final approval | ADAPTED |
| Blanket Order | Draft → Waiting Approval → Confirmed/Rejected | Bounded line and vendor eligibility checks run on submit and final approval | MATCHED |
| Purchase Request | Draft → Pending Approval → Approved/Rejected; Purchase Manager and Purchase Requestor Manager | Every line requires an approve/reject decision; quantity amendments and rejections require a 10-character reason | ADAPTED |
| Vendor Performance Evaluation | Draft → Waiting Approval → Confirmed/Rejected | Template snapshot and scores validated; confirmer metadata and optional score recalculation preserved | MATCHED |
| Purchase KPI Target | Draft → Waiting Approval → Active/Rejected | Matrix is fail-closed; header and every KPI line record activation user/time | MATCHED |
| Tender Decision | Tender and awarded-decision cycles each create an immutable Approval transaction | Award allocation is revalidated; rejection reason and decision revision lifecycle are preserved | ADAPTED |

`ADAPTED` means the local schema names or normalized storage differ while observable
state, authorization, validation, audit history, and final side effects match HMX.
There are no deferred or unverified Purchase approval workflow gaps in this scope.

## Local integration contract

- Approval Workflow owns rule resolution, frozen approver snapshots, delegation,
  quorum, audit actions, notifications, and transaction completion.
- Purchase owns every domain transition and registers it through the generic
  approval adapter registry. Shared Approval and core views contain no
  Purchase-model branch for these workflows.
- Purchase Request approval cannot bypass its line-decision workspace.
- List-sized reads and writes are bounded; approval rule and approver resolution
  use fixed query plans rather than record-count loops.

## Evidence

- HMX configuration: `basic/core_purchase/data/approval_workflow_data.xml` and
  `basic/core_purchase/data/approval_rules_data.xml`.
- HMX Purchase Request decisions:
  `basic/core_purchase/wizard/purchase_request_approval_wizard.py`.
- HMX Amendment lifecycle:
  `basic/core_purchase/models/vendor_pricelist_amendment.py`.
- HMX KPI activation:
  `basic/core_purchase/models/purchase_kpi.py`.
- GS Shop integration tests:
  `modules/purchase/tests_purchase_approval.py` and
  `modules/purchase/tests_purchase_approval_parity.py`.
