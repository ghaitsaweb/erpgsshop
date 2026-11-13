from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


FROZEN_BLANKET_STATUSES = {
    "confirmed",
    "running",
    "active",
    "exhausted",
    "expired",
    "closed",
}


def _snapshot(quantity, unit_price, tax_percent, *, free_product=False, discount_percent=Decimal("0")):
    quantity = Decimal(quantity or 0)
    unit_price = Decimal(unit_price or 0)
    tax_percent = Decimal(tax_percent or 0)
    discount_percent = Decimal(discount_percent or 0)
    gross = Decimal("0") if free_product else quantity * unit_price
    discount = gross * discount_percent / Decimal("100")
    untaxed = gross - discount
    tax = untaxed * tax_percent / Decimal("100")
    taxes = []
    if tax_percent:
        taxes.append(
            {
                "name": f"Tax {tax_percent}%",
                "amount_type": "percent",
                "amount": str(tax_percent),
                "price_include": False,
            }
        )
    return {
        "taxes": taxes,
        "subtotal": str(untaxed + tax),
        "discount_line_amount": str(discount),
        "global_discount_line_amount": "0",
        "total_discount_line_amount": str(discount),
        "untaxed_amount": str(untaxed),
        "taxes_amount": str(tax),
        "total_amount": str(untaxed + tax),
    }


def backfill_blanket_tax_snapshots(apps, schema_editor):
    BlanketOrder = apps.get_model("gsshop_purchase", "BlanketOrder")
    BlanketOrderLine = apps.get_model("gsshop_purchase", "BlanketOrderLine")

    BlanketOrder.objects.all().update(
        untaxed_amount=models.F("agreement_value"),
        tax_amount=Decimal("0"),
        total_amount=models.F("agreement_value"),
    )

    batch = []
    rows = (
        BlanketOrderLine.objects.filter(
            blanket_order__status__in=FROZEN_BLANKET_STATUSES,
            tax_snapshot={},
        )
        .only(
            "pk",
            "agreed_quantity",
            "unit_price",
            "tax_percent",
            "free_product",
            "tax_snapshot",
        )
        .iterator(chunk_size=500)
    )
    for line in rows:
        line.tax_snapshot = _snapshot(
            line.agreed_quantity,
            line.unit_price,
            line.tax_percent,
            free_product=line.free_product,
        )
        batch.append(line)
        if len(batch) == 500:
            BlanketOrderLine.objects.bulk_update(batch, ["tax_snapshot"], batch_size=500)
            batch = []
    if batch:
        BlanketOrderLine.objects.bulk_update(batch, ["tax_snapshot"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0041_purchase_order_line_pricing_provenance"),
    ]

    operations = [
        migrations.AddField(
            model_name="blanketorder",
            name="tax_amount",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=18),
        ),
        migrations.AddField(
            model_name="blanketorder",
            name="total_amount",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=18),
        ),
        migrations.AddField(
            model_name="blanketorder",
            name="untaxed_amount",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=18),
        ),
        migrations.AddField(
            model_name="blanketorderline",
            name="tax_percent",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=5,
                validators=[MinValueValidator(0), MaxValueValidator(100)],
            ),
        ),
        migrations.AddField(
            model_name="blanketorderline",
            name="tax_snapshot",
            field=models.JSONField(blank=True, default=dict, editable=False),
        ),
        migrations.AddField(
            model_name="purchaseorderlinepricingprovenance",
            name="discount_percent",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                editable=False,
                max_digits=5,
                validators=[MinValueValidator(0), MaxValueValidator(100)],
            ),
        ),
        migrations.AddField(
            model_name="purchaseorderlinepricingprovenance",
            name="tax_percent",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                editable=False,
                max_digits=5,
                validators=[MinValueValidator(0), MaxValueValidator(100)],
            ),
        ),
        migrations.AddField(
            model_name="purchaseorderlinepricingprovenance",
            name="tax_snapshot",
            field=models.JSONField(blank=True, default=dict, editable=False),
        ),
        migrations.AddField(
            model_name="purchasequotationline",
            name="tax_snapshot",
            field=models.JSONField(blank=True, default=dict, editable=False),
        ),
        migrations.AlterField(
            model_name="purchasequotationline",
            name="tax_percent",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=5,
                validators=[MinValueValidator(0), MaxValueValidator(100)],
            ),
        ),
        migrations.RunPython(backfill_blanket_tax_snapshots, migrations.RunPython.noop),
    ]
