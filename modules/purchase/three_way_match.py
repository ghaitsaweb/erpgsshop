"""Purchase-owned three-way matching calculations and posting guard."""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from core.models import Invoice, Product, PurchaseOrder, PurchaseOrderLine

from .models import (
    ApprovalStatus,
    PurchaseAccountingDocument,
    PurchaseBillLineProvenance,
    PurchaseOrderControl,
    PurchaseSetting,
    PurchaseThreeWayMatch,
    THREE_WAY_MATCH_STATUS_PRECEDENCE,
    ThreeWayMatchStatus,
)


ZERO = Decimal("0")
MAX_MATCH_LINES = 5000


def _bounded(rows, label):
    if len(rows) > MAX_MATCH_LINES:
        raise ValidationError(f"{label} exceeds the operational limit of {MAX_MATCH_LINES} rows.")
    return rows


def _match_status(*, line, billed, maximum_posted_price, quantity_tolerance, price_tolerance):
    if line.product.product_type != Product.ProductType.STOCKABLE:
        return ""
    ordered = line.quantity or ZERO
    received = line.received_quantity or ZERO
    if maximum_posted_price is not None and maximum_posted_price > line.unit_price + price_tolerance:
        return ThreeWayMatchStatus.PRICE_EXCEPTION
    if billed > received + quantity_tolerance:
        return ThreeWayMatchStatus.BILLING_EXCEPTION
    if received > ordered + quantity_tolerance:
        return ThreeWayMatchStatus.RECEIPT_EXCEPTION
    if received >= ordered - quantity_tolerance and abs(received - billed) <= quantity_tolerance:
        return ThreeWayMatchStatus.FULLY_MATCHED
    if received > ZERO and billed > ZERO:
        return ThreeWayMatchStatus.PARTIALLY_MATCHED
    if received > ZERO:
        return ThreeWayMatchStatus.PARTIALLY_RECEIVED
    return ThreeWayMatchStatus.AWAITING_RECEIPT


@transaction.atomic
def sync_purchase_three_way_match(order_ids):
    """Refresh line snapshots and aggregate PO status with bounded query count."""
    order_ids = sorted({int(order_id) for order_id in order_ids if order_id})
    if not order_ids:
        return
    lines = _bounded(
        list(
            PurchaseOrderLine.objects.filter(order_id__in=order_ids)
            .select_related("order", "product")
            .order_by("order_id", "pk")[: MAX_MATCH_LINES + 1]
        ),
        "Purchase matching",
    )
    line_ids = [line.pk for line in lines]
    company_ids = {line.order.company_id for line in lines}
    settings_by_company = {
        setting.company_id: setting
        for setting in PurchaseSetting.objects.filter(company_id__in=company_ids)
    }
    billed_by_line = {}
    if line_ids:
        billed_by_line = {
            row["order_line_id"]: row
            for row in (
                PurchaseBillLineProvenance.objects.filter(
                    order_line_id__in=line_ids,
                    accounting_document__document_type=PurchaseAccountingDocument.DocumentType.VENDOR_BILL,
                    accounting_document__status=ApprovalStatus.APPROVED,
                    invoice_line__invoice__status__in=[Invoice.Status.POSTED, Invoice.Status.PAID],
                )
                .values("order_line_id")
                .annotate(
                    billed_quantity=Sum("invoice_line__quantity"),
                    maximum_price=Max("invoice_line__unit_price"),
                )
                .order_by("order_line_id")
            )
        }
    existing = {
        snapshot.order_line_id: snapshot
        for snapshot in PurchaseThreeWayMatch.objects.filter(order_line_id__in=line_ids)
    }
    now = timezone.now()
    create_rows = []
    update_rows = []
    statuses_by_order = defaultdict(list)
    for line in lines:
        setting = settings_by_company.get(line.order.company_id)
        quantity_tolerance = setting.purchase_match_quantity_tolerance if setting else ZERO
        price_tolerance = setting.purchase_match_price_tolerance if setting else ZERO
        billed_row = billed_by_line.get(line.pk, {})
        billed = billed_row.get("billed_quantity") or ZERO
        ordered = line.quantity or ZERO
        received = line.received_quantity or ZERO
        status = _match_status(
            line=line,
            billed=billed,
            maximum_posted_price=billed_row.get("maximum_price"),
            quantity_tolerance=quantity_tolerance,
            price_tolerance=price_tolerance,
        )
        values = {
            "matching_billed_quantity": billed,
            "remaining_to_receive": max(ordered - received, ZERO),
            "received_not_billed": max(received - billed, ZERO),
            "over_received": max(received - ordered, ZERO),
            "over_billed": max(billed - received, ZERO),
            "match_status": status,
        }
        snapshot = existing.get(line.pk)
        if snapshot is None:
            create_rows.append(PurchaseThreeWayMatch(order_line_id=line.pk, **values))
        else:
            for field_name, value in values.items():
                setattr(snapshot, field_name, value)
            snapshot.updated_at = now
            update_rows.append(snapshot)
        if status:
            statuses_by_order[line.order_id].append(status)
    if create_rows:
        PurchaseThreeWayMatch.objects.bulk_create(create_rows)
    if update_rows:
        PurchaseThreeWayMatch.objects.bulk_update(
            update_rows,
            [
                "matching_billed_quantity",
                "remaining_to_receive",
                "received_not_billed",
                "over_received",
                "over_billed",
                "match_status",
                "updated_at",
            ],
        )

    PurchaseOrderControl.objects.bulk_create(
        [PurchaseOrderControl(order_id=order_id) for order_id in order_ids],
        ignore_conflicts=True,
    )
    controls = list(PurchaseOrderControl.objects.filter(order_id__in=order_ids).order_by("order_id"))
    for control in controls:
        statuses = statuses_by_order.get(control.order_id, ())
        control.match_status = (
            max(statuses, key=lambda value: THREE_WAY_MATCH_STATUS_PRECEDENCE[value])
            if statuses
            else ""
        )
        control.updated_at = now
    if controls:
        PurchaseOrderControl.objects.bulk_update(controls, ["match_status", "updated_at"])


def validate_strict_purchase_three_way_match(*, invoice, rows):
    """Reject a linked vendor bill before posting when strict matching fails."""
    if invoice.invoice_type != Invoice.InvoiceType.VENDOR:
        return
    document = (
        PurchaseAccountingDocument.objects.select_for_update()
        .select_related("order")
        .filter(
            invoice_id=invoice.pk,
            document_type=PurchaseAccountingDocument.DocumentType.VENDOR_BILL,
            status=ApprovalStatus.DRAFT,
        )
        .first()
    )
    if document is None:
        return
    setting = PurchaseSetting.objects.filter(company_id=document.order.company_id).first()
    if setting is None or setting.purchase_three_way_match_mode != PurchaseSetting.ThreeWayMatchMode.STRICT:
        return
    if invoice.company_id != document.order.company_id:
        raise ValidationError("Vendor bill and Purchase Order must belong to the same company.")

    PurchaseOrder.objects.select_for_update().get(pk=document.order_id)
    provenance = _bounded(
        list(
            PurchaseBillLineProvenance.objects.filter(accounting_document_id=document.pk)
            .select_related("invoice_line", "order_line__product")
            .order_by("order_line_id", "pk")[: MAX_MATCH_LINES + 1]
        ),
        "Vendor bill matching",
    )
    if not provenance:
        return
    current_rows = {row.pk: row for row in rows}
    line_ids = sorted({source.order_line_id for source in provenance})
    locked_lines = PurchaseOrderLine.objects.select_for_update().select_related("product").in_bulk(line_ids)
    current_quantity_by_line = defaultdict(Decimal)
    current_prices_by_line = defaultdict(list)
    for source in provenance:
        row = current_rows.get(source.invoice_line_id, source.invoice_line)
        current_quantity_by_line[source.order_line_id] += row.quantity or ZERO
        current_prices_by_line[source.order_line_id].append(row.unit_price or ZERO)

    posted_quantity_by_line = {
        row["order_line_id"]: row["quantity"] or ZERO
        for row in (
            PurchaseBillLineProvenance.objects.filter(
                order_line_id__in=line_ids,
                accounting_document__document_type=PurchaseAccountingDocument.DocumentType.VENDOR_BILL,
                accounting_document__status=ApprovalStatus.APPROVED,
                invoice_line__invoice__status__in=[Invoice.Status.POSTED, Invoice.Status.PAID],
            )
            .values("order_line_id")
            .annotate(quantity=Sum("invoice_line__quantity"))
            .order_by("order_line_id")
        )
    }
    quantity_tolerance = setting.purchase_match_quantity_tolerance or ZERO
    price_tolerance = setting.purchase_match_price_tolerance or ZERO
    for line_id in line_ids:
        line = locked_lines[line_id]
        if line.product.product_type != Product.ProductType.STOCKABLE:
            continue
        projected_quantity = posted_quantity_by_line.get(line_id, ZERO) + current_quantity_by_line[line_id]
        if (
            document.order.invoicing_policy == PurchaseOrder.InvoicingPolicy.RECEIVED
            and projected_quantity > line.received_quantity + quantity_tolerance
        ):
            raise ValidationError(
                "Cannot post Vendor Bill. Projected billed quantity for "
                f"{line.product} is {projected_quantity}, above received quantity "
                f"{line.received_quantity} plus tolerance {quantity_tolerance}."
            )
        maximum_price = line.unit_price + price_tolerance
        if any(price > maximum_price for price in current_prices_by_line[line_id]):
            raise ValidationError(
                "Cannot post Vendor Bill. Unit price for "
                f"{line.product} exceeds the Purchase Order price plus tolerance {price_tolerance}."
            )
