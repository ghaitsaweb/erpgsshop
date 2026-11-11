"""Vendor-pricelist product eligibility owned by the Purchase module."""

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from .models import PurchaseSetting, VendorPricelist, VendorPricelistItem, VendorProfile


MAX_ELIGIBLE_PRODUCTS = 5000


def _bounded_product_ids(queryset, label):
    rows = list(queryset.order_by("product_id").values_list("product_id", flat=True)[: MAX_ELIGIBLE_PRODUCTS + 1])
    if len(rows) > MAX_ELIGIBLE_PRODUCTS:
        raise ValidationError(f"{label} exceeds the operational limit of {MAX_ELIGIBLE_PRODUCTS} products.")
    return set(rows)


def eligible_vendor_pricelist_product_ids(*, company_id, vendor_id, transaction_date=None):
    """Return products in active, date-valid pricelists for one vendor."""
    if not company_id or not vendor_id:
        return set()
    transaction_date = transaction_date or timezone.localdate()
    profile_id = (
        VendorProfile.objects.filter(company_id=company_id, partner_id=vendor_id)
        .values_list("pk", flat=True)
        .first()
    )
    if profile_id is None:
        return set()
    valid_pricelist = Q(
        pricelist__company_id=company_id,
        pricelist__vendor_id=profile_id,
        pricelist__active=True,
        pricelist__status=VendorPricelist.Status.ACTIVE,
        pricelist__date_start__lte=transaction_date,
        active=True,
    ) & (Q(pricelist__date_end__isnull=True) | Q(pricelist__date_end__gte=transaction_date))
    item_ids = _bounded_product_ids(
        VendorPricelistItem.objects.filter(valid_pricelist),
        "Vendor pricelist item eligibility",
    )
    legacy_ids = _bounded_product_ids(
        VendorPricelist.objects.filter(
            company_id=company_id,
            vendor_id=profile_id,
            active=True,
            status=VendorPricelist.Status.ACTIVE,
            date_start__lte=transaction_date,
            product_id__isnull=False,
        ).filter(Q(date_end__isnull=True) | Q(date_end__gte=transaction_date)),
        "Legacy vendor pricelist eligibility",
    )
    return item_ids | legacy_ids


def validate_vendor_pricelist_product_eligibility(*, document, lines, transaction_date):
    """Validate a bounded, preloaded line collection using one shared eligibility set."""
    restricted = (
        PurchaseSetting.objects.filter(company_id=document.company_id)
        .values_list("restrict_product_by_vendor_pricelist", flat=True)
        .first()
    )
    if not restricted or not document.vendor_id:
        return
    eligible_ids = eligible_vendor_pricelist_product_ids(
        company_id=document.company_id,
        vendor_id=document.vendor_id,
        transaction_date=transaction_date,
    )
    invalid_products = {}
    for line in lines:
        if line.product_id not in eligible_ids:
            invalid_products[line.product_id] = line.product
    if not invalid_products:
        return
    product_names = ", ".join(str(product) for product in invalid_products.values())
    raise ValidationError(
        "Products are not available in an active vendor pricelist for "
        f"{document.vendor} on {transaction_date}: {product_names}. "
        "Choose another product, change the vendor, or update the vendor pricelist."
    )
