"""Purchase-owned vendor limit aggregation and workflow enforcement."""

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from core.models import Invoice, PurchaseOrder

from .models import PurchaseAccountingDocument, PurchaseOrderControl, PurchaseSetting, VendorProfile


ZERO = Decimal("0")
HUNDRED = Decimal("100")
DEBT_BEARING_STATUSES = (
    PurchaseOrder.Status.WAITING_APPROVAL,
    PurchaseOrder.Status.CONFIRMED,
    PurchaseOrder.Status.COMPLETED,
    PurchaseOrder.Status.CLOSED,
)


@dataclass(frozen=True)
class VendorPurchaseLimitResult:
    credit_used: Decimal = ZERO
    projected_total: Decimal = ZERO
    limit: Decimal = ZERO
    hard_blocked: bool = False
    requires_approval: bool = False
    warning_reached: bool = False

    @property
    def available(self):
        return self.limit - self.credit_used

    @property
    def warning_message(self):
        if not self.warning_reached:
            return ""
        return (
            "Vendor purchase debt has reached the purchase limit threshold. "
            f"Total Debt: {self.projected_total}. Limit: {self.limit}."
        )


def vendor_purchase_credit_used(*, company_id, vendor_id, exclude_order_id=None):
    """Aggregate unpaid debt for one vendor with a fixed two-query shape."""
    orders = PurchaseOrder.objects.filter(
        company_id=company_id,
        vendor_id=vendor_id,
        status__in=DEBT_BEARING_STATUSES,
    )
    documents = PurchaseAccountingDocument.objects.filter(
        order__company_id=company_id,
        order__vendor_id=vendor_id,
        order__status__in=DEBT_BEARING_STATUSES,
        document_type=PurchaseAccountingDocument.DocumentType.VENDOR_BILL,
        invoice__status=Invoice.Status.PAID,
    )
    if exclude_order_id:
        orders = orders.exclude(pk=exclude_order_id)
        documents = documents.exclude(order_id=exclude_order_id)
    ordered_total = orders.aggregate(total=Sum("total"))["total"] or ZERO
    paid_total = documents.aggregate(total=Sum("invoice__total"))["total"] or ZERO
    return max(ordered_total - paid_total, ZERO)


def _threshold_amount(limit, threshold_percent):
    return limit * threshold_percent / HUNDRED if threshold_percent > ZERO else limit


def evaluate_vendor_purchase_limit(*, order, order_total, lock_vendor=False):
    setting = PurchaseSetting.objects.filter(company_id=order.company_id).first()
    profile_query = VendorProfile.objects.filter(
        company_id=order.company_id,
        partner_id=order.vendor_id,
    )
    if lock_vendor:
        profile_query = profile_query.select_for_update()
    profile = profile_query.first()
    limit = (profile.purchase_credit_limit if profile else ZERO) or ZERO
    if setting is None or limit <= ZERO:
        return VendorPurchaseLimitResult(limit=limit)

    used = vendor_purchase_credit_used(
        company_id=order.company_id,
        vendor_id=order.vendor_id,
        exclude_order_id=order.pk,
    )
    projected = used + (order_total or ZERO)
    hard_threshold = setting.hard_block_threshold_percent or ZERO
    soft_threshold = setting.soft_block_threshold_percent or ZERO
    warning_threshold = setting.warning_block_threshold_percent or ZERO
    return VendorPurchaseLimitResult(
        credit_used=used,
        projected_total=projected,
        limit=limit,
        hard_blocked=bool(
            setting.is_hard_block_limit
            and hard_threshold > ZERO
            and projected > _threshold_amount(limit, hard_threshold)
        ),
        requires_approval=bool(
            setting.is_soft_block_limit
            and soft_threshold > ZERO
            and projected > _threshold_amount(limit, soft_threshold)
        ),
        warning_reached=bool(
            setting.is_warning_block_limit
            and warning_threshold > ZERO
            and projected > _threshold_amount(limit, warning_threshold)
        ),
    )


def enforce_vendor_purchase_limit(*, order, order_total):
    """Lock the vendor limit, reject hard breaches, and persist UI control state."""
    result = evaluate_vendor_purchase_limit(order=order, order_total=order_total, lock_vendor=True)
    if result.hard_blocked:
        raise ValidationError(
            "Vendor purchase debt cannot exceed the vendor purchase limit. "
            f"Total Debt: {result.projected_total}. Limit: {result.limit}."
        )
    PurchaseOrderControl.objects.update_or_create(
        order_id=order.pk,
        defaults={
            "purchase_limit_requires_approval": result.requires_approval,
            "purchase_limit_warning_message": result.warning_message,
            "purchase_limit_total": result.projected_total,
        },
    )
    return result
