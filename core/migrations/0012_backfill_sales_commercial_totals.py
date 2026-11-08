from decimal import Decimal

from django.db import migrations, models


ORDER_BATCH_SIZE = 500
LINE_BATCH_SIZE = 1000


def backfill_sales_commercial_fields(apps, schema_editor):
    SaleOrder = apps.get_model("core", "SaleOrder")
    SaleOrderLine = apps.get_model("core", "SaleOrderLine")
    money = Decimal("0.01")
    hundred = Decimal("100")

    last_order_id = 0
    while True:
        orders = list(
            SaleOrder.objects.filter(pk__gt=last_order_id)
            .select_related("company", "customer")
            .order_by("pk")[:ORDER_BATCH_SIZE]
        )
        if not orders:
            break
        order_ids = [order.pk for order in orders]
        gross = models.ExpressionWrapper(
            models.F("quantity") * models.F("unit_price"),
            output_field=models.DecimalField(max_digits=30, decimal_places=8),
        )
        untaxed = models.ExpressionWrapper(
            gross * (hundred - models.F("discount_percent")) / hundred,
            output_field=models.DecimalField(max_digits=30, decimal_places=8),
        )
        tax = models.ExpressionWrapper(
            untaxed * models.F("tax_percent") / hundred,
            output_field=models.DecimalField(max_digits=30, decimal_places=8),
        )
        summaries = {
            row["order_id"]: row
            for row in SaleOrderLine.objects.filter(
                order_id__in=order_ids,
                display_type="",
            ).values("order_id").annotate(
                gross=models.Sum(gross),
                untaxed=models.Sum(untaxed),
                tax=models.Sum(tax),
            )
        }
        for order in orders:
            summary = summaries.get(order.pk, {})
            gross_amount = (summary.get("gross") or Decimal("0")).quantize(money)
            line_untaxed = summary.get("untaxed") or Decimal("0")
            line_tax = summary.get("tax") or Decimal("0")
            factor = Decimal("1") - order.global_discount_percent / hundred
            order.subtotal_amount = gross_amount
            order.untaxed_amount = (line_untaxed * factor).quantize(money)
            order.tax_amount = (line_tax * factor).quantize(money)
            order.discount_amount = (gross_amount - order.untaxed_amount).quantize(money)
            order.currency = order.company.currency
            if not order.delivery_address:
                order.delivery_address = order.customer.address
            if not order.invoice_address:
                order.invoice_address = order.customer.address
        SaleOrder.objects.bulk_update(
            orders,
            [
                "subtotal_amount",
                "discount_amount",
                "untaxed_amount",
                "tax_amount",
                "currency",
                "delivery_address",
                "invoice_address",
            ],
            batch_size=ORDER_BATCH_SIZE,
        )
        last_order_id = order_ids[-1]

    last_line_id = 0
    while True:
        lines = list(
            SaleOrderLine.objects.filter(pk__gt=last_line_id)
            .select_related("product", "order")
            .order_by("pk")[:LINE_BATCH_SIZE]
        )
        if not lines:
            break
        changed = []
        for line in lines:
            if not line.uom and line.product_id:
                line.uom = line.product.unit
            if not line.delivery_address:
                line.delivery_address = line.order.delivery_address[:255]
            changed.append(line)
        SaleOrderLine.objects.bulk_update(
            changed,
            ["uom", "delivery_address"],
            batch_size=LINE_BATCH_SIZE,
        )
        last_line_id = lines[-1].pk


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_expand_sales_order_commercial_fields"),
    ]

    operations = [
        migrations.RunPython(backfill_sales_commercial_fields, migrations.RunPython.noop),
    ]
