from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_purchase_receiving_fields"),
        ("gsshop_purchase", "0006_purchase_bill_line_provenance"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseinventorytransferline",
            name="destination_stock_move",
            field=models.OneToOneField(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_transfer_destination_line",
                to="core.stockmove",
            ),
        ),
        migrations.AddField(
            model_name="purchaseinventorytransferline",
            name="source_stock_move",
            field=models.OneToOneField(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_transfer_source_line",
                to="core.stockmove",
            ),
        ),
    ]
