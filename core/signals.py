from django.core.exceptions import ValidationError
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from .models import (
    InvoiceLine,
    PurchaseOrderLine,
    SaleOrderLine,
    SaleOrderType,
    recalculate_sale_order_totals,
    recalculate_total,
)
from .sales import sale_total_recalculation_suppressed


@receiver([post_save, post_delete], sender=SaleOrderLine)
def update_sale_total(sender, instance, **kwargs):
    if not sale_total_recalculation_suppressed():
        recalculate_sale_order_totals(instance.order)


@receiver([post_save, post_delete], sender=PurchaseOrderLine)
def update_purchase_total(sender, instance, **kwargs):
    recalculate_total(instance.order)


@receiver([post_save, post_delete], sender=InvoiceLine)
def update_invoice_total(sender, instance, **kwargs):
    recalculate_total(instance.invoice)


@receiver(m2m_changed, sender=SaleOrderType.companies.through)
def protect_used_sale_order_type_companies(sender, instance, action, **kwargs):
    if action in {"pre_add", "pre_remove", "pre_clear"} and instance.pk and instance.sale_orders.exists():
        raise ValidationError(
            "The Companies field cannot be modified because this Order Type is already used in transactions."
        )

