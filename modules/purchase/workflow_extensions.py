"""Purchase-owned extensions to the generic document workflow registry."""

from core.document_registry import register_document_action, register_document_option
from core.models import PurchaseOrder

from .models import BlanketOrder, PurchaseRequest, PurchaseSetting, PurchaseTender


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
        "editable_statuses",
        [PurchaseOrder.Status.DRAFT],
        override=True,
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
