"""Generic CRUD registry for the Purchase workspace."""

from django.db import models

from core.models import PurchaseOrderLine

from . import models as purchase_models
from .forms import PurchaseLegalDocumentForm


APPROVAL_WORKFLOW = {
    "submit": {"label": "Submit", "from": ["draft"], "to": "submitted", "primary": True},
    "approve": {"label": "Approve", "from": ["submitted"], "to": "approved", "primary": True},
    "reject": {"label": "Reject", "from": ["submitted"], "to": "rejected"},
    "cancel": {"label": "Cancel", "from": ["draft", "submitted"], "to": "cancelled"},
    "reset": {"label": "Reset to Draft", "from": ["rejected", "cancelled"], "to": "draft"},
}

REQUEST_WORKFLOW = {
    "submit": {"label": "Submit", "from": ["draft"], "to": "pending", "primary": True},
    "approve": {
        "label": "Approve",
        "from": ["pending", "partial_approved"],
        "to": "approved",
        "primary": True,
    },
    "reject": {"label": "Reject", "from": ["pending", "partial_approved"], "to": "rejected"},
    "re-request": {
        "label": "Re-Request",
        "from": ["approved", "partial_rfq", "rfq", "partial_ordered", "in_progress"],
        "to": "approved",
    },
    "create-rfq": {
        "label": "Create RFQ",
        "from": ["approved", "partial_rfq", "rfq", "in_progress"],
        "to": "in_progress",
        "primary": True,
    },
    "create-blanket": {
        "label": "Create Blanket Order",
        "from": ["approved", "partial_rfq", "rfq", "in_progress"],
        "to": "in_progress",
    },
    "create-tender": {
        "label": "Create Tender",
        "from": ["approved", "partial_rfq", "rfq", "in_progress"],
        "to": "in_progress",
    },
    "create-direct": {
        "label": "Create Direct Purchase",
        "from": ["approved", "partial_rfq", "rfq", "in_progress"],
        "to": "in_progress",
    },
    "mark-ordered": {
        "label": "Mark Fully Ordered",
        "from": ["rfq", "partial_ordered"],
        "to": "fully_ordered",
        "primary": True,
    },
    "start": {"label": "Start Fulfilment", "from": ["fully_ordered"], "to": "in_progress", "primary": True},
    "close": {"label": "Close", "from": ["in_progress"], "to": "closed", "primary": True},
}

TENDER_WORKFLOW = {
    "submit": {"label": "Submit for Approval", "from": ["draft"], "to": "waiting_approval", "primary": True, "service_controls_state": True},
    "approve": {"label": "Approve", "from": ["waiting_approval"], "to": "confirmed", "primary": True, "service_controls_state": True},
    "reject": {"label": "Reject", "from": ["waiting_approval"], "to": "rejected", "service_controls_state": True},
    "publish": {"label": "Publish", "from": ["confirmed"], "to": "published", "primary": True, "service_controls_state": True},
    "open-submission": {"label": "Open Submission", "from": ["published"], "to": "bid_submission", "primary": True, "service_controls_state": True},
    "open-bids": {"label": "Open Bid Submission", "from": ["document_submission"], "to": "bid_submission", "primary": True, "service_controls_state": True},
    "evaluate": {"label": "Start Bid Selection", "from": ["bid_submission", "submission"], "to": "bid_selection", "primary": True, "service_controls_state": True},
    "award": {"label": "Award", "from": ["bid_selection"], "to": "awarded", "primary": True, "service_controls_state": True},
    "submit-decision": {"label": "Submit Decision", "from": ["awarded"], "to": "awarded", "primary": True, "service_controls_state": True},
    "approve-decision": {"label": "Approve Decision", "from": ["awarded"], "to": "awarded", "primary": True, "service_controls_state": True},
    "reject-decision": {
        "label": "Reject Decision",
        "from": ["awarded"],
        "to": "awarded",
        "reason_label": "Tender decision rejection reason",
        "service_controls_state": True,
    },
    "reset-decision": {"label": "Reset Decision", "from": ["awarded"], "to": "awarded", "service_controls_state": True},
    "reopen-award": {"label": "Reopen Award", "from": ["awarded"], "to": "bid_selection", "service_controls_state": True},
    "close": {"label": "Close", "from": ["awarded"], "to": "closed", "primary": True, "service_controls_state": True},
    "cancel": {"label": "Cancel", "from": ["draft", "waiting_approval", "confirmed", "published", "document_submission", "bid_submission", "bid_selection"], "to": "cancelled", "service_controls_state": True},
}

VENDOR_PRICELIST_WORKFLOW = {
    "submit": {
        "label": "Submit for Approval",
        "from": ["draft"],
        "to": "waiting_approval",
        "primary": True,
        "service_controls_state": True,
    },
    "approve": {
        "label": "Approve & Activate",
        "from": ["waiting_approval"],
        "to": "active",
        "primary": True,
        "service_controls_state": True,
    },
    "reject": {
        "label": "Reject",
        "from": ["waiting_approval"],
        "to": "rejected",
        "service_controls_state": True,
    },
    "archive": {
        "label": "Archive",
        "from": ["active", "expired"],
        "to": "inactive",
        "service_controls_state": True,
    },
    "reset": {
        "label": "Reset to Draft",
        "from": ["rejected", "inactive"],
        "to": "draft",
        "service_controls_state": True,
    },
}

VENDOR_PRICELIST_AMENDMENT_WORKFLOW = {
    "submit": {
        "label": "Submit for Approval",
        "from": ["draft", "submitted"],
        "to": "waiting_approval",
        "primary": True,
        "service_controls_state": True,
    },
    "approve": {
        "label": "Approve",
        "from": ["waiting_approval"],
        "to": "approved",
        "primary": True,
        "service_controls_state": True,
    },
    "reject": {
        "label": "Reject",
        "from": ["waiting_approval"],
        "to": "rejected",
        "service_controls_state": True,
    },
    "cancel": {
        "label": "Cancel",
        "from": ["draft", "submitted", "waiting_approval"],
        "to": "cancelled",
        "service_controls_state": True,
    },
    "reset": {
        "label": "Reset to Draft",
        "from": ["rejected", "cancelled"],
        "to": "draft",
        "service_controls_state": True,
    },
}

VENDOR_PERFORMANCE_EVALUATION_WORKFLOW = {
    "submit": {
        "label": "Submit for Approval",
        "from": ["draft"],
        "to": "waiting_approval",
        "primary": True,
        "service_controls_state": True,
    },
    "approve": {
        "label": "Approve & Confirm",
        "from": ["waiting_approval"],
        "to": "confirmed",
        "primary": True,
        "service_controls_state": True,
    },
    "reject": {
        "label": "Reject",
        "from": ["waiting_approval"],
        "to": "rejected",
        "reason_label": "Vendor Performance rejection reason",
        "service_controls_state": True,
    },
}

SERVICE_WORK_ORDER_WORKFLOW = {
    "start": {
        "label": "Start",
        "from": ["draft"],
        "to": "in_progress",
        "primary": True,
        "service_controls_state": True,
    },
    "done": {
        "label": "Mark Done",
        "from": ["in_progress"],
        "to": "done",
        "primary": True,
        "service_controls_state": True,
    },
    "create-bill": {
        "label": "Create Vendor Bill",
        "from": ["in_progress", "done"],
        "to": "in_progress",
        "service_controls_state": True,
    },
}

SERVICE_WORK_ORDER_LINE_WORKFLOW = {
    "done": {
        "label": "Mark Done",
        "from": ["in_progress"],
        "to": "done",
        "primary": True,
        "service_controls_state": True,
    },
}

VENDOR_PROFILE_WORKFLOW = {
    "submit": {
        "label": "Submit for Approval",
        "from": ["draft"],
        "to": "waiting_approval",
        "primary": True,
        "service_controls_state": True,
    },
    "approve": {
        "label": "Approve & Confirm",
        "from": ["waiting_approval"],
        "to": "confirmed",
        "primary": True,
        "service_controls_state": True,
    },
    "reject": {
        "label": "Reject",
        "from": ["waiting_approval"],
        "to": "rejected",
        "reason_label": "Vendor rejection reason",
        "service_controls_state": True,
    },
    "reset": {
        "label": "Reset to Draft",
        "from": ["rejected"],
        "to": "draft",
        "service_controls_state": True,
    },
    "blacklist": {
        "label": "Blacklist",
        "from": ["draft", "waiting_approval", "confirmed", "rejected"],
        "to": "blacklisted",
        "reason_label": "Blacklist reason",
        "service_controls_state": True,
    },
    "unblacklist": {
        "label": "Unblacklist",
        "from": ["blacklisted"],
        "to": "draft",
        "reason_label": "Unblacklist reason",
        "service_controls_state": True,
    },
}

VENDOR_LEGAL_DOCUMENT_WORKFLOW = {
    "submit": {
        "label": "Submit Renewal",
        "from": ["draft", "rejected"],
        "to": "pending_review",
        "primary": True,
        "service_controls_state": True,
    },
    "approve": {
        "label": "Approve Renewal",
        "from": ["pending_review"],
        "to": "approved",
        "primary": True,
        "service_controls_state": True,
    },
    "reject": {
        "label": "Reject Renewal",
        "from": ["pending_review"],
        "to": "rejected",
        "reason_label": "Document rejection reason",
        "service_controls_state": True,
    },
}

VENDOR_PREQUALIFICATION_WORKFLOW = {
    "submit": {
        "label": "Submit",
        "from": ["draft", "rejected"],
        "to": "pending",
        "primary": True,
        "service_controls_state": True,
    },
    "review": {
        "label": "Start Review",
        "from": ["pending"],
        "to": "review",
        "primary": True,
        "service_controls_state": True,
    },
    "approve": {
        "label": "Approve",
        "from": ["pending", "review"],
        "to": "approved",
        "primary": True,
        "service_controls_state": True,
    },
    "reject": {
        "label": "Reject",
        "from": ["pending", "review"],
        "to": "rejected",
        "reason_label": "Prequalification rejection reason",
        "service_controls_state": True,
    },
}

VENDOR_DOCUMENT_REVIEW_WORKFLOW = {
    "submit": {
        "label": "Submit Document",
        "from": ["draft"],
        "to": "pending",
        "primary": True,
        "service_controls_state": True,
    },
    "approve": {
        "label": "Approve",
        "from": ["pending", "revision"],
        "to": "approved",
        "primary": True,
        "service_controls_state": True,
    },
    "reject": {
        "label": "Reject",
        "from": ["pending"],
        "to": "rejected",
        "reason_label": "Document rejection reason",
        "service_controls_state": True,
    },
    "revision": {
        "label": "Request Revision",
        "from": ["pending"],
        "to": "revision",
        "reason_label": "Revision reason",
        "service_controls_state": True,
    },
    "reset": {
        "label": "Reset to Draft",
        "from": ["rejected", "revision"],
        "to": "draft",
        "service_controls_state": True,
    },
}

VENDOR_PRICELIST_IMPORT_WORKFLOW = {
    "validate": {
        "label": "Validate & Preview",
        "from": ["draft", "failed"],
        "to": "validated",
        "primary": True,
        "service_controls_state": True,
    },
    "import": {
        "label": "Start Import",
        "from": ["validated"],
        "to": "processing",
        "primary": True,
        "service_controls_state": True,
    },
}

PURCHASE_KPI_TARGET_WORKFLOW = {
    "submit": {
        "label": "Submit for Approval",
        "from": ["draft"],
        "to": "waiting_approval",
        "primary": True,
        "service_controls_state": True,
    },
    "approve": {
        "label": "Approve & Activate",
        "from": ["waiting_approval"],
        "to": "active",
        "primary": True,
        "service_controls_state": True,
    },
    "reject": {
        "label": "Reject",
        "from": ["waiting_approval"],
        "to": "rejected",
        "service_controls_state": True,
    },
    "calculate": {
        "label": "Calculate Result",
        "from": ["active"],
        "to": "active",
        "preserve_status": True,
        "service_controls_state": True,
    },
    "close": {
        "label": "Close & Finalize",
        "from": ["active"],
        "to": "closed",
        "primary": True,
        "service_controls_state": True,
    },
}

WORKFLOW_OVERRIDES = {
    "vendor-pricelists": VENDOR_PRICELIST_WORKFLOW,
    "vendor-pricelist-amendments": VENDOR_PRICELIST_AMENDMENT_WORKFLOW,
    "vendor-performance-evaluations": VENDOR_PERFORMANCE_EVALUATION_WORKFLOW,
    "service-work-orders": SERVICE_WORK_ORDER_WORKFLOW,
    "service-work-order-lines": SERVICE_WORK_ORDER_LINE_WORKFLOW,
    "vendor-profiles": VENDOR_PROFILE_WORKFLOW,
    "vendor-legal-documents": VENDOR_LEGAL_DOCUMENT_WORKFLOW,
    "vendor-prequalifications": VENDOR_PREQUALIFICATION_WORKFLOW,
    "vendor-document-reviews": VENDOR_DOCUMENT_REVIEW_WORKFLOW,
    "vendor-pricelist-imports": VENDOR_PRICELIST_IMPORT_WORKFLOW,
    "purchase-kpi-targets": PURCHASE_KPI_TARGET_WORKFLOW,
    "purchase-quotations": {
        "send": {"label": "Send to Vendor", "from": ["draft"], "to": "sent", "primary": True, "service_controls_state": True},
        "receive": {"label": "Record Response", "from": ["sent"], "to": "received", "primary": True, "service_controls_state": True},
        "select": {"label": "Select Quote", "from": ["received"], "to": "selected", "primary": True, "service_controls_state": True},
        "cancel": {"label": "Cancel", "from": ["draft", "sent", "received"], "to": "cancelled", "service_controls_state": True},
    },
    "tender-bids": {
        "submit": {"label": "Submit", "from": ["draft"], "to": "submitted", "primary": True},
        "shortlist": {"label": "Shortlist", "from": ["submitted"], "to": "shortlisted", "primary": True},
        "award": {"label": "Award", "from": ["shortlisted"], "to": "awarded", "primary": True},
        "reject": {"label": "Reject", "from": ["submitted", "shortlisted"], "to": "rejected"},
    },
    "blanket-orders": {
        "submit": {"label": "Submit for Approval", "from": ["draft"], "to": "waiting_approval", "primary": True, "service_controls_state": True},
        "approve": {"label": "Approve", "from": ["waiting_approval"], "to": "confirmed", "primary": True, "service_controls_state": True},
        "reject": {"label": "Reject", "from": ["waiting_approval"], "to": "rejected", "service_controls_state": True},
        "create-rfq": {"label": "Create RFQ", "from": ["confirmed", "running"], "to": "running", "primary": True, "service_controls_state": True},
        "run": {"label": "Run", "from": ["confirmed"], "to": "running", "service_controls_state": True},
        "close": {"label": "Close", "from": ["confirmed", "running"], "to": "closed", "service_controls_state": True},
        "reopen": {"label": "Reopen", "from": ["closed"], "to": "running", "service_controls_state": True},
        "cancel": {"label": "Cancel", "from": ["draft", "waiting_approval", "confirmed", "running"], "to": "cancelled", "service_controls_state": True},
    },
    "purchase-order-milestones": {
        "mark-due": {"label": "Mark Due", "from": ["pending"], "to": "due"},
        "complete": {"label": "Complete", "from": ["pending", "due"], "to": "completed", "primary": True},
        "waive": {"label": "Waive", "from": ["pending", "due"], "to": "waived"},
    },
    "goods-receipts": {
        "inspect": {"label": "Start Inspection", "from": ["draft"], "to": "inspecting", "primary": True},
        "receive": {"label": "Accept Receipt", "from": ["inspecting"], "to": "received", "primary": True},
        "reject": {"label": "Reject Receipt", "from": ["inspecting"], "to": "rejected"},
        "cancel": {"label": "Cancel", "from": ["draft"], "to": "cancelled"},
    },
    "purchase-peppol-submissions": {
        "submit": {"label": "Submit", "from": ["draft"], "to": "submitted", "primary": True},
        "process": {"label": "Mark Processing", "from": ["submitted"], "to": "processing"},
        "succeed": {"label": "Mark Success", "from": ["processing"], "to": "success", "primary": True},
        "fail": {"label": "Mark Failed", "from": ["submitted", "processing"], "to": "failed"},
        "cancel": {"label": "Cancel", "from": ["draft", "submitted"], "to": "cancelled"},
    },
    "tender-award-responses": {
        "accept": {"label": "Accept Award", "from": ["pending"], "to": "accepted", "primary": True, "service_controls_state": True},
        "reject": {"label": "Reject Award", "from": ["pending"], "to": "rejected", "service_controls_state": True},
        "expire": {"label": "Mark No Response", "from": ["pending"], "to": "no_response", "service_controls_state": True},
    },
}


ENTITY_MODELS = {
    "purchase-requests": purchase_models.PurchaseRequest,
    "purchase-request-lines": purchase_models.PurchaseRequestLine,
    "purchase-quotations": purchase_models.PurchaseQuotation,
    "direct-purchases": purchase_models.DirectPurchase,
    "service-work-orders": purchase_models.ServiceWorkOrder,
    "award-strategies": purchase_models.AwardStrategy,
    "purchase-tenders": purchase_models.PurchaseTender,
    "tender-bids": purchase_models.TenderBid,
    "blanket-orders": purchase_models.BlanketOrder,
    "vendor-categories": purchase_models.VendorCategory,
    "purchase-legal-documents": purchase_models.PurchaseLegalDocument,
    "purchase-legal-document-attachments": purchase_models.PurchaseLegalDocumentAttachment,
    "vendor-profiles": purchase_models.VendorProfile,
    "vendor-legal-documents": purchase_models.VendorLegalDocument,
    "vendor-prequalifications": purchase_models.VendorPrequalification,
    "vendor-document-reviews": purchase_models.VendorDocumentReview,
    "vendor-pricelists": purchase_models.VendorPricelist,
    "vendor-pricelist-items": purchase_models.VendorPricelistItem,
    "vendor-pricelist-tiers": purchase_models.VendorPricelistTier,
    "vendor-pricelist-amendments": purchase_models.VendorPricelistAmendment,
    "vendor-pricelist-amendment-lines": purchase_models.VendorPricelistAmendmentLine,
    "vendor-pricelist-imports": purchase_models.VendorPricelistImportLog,
    "vendor-pricelist-import-previews": purchase_models.VendorPricelistImportPreview,
    "vendor-performance-templates": purchase_models.VendorPerformanceTemplate,
    "vendor-performance-template-lines": purchase_models.VendorPerformanceTemplateLine,
    "vendor-performance-evaluations": purchase_models.VendorPerformanceEvaluation,
    "vendor-performance-evaluation-lines": purchase_models.VendorPerformanceEvaluationLine,
    "vendor-performance-score-history": purchase_models.VendorPerformanceScoreHistory,
    "purchase-teams": purchase_models.PurchaseTeam,
    "purchase-team-members": purchase_models.PurchaseTeamMember,
    "purchase-kpi-templates": purchase_models.PurchaseKPITemplate,
    "purchase-kpi-template-lines": purchase_models.PurchaseKPITemplateLine,
    "purchase-kpi-targets": purchase_models.PurchaseKPITarget,
    "purchase-kpi-target-lines": purchase_models.PurchaseKPITargetLine,
    "purchase-order-types": purchase_models.PurchaseOrderType,
    "purchase-order-type-sequences": purchase_models.PurchaseOrderTypeSequenceLine,
    "purchase-order-templates": purchase_models.PurchaseOrderTemplate,
    "purchase-order-template-lines": purchase_models.PurchaseOrderTemplateLine,
    "purchase-remark-templates": purchase_models.PurchaseRemarkTemplate,
    "purchase-milestone-templates": purchase_models.PurchaseMilestoneTemplate,
    "purchase-milestone-template-lines": purchase_models.PurchaseMilestoneTemplateLine,
    "purchase-settings": purchase_models.PurchaseSetting,
    "purchase-order-controls": purchase_models.PurchaseOrderControl,
    "purchase-three-way-matches": purchase_models.PurchaseThreeWayMatch,
    "purchase-order-lines": PurchaseOrderLine,
    "purchase-order-milestones": purchase_models.PurchaseOrderMilestone,
    "product-budget-groups": purchase_models.ProductBudgetGroup,
    "product-budgets": purchase_models.ProductBudget,
    "budget-product-transfers": purchase_models.BudgetProductTransfer,
    "budget-product-carry-overs": purchase_models.BudgetProductCarryOver,
    "budget-product-change-requests": purchase_models.BudgetProductChangeRequest,
    "goods-receipts": purchase_models.GoodsReceipt,
    "goods-receipt-lines": purchase_models.GoodsReceiptLine,
    "purchase-return-requests": purchase_models.PurchaseReturnRequest,
    "purchase-accounting-settings": purchase_models.PurchaseAccountingSetting,
    "purchase-account-mappings": purchase_models.PurchaseAccountMapping,
    "purchase-peppol-submissions": purchase_models.PurchasePeppolSubmission,
    "purchase-insights": purchase_models.PurchaseInsight,
    "purchase-quotation-lines": purchase_models.PurchaseQuotationLine,
    "direct-purchase-lines": purchase_models.DirectPurchaseLine,
    "service-work-order-lines": purchase_models.ServiceWorkOrderLine,
    "purchase-tender-lines": purchase_models.PurchaseTenderLine,
    "tender-bid-lines": purchase_models.TenderBidLine,
    "tender-award-allocations": purchase_models.TenderAwardAllocation,
    "tender-award-responses": purchase_models.TenderAwardResponse,
    "tender-invitations": purchase_models.TenderInvitation,
    "tender-documents": purchase_models.TenderDocument,
    "tender-document-completion-matrices": purchase_models.TenderDocumentCompletionMatrix,
    "tender-document-completion-lines": purchase_models.TenderDocumentCompletionLine,
    "tender-audit-logs": purchase_models.TenderAuditLog,
    "blanket-order-lines": purchase_models.BlanketOrderLine,
    "purchase-product-categories": purchase_models.PurchaseProductCategory,
    "purchase-product-variants": purchase_models.PurchaseProductVariant,
    "vendor-communications": purchase_models.VendorCommunication,
    "vendor-transaction-history": purchase_models.VendorTransactionHistory,
    "purchase-order-communications": purchase_models.PurchaseOrderCommunication,
    "product-budget-movements": purchase_models.ProductBudgetMovement,
    "purchase-accounting-documents": purchase_models.PurchaseAccountingDocument,
    "purchase-accounting-lines": purchase_models.PurchaseAccountingLine,
    "purchase-order-billing-plans": purchase_models.PurchaseOrderInvoiceAccounting,
    "purchase-stock-demands": purchase_models.PurchaseStockDemand,
    "purchase-inventory-transfers": purchase_models.PurchaseInventoryTransfer,
    "purchase-inventory-transfer-lines": purchase_models.PurchaseInventoryTransferLine,
    "purchase-peppol-events": purchase_models.PurchasePeppolEvent,
    "purchase-contract-terms": purchase_models.PurchaseContractTerm,
    "purchase-order-revisions": purchase_models.PurchaseOrderRevision,
}


def _title(entity):
    return entity.replace("-", " ").title()


def _build_config(entity, model):
    fields = [
        field
        for field in model._meta.concrete_fields
        if field.editable and not field.auto_created and field.name not in {"id", "created_at", "updated_at"}
    ]
    form_fields = fields + [
        field
        for field in model._meta.many_to_many
        if field.editable and not field.auto_created
    ]
    display_fields = [field for field in fields if not isinstance(field, models.TextField)][:5]
    status_field = next((field for field in fields if field.name == "status"), None)
    if status_field and status_field not in display_fields:
        display_fields.append(status_field)
    config = {
        "model": model,
        "title": _title(entity),
        "singular": str(model._meta.verbose_name).title(),
        "columns": [(field.name, str(field.verbose_name).title()) for field in display_fields],
        "search": [
            field.name
            for field in fields
            if isinstance(field, (models.CharField, models.TextField, models.EmailField, models.URLField))
        ],
        "form_fields": [field.name for field in form_fields],
        "select_related": [field.name for field in fields if field.many_to_one or field.one_to_one],
    }
    if entity == "purchase-requests":
        config["workflow"] = REQUEST_WORKFLOW
        config["workflow_locked_statuses"] = {"rejected", "closed"}
    elif entity == "purchase-tenders":
        config["workflow"] = TENDER_WORKFLOW
        config["workflow_locked_statuses"] = {"closed", "cancelled"}
    elif entity == "direct-purchases":
        config["workflow"] = {
            key: {**definition, "service_controls_state": True}
            for key, definition in APPROVAL_WORKFLOW.items()
        }
    elif entity in WORKFLOW_OVERRIDES:
        config["workflow"] = WORKFLOW_OVERRIDES[entity]
    else:
        status_values = {value for value, _label in status_field.choices} if status_field and status_field.choices else set()
        if {"draft", "submitted", "approved", "rejected", "cancelled"}.issubset(status_values):
            config["workflow"] = APPROVAL_WORKFLOW
    if config.get("workflow"):
        config["form_fields"] = [
            field
            for field in config["form_fields"]
            if field not in {"status", "review_status", "approver", "approved_by"}
        ]
    if entity == "vendor-profiles":
        config["workflow_locked_statuses"] = {"confirmed", "blacklisted"}
        config["columns"] = [
            ("registration_number", "Vendor Code"),
            ("partner", "Vendor"),
            ("category", "Category"),
            ("purchase_credit_limit", "Purchase Limit"),
            ("active", "Active"),
            ("status", "Status"),
        ]
    if entity == "purchase-legal-documents":
        config["form"] = PurchaseLegalDocumentForm
        config["columns"] = [
            ("name", "Document Name"),
            ("document_usage", "Usage"),
            ("requirement_type", "Requirement Type"),
            ("is_blocking", "Blocking"),
            ("active", "Active"),
        ]
    if entity == "purchase-legal-document-attachments":
        config["columns"] = [
            ("legal_document", "Legal Document"),
            ("file_name", "File Name"),
            ("size_bytes", "Size (Bytes)"),
        ]
        config["form_fields"] = ["legal_document", "file"]
    if entity == "vendor-legal-documents":
        config["workflow_field"] = "review_status"
        config["workflow_locked_statuses"] = {"approved", "superseded"}
        config["columns"] = [
            ("vendor", "Vendor"),
            ("document_type", "Document Type"),
            ("expiry_date", "Expiry Date"),
            ("status", "Expiry Status"),
            ("review_status", "Review Status"),
        ]
        config["form_fields"] = [
            "vendor",
            "legal_document",
            "document_number",
            "issue_date",
            "expiry_date",
            "attachment_url",
            "renewal_source",
        ]
    if entity == "tender-documents":
        config["columns"] = [
            ("tender", "Tender"),
            ("document_type", "Document Type"),
            ("title", "Title"),
            ("mandatory", "Mandatory"),
            ("status", "Status"),
        ]
        config["form_fields"] = [
            "tender",
            "legal_document",
            "bid",
            "vendor",
            "document_type",
            "title",
            "attachment_url",
            "mandatory",
            "status",
            "review_note",
        ]
    if entity == "tender-document-completion-matrices":
        config.update(
            {
                "creatable": False,
                "editable": False,
                "deletable": False,
                "readonly_detail": True,
            }
        )
        config["columns"] = [
            ("tender", "Tender"),
            ("name", "Matrix"),
        ]
    if entity == "tender-document-completion-lines":
        config.update(
            {
                "creatable": False,
                "editable": False,
                "deletable": False,
                "readonly_detail": True,
            }
        )
        config["columns"] = [
            ("tender", "Tender"),
            ("vendor_name", "Vendor"),
            ("vendor_subtitle", "Vendor Info"),
            ("completion_summary", "Completion"),
        ]
    if entity == "vendor-prequalifications":
        config["workflow_locked_statuses"] = {"approved"}
        config["columns"] = [
            ("number", "Sequence"),
            ("tender", "Tender"),
            ("vendor", "Vendor"),
            ("document_fulfillment_percentage", "Document Fulfillment"),
            ("status", "Compliance Status"),
        ]
    if entity == "vendor-document-reviews":
        config["workflow_locked_statuses"] = {"approved"}
        config["columns"] = [
            ("prequalification", "Prequalification"),
            ("sequence", "Sequence"),
            ("document_type", "Document Type"),
            ("requirement_type", "Requirement Type"),
            ("status", "Status"),
        ]
    if entity == "vendor-pricelist-imports":
        config["workflow_locked_statuses"] = {"processing", "completed", "partial"}
        config["columns"] = [
            ("company", "Company"),
            ("source_file", "CSV File"),
            ("total_rows", "Rows"),
            ("error_rows", "Errors"),
            ("status", "Status"),
        ]
    if entity == "vendor-pricelist-import-previews":
        config.update(
            {
                "creatable": False,
                "editable": False,
                "deletable": False,
                "readonly_detail": True,
            }
        )
        config["columns"] = [
            ("import_log", "Import"),
            ("validated_at", "Validated At"),
        ]
    if entity == "vendor-pricelists":
        config["form_fields"] = [
            field
            for field in config["form_fields"]
            if field not in {"product", "minimum_quantity", "unit_price", "lead_time_days", "active"}
        ]
        config["columns"] = [
            ("code", "Pricelist Code"),
            ("name", "Pricelist Name"),
            ("vendor", "Vendor"),
            ("date_start", "Effective Date"),
            ("date_end", "Expiry Date"),
            ("status", "Status"),
        ]
    if entity == "vendor-pricelist-amendments":
        config["form_fields"] = [
            field
            for field in config["form_fields"]
            if field not in {"previous_price", "new_price"}
        ]
    if entity == "vendor-performance-templates":
        config["form_fields"] = [
            field
            for field in config["form_fields"]
            if field not in {"quality_weight", "delivery_weight", "service_weight"}
        ]
    if entity == "vendor-performance-evaluations":
        config["form_fields"] = [
            field
            for field in config["form_fields"]
            if field
            not in {
                "quality_score",
                "delivery_score",
                "service_score",
                "overall_score",
                "rating",
            }
        ]
    if entity == "vendor-performance-evaluation-lines":
        config.update({"creatable": False, "deletable": False})
        config["form_fields"] = ["assessment", "officer_comment", "score"]
    if entity == "service-work-orders":
        config.update({"creatable": False, "deletable": False})
        config["form_fields"] = []
    if entity == "service-work-order-lines":
        config.update(
            {
                "creatable": False,
                "editable": False,
                "deletable": False,
                "readonly_detail": True,
            }
        )
    if entity == "purchase-teams":
        config["columns"] = [
            ("code", "Team Code"),
            ("name", "Team Name"),
            ("manager", "Team Lead"),
            ("parameter", "Routing Parameter"),
            ("status", "Status"),
        ]
        config["form_fields"] = [
            "company",
            "name",
            "manager",
            "description",
            "parameter",
            "product_categories",
            "order_types",
            "vendor_categories",
            "active",
        ]
    if entity == "purchase-team-members":
        config["columns"] = [
            ("team", "Purchase Team"),
            ("user", "Member"),
            ("role", "Role"),
            ("is_primary", "Primary Buyer"),
            ("active", "Active"),
        ]
        config["form_fields"] = ["team", "user", "role", "categories", "is_primary", "active"]
    if entity == "purchase-kpi-templates":
        config["columns"] = [
            ("code", "Code"),
            ("name", "Template Name"),
            ("company", "Company"),
            ("version", "Version"),
            ("active", "Active"),
        ]
        config["form_fields"] = ["company", "name", "description", "active"]
    if entity == "purchase-kpi-template-lines":
        config["columns"] = [
            ("template", "KPI Template"),
            ("sequence", "Sequence"),
            ("metric", "KPI"),
            ("weight", "Weight (%)"),
            ("default_target", "Default Target"),
        ]
        config["form_fields"] = [
            "template",
            "sequence",
            "metric",
            "weight",
            "default_target",
            "unit",
            "direction",
            "calculation",
        ]
    if entity == "purchase-kpi-targets":
        config["workflow_locked_statuses"] = {"waiting_approval", "active", "rejected", "closed"}
        config["columns"] = [
            ("name", "Name"),
            ("period_start", "Period Start"),
            ("period_end", "Period End"),
            ("target_scope", "Scope"),
            ("status", "Status"),
        ]
        config["form_fields"] = [
            "company",
            "name",
            "target_scope",
            "team",
            "buyer",
            "template",
            "period_type",
            "period_start",
            "period_end",
            "notes",
            "active",
        ]
    if entity == "purchase-kpi-target-lines":
        config.update({"creatable": False, "deletable": False})
        config["columns"] = [
            ("target", "KPI Target"),
            ("sequence", "Sequence"),
            ("metric", "KPI"),
            ("target_value", "Target"),
            ("actual_value", "Actual"),
        ]
        config["form_fields"] = ["target_value"]
    if entity == "purchase-order-types":
        config["columns"] = [
            ("name", "Order Type"),
            ("prefix_sequence", "Prefix"),
            ("company", "Owner Company"),
            ("requires_approval", "Requires Approval"),
            ("active", "Active"),
        ]
        config["form_fields"] = [
            "company",
            "companies",
            "name",
            "icon",
            "description",
            "product_categories",
            "requires_approval",
            "allow_direct_purchase",
            "active",
        ]
    if entity == "purchase-order-type-sequences":
        config.update(
            {
                "creatable": False,
                "editable": False,
                "deletable": False,
                "readonly_detail": True,
            }
        )
        config["columns"] = [
            ("order_type", "Order Type"),
            ("company", "Company"),
            ("period", "Period"),
            ("purchase_request_next", "Next PR"),
            ("purchase_order_next", "Next PO"),
        ]
    if entity == "purchase-order-templates":
        config["columns"] = [
            ("name", "Template Name"),
            ("company", "Company"),
            ("vendor", "Vendor"),
            ("order_type", "Order Type"),
            ("active", "Active"),
        ]
        config["form_fields"] = [
            "company",
            "name",
            "vendor",
            "order_type",
            "payment_terms",
            "delivery_terms",
            "legal_terms",
            "active",
        ]
    if entity == "purchase-order-template-lines":
        config["columns"] = [
            ("order_template", "Order Template"),
            ("sequence", "Sequence"),
            ("product", "Product"),
            ("quantity", "Quantity"),
            ("price", "Price"),
        ]
        config["form_fields"] = [
            "order_template",
            "sequence",
            "product",
            "description",
            "quantity",
            "uom",
            "price",
        ]
    if entity == "purchase-milestone-templates":
        config["columns"] = [
            ("name", "Template Name"),
            ("company", "Company"),
        ]
        config["form_fields"] = ["company", "name"]
    if entity == "purchase-milestone-template-lines":
        config["columns"] = [
            ("template", "Milestone Template"),
            ("sequence", "Sequence"),
            ("milestone_name", "Milestone Name"),
            ("contract_term_percentage", "Contract Term (%)"),
            ("due_days", "Due Days"),
        ]
        config["form_fields"] = [
            "template",
            "sequence",
            "milestone_name",
            "contract_term_percentage",
            "due_days",
        ]
    if entity in {
        "purchase-order-lines",
        "purchase-three-way-matches",
        "tender-audit-logs",
        "vendor-transaction-history",
        "vendor-performance-score-history",
        "purchase-peppol-events",
        "purchase-order-revisions",
    }:
        config.update(
            {
                "creatable": False,
                "editable": False,
                "deletable": False,
                "readonly_detail": True,
            }
        )
    return config


PURCHASE_ENTITY_CONFIG = {entity: _build_config(entity, model) for entity, model in ENTITY_MODELS.items()}
ENTITY_CONFIG = PURCHASE_ENTITY_CONFIG

if "purchase-accounting-documents" in PURCHASE_ENTITY_CONFIG:
    approval = PURCHASE_ENTITY_CONFIG["purchase-accounting-documents"].get("workflow", {}).get("approve")
    if approval:
        approval["service_controls_state"] = True
