"""Purchase-owned extensions to the generic document workflow registry."""

from core.document_registry import register_document_action, register_document_option
from core.models import PurchaseOrder

from .models import PurchaseSetting


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
        "revise",
        {
            "label": "Revise",
            "from": [
                PurchaseOrder.Status.DRAFT,
                PurchaseOrder.Status.SENT,
                PurchaseOrder.Status.WAITING_APPROVAL,
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
