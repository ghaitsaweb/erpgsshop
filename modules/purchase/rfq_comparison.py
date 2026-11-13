"""Bounded HMX-style RFQ comparison and winner confirmation."""

from collections import Counter
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from core.models import PurchaseOrder, PurchaseOrderLine

from .models import GoodsReceipt, PurchaseQuotation, PurchaseQuotationLine, VendorProfile
from .services import confirm_purchase_order, transition_purchase_quotation


ZERO = Decimal("0")
HUNDRED = Decimal("100")
MAX_RFQ_COMPARISON_RFQS = 25
MAX_RFQ_COMPARISON_LINES = 5000
COMPARE_RFQ_ALLOWED_STATUSES = {
    PurchaseQuotation.Status.DRAFT,
    PurchaseQuotation.Status.SENT,
    PurchaseQuotation.Status.RECEIVED,
}


def _ordered_ids(raw_ids):
    ids = []
    seen = set()
    for raw_id in raw_ids:
        try:
            value = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError("The RFQ selection is invalid.") from exc
        if value < 1 or value in seen:
            raise ValidationError("The RFQ selection is invalid.")
        ids.append(value)
        seen.add(value)
    if len(ids) < 2:
        raise ValidationError("Please select at least two RFQs to compare.")
    if len(ids) > MAX_RFQ_COMPARISON_RFQS:
        raise ValidationError(
            f"RFQ comparison supports at most {MAX_RFQ_COMPARISON_RFQS} RFQs at once."
        )
    return ids


def _format_amount(amount, currency):
    amount = Decimal(amount or 0)
    if currency == "IDR":
        return f"{currency} {amount:,.0f}"
    return f"{currency} {amount:,.2f}"


def _format_date(value):
    return value.strftime("%d %b %Y") if value else "-"


def _format_quantity(value, unit=""):
    display = f"{Decimal(value or 0):,.3f}".rstrip("0").rstrip(".")
    return f"{display} {unit}".strip()


def _comparison_rates(quotations):
    company = quotations[0].company
    target_currency = company.currency.upper()
    foreign_currencies = {
        quotation.currency.upper()
        for quotation in quotations
        if quotation.currency.upper() != target_currency
    }
    if not foreign_currencies:
        return target_currency, {}
    try:
        CurrencyRate = apps.get_model("gsshop_finance", "CurrencyRate")
    except LookupError as exc:
        raise ValidationError("Finance Currency Rates are required for mixed-currency RFQ comparison.") from exc
    comparison_date = max(
        (quotation.issue_date for quotation in quotations if quotation.issue_date),
        default=timezone.localdate(),
    )
    rate_rows = list(
        CurrencyRate.objects.filter(
            company_id=company.pk,
            currency__in=foreign_currencies,
            rate_date__lte=comparison_date,
        )
        .order_by("currency", "-rate_date", "-pk")
        .distinct("currency")
        .values("currency", "rate")
    )
    rates = {row["currency"].upper(): Decimal(row["rate"]) for row in rate_rows}
    missing = sorted(foreign_currencies.difference(rates))
    if missing:
        raise ValidationError(f"Missing currency rate for: {', '.join(missing)}.")
    return target_currency, rates


def _money(amount, source_currency, target_currency, rates):
    source_currency = source_currency.upper()
    source_amount = Decimal(amount or 0)
    converted = source_currency != target_currency
    conversion_rate = rates[source_currency] if converted else Decimal("1")
    target_amount = source_amount * conversion_rate
    return {
        "amount": target_amount,
        "display": _format_amount(target_amount, target_currency),
        "source_amount": source_amount,
        "source_display": _format_amount(source_amount, source_currency),
        "source_currency": source_currency,
        "converted": converted,
        "conversion_rate": conversion_rate,
        "secondary": (
            f"{_format_amount(source_amount, source_currency)} @ {conversion_rate}"
            if converted
            else ""
        ),
    }


def _metric(value, suffix="", *, count=0):
    if value is None:
        return {"value": None, "display": "-", "count": count}
    value = Decimal(value)
    return {"value": value, "display": f"{value:.0f}{suffix}", "count": count}


def _vendor_metrics(vendor_ids):
    profiles = {
        row["partner_id"]: row
        for row in VendorProfile.objects.filter(partner_id__in=vendor_ids).values(
            "partner_id",
            "rating",
            "latest_performance_score",
        )
    }
    receipt_stats = {
        row["order__vendor_id"]: row
        for row in GoodsReceipt.objects.filter(
            order__vendor_id__in=vendor_ids,
            status=GoodsReceipt.Status.RECEIVED,
        )
        .values("order__vendor_id")
        .annotate(
            total=Count("pk"),
            on_time=Count(
                "pk",
                filter=Q(
                    delivery_status__in=[
                        GoodsReceipt.DeliveryStatus.EARLY,
                        GoodsReceipt.DeliveryStatus.ON_TIME,
                    ]
                ),
            ),
        )
    }
    fulfillment_stats = {
        row["order__vendor_id"]: row
        for row in PurchaseOrderLine.objects.filter(
            order__vendor_id__in=vendor_ids,
            order__status__in=[PurchaseOrder.Status.CONFIRMED, PurchaseOrder.Status.COMPLETED],
        )
        .values("order__vendor_id")
        .annotate(
            total=Count("pk"),
            fulfilled=Count("pk", filter=Q(received_quantity__gte=F("quantity"))),
        )
    }
    result = {}
    for vendor_id in vendor_ids:
        receipt = receipt_stats.get(vendor_id, {})
        fulfillment = fulfillment_stats.get(vendor_id, {})
        receipt_total = receipt.get("total", 0)
        line_total = fulfillment.get("total", 0)
        profile = profiles.get(vendor_id, {})
        schedule = (
            Decimal(receipt.get("on_time", 0)) * HUNDRED / Decimal(receipt_total)
            if receipt_total
            else None
        )
        fulfilled = (
            Decimal(fulfillment.get("fulfilled", 0)) * HUNDRED / Decimal(line_total)
            if line_total
            else None
        )
        rating = profile.get("rating") or None
        if not rating and profile.get("latest_performance_score"):
            rating = Decimal(profile["latest_performance_score"]) / Decimal("20")
        result[vendor_id] = {
            "delivery_on_schedule": _metric(schedule, "%", count=receipt_total),
            "delivery_fulfillment": _metric(fulfilled, "%", count=line_total),
            "star_rating": _metric(rating, count=0),
        }
    return result


def compare_purchase_quotations(raw_ids):
    """Return a comparison payload with fixed-query, bounded source reads."""
    quotation_ids = _ordered_ids(raw_ids)
    quotations_by_id = {
        quotation.pk: quotation
        for quotation in PurchaseQuotation.objects.filter(pk__in=quotation_ids)
        .select_related("company", "vendor", "warehouse")
    }
    if len(quotations_by_id) != len(quotation_ids):
        raise ValidationError("The RFQ selection changed or is outside your access scope.")
    quotations = [quotations_by_id[quotation_id] for quotation_id in quotation_ids]
    if len({quotation.company_id for quotation in quotations}) != 1:
        raise ValidationError("RFQs from different companies cannot be compared.")
    invalid = [
        quotation.number
        for quotation in quotations
        if quotation.status not in COMPARE_RFQ_ALLOWED_STATUSES
    ]
    if invalid:
        raise ValidationError("Only draft, sent, or received RFQs can be compared.")

    lines = list(
        PurchaseQuotationLine.objects.filter(quotation_id__in=quotation_ids)
        .select_related("product", "request_line")
        .order_by("quotation_id", "pk")[: MAX_RFQ_COMPARISON_LINES + 1]
    )
    if len(lines) > MAX_RFQ_COMPARISON_LINES:
        raise ValidationError(
            f"RFQ comparison exceeds the operational limit of {MAX_RFQ_COMPARISON_LINES} lines."
        )
    lines_by_quotation = {quotation_id: [] for quotation_id in quotation_ids}
    first_line_by_quote_product = {}
    products = {}
    quote_count_by_product = Counter()
    seen_quote_products = set()
    for line in lines:
        lines_by_quotation[line.quotation_id].append(line)
        if not line.product_id:
            continue
        products.setdefault(line.product_id, line.product)
        key = (line.quotation_id, line.product_id)
        first_line_by_quote_product.setdefault(key, line)
        if key not in seen_quote_products:
            quote_count_by_product[line.product_id] += 1
            seen_quote_products.add(key)
    if not any(count >= 2 for count in quote_count_by_product.values()):
        raise ValidationError("No matching products found across the selected RFQs.")

    target_currency, rates = _comparison_rates(quotations)
    metrics_by_vendor = _vendor_metrics({quotation.vendor_id for quotation in quotations})
    totals_by_quote = {}
    for quotation in quotations:
        quote_lines = lines_by_quotation[quotation.pk]
        gross = sum(
            (ZERO if line.free_product else line.quantity * line.unit_price for line in quote_lines),
            ZERO,
        )
        untaxed = sum((line.untaxed_amount for line in quote_lines), ZERO)
        tax = sum((line.tax_amount for line in quote_lines), ZERO)
        total = sum((line.total_amount for line in quote_lines), ZERO)
        totals_by_quote[quotation.pk] = {
            "gross": gross,
            "discount": gross - untaxed,
            "tax": tax,
            "total": total,
        }

    columns = []
    for quotation in quotations:
        metrics = metrics_by_vendor[quotation.vendor_id]
        columns.append(
            {
                "id": quotation.pk,
                "code": quotation.number,
                "reference": quotation.number,
                "status": quotation.status,
                "status_display": quotation.get_status_display(),
                "vendor_id": quotation.vendor_id,
                "vendor_name": quotation.vendor.name,
                "vendor_email": quotation.vendor.email,
                "vendor_phone": quotation.vendor.phone,
                "delivery_on_schedule": metrics["delivery_on_schedule"],
                "delivery_fulfillment": metrics["delivery_fulfillment"],
                "star_rating": metrics["star_rating"],
                "expected_date": _format_date(quotation.expected_date),
                "quotation_expiry_date": _format_date(quotation.response_deadline),
                "currency_name": quotation.currency.upper(),
                "total": _money(
                    totals_by_quote[quotation.pk]["total"],
                    quotation.currency,
                    target_currency,
                    rates,
                ),
            }
        )

    product_rows = []
    for product_id, product in products.items():
        cells = []
        quoted_cells = []
        source_line = None
        for quotation in quotations:
            line = first_line_by_quote_product.get((quotation.pk, product_id))
            source_line = source_line or line
            if line is None:
                cells.append(
                    {
                        "rfq_id": quotation.pk,
                        "quoted": False,
                        "highlight": "",
                        "badges": [],
                        "line_id": None,
                    }
                )
                continue
            cell = {
                "rfq_id": quotation.pk,
                "quoted": True,
                "highlight": "",
                "badges": [],
                "line_id": line.pk,
                "product_id": product_id,
                "description": line.description,
                "unit_price": _money(
                    line.unit_price,
                    quotation.currency,
                    target_currency,
                    rates,
                ),
                "quantity": _format_quantity(line.quantity, product.unit),
                "quantity_value": line.quantity,
                "uom": product.unit,
                "expected_date": _format_date(line.promised_date or quotation.expected_date),
                "discount": (
                    f"{line.discount_percent.normalize()}%" if line.discount_percent else ""
                ),
                "subtotal": _money(
                    line.total_amount,
                    quotation.currency,
                    target_currency,
                    rates,
                ),
            }
            cells.append(cell)
            quoted_cells.append(cell)
        prices = [cell["unit_price"]["amount"] for cell in quoted_cells]
        if len(prices) >= 2 and min(prices) != max(prices):
            for cell in quoted_cells:
                amount = cell["unit_price"]["amount"]
                if amount == min(prices):
                    cell["highlight"] = "best"
                    cell["badges"].append("Best")
                elif amount == max(prices):
                    cell["highlight"] = "highest"
                    cell["badges"].append("Highest")
        target_price = (
            source_line.request_line.estimated_unit_price
            if source_line and source_line.request_line_id
            else source_line.unit_price if source_line else ZERO
        )
        product_rows.append(
            {
                "product_id": product_id,
                "product_name": product.name,
                "product_code": product.sku,
                "description": source_line.description if source_line else product.name,
                "target_quantity": (
                    _format_quantity(source_line.quantity, product.unit) if source_line else "-"
                ),
                "target_price": _money(
                    target_price,
                    quotations_by_id[source_line.quotation_id].currency,
                    target_currency,
                    rates,
                )
                if source_line
                else None,
                "cells": cells,
            }
        )

    summary_specs = [
        ("initial_quotation", "Initial Quotation", "gross"),
        ("discount_amount", "Discount Amount", "discount"),
        ("total_taxes", "Total Taxes", "tax"),
        ("total", "Total", "total"),
    ]
    summary_rows = [
        {
            "key": key,
            "label": label,
            "cells": [
                {
                    "rfq_id": quotation.pk,
                    "money": _money(
                        totals_by_quote[quotation.pk][amount_key],
                        quotation.currency,
                        target_currency,
                        rates,
                    ),
                }
                for quotation in quotations
            ],
        }
        for key, label, amount_key in summary_specs
    ]
    target_budget = min(
        (totals_by_quote[quotation.pk]["total"] * (
            rates[quotation.currency.upper()]
            if quotation.currency.upper() != target_currency
            else Decimal("1")
        ) for quotation in quotations),
        default=ZERO,
    )
    deadlines = [
        quotation.response_deadline or quotation.expected_date
        for quotation in quotations
        if quotation.response_deadline or quotation.expected_date
    ]
    return {
        "title": "RFQ Comparison",
        "selected_count": len(quotations),
        "total_vendors": len(quotations),
        "total_products": len(products),
        "source_count_text": f"{len(products)} Material Source",
        "company_currency": target_currency,
        "has_mixed_currencies": len({quotation.currency.upper() for quotation in quotations}) > 1,
        "currency_names": sorted({quotation.currency.upper() for quotation in quotations}),
        "columns": columns,
        "product_rows": product_rows,
        "summary_rows": summary_rows,
        "insights": {
            "total_vendors": len(quotations),
            "total_products": len(products),
            "target_budget": _money(target_budget, target_currency, target_currency, rates),
            "deadline_bid": _format_date(min(deadlines) if deadlines else None),
        },
        "empty_value": "-",
    }


@transaction.atomic
def confirm_quotation_from_comparison(quotation, actor=None):
    """Select one compared RFQ and run the authoritative local PO confirmation flow."""
    locked = PurchaseQuotation.objects.select_for_update(of=("self",)).get(pk=quotation.pk)
    if locked.status not in COMPARE_RFQ_ALLOWED_STATUSES:
        raise ValidationError("Only draft, sent, or received RFQs can be confirmed from RFQ Comparison.")
    if locked.status != PurchaseQuotation.Status.RECEIVED:
        locked.status = PurchaseQuotation.Status.RECEIVED
        locked.save(update_fields=["status", "updated_at"])
    selected = transition_purchase_quotation(locked, "select", actor)
    order = PurchaseOrder.objects.get(pk=selected._created_purchase_order_id)
    return confirm_purchase_order(order, actor)
