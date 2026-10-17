"""Generic CRUD registry for the Purchase workspace."""

from django.db import models

from . import models as purchase_models


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
    "create-rfq": {"label": "Create RFQ", "from": ["approved", "partial_rfq"], "to": "rfq", "primary": True},
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
    "publish": {"label": "Publish", "from": ["draft"], "to": "published", "primary": True},
    "open-submission": {"label": "Open Submission", "from": ["published"], "to": "submission", "primary": True},
    "evaluate": {"label": "Start Evaluation", "from": ["submission"], "to": "evaluation", "primary": True},
    "award": {"label": "Award", "from": ["evaluation"], "to": "awarded", "primary": True},
    "close": {"label": "Close", "from": ["awarded"], "to": "closed", "primary": True},
    "cancel": {"label": "Cancel", "from": ["draft", "published", "submission", "evaluation"], "to": "cancelled"},
}

WORKFLOW_OVERRIDES = {
    "purchase-quotations": {
        "send": {"label": "Send to Vendor", "from": ["draft"], "to": "sent", "primary": True},
        "receive": {"label": "Record Response", "from": ["sent"], "to": "received", "primary": True},
        "select": {"label": "Select Quote", "from": ["received"], "to": "selected", "primary": True},
        "cancel": {"label": "Cancel", "from": ["draft", "sent", "received"], "to": "cancelled"},
    },
    "tender-bids": {
        "submit": {"label": "Submit", "from": ["draft"], "to": "submitted", "primary": True},
        "shortlist": {"label": "Shortlist", "from": ["submitted"], "to": "shortlisted", "primary": True},
        "award": {"label": "Award", "from": ["shortlisted"], "to": "awarded", "primary": True},
        "reject": {"label": "Reject", "from": ["submitted", "shortlisted"], "to": "rejected"},
    },
    "blanket-orders": {
        "activate": {"label": "Activate", "from": ["draft"], "to": "active", "primary": True},
        "exhaust": {"label": "Mark Exhausted", "from": ["active"], "to": "exhausted"},
        "expire": {"label": "Mark Expired", "from": ["active"], "to": "expired"},
        "cancel": {"label": "Cancel", "from": ["draft", "active"], "to": "cancelled"},
    },
    "vendor-profiles": {
        "activate": {"label": "Activate", "from": ["draft", "suspended"], "to": "active", "primary": True},
        "suspend": {"label": "Suspend", "from": ["active"], "to": "suspended"},
        "blacklist": {"label": "Blacklist", "from": ["draft", "active", "suspended"], "to": "blacklisted"},
    },
    "vendor-legal-documents": {
        "validate": {"label": "Validate", "from": ["draft"], "to": "valid", "primary": True},
        "expire": {"label": "Mark Expired", "from": ["valid"], "to": "expired"},
        "reject": {"label": "Reject", "from": ["draft"], "to": "rejected"},
    },
    "vendor-prequalifications": {
        "submit": {"label": "Submit", "from": ["draft"], "to": "pending", "primary": True},
        "review": {"label": "Start Review", "from": ["pending"], "to": "under_review", "primary": True},
        "approve": {"label": "Approve", "from": ["under_review"], "to": "approved", "primary": True},
        "reject": {"label": "Reject", "from": ["under_review"], "to": "rejected"},
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
    "vendor-profiles": purchase_models.VendorProfile,
    "vendor-legal-documents": purchase_models.VendorLegalDocument,
    "vendor-prequalifications": purchase_models.VendorPrequalification,
    "vendor-document-reviews": purchase_models.VendorDocumentReview,
    "vendor-pricelists": purchase_models.VendorPricelist,
    "vendor-pricelist-amendments": purchase_models.VendorPricelistAmendment,
    "vendor-performance-templates": purchase_models.VendorPerformanceTemplate,
    "vendor-performance-evaluations": purchase_models.VendorPerformanceEvaluation,
    "purchase-teams": purchase_models.PurchaseTeam,
    "purchase-team-members": purchase_models.PurchaseTeamMember,
    "purchase-kpi-templates": purchase_models.PurchaseKPITemplate,
    "purchase-kpi-targets": purchase_models.PurchaseKPITarget,
    "purchase-order-types": purchase_models.PurchaseOrderType,
    "purchase-order-templates": purchase_models.PurchaseOrderTemplate,
    "purchase-milestone-templates": purchase_models.PurchaseMilestoneTemplate,
    "purchase-settings": purchase_models.PurchaseSetting,
    "purchase-order-controls": purchase_models.PurchaseOrderControl,
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
    "tender-invitations": purchase_models.TenderInvitation,
    "tender-documents": purchase_models.TenderDocument,
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
        "form_fields": [field.name for field in fields],
        "select_related": [field.name for field in fields if field.many_to_one or field.one_to_one],
    }
    if entity == "purchase-requests":
        config["workflow"] = REQUEST_WORKFLOW
        config["workflow_locked_statuses"] = {"rejected", "closed"}
    elif entity == "purchase-tenders":
        config["workflow"] = TENDER_WORKFLOW
        config["workflow_locked_statuses"] = {"closed", "cancelled"}
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
            if field not in {"status", "approver", "approved_by"}
        ]
    if entity in {
        "tender-audit-logs",
        "vendor-transaction-history",
        "purchase-peppol-events",
        "purchase-order-revisions",
    }:
        config.update({"creatable": False, "editable": False, "deletable": False})
    return config


PURCHASE_ENTITY_CONFIG = {entity: _build_config(entity, model) for entity, model in ENTITY_MODELS.items()}

if "purchase-accounting-documents" in PURCHASE_ENTITY_CONFIG:
    approval = PURCHASE_ENTITY_CONFIG["purchase-accounting-documents"].get("workflow", {}).get("approve")
    if approval:
        approval["service_controls_state"] = True
