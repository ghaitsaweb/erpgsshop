"""Bounded materialization for the HMX Vendor Transaction History contract."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

from core.models import PurchaseOrder, PurchaseOrderLine

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseReturnRequest,
    TenderBid,
    VendorProfile,
    VendorTransactionHistory,
)
from .vendor_transaction_history_contract import as_date, as_decimal, delivery_snapshot


VENDOR_HISTORY_PAGE_SIZE = 100
MAX_VENDOR_HISTORY_PAGE_SIZE = 500
VENDOR_HISTORY_SOURCES = ("purchase", "tender", "receipt", "return")
ZERO = Decimal("0")


def _page(queryset, batch_size):
    rows = list(queryset[: batch_size + 1])
    selected = rows[:batch_size]
    return selected, len(rows) > batch_size


def _purchase_type(status):
    if status in {
        PurchaseOrder.Status.CONFIRMED,
        PurchaseOrder.Status.COMPLETED,
        PurchaseOrder.Status.CLOSED,
    }:
        return "Purchase Order"
    if status == PurchaseOrder.Status.REJECTED:
        return "Rejected RFQ"
    if status in {
        PurchaseOrder.Status.SENT,
        PurchaseOrder.Status.WAITING_APPROVAL,
        PurchaseOrder.Status.EXPIRED,
    }:
        return "Quotation"
    return "RFQ"


def _history(profile, **values):
    value = values.get("value")
    summary = values.get("summary") or ""
    return VendorTransactionHistory(
        vendor_id=profile.pk,
        order_id=values.get("order_id"),
        transaction_type=values["transaction_type"],
        reference=values["reference"],
        transaction_date=as_date(values.get("transaction_date")) or timezone.localdate(),
        amount=value or ZERO,
        status=values.get("status") or "",
        notes=summary,
        source_document=values.get("source_document") or values["reference"],
        source_model=values.get("source_model") or "",
        source_record_id=values.get("source_record_id"),
        product_id=values.get("product_id"),
        summary=summary,
        value=value,
        currency=profile.company.currency,
        ordered_quantity=values.get("ordered_quantity"),
        received_quantity=values.get("received_quantity"),
        remaining_quantity=values.get("remaining_quantity"),
        expected_date=as_date(values.get("expected_date")),
        actual_delivery_date=as_date(values.get("actual_delivery_date")),
        delivery_status=values.get("delivery_status") or "",
        lead_time_variance=values.get("lead_time_variance"),
        rejection_indicator=bool(values.get("rejection_indicator")),
        rejection_reason=values.get("rejection_reason") or "",
        company_snapshot_id=profile.company_id,
        branch_snapshot_id=values.get("branch_snapshot_id"),
    )


def _purchase_rows(profile, before_id, batch_size):
    queryset = PurchaseOrderLine.objects.filter(
        order__company_id=profile.company_id,
        order__vendor_id=profile.partner_id,
    )
    if before_id:
        queryset = queryset.filter(pk__lt=before_id)
    queryset = (
        queryset.values(
            "id",
            "order_id",
            "order__number",
            "order__order_date",
            "order__status",
            "order__warehouse__branch_id",
            "product_id",
            "product__name",
            "description",
            "quantity",
            "unit_price",
            "received_quantity",
            "purchase_pricing_provenance__expected_date",
            "order__purchase_control__expected_date",
        )
        .annotate(
            actual_receipt_date=Max(
                "receipt_lines__receipt__receipt_date",
                filter=Q(receipt_lines__receipt__status=GoodsReceipt.Status.RECEIVED),
            ),
            rejected_quantity=Sum(
                "receipt_lines__rejected_quantity",
                filter=Q(receipt_lines__receipt__status=GoodsReceipt.Status.RECEIVED),
            ),
        )
        .order_by("-id")
    )
    rows, has_more = _page(queryset, batch_size)
    histories = []
    for row in rows:
        expected_date = (
            row["purchase_pricing_provenance__expected_date"]
            or row["order__purchase_control__expected_date"]
        )
        delivery = delivery_snapshot(
            ordered_quantity=row["quantity"],
            received_quantity=row["received_quantity"],
            expected_date=expected_date,
            actual_date=row["actual_receipt_date"],
        )
        rejected = row["rejected_quantity"] or ZERO
        histories.append(
            _history(
                profile,
                order_id=row["order_id"],
                transaction_type=_purchase_type(row["order__status"]),
                reference=f"purchase_line:{row['id']}",
                transaction_date=row["order__order_date"],
                status=row["order__status"],
                source_document=row["order__number"],
                source_model="core.purchaseorderline",
                source_record_id=row["id"],
                product_id=row["product_id"],
                summary=row["description"] or row["product__name"],
                value=as_decimal(row["quantity"]) * as_decimal(row["unit_price"]),
                ordered_quantity=row["quantity"],
                received_quantity=row["received_quantity"],
                rejection_indicator=(
                    row["order__status"] == PurchaseOrder.Status.REJECTED or rejected > ZERO
                ),
                branch_snapshot_id=row["order__warehouse__branch_id"],
                **delivery,
            )
        )
    return histories, has_more, rows[-1]["id"] if rows else None


def _tender_rows(profile, before_id, batch_size):
    queryset = TenderBid.objects.filter(
        tender__company_id=profile.company_id,
        vendor_id=profile.partner_id,
    )
    if before_id:
        queryset = queryset.filter(pk__lt=before_id)
    rows, has_more = _page(
        queryset.values(
            "id",
            "tender__number",
            "tender__title",
            "tender__publish_date",
            "tender__company_id",
            "submitted_at",
            "created_at",
            "bid_amount",
            "status",
        ).order_by("-id"),
        batch_size,
    )
    histories = [
        _history(
            profile,
            transaction_type="Tender Participation",
            reference=f"tender_bid:{row['id']}",
            transaction_date=row["submitted_at"] or row["tender__publish_date"] or row["created_at"],
            status=row["status"],
            source_document=row["tender__number"],
            source_model="gsshop_purchase.tenderbid",
            source_record_id=row["id"],
            summary=row["tender__title"],
            value=row["bid_amount"],
        )
        for row in rows
    ]
    return histories, has_more, rows[-1]["id"] if rows else None


def _receipt_rows(profile, before_id, batch_size):
    queryset = GoodsReceiptLine.objects.filter(
        receipt__company_id=profile.company_id,
        receipt__order__vendor_id=profile.partner_id,
    )
    if before_id:
        queryset = queryset.filter(pk__lt=before_id)
    rows, has_more = _page(
        queryset.values(
            "id",
            "receipt__order_id",
            "receipt__number",
            "receipt__receipt_date",
            "receipt__status",
            "receipt__delivery_status",
            "receipt__warehouse__branch_id",
            "order_line__product_id",
            "order_line__product__name",
            "order_line__description",
            "order_line__quantity",
            "order_line__received_quantity",
            "order_line__purchase_pricing_provenance__expected_date",
            "order_line__order__purchase_control__expected_date",
            "received_quantity",
            "accepted_quantity",
            "rejected_quantity",
            "returned_quantity",
            "rejection_reason",
        ).order_by("-id"),
        batch_size,
    )
    histories = []
    for row in rows:
        expected_date = (
            row["order_line__purchase_pricing_provenance__expected_date"]
            or row["order_line__order__purchase_control__expected_date"]
        )
        delivery = delivery_snapshot(
            ordered_quantity=row["order_line__quantity"],
            received_quantity=row["order_line__received_quantity"],
            expected_date=expected_date,
            actual_date=row["receipt__receipt_date"],
        )
        histories.append(
            _history(
                profile,
                order_id=row["receipt__order_id"],
                transaction_type="Goods Receipt",
                reference=f"goods_receipt_line:{row['id']}",
                transaction_date=row["receipt__receipt_date"],
                status=row["receipt__status"],
                source_document=row["receipt__number"],
                source_model="gsshop_purchase.goodsreceiptline",
                source_record_id=row["id"],
                product_id=row["order_line__product_id"],
                summary=row["order_line__description"] or row["order_line__product__name"],
                ordered_quantity=row["order_line__quantity"],
                received_quantity=row["received_quantity"],
                rejection_indicator=row["rejected_quantity"] > ZERO,
                rejection_reason=row["rejection_reason"],
                branch_snapshot_id=row["receipt__warehouse__branch_id"],
                **delivery,
            )
        )
        histories[-1].delivery_status = row["receipt__delivery_status"] or delivery["delivery_status"]
    return histories, has_more, rows[-1]["id"] if rows else None


def _return_rows(profile, before_id, batch_size):
    queryset = PurchaseReturnRequest.objects.filter(
        company_id=profile.company_id,
        receipt__order__vendor_id=profile.partner_id,
    )
    if before_id:
        queryset = queryset.filter(pk__lt=before_id)
    rows, has_more = _page(
        queryset.values(
            "id",
            "number",
            "return_date",
            "reason",
            "status",
            "receipt__order_id",
            "receipt__warehouse__branch_id",
        )
        .annotate(returned_quantity=Sum("receipt__lines__returned_quantity"))
        .order_by("-id"),
        batch_size,
    )
    histories = [
        _history(
            profile,
            order_id=row["receipt__order_id"],
            transaction_type="Vendor Return",
            reference=f"purchase_return:{row['id']}",
            transaction_date=row["return_date"],
            status=row["status"],
            source_document=row["number"],
            source_model="gsshop_purchase.purchasereturnrequest",
            source_record_id=row["id"],
            summary=row["reason"],
            received_quantity=row["returned_quantity"],
            actual_delivery_date=row["return_date"],
            delivery_status=row["status"],
            rejection_indicator=True,
            rejection_reason=row["reason"],
            branch_snapshot_id=row["receipt__warehouse__branch_id"],
        )
        for row in rows
    ]
    return histories, has_more, rows[-1]["id"] if rows else None


SOURCE_BUILDERS = {
    "purchase": _purchase_rows,
    "tender": _tender_rows,
    "receipt": _receipt_rows,
    "return": _return_rows,
}


HISTORY_UPDATE_FIELDS = [
    "order",
    "transaction_date",
    "amount",
    "status",
    "notes",
    "source_document",
    "source_model",
    "source_record_id",
    "product",
    "summary",
    "value",
    "currency",
    "ordered_quantity",
    "received_quantity",
    "remaining_quantity",
    "expected_date",
    "actual_delivery_date",
    "delivery_status",
    "lead_time_variance",
    "rejection_indicator",
    "rejection_reason",
    "company_snapshot",
    "branch_snapshot",
    "updated_at",
]


@transaction.atomic
def sync_vendor_transaction_history_page(
    vendor,
    source,
    *,
    before_id=None,
    batch_size=VENDOR_HISTORY_PAGE_SIZE,
):
    if source not in SOURCE_BUILDERS:
        raise ValidationError("Unsupported Vendor Transaction History source.")
    if batch_size <= 0 or batch_size > MAX_VENDOR_HISTORY_PAGE_SIZE:
        raise ValidationError(
            f"Vendor Transaction History batch size must be between 1 and {MAX_VENDOR_HISTORY_PAGE_SIZE}."
        )
    profile = VendorProfile.objects.select_related("company", "partner").get(
        pk=getattr(vendor, "pk", vendor)
    )
    histories, has_more, next_before_id = SOURCE_BUILDERS[source](
        profile,
        before_id,
        batch_size,
    )
    if histories:
        VendorTransactionHistory.objects.bulk_create(
            histories,
            batch_size=batch_size,
            update_conflicts=True,
            update_fields=HISTORY_UPDATE_FIELDS,
            unique_fields=["vendor", "transaction_type", "reference"],
        )
    return {
        "source": source,
        "processed": len(histories),
        "has_more": has_more,
        "next_before_id": next_before_id if has_more else None,
    }


def _schedule_remaining(profile_id, result):
    if not result["has_more"]:
        return
    from .tasks import sync_vendor_transaction_history_task

    sync_vendor_transaction_history_task.apply_async(
        kwargs={
            "vendor_id": profile_id,
            "source": result["source"],
            "before_id": result["next_before_id"],
        },
        countdown=2,
    )


def prepare_vendor_transaction_history(vendor):
    """Materialize recent rows immediately and keyset-backfill older history."""

    purchase = sync_vendor_transaction_history_page(vendor, "purchase")
    tender = sync_vendor_transaction_history_page(vendor, "tender")
    receipt = sync_vendor_transaction_history_page(vendor, "receipt")
    returns = sync_vendor_transaction_history_page(vendor, "return")
    _schedule_remaining(vendor.pk, purchase)
    _schedule_remaining(vendor.pk, tender)
    _schedule_remaining(vendor.pk, receipt)
    _schedule_remaining(vendor.pk, returns)
    return {
        "processed": sum(
            result["processed"] for result in (purchase, tender, receipt, returns)
        ),
        "has_more": any(
            result["has_more"] for result in (purchase, tender, receipt, returns)
        ),
    }
