from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import POSOrderLine
from .retail_models import POSCashMovement, POSPayment


@receiver([post_save, post_delete], sender=POSOrderLine)
def update_pos_total(sender, instance, **kwargs):
    percentage_discount = (
        F("quantity") * F("unit_price") * F("discount_percent") / Decimal("100")
    )
    line_total = ExpressionWrapper(
        F("quantity") * F("unit_price") - percentage_discount - F("discount_amount") + F("tax_amount"),
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    subtotal = instance.order.lines.aggregate(
        total=Coalesce(
            Sum(line_total),
            Decimal("0"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )
    )["total"]
    order_values = instance.order.__class__.objects.filter(pk=instance.order_id).values(
        "discount_amount", "service_charge_amount", "takeaway_fee_amount", "tax_amount"
    ).first()
    if order_values is None:
        return
    total = (
        subtotal
        - order_values["discount_amount"]
        + order_values["service_charge_amount"]
        + order_values["takeaway_fee_amount"]
        + order_values["tax_amount"]
    )
    instance.order.__class__.objects.filter(pk=instance.order_id).update(
        subtotal_amount=subtotal,
        total=total,
    )


@receiver([post_save, post_delete], sender=POSPayment)
def update_pos_paid_amount(sender, instance, **kwargs):
    paid_amount = POSPayment.objects.filter(order_id=instance.order_id).aggregate(
        value=Coalesce(
            Sum("amount"),
            Decimal("0"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )
    )["value"]
    instance.order.__class__.objects.filter(pk=instance.order_id).update(paid_amount=paid_amount)


@receiver([post_save, post_delete], sender=POSCashMovement)
def update_pos_session_cash_summary(sender, instance, **kwargs):
    totals = POSCashMovement.objects.filter(session_id=instance.session_id, active=True).aggregate(
        cash_in=Coalesce(
            Sum("amount", filter=Q(movement_type=POSCashMovement.MovementType.CASH_IN)),
            Decimal("0"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        ),
        cash_out=Coalesce(
            Sum("amount", filter=Q(movement_type=POSCashMovement.MovementType.CASH_OUT)),
            Decimal("0"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        ),
    )
    session_model = instance.session.__class__
    session_model.objects.filter(pk=instance.session_id).update(
        cash_in_total=totals["cash_in"],
        cash_out_total=totals["cash_out"],
        expected_closing_cash=F("opening_cash") + F("cash_sales_total") + totals["cash_in"] - totals["cash_out"],
    )
