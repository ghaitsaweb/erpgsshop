"""Bounded HMX-style Tender Bid Allocation Matrix payload."""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import (
    PurchaseOrderControl,
    PurchaseTender,
    PurchaseTenderLine,
    TenderAwardAllocation,
    TenderBid,
    TenderBidLine,
)
from .services import (
    MAX_PURCHASE_DOCUMENT_LINES,
    MAX_RFQ_VENDORS,
    MAX_TENDER_AWARD_ALLOCATIONS,
)


ZERO = Decimal("0")


def _bounded(queryset, *, limit, label):
    rows = list(queryset[: limit + 1])
    if len(rows) > limit:
        raise ValidationError(f"{label} exceeds the operational limit of {limit} records.")
    return rows


def _format_quantity(value, unit=""):
    display = f"{Decimal(value or 0):,.3f}".rstrip("0").rstrip(".")
    return f"{display} {unit or ''}".strip()


def _format_amount(value, currency):
    value = Decimal(value or 0)
    currency = str(currency or "").upper()
    decimals = 0 if currency == "IDR" else 2
    return f"{currency} {value:,.{decimals}f}".strip()


def build_tender_award_allocation_workspace(tender):
    """Build a fixed-query matrix containing every eligible Tender Bid line."""
    if tender.status != PurchaseTender.Status.AWARDED:
        raise ValidationError("Bid Allocation Matrix is only available during Awarded status.")
    if tender.decision_approval_status not in {
        PurchaseTender.DecisionStatus.DRAFT,
        PurchaseTender.DecisionStatus.REJECTED,
    }:
        raise ValidationError("Reset the Tender decision before changing its allocations.")
    if PurchaseOrderControl.objects.filter(tender_id=tender.pk).exists():
        raise ValidationError("Tender allocations cannot change after Purchase Orders are generated.")

    tender_lines = _bounded(
        PurchaseTenderLine.objects.filter(tender_id=tender.pk)
        .select_related("product")
        .order_by("pk"),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Tender allocation matrix lines",
    )
    bids = _bounded(
        TenderBid.objects.filter(
            tender_id=tender.pk,
            status__in=[
                TenderBid.Status.SUBMITTED,
                TenderBid.Status.SHORTLISTED,
                TenderBid.Status.AWARDED,
            ],
        )
        .select_related("vendor")
        .order_by("pk"),
        limit=MAX_RFQ_VENDORS,
        label="Tender allocation matrix vendors",
    )
    bid_lines = _bounded(
        TenderBidLine.objects.filter(bid_id__in=[bid.pk for bid in bids])
        .select_related("bid", "bid__vendor", "tender_line", "tender_line__product")
        .order_by("tender_line_id", "rank", "unit_price", "pk"),
        limit=MAX_TENDER_AWARD_ALLOCATIONS,
        label="Tender allocation matrix candidates",
    )
    allocations = _bounded(
        TenderAwardAllocation.objects.filter(tender_id=tender.pk)
        .select_related("bid_line")
        .order_by("tender_line_id", "rank", "pk"),
        limit=MAX_TENDER_AWARD_ALLOCATIONS,
        label="Tender award allocation",
    )

    tender_line_ids = {line.pk for line in tender_lines}
    valid_candidates = [
        bid_line
        for bid_line in bid_lines
        if (
            bid_line.tender_line_id in tender_line_ids
            and not bid_line.no_bid
            and bid_line.compliant
            and bid_line.offered_quantity > 0
            and bid_line.unit_price > 0
        )
    ]
    if not valid_candidates:
        raise ValidationError("At least one compliant submitted Tender Bid line is required.")

    allocation_by_bid_line_id = {
        allocation.bid_line_id: allocation for allocation in allocations
    }
    default_to_offered_quantity = not allocations
    candidates_by_tender_line_id = defaultdict(list)
    for candidate in valid_candidates:
        allocation = allocation_by_bid_line_id.get(candidate.pk)
        if allocation is not None:
            allocated_quantity = allocation.allocated_quantity
        elif default_to_offered_quantity:
            # HMX seeds the first Award matrix with each vendor's offered quantity.
            allocated_quantity = candidate.offered_quantity
        else:
            allocated_quantity = ZERO
        candidates_by_tender_line_id[candidate.tender_line_id].append(
            {
                "bid_line_id": candidate.pk,
                "bid_id": candidate.bid_id,
                "vendor_id": candidate.bid.vendor_id,
                "vendor_name": candidate.bid.vendor.name,
                "rank": candidate.rank,
                "score": candidate.total_score,
                "score_display": f"{candidate.total_score:.1f}/100",
                "offered_quantity": candidate.offered_quantity,
                "offered_quantity_display": _format_quantity(candidate.offered_quantity),
                "allocated_quantity": allocated_quantity,
                "unit_price": candidate.unit_price,
                "unit_price_display": _format_amount(candidate.unit_price, tender.currency),
                "subtotal": allocated_quantity * candidate.unit_price,
                "subtotal_display": _format_amount(
                    allocated_quantity * candidate.unit_price,
                    tender.currency,
                ),
            }
        )

    line_rows = []
    for tender_line in tender_lines:
        unit = getattr(tender_line.product, "unit", "") if tender_line.product else ""
        candidates = candidates_by_tender_line_id.get(tender_line.pk, [])
        allocated_total = sum(
            (candidate["allocated_quantity"] for candidate in candidates),
            ZERO,
        )
        line_rows.append(
            {
                "line_id": tender_line.pk,
                "product_name": (
                    tender_line.product.name
                    if tender_line.product_id
                    else tender_line.description
                ) or "-",
                "sku": tender_line.product.sku if tender_line.product_id else "",
                "description": tender_line.description or tender_line.specification or "",
                "required_quantity": tender_line.quantity,
                "required_quantity_display": _format_quantity(tender_line.quantity, unit),
                "allocated_total": allocated_total,
                "allocated_total_display": _format_quantity(allocated_total, unit),
                "balanced": allocated_total == tender_line.quantity,
                "candidates": candidates,
            }
        )

    return {
        "id": tender.pk,
        "code": tender.number,
        "name": tender.title,
        "currency": tender.currency,
        "status_display": tender.get_status_display(),
        "strategy_display": tender.award_strategy.get_strategy_display(),
        "line_rows": line_rows,
        "line_count": len(line_rows),
        "candidate_count": len(valid_candidates),
        "vendor_count": len({candidate.bid.vendor_id for candidate in valid_candidates}),
        "initial_defaults": default_to_offered_quantity,
    }
