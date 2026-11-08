from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_purchase_receiving_fields"),
        ("gsshop_purchase", "0005_purchase_receiving_inventory_links"),
    ]

    operations = [
        migrations.CreateModel(
            name="PurchaseBillLineProvenance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=18)),
                ("unit_price", models.DecimalField(decimal_places=2, max_digits=18)),
                (
                    "accounting_document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="bill_line_provenance",
                        to="gsshop_purchase.purchaseaccountingdocument",
                    ),
                ),
                (
                    "invoice_line",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="purchase_provenance",
                        to="core.invoiceline",
                    ),
                ),
                (
                    "order_line",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="bill_line_provenance",
                        to="core.purchaseorderline",
                    ),
                ),
            ],
            options={"ordering": ["invoice_line_id"]},
        ),
    ]
