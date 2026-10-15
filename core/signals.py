from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import InvoiceLine, PurchaseOrderLine, SaleOrderLine, recalculate_total


@receiver([post_save, post_delete], sender=SaleOrderLine)
def update_sale_total(sender, instance, **kwargs):
    recalculate_total(instance.order)


@receiver([post_save, post_delete], sender=PurchaseOrderLine)
def update_purchase_total(sender, instance, **kwargs):
    recalculate_total(instance.order)


@receiver([post_save, post_delete], sender=InvoiceLine)
def update_invoice_total(sender, instance, **kwargs):
    recalculate_total(instance.invoice)

