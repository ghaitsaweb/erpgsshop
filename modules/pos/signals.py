from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import POSOrderLine


@receiver([post_save, post_delete], sender=POSOrderLine)
def update_pos_total(sender, instance, **kwargs):
    line_total = ExpressionWrapper(
        F("quantity") * F("unit_price"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    total = instance.order.lines.aggregate(
        total=Coalesce(
            Sum(line_total),
            Decimal("0"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )
    )["total"]
    instance.order.__class__.objects.filter(pk=instance.order_id).update(total=total)
