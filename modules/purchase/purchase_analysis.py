"""Bounded Purchase Request and Purchase Order analysis contracts."""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import (
    DateField,
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.models import PurchaseOrder, PurchaseOrderLine
from modules.organization.context import scope_queryset as scope_organization_queryset

from .access import scope_purchase_queryset
from .models import GoodsReceipt, GoodsReceiptLine, PurchaseRequest, PurchaseRequestLine


MAX_ANALYSIS_DAYS = 366
MAX_ANALYSIS_GROUPS = 500

PURCHASE_ORDER_ANALYSIS_CONTRACT = {
    "title": "Purchase Order Analysis",
    "row_groupby": ["product"],
    "column_groupby": ["expected_date:month"],
    "measures": [
        "quantity",
        "total_discount_line_amount",
        "untaxed_amount",
        "taxes_amount",
        "total_amount",
        "subtotal_lines",
    ],
    "dimensions": [
        "vendor",
        "branch",
        "order_type",
        "purchase_team",
        "purchase_pic",
        "product_category",
        "product",
        "currency",
        "order_status",
        "po_number",
    ],
}

PURCHASE_REQUEST_ANALYSIS_CONTRACT = {
    "title": "Purchase Request Analysis",
    "row_groupby": ["product"],
    "column_groupby": ["expected_date:month"],
    "measures": [
        "quantity",
        "approved_qty",
        "rfq_qty",
        "po_qty",
        "received_qty",
    ],
}


def _analysis_period(params, today=None):
    today = today or timezone.localdate()
    default_start = today - timedelta(days=MAX_ANALYSIS_DAYS - 1)
    raw_start = str(params.get("date_from") or "").strip()
    raw_end = str(params.get("date_to") or "").strip()
    start = parse_date(raw_start) if raw_start else default_start
    end = parse_date(raw_end) if raw_end else today
    if start is None or end is None:
        raise ValidationError("Analysis dates must use YYYY-MM-DD format.")
    if start > end:
        raise ValidationError("Analysis start date cannot be after the end date.")
    if (end - start).days >= MAX_ANALYSIS_DAYS:
        raise ValidationError(f"Analysis supports at most {MAX_ANALYSIS_DAYS} inclusive days.")
    return start, end


def _bounded_groups(queryset, label):
    rows = list(queryset[: MAX_ANALYSIS_GROUPS + 1])
    if len(rows) > MAX_ANALYSIS_GROUPS:
        raise ValidationError(
            f"{label} exceeds the operational limit of {MAX_ANALYSIS_GROUPS} product-month groups."
        )
    return rows


def _scoped_order_ids(user):
    return scope_organization_queryset(
        scope_purchase_queryset(user, "purchases", PurchaseOrder.objects.all())
    ).values("pk")


def _scoped_request_ids(user):
    return scope_organization_queryset(
        scope_purchase_queryset(user, "purchase-requests", PurchaseRequest.objects.all())
    ).values("pk")


def build_purchase_order_analysis(user, params, *, today=None):
    start, end = _analysis_period(params, today=today)
    money = DecimalField(max_digits=30, decimal_places=6)
    gross = ExpressionWrapper(F("quantity") * F("unit_price"), output_field=money)
    discount_percent = Coalesce(
        F("purchase_pricing_provenance__discount_percent"),
        Value(Decimal("0")),
        output_field=money,
    )
    tax_percent = Coalesce(
        F("purchase_pricing_provenance__tax_percent"),
        Value(Decimal("0")),
        output_field=money,
    )
    discount = ExpressionWrapper(gross * discount_percent / Value(Decimal("100")), output_field=money)
    untaxed = ExpressionWrapper(gross - discount, output_field=money)
    taxes = ExpressionWrapper(untaxed * tax_percent / Value(Decimal("100")), output_field=money)
    total = ExpressionWrapper(untaxed + taxes, output_field=money)
    expected = Coalesce(
        F("purchase_pricing_provenance__expected_date"),
        F("order__order_date"),
        output_field=DateField(),
    )
    groups = (
        PurchaseOrderLine.objects.filter(
            order_id__in=Subquery(_scoped_order_ids(user)),
            order__status__in=[
                PurchaseOrder.Status.CONFIRMED,
                PurchaseOrder.Status.COMPLETED,
                PurchaseOrder.Status.CLOSED,
            ],
            order__order_date__range=(start, end),
        )
        .annotate(expected_month=TruncMonth(expected))
        .values(
            "product_id",
            "product__sku",
            "product__name",
            "expected_month",
        )
        .annotate(
            measure_quantity=Sum("quantity"),
            measure_total_discount_line_amount=Sum(discount),
            measure_untaxed_amount=Sum(untaxed),
            measure_taxes_amount=Sum(taxes),
            measure_total_amount=Sum(total),
            measure_subtotal_lines=Sum(gross),
        )
        .order_by("product__name", "expected_month", "product_id")
    )
    rows = _bounded_groups(groups, "Purchase Order Analysis")
    for row in rows:
        for measure in PURCHASE_ORDER_ANALYSIS_CONTRACT["measures"]:
            row[measure] = row.pop(f"measure_{measure}")
    return {
        "contract": PURCHASE_ORDER_ANALYSIS_CONTRACT,
        "date_from": start,
        "date_to": end,
        "rows": rows,
    }


def build_purchase_request_analysis(user, params, *, today=None):
    start, end = _analysis_period(params, today=today)
    received_by_request_line = (
        GoodsReceiptLine.objects.filter(
            receipt__status=GoodsReceipt.Status.RECEIVED,
            order_line__purchase_pricing_provenance__quotation_line__request_line_id=OuterRef("pk"),
        )
        .values("order_line__purchase_pricing_provenance__quotation_line__request_line_id")
        .annotate(total=Sum("accepted_quantity"))
        .values("total")[:1]
    )
    expected = Coalesce(
        F("expected_date"),
        F("request__required_date"),
        F("request__request_date"),
        output_field=DateField(),
    )
    lines = (
        PurchaseRequestLine.objects.filter(
            request_id__in=Subquery(_scoped_request_ids(user)),
            request__status__in=[
                PurchaseRequest.Status.APPROVED,
                PurchaseRequest.Status.PARTIALLY_RFQ,
                PurchaseRequest.Status.RFQ,
                PurchaseRequest.Status.PARTIALLY_ORDERED,
                PurchaseRequest.Status.FULLY_ORDERED,
                PurchaseRequest.Status.IN_PROGRESS,
                PurchaseRequest.Status.CLOSED,
            ],
        )
        .exclude(
            line_status__in=[
                PurchaseRequestLine.Status.DRAFT,
                PurchaseRequestLine.Status.PENDING,
                PurchaseRequestLine.Status.REJECTED,
            ]
        )
        .annotate(
            expected_month=TruncMonth(expected),
            received_value=Coalesce(
                Subquery(received_by_request_line),
                Value(Decimal("0")),
                output_field=DecimalField(max_digits=18, decimal_places=3),
            ),
        )
        .filter(expected_month__range=(start, end))
        .values(
            "product_id",
            "product__sku",
            "product__name",
            "expected_month",
        )
        .annotate(
            quantity=Sum("quantity"),
            approved_qty=Sum("approved_quantity"),
            rfq_qty=Sum("rfq_quantity"),
            po_qty=Sum("ordered_quantity"),
            received_qty=Sum("received_value"),
        )
        .order_by("product__name", "expected_month", "product_id")
    )
    return {
        "contract": PURCHASE_REQUEST_ANALYSIS_CONTRACT,
        "date_from": start,
        "date_to": end,
        "rows": _bounded_groups(lines, "Purchase Request Analysis"),
    }
