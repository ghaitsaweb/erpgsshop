"""Bounded HMX-style Tender bid-comparison workspace payload."""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import (
    PurchaseOrderControl,
    PurchaseTender,
    PurchaseTenderLine,
    TenderBid,
    TenderBidLine,
)
from .services import (
    MAX_PURCHASE_DOCUMENT_LINES,
    MAX_RFQ_VENDORS,
    MAX_TENDER_AWARD_ALLOCATIONS,
)


ZERO = Decimal("0")
TENDER_BID_COMPARISON_STATUSES = {
    PurchaseTender.Status.BID_SELECTION,
    PurchaseTender.Status.AWARDED,
    PurchaseTender.Status.CLOSED,
}
TENDER_BID_COMPARISON_BID_STATUSES = {
    TenderBid.Status.SUBMITTED,
    TenderBid.Status.SHORTLISTED,
    TenderBid.Status.AWARDED,
}


def _bounded(queryset, *, limit, label):
    rows = list(queryset[: limit + 1])
    if len(rows) > limit:
        raise ValidationError(f"{label} exceeds the operational limit of {limit} records.")
    return rows


def _format_amount(amount, currency):
    amount = Decimal(amount or 0)
    currency = str(currency or "").upper()
    if currency == "IDR":
        return f"{currency} {amount:,.0f}"
    return f"{currency} {amount:,.2f}".strip()


def _format_quantity(value, unit=""):
    display = f"{Decimal(value or 0):,.3f}".rstrip("0").rstrip(".")
    return f"{display} {unit or ''}".strip()


def _vendor_initials(name):
    tokens = [token for token in str(name or "").replace("&", " ").split() if token]
    return "".join(token[:1] for token in tokens[:2]).upper() or "V"


def build_tender_bid_comparison(tender):
    """Return one fixed-query, bounded comparison payload for a scoped Tender."""
    if tender.status not in TENDER_BID_COMPARISON_STATUSES:
        raise ValidationError("Bid Comparison is only available from Bid Selection onward.")

    tender_lines = _bounded(
        PurchaseTenderLine.objects.filter(tender_id=tender.pk)
        .select_related("product")
        .order_by("pk"),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Tender bid comparison lines",
    )
    bids = _bounded(
        TenderBid.objects.filter(
            tender_id=tender.pk,
            status__in=TENDER_BID_COMPARISON_BID_STATUSES,
        )
        .select_related("vendor")
        .order_by("pk"),
        limit=MAX_RFQ_VENDORS,
        label="Tender bid comparison vendors",
    )
    bid_lines = _bounded(
        TenderBidLine.objects.filter(bid_id__in=[bid.pk for bid in bids])
        .select_related("bid", "bid__vendor", "tender_line", "tender_line__product")
        .order_by("tender_line_id", "bid_id", "pk"),
        limit=MAX_TENDER_AWARD_ALLOCATIONS,
        label="Tender bid comparison quotations",
    )

    tender_line_ids = {line.pk for line in tender_lines}
    bid_ids = {bid.pk for bid in bids}
    if any(
        line.tender_line_id not in tender_line_ids or line.bid_id not in bid_ids
        for line in bid_lines
    ):
        raise ValidationError("Tender bid comparison contains an invalid quotation line.")

    valid_lines = [
        line
        for line in bid_lines
        if not line.no_bid and line.offered_quantity > 0 and line.unit_price > 0
    ]
    valid_lines_by_bid_id = defaultdict(list)
    valid_line_by_pair = {}
    lowest_price_by_tender_line_id = {}
    for line in valid_lines:
        valid_lines_by_bid_id[line.bid_id].append(line)
        valid_line_by_pair[(line.bid_id, line.tender_line_id)] = line
        current_lowest = lowest_price_by_tender_line_id.get(line.tender_line_id)
        if current_lowest is None or line.unit_price < current_lowest:
            lowest_price_by_tender_line_id[line.tender_line_id] = line.unit_price

    vendor_rows = []
    for index, bid in enumerate(bids, start=1):
        lines = valid_lines_by_bid_id.get(bid.pk, [])
        total_quote = sum(
            (line.unit_price * line.offered_quantity for line in lines),
            ZERO,
        )
        delivery_days = [line.delivery_days for line in lines if line.delivery_days > 0]
        average_delivery_days = (
            sum(delivery_days) / len(delivery_days)
            if delivery_days
            else 0
        )
        vendor_rows.append(
            {
                "bid_id": bid.pk,
                "vendor_id": bid.vendor_id,
                "vendor_name": bid.vendor.name or "-",
                "initials": _vendor_initials(bid.vendor.name),
                "sequence_label": chr(64 + index) if index <= 26 else str(index),
                "total_quote": total_quote,
                "total_quote_display": _format_amount(total_quote, tender.currency),
                "avg_lead_time": average_delivery_days,
                "avg_lead_time_display": (
                    f"{average_delivery_days:g} Days" if average_delivery_days else "-"
                ),
                "compliance_score": bid.compliance_score,
                "compliance_display": f"{bid.compliance_score:.0f}%",
                "score": bid.total_score,
                "score_display": f"{bid.total_score:.1f}/100",
                "rank": bid.rank,
                "recommended": False,
            }
        )
    vendor_rows.sort(
        key=lambda row: (
            -row["score"],
            row["total_quote"],
            row["vendor_name"].casefold(),
            row["bid_id"],
        )
    )
    if vendor_rows:
        vendor_rows[0]["recommended"] = True

    bid_by_vendor_id = {bid.vendor_id: bid for bid in bids}
    line_rows = []
    for tender_line in tender_lines:
        cells = []
        lowest_price = lowest_price_by_tender_line_id.get(tender_line.pk)
        for vendor in vendor_rows:
            bid = bid_by_vendor_id[vendor["vendor_id"]]
            bid_line = valid_line_by_pair.get((bid.pk, tender_line.pk))
            if bid_line is None:
                cells.append(
                    {
                        "vendor_id": vendor["vendor_id"],
                        "quoted": False,
                        "unit_price_display": "-",
                        "total_display": "-",
                        "lead_time_display": "-",
                        "score_display": "-",
                        "is_lowest_price": False,
                    }
                )
                continue
            subtotal = bid_line.offered_quantity * bid_line.unit_price
            cells.append(
                {
                    "vendor_id": vendor["vendor_id"],
                    "quoted": True,
                    "unit_price": bid_line.unit_price,
                    "unit_price_display": _format_amount(bid_line.unit_price, tender.currency),
                    "total": subtotal,
                    "total_display": _format_amount(subtotal, tender.currency),
                    "quantity": bid_line.offered_quantity,
                    "quantity_display": _format_quantity(
                        bid_line.offered_quantity,
                        getattr(tender_line.product, "unit", "") if tender_line.product else "",
                    ),
                    "delivery_days": bid_line.delivery_days,
                    "lead_time_display": (
                        f"{bid_line.delivery_days} d" if bid_line.delivery_days else "-"
                    ),
                    "score": bid_line.total_score,
                    "score_display": f"{bid_line.total_score:.1f}%",
                    "is_lowest_price": bool(
                        lowest_price is not None and bid_line.unit_price == lowest_price
                    ),
                }
            )
        product_name = tender_line.product.name if tender_line.product_id else tender_line.description
        line_rows.append(
            {
                "line_id": tender_line.pk,
                "product_id": tender_line.product_id,
                "product_name": product_name or "-",
                "sku": tender_line.product.sku if tender_line.product_id else "",
                "description": tender_line.description or tender_line.specification or "",
                "quantity": tender_line.quantity,
                "quantity_display": _format_quantity(
                    tender_line.quantity,
                    getattr(tender_line.product, "unit", "") if tender_line.product else "",
                ),
                "cells": cells,
            }
        )

    has_generated_orders = PurchaseOrderControl.objects.filter(tender_id=tender.pk).exists()
    award_action = ""
    award_label = "Award Contract"
    if not has_generated_orders and tender.status == PurchaseTender.Status.BID_SELECTION:
        award_action = "award"
    elif (
        not has_generated_orders
        and tender.status == PurchaseTender.Status.AWARDED
        and tender.decision_approval_status
        in {
            PurchaseTender.DecisionStatus.DRAFT,
            PurchaseTender.DecisionStatus.REJECTED,
        }
    ):
        award_action = "manage-award-allocation"

    return {
        "id": tender.pk,
        "code": tender.number,
        "name": tender.title,
        "status": tender.status,
        "status_display": tender.get_status_display(),
        "description": (
            f"Evaluating {len(vendor_rows)} supplier responses for {tender.title}."
        ),
        "currency": tender.currency,
        "vendors": vendor_rows,
        "line_rows": line_rows,
        "vendor_count": len(vendor_rows),
        "line_count": len(line_rows),
        "award_action": award_action,
        "award_label": award_label,
        "can_award": bool(award_action),
    }
