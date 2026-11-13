"""Purchase-owned extensions to the generic document workflow registry."""

from django.urls import reverse

from core.document_registry import (
    register_document_action,
    register_document_header_field,
    register_document_line_field,
    register_document_option,
)
from core.models import PurchaseOrder

from .forms import (
    BlanketOrderDocumentForm,
    BlanketOrderLineForm,
    PurchaseOrderDocumentForm,
    PurchaseOrderLineDocumentForm,
    PurchaseRequestDocumentForm,
    PurchaseRequestLineDocumentForm,
    PurchaseTenderDocumentForm,
    PurchaseTenderLineDocumentForm,
)
from .models import BlanketOrder, PurchaseRequest, PurchaseSetting, PurchaseTender
from .services import (
    auto_route_purchase_order_action,
    auto_route_purchase_request_action,
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


def register_purchase_document_extensions():
    register_document_option(
        "purchases",
        "header_form",
        PurchaseOrderDocumentForm,
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
