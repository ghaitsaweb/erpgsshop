"""Purchase-owned creation of immutable-source Purchase Order revisions."""

import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import PurchaseOrder, PurchaseOrderLine

from .models import (
    GoodsReceipt,
    PurchaseOrderControl,
    PurchaseOrderRevision,
    PurchaseSetting,
    VendorPricelist,
    VendorProfile,
)


MAX_REVISION_LINES = 500
REVISION_ALLOWED_STATUSES = {
    PurchaseOrder.Status.DRAFT,
    PurchaseOrder.Status.SENT,
    PurchaseOrder.Status.WAITING_APPROVAL,
    PurchaseOrder.Status.CONFIRMED,
    PurchaseOrder.Status.COMPLETED,
}


def _next_suffix_number(number, revision_number):
    base = re.sub(r"/R\d+$", "", number or "")
    return f"{base}/R{revision_number}"


def _revision_number(source, source_control):
    if source_control and source_control.revision_number:
        return source_control.revision_number + 1
    matched = re.search(r"/R(\d+)$", source.number or "")
    return int(matched.group(1)) + 1 if matched else 1


@transaction.atomic
def revise_purchase_order(order, actor=None, *, reason=""):
    if actor is None:
        raise ValidationError("A user is required to revise a Purchase Order.")
    source = (
        PurchaseOrder.objects.select_for_update(of=("self",))
        .select_related("vendor", "warehouse")
        .get(pk=getattr(order, "pk", order))
    )
    if source.status not in REVISION_ALLOWED_STATUSES:
        raise ValidationError(
            "Purchase Orders in Closed, Revised, Expired, Cancelled, or Rejected status cannot be revised."
        )
    setting = PurchaseSetting.objects.filter(company_id=source.company_id).first()
    if setting is None or not setting.allow_order_revision:
        raise ValidationError("Revision is disabled. Enable it in Purchase Settings first.")
    profile_status = (
        VendorProfile.objects.filter(company_id=source.company_id, partner_id=source.vendor_id)
        .values_list("status", flat=True)
        .first()
    )
    if profile_status == VendorProfile.Status.BLACKLISTED:
        raise ValidationError("Cannot revise a document for a blacklisted vendor.")

    source_control = (
        PurchaseOrderControl.objects.select_for_update(of=("self",))
        .select_related("vendor_pricelist", "order_type")
        .filter(order_id=source.pk)
        .first()
    )
    pricelist = source_control.vendor_pricelist if source_control else None
    if pricelist and (
        pricelist.status == VendorPricelist.Status.EXPIRED
        or (pricelist.date_end and pricelist.date_end < timezone.localdate())
    ):
        raise ValidationError("Cannot revise a document with an expired vendor pricelist.")
    if source.lines.filter(received_quantity__gt=0).exists():
        raise ValidationError(f"{source.number} has already been received and cannot be revised.")
    if GoodsReceipt.objects.filter(order_id=source.pk).exclude(
        status__in=[GoodsReceipt.Status.REJECTED, GoodsReceipt.Status.CANCELLED]
    ).exists():
        raise ValidationError(
            f"{source.number} already has a goods receipt; reject or cancel it before revision."
        )

    lines = list(
        PurchaseOrderLine.objects.select_for_update(of=("self",))
        .filter(order_id=source.pk)
        .select_related("product", "warehouse")
        .order_by("pk")[: MAX_REVISION_LINES + 1]
    )
    if len(lines) > MAX_REVISION_LINES:
        raise ValidationError(f"Purchase Order revision exceeds the operational limit of {MAX_REVISION_LINES} lines.")
    number = _revision_number(source, source_control)
    if setting.order_revision_sequence_mode == PurchaseSetting.RevisionSequenceMode.FOLLOW_SEQUENCE:
        if source_control is None or source_control.order_type_id is None:
            raise ValidationError("Configure a Purchase Order Type before using sequence-based revisions.")
        from .services import allocate_purchase_document_number

        new_number = allocate_purchase_document_number(
            source_control.order_type_id,
            source.company_id,
            "purchase_order",
        )
    else:
        new_number = _next_suffix_number(source.number, number)

    revised = PurchaseOrder.objects.create(
        company_id=source.company_id,
        number=new_number,
        vendor_id=source.vendor_id,
        warehouse_id=source.warehouse_id,
        invoicing_policy=source.invoicing_policy,
        order_date=source.order_date,
        status=PurchaseOrder.Status.DRAFT,
        total=source.total,
        notes=source.notes,
    )
    PurchaseOrderLine.objects.bulk_create(
        [
            PurchaseOrderLine(
                order=revised,
                product_id=line.product_id,
                description=line.description,
                warehouse_id=line.warehouse_id,
                quantity=line.quantity,
                unit_price=line.unit_price,
            )
            for line in lines
        ]
    )
    control_values = {"revision_number": number}
    if source_control:
        for field_name in (
            "order_type_id",
            "order_template_id",
            "milestone_template_id",
            "quotation_id",
            "blanket_order_id",
            "tender_id",
            "vendor_pricelist_id",
            "team_id",
            "assigned_to_id",
            "expiry_date",
            "tax_percent",
            "discount_percent",
            "cost_center",
            "contract_terms",
            "communication_history",
        ):
            control_values[field_name] = getattr(source_control, field_name)
    PurchaseOrderControl.objects.create(order=revised, **control_values)

    previous_revision = (
        PurchaseOrderRevision.objects.select_related("original_order", "order")
        .filter(revised_order_id=source.pk)
        .first()
    )
    original = (
        previous_revision.original_order
        if previous_revision and previous_revision.original_order_id
        else previous_revision.order
        if previous_revision
        else source
    )
    snapshot = {
        "previous_status": source.status,
        "total": str(source.total),
        "order_date": source.order_date.isoformat(),
        "lines": [
            {
                "product_id": line.product_id,
                "description": line.description,
                "warehouse_id": line.warehouse_id,
                "quantity": str(line.quantity),
                "unit_price": str(line.unit_price),
            }
            for line in lines
        ],
    }
    PurchaseOrderRevision.objects.create(
        order=source,
        revised_order=revised,
        original_order=original,
        revision_number=number,
        reason=(reason or "Workflow revision requested").strip(),
        snapshot=snapshot,
        changed_by=actor,
    )
    source.status = PurchaseOrder.Status.REVISED
    source.save(update_fields=["status", "updated_at"])
    revised._revision_source_order_id = source.pk
    return revised
