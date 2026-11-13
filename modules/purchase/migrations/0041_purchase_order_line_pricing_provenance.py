import django.db.models.deletion
from django.db import migrations, models


PRICE_SOURCE_CHOICES = [
    ("pricelist", "Pricelist"),
    ("manual", "Manual"),
    ("no_match", "No Match"),
]


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_purchase_order_generated_number"),
        ("gsshop_purchase", "0040_blanket_pricing_traceability"),
    ]

    operations = [
        migrations.CreateModel(
            name="PurchaseOrderLinePricingProvenance",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("free_product", models.BooleanField(default=False, editable=False)),
                (
                    "price_source",
                    models.CharField(blank=True, choices=PRICE_SOURCE_CHOICES, max_length=20),
                ),
                ("price_source_note", models.CharField(blank=True, editable=False, max_length=255)),
                ("discount_formula", models.CharField(blank=True, editable=False, max_length=64)),
                (
                    "applied_pricelist",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="purchase_order_line_provenance",
                        to="gsshop_purchase.vendorpricelist",
                    ),
                ),
                (
                    "applied_pricelist_tier",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="purchase_order_line_provenance",
                        to="gsshop_purchase.vendorpricelisttier",
                    ),
                ),
                (
                    "blanket_order_line",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="purchase_order_pricing_provenance",
                        to="gsshop_purchase.blanketorderline",
                    ),
                ),
                (
                    "free_product_source_order_line",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="free_product_benefit_provenance",
                        to="core.purchaseorderline",
                    ),
                ),
                (
                    "order_line",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="purchase_pricing_provenance",
                        to="core.purchaseorderline",
                    ),
                ),
                (
                    "quotation_line",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="purchase_order_pricing_provenance",
                        to="gsshop_purchase.purchasequotationline",
                    ),
                ),
            ],
            options={"ordering": ["order_line_id"]},
        ),
    ]
