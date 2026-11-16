"""Purchase-owned extensions to the generic document workflow registry."""

from django.urls import reverse
from django.utils import timezone

from core.document_registry import (
    register_document_action,
    register_document_header_field,
    register_document_line_field,
    register_document_option,
    register_document_tab,
)
from core.models import PurchaseOrder

from .forms import (
    BlanketOrderDocumentForm,
    BlanketOrderLineForm,
    PurchaseOrderDocumentForm,
    PurchaseOrderLineDocumentForm,
    PurchaseQuotationDocumentForm,
    PurchaseRequestDocumentForm,
    PurchaseRequestLineDocumentForm,
    PurchaseTenderDocumentForm,
    PurchaseTenderLineDocumentForm,
    save_purchase_document_attachments_after_lines,
)
from .models import (
    BlanketOrder,
    PurchaseRequest,
    PurchaseSetting,
    PurchaseTender,
    TenderAuditLog,
)
from .services import (
    apply_tender_award_strategy_action,
    auto_route_purchase_order_action,
    auto_route_purchase_request_action,
    purchase_order_document_tab_context,
    purchase_quotation_document_tab_context,
    purchase_request_document_tab_context,
    purchase_tender_document_context,
    update_blanket_order_prices_action,
)


def purchase_revision_action_available(order):
    enabled = (
        PurchaseSetting.objects.filter(company_id=order.company_id)
        .values_list("allow_order_revision", flat=True)
        .first()
    )
    if not enabled:
        return False
    return not order.lines.filter(received_quantity__gt=0).exists()


def purchase_tender_action_available(tender, action_key):
    if action_key == "submit-quotation":
        today = timezone.localdate()
        start_date = tender.bid_start_date or tender.publish_date
        return (
            tender.status == PurchaseTender.Status.BID_SUBMISSION
            and (not start_date or today >= start_date)
            and (not tender.close_date or today <= tender.close_date)
        )
    if tender.status != PurchaseTender.Status.AWARDED:
        return True
    actions_by_decision_status = {
        PurchaseTender.DecisionStatus.DRAFT: {
            "apply-award-strategy",
            "bid-comparison",
            "manage-award-allocation",
            "submit-decision",
            "reopen-award",
        },
        PurchaseTender.DecisionStatus.WAITING_APPROVAL: {
            "approve-decision",
            "bid-comparison",
            "reject-decision",
        },
        PurchaseTender.DecisionStatus.APPROVED: {
            "bid-comparison",
            "close",
            "reset-decision",
        },
        PurchaseTender.DecisionStatus.REJECTED: {
            "apply-award-strategy",
            "bid-comparison",
            "manage-award-allocation",
            "submit-decision",
            "reopen-award",
        },
    }
    return action_key in actions_by_decision_status.get(
        tender.decision_approval_status,
        set(),
    )


def purchase_tender_action_audit(tender, action, actor, previous_status):
    details = dict(getattr(tender, "_tender_audit_details", {}))
    details.update(
        {
            "award_cycle": tender.award_cycle,
            "decision_approval_status": tender.decision_approval_status,
        }
    )
    TenderAuditLog.objects.create(
        tender=tender,
        action=action,
        previous_status=previous_status or "",
        new_status=tender.status,
        actor=actor,
        details=details,
    )


def purchase_tender_completion_tab_visible(tender, actor):
    del actor
    return bool(tender and tender.method == PurchaseTender.Method.PQTE)


def purchase_tender_scoring_tab_visible(tender, actor):
    del actor
    return bool(
        tender
        and tender.status
        in {
            PurchaseTender.Status.BID_SELECTION,
            PurchaseTender.Status.AWARDED,
            PurchaseTender.Status.CLOSED,
        }
    )


def register_purchase_document_extensions():
    register_document_option(
        "purchases",
        "header_form",
        PurchaseOrderDocumentForm,
    )
    register_document_option(
        "purchases",
        "context_builder",
        purchase_order_document_tab_context,
    )
    register_document_option(
        "purchases",
        "always_editable_fields",
        ["attachment_files", "remove_attachments"],
    )
    register_document_option(
        "purchases",
        "after_save",
        save_purchase_document_attachments_after_lines,
    )
    register_document_option(
        "purchase-quotations",
        "header_form",
        PurchaseQuotationDocumentForm,
    )
    register_document_option(
        "purchase-quotations",
        "context_builder",
        purchase_quotation_document_tab_context,
    )
    register_document_action(
        "purchase-quotations",
        "send",
        {
            "label": "Send to Vendor",
            "from": ["draft"],
            "to": "sent",
            "primary": True,
            "service_controls_state": True,
            "href_builder": lambda quotation: reverse(
                "purchase_quotation_email",
                kwargs={"pk": quotation.pk},
            ),
        },
        override=True,
    )
    register_document_option(
        "purchase-requests",
        "header_form",
        PurchaseRequestDocumentForm,
    )
    register_document_option(
        "purchase-requests",
        "context_builder",
        purchase_request_document_tab_context,
    )
    register_document_option(
        "purchase-requests",
        "always_editable_fields",
        ["attachment_files", "remove_attachments"],
    )
    register_document_option(
        "purchase-requests",
        "after_save",
        save_purchase_document_attachments_after_lines,
    )
    register_document_option(
        "purchase-requests",
        "line_form",
        PurchaseRequestLineDocumentForm,
    )
    register_document_option(
        "purchase-tenders",
        "header_form",
        PurchaseTenderDocumentForm,
    )
    register_document_option(
        "purchase-tenders",
        "line_form",
        PurchaseTenderLineDocumentForm,
    )
    register_document_option(
        "purchase-tenders",
        "extra_js_assets",
        ["purchase/js/tender_onchange.js"],
    )
    register_document_option(
        "purchase-tenders",
        "context_builder",
        purchase_tender_document_context,
    )
    register_document_option(
        "purchase-tenders",
        "available_action_filter",
        purchase_tender_action_available,
    )
    register_document_option(
        "purchase-tenders",
        "action_audit_logger",
        purchase_tender_action_audit,
    )
    register_document_action(
        "purchase-tenders",
        "submit-quotation",
        {
            "label": "Submit Quotation",
            "from": [PurchaseTender.Status.BID_SUBMISSION],
            "to": None,
            "preserve_status": True,
            "primary": True,
            "href_builder": lambda tender: reverse(
                "purchase_tender_submit_quotation",
                kwargs={"pk": tender.pk},
            ),
        },
    )
    register_document_action(
        "purchase-tenders",
        "bid-comparison",
        {
            "label": "Bid Comparison",
            "from": [
                PurchaseTender.Status.BID_SELECTION,
                PurchaseTender.Status.AWARDED,
                PurchaseTender.Status.CLOSED,
            ],
            "to": None,
            "preserve_status": True,
            "href_builder": lambda tender: reverse(
                "purchase_tender_bid_comparison",
                kwargs={"pk": tender.pk},
            ),
        },
    )
    register_document_action(
        "purchase-tenders",
        "manage-award-allocation",
        {
            "label": "Bid Allocation Matrix",
            "from": [PurchaseTender.Status.AWARDED],
            "to": None,
            "preserve_status": True,
            "href_builder": lambda tender: reverse(
                "purchase_tender_award_allocation",
                kwargs={"pk": tender.pk},
            ),
        },
    )
    register_document_option(
        "blanket-orders",
        "header_form",
        BlanketOrderDocumentForm,
    )
    register_document_option(
        "blanket-orders",
        "line_form",
        BlanketOrderLineForm,
    )
    register_document_option(
        "purchase-requests",
        "line_select_related",
        ["product", "destination_warehouse"],
        override=True,
    )
    register_document_option(
        "purchases",
        "line_form",
        PurchaseOrderLineDocumentForm,
    )
    register_document_option(
        "purchases",
        "line_select_related",
        [
            "order",
            "order__purchase_control",
            "product",
            "warehouse",
            "purchase_pricing_provenance",
        ],
        override=True,
    )
    register_document_line_field("purchases", "cost_center", after="unit_price")
    register_document_line_field("purchases", "cost_center_mode", after="unit_price")
    register_document_line_field("purchases", "cost_center_distribution_ids", after="unit_price")
    register_document_line_field("purchases", "cost_center_distribution_data", after="unit_price")
    register_document_line_field("purchases", "cost_center_analytic_tag_ids", after="unit_price")
    register_document_line_field("purchases", "expected_date", after="cost_center")
    register_document_line_field("purchases", "expiry_date", after="expected_date")
    register_document_line_field("purchases", "rent_duration", after="expiry_date")
    register_document_line_field("purchases", "rental_period", after="rent_duration")
    register_document_header_field("purchase-requests", "team", after="order_type")
    register_document_header_field("purchase-requests", "remark_template", after="notes")
    register_document_line_field("purchase-requests", "expected_date", after="quantity")
    register_document_line_field("purchase-requests", "cost_center", after="quantity")
    register_document_line_field("purchase-requests", "cost_center_mode", after="quantity")
    register_document_line_field("purchase-requests", "cost_center_distribution_ids", after="quantity")
    register_document_line_field("purchase-requests", "cost_center_distribution_data", after="quantity")
    register_document_line_field("purchase-requests", "cost_center_analytic_tag_ids", after="quantity")
    register_document_line_field(
        "purchase-requests",
        "destination_warehouse",
        after="quantity",
    )
    register_document_line_field(
        "purchase-requests",
        "expiry_date",
        after="quantity",
    )
    register_document_line_field(
        "blanket-orders",
        "tax_percent",
        after="unit_price",
    )
    register_document_line_field("blanket-orders", "cost_center", after="unit_price")
    register_document_line_field("blanket-orders", "cost_center_mode", after="unit_price")
    register_document_line_field("blanket-orders", "cost_center_distribution_ids", after="unit_price")
    register_document_line_field("blanket-orders", "cost_center_distribution_data", after="unit_price")
    register_document_line_field("blanket-orders", "cost_center_analytic_tag_ids", after="unit_price")
    register_document_line_field("purchase-tenders", "cost_center", after="target_unit_price")
    register_document_line_field("purchase-tenders", "cost_center_mode", after="target_unit_price")
    register_document_line_field("purchase-tenders", "cost_center_distribution_ids", after="target_unit_price")
    register_document_line_field("purchase-tenders", "cost_center_distribution_data", after="target_unit_price")
    register_document_line_field("purchase-tenders", "cost_center_analytic_tag_ids", after="target_unit_price")
    register_document_header_field("purchase-tenders", "expiry_date", after="close_date")
    register_document_line_field("purchase-tenders", "expiry_date", after="delivery_date")
    register_document_line_field(
        "purchase-tenders",
        "destination_warehouse",
        after="delivery_date",
    )
    register_document_option(
        "blanket-orders",
        "total_fields",
        [
            ("Untaxed", "untaxed_amount"),
            ("Tax", "tax_amount"),
            ("Total", "total_amount"),
            ("Consumed", "consumed_value"),
        ],
        override=True,
    )
    register_document_option(
        "purchase-requests",
        "summary_template",
        "purchase/purchase_routing_summary.html",
    )
    register_document_option(
        "purchases",
        "summary_template",
        "purchase/purchase_routing_summary.html",
    )
    register_document_tab(
        "purchase-requests",
        "order-lines",
        {
            "label": "Order Lines",
            "template": "core/document_tabs/lines.html",
            "line_relation": "lines",
        },
    )
    register_document_tab(
        "purchase-requests",
        "attachment-file",
        {
            "label": "Attachment File",
            "template": "purchase/document_tabs/attachments.html",
            "field_names": ["attachment_files", "remove_attachments"],
        },
        after="order-lines",
    )
    register_document_tab(
        "purchase-requests",
        "purchase-team",
        {
            "label": "Purchase Teams",
            "template": "purchase/document_tabs/team.html",
            "field_names": ["team"],
            "captures": ["summary"],
        },
        after="attachment-file",
    )
    register_document_tab(
        "purchase-requests",
        "approval-history",
        {
            "label": "Approval History",
            "template": "core/document_tabs/approval.html",
            "requires_object": True,
            "captures": ["approval"],
        },
        after="purchase-team",
    )
    register_document_tab(
        "purchase-quotations",
        "order-lines",
        {
            "label": "Order Lines",
            "template": "core/document_tabs/lines.html",
            "line_relation": "lines",
        },
    )
    register_document_tab(
        "purchase-quotations",
        "purchase-team",
        {
            "label": "Purchase Teams",
            "template": "purchase/document_tabs/team.html",
            "field_names": ["team", "assigned_to"],
        },
        after="order-lines",
    )
    register_document_tab(
        "purchase-quotations",
        "communication-history",
        {
            "label": "Communication History",
            "template": "purchase/document_tabs/communications.html",
            "requires_object": True,
        },
        after="purchase-team",
    )
    register_document_tab(
        "purchase-quotations",
        "other-information",
        {
            "label": "Other Information",
            "template": "core/document_tabs/metadata.html",
            "requires_object": True,
        },
        after="communication-history",
    )
    register_document_tab(
        "purchases",
        "order-lines",
        {
            "label": "Order Lines",
            "template": "core/document_tabs/lines.html",
            "line_relation": "lines",
        },
    )
    register_document_tab(
        "purchases",
        "milestone-contract-terms",
        {
            "label": "Milestone and Contract Terms",
            "template": "purchase/document_tabs/milestones.html",
            "field_names": ["milestone_template"],
        },
        after="order-lines",
    )
    register_document_tab(
        "purchases",
        "service-work-order",
        {
            "label": "Service Work Order",
            "template": "purchase/document_tabs/service_work_orders.html",
            "requires_object": True,
        },
        after="milestone-contract-terms",
    )
    register_document_tab(
        "purchases",
        "billing-policy",
        {
            "label": "Billing Policy",
            "template": "purchase/document_tabs/billing_policy.html",
            "field_names": [
                "invoicing_policy",
                "downpayment_method",
                "downpayment_type",
                "downpayment_amount",
                "downpayment_percentage",
            ],
        },
        after="service-work-order",
    )
    register_document_tab(
        "purchases",
        "purchase-team",
        {
            "label": "Purchase Teams",
            "template": "purchase/document_tabs/team.html",
            "field_names": ["purchase_team", "assigned_to"],
            "captures": ["summary"],
        },
        after="billing-policy",
    )
    register_document_tab(
        "purchases",
        "communication-history",
        {
            "label": "Communication History",
            "template": "purchase/document_tabs/communications.html",
            "requires_object": True,
        },
        after="purchase-team",
    )
    register_document_tab(
        "purchases",
        "attachment-file",
        {
            "label": "Attachment File",
            "template": "purchase/document_tabs/attachments.html",
            "field_names": ["attachment_files", "remove_attachments"],
        },
        after="communication-history",
    )
    register_document_tab(
        "purchases",
        "other-information",
        {
            "label": "Other Information",
            "template": "core/document_tabs/metadata.html",
            "requires_object": True,
        },
        after="attachment-file",
    )
    register_document_tab(
        "purchases",
        "approval-history",
        {
            "label": "Approval History",
            "template": "core/document_tabs/approval.html",
            "requires_object": True,
            "captures": ["approval"],
        },
        after="other-information",
    )
    register_document_tab(
        "purchase-tenders",
        "order-lines",
        {
            "label": "Order Lines",
            "template": "core/document_tabs/lines.html",
            "line_relation": "lines",
        },
    )
    register_document_tab(
        "purchase-tenders",
        "participating-vendors",
        {
            "label": "Participating Vendors",
            "template": "purchase/document_tabs/tender_participants.html",
            "requires_object": True,
        },
        after="order-lines",
    )
    register_document_tab(
        "purchase-tenders",
        "document-completion",
        {
            "label": "Document Completion",
            "template": "purchase/document_tabs/tender_completion.html",
            "requires_object": True,
            "is_visible": purchase_tender_completion_tab_visible,
        },
        after="participating-vendors",
    )
    register_document_tab(
        "purchase-tenders",
        "vendor-scoring",
        {
            "label": "Vendor Scoring",
            "template": "purchase/document_tabs/tender_scoring.html",
            "requires_object": True,
            "is_visible": purchase_tender_scoring_tab_visible,
        },
        after="document-completion",
    )
    register_document_tab(
        "purchase-tenders",
        "purchase-team",
        {
            "label": "Purchase Teams",
            "template": "purchase/document_tabs/team.html",
            "field_names": ["team", "assigned_to"],
        },
        after="vendor-scoring",
    )
    register_document_tab(
        "purchase-tenders",
        "approval-history",
        {
            "label": "Approval History",
            "template": "core/document_tabs/approval.html",
            "requires_object": True,
            "captures": ["approval"],
        },
        after="purchase-team",
    )
    register_document_tab(
        "purchase-tenders",
        "other-information",
        {
            "label": "Other Information",
            "template": "core/document_tabs/metadata.html",
            "requires_object": True,
        },
        after="approval-history",
    )
    register_document_tab(
        "blanket-orders",
        "order-lines",
        {
            "label": "Order Lines",
            "template": "core/document_tabs/lines.html",
            "line_relation": "lines",
        },
    )
    register_document_tab(
        "blanket-orders",
        "agreement-policy",
        {
            "label": "Agreement Policy",
            "template": "purchase/document_tabs/agreement_policy.html",
            "field_names": ["agreement_policy"],
        },
        after="order-lines",
    )
    register_document_tab(
        "blanket-orders",
        "approval-history",
        {
            "label": "Approval History",
            "template": "core/document_tabs/approval.html",
            "requires_object": True,
            "captures": ["approval"],
        },
        after="agreement-policy",
    )
    register_document_tab(
        "blanket-orders",
        "other-information",
        {
            "label": "Other Information",
            "template": "core/document_tabs/metadata.html",
            "requires_object": True,
        },
        after="approval-history",
    )
    register_document_option(
        "purchases",
        "editable_statuses",
        [PurchaseOrder.Status.DRAFT],
        override=True,
    )
    register_document_action(
        "purchase-requests",
        "auto-route",
        {
            "label": "Auto Route to Purchase Team",
            "from": [value for value, _label in PurchaseRequest.Status.choices],
            "to": None,
            "preserve_status": True,
            "handler": auto_route_purchase_request_action,
        },
    )
    register_document_action(
        "purchases",
        "auto-route",
        {
            "label": "Auto Route to Purchase Team",
            "from": [value for value, _label in PurchaseOrder.Status.choices],
            "to": None,
            "preserve_status": True,
            "handler": auto_route_purchase_order_action,
        },
    )
    register_document_action(
        "blanket-orders",
        "update-prices",
        {
            "label": "Update Prices",
            "from": [BlanketOrder.Status.DRAFT],
            "to": None,
            "preserve_status": True,
            "handler": update_blanket_order_prices_action,
        },
    )
    register_document_action(
        "purchase-tenders",
        "apply-award-strategy",
        {
            "label": "Apply Award Strategy",
            "from": [PurchaseTender.Status.AWARDED],
            "to": None,
            "preserve_status": True,
            "handler": apply_tender_award_strategy_action,
        },
    )
    register_document_action(
        "purchases",
        "request-approval",
        {
            "label": "Submit for Approval",
            "from": [PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.SENT],
            "to": PurchaseOrder.Status.WAITING_APPROVAL,
            "primary": True,
            "approval_action": "submit",
        },
        override=True,
    )
    for entity, action, definition in (
        (
            "purchase-requests",
            "submit",
            {
                "label": "Submit for Approval",
                "from": [PurchaseRequest.Status.DRAFT],
                "to": PurchaseRequest.Status.PENDING,
                "primary": True,
                "approval_action": "submit",
                "service_controls_state": True,
            },
        ),
        (
            "purchase-requests",
            "approve",
            {
                "label": "Approve",
                "from": [PurchaseRequest.Status.PENDING, PurchaseRequest.Status.PARTIALLY_APPROVED],
                "to": PurchaseRequest.Status.APPROVED,
                "primary": True,
                "approval_action": "approve",
                "service_controls_state": True,
            },
        ),
        (
            "purchase-requests",
            "reject",
            {
                "label": "Reject",
                "from": [PurchaseRequest.Status.PENDING, PurchaseRequest.Status.PARTIALLY_APPROVED],
                "to": PurchaseRequest.Status.REJECTED,
                "reason_label": "Rejection reason",
                "approval_action": "reject",
                "service_controls_state": True,
            },
        ),
        (
            "blanket-orders",
            "submit",
            {
                "label": "Submit for Approval",
                "from": [BlanketOrder.Status.DRAFT],
                "to": BlanketOrder.Status.WAITING_APPROVAL,
                "primary": True,
                "approval_action": "submit",
                "service_controls_state": True,
            },
        ),
        (
            "blanket-orders",
            "approve",
            {
                "label": "Approve",
                "from": [BlanketOrder.Status.WAITING_APPROVAL],
                "to": BlanketOrder.Status.CONFIRMED,
                "primary": True,
                "approval_action": "approve",
                "service_controls_state": True,
            },
        ),
        (
            "blanket-orders",
            "reject",
            {
                "label": "Reject",
                "from": [BlanketOrder.Status.WAITING_APPROVAL],
                "to": BlanketOrder.Status.REJECTED,
                "reason_label": "Rejection reason",
                "approval_action": "reject",
                "service_controls_state": True,
            },
        ),
        (
            "purchase-tenders",
            "submit",
            {
                "label": "Submit for Approval",
                "from": [PurchaseTender.Status.DRAFT],
                "to": PurchaseTender.Status.WAITING_APPROVAL,
                "primary": True,
                "approval_action": "submit",
                "service_controls_state": True,
            },
        ),
        (
            "purchase-tenders",
            "approve",
            {
                "label": "Approve",
                "from": [PurchaseTender.Status.WAITING_APPROVAL],
                "to": PurchaseTender.Status.CONFIRMED,
                "primary": True,
                "approval_action": "approve",
                "service_controls_state": True,
            },
        ),
        (
            "purchase-tenders",
            "reject",
            {
                "label": "Reject",
                "from": [PurchaseTender.Status.WAITING_APPROVAL],
                "to": PurchaseTender.Status.REJECTED,
                "reason_label": "Rejection reason",
                "approval_action": "reject",
                "service_controls_state": True,
            },
        ),
        (
            "purchase-tenders",
            "submit-decision",
            {
                "label": "Submit Decision",
                "from": [PurchaseTender.Status.AWARDED],
                "to": PurchaseTender.Status.AWARDED,
                "primary": True,
                "approval_action": "submit",
                "service_controls_state": True,
            },
        ),
        (
            "purchase-tenders",
            "approve-decision",
            {
                "label": "Approve Decision",
                "from": [PurchaseTender.Status.AWARDED],
                "to": PurchaseTender.Status.AWARDED,
                "primary": True,
                "approval_action": "approve",
                "service_controls_state": True,
            },
        ),
        (
            "purchase-tenders",
            "reject-decision",
            {
                "label": "Reject Decision",
                "from": [PurchaseTender.Status.AWARDED],
                "to": PurchaseTender.Status.AWARDED,
                "reason_label": "Tender decision rejection reason",
                "approval_action": "reject",
                "service_controls_state": True,
            },
        ),
    ):
        register_document_action(entity, action, definition, override=True)
    register_document_action(
        "purchases",
        "confirm",
        {
            "label": "Approve",
            "from": [PurchaseOrder.Status.WAITING_APPROVAL],
            "to": PurchaseOrder.Status.CONFIRMED,
            "primary": True,
            "reason_label": "Approval reason",
            "approval_action": "approve",
            "service_controls_state": True,
        },
        override=True,
    )
    register_document_action(
        "purchases",
        "reject",
        {
            "label": "Reject",
            "from": [PurchaseOrder.Status.WAITING_APPROVAL],
            "to": PurchaseOrder.Status.REJECTED,
            "reason_label": "Rejection reason",
            "approval_action": "reject",
        },
        override=True,
    )
    register_document_action(
        "purchases",
        "revise",
        {
            "label": "Revise",
            "from": [
                PurchaseOrder.Status.DRAFT,
                PurchaseOrder.Status.SENT,
                PurchaseOrder.Status.CONFIRMED,
                PurchaseOrder.Status.COMPLETED,
            ],
            "to": PurchaseOrder.Status.DRAFT,
            "reason_label": "Revision reason",
            "service_controls_state": True,
            "redirect_to_result": True,
            "is_available": purchase_revision_action_available,
        },
        override=True,
    )
