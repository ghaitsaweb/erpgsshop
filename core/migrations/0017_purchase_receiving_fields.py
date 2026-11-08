from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0016_sale_order_type_and_sequences"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseorder",
            name="invoicing_policy",
            field=models.CharField(
                choices=[("on_ordered", "Ordered quantities"), ("on_delivered", "Received quantities")],
                default="on_ordered",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="purchaseorder",
            name="received_percentage",
            field=models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=5),
        ),
        migrations.AddField(
            model_name="purchaseorder",
            name="warehouse",
            field=models.ForeignKey(
                blank=True,
                help_text="Default destination warehouse for stockable order lines.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_orders",
                to="core.warehouse",
            ),
        ),
        migrations.AddField(
            model_name="purchaseorderline",
            name="billed_quantity",
            field=models.DecimalField(decimal_places=3, default=0, editable=False, max_digits=18),
        ),
        migrations.AddField(
            model_name="purchaseorderline",
            name="received_quantity",
            field=models.DecimalField(decimal_places=3, default=0, editable=False, max_digits=18),
        ),
        migrations.AddField(
            model_name="purchaseorderline",
            name="warehouse",
            field=models.ForeignKey(
                blank=True,
                help_text="Overrides the purchase order destination warehouse for this line.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_order_lines",
                to="core.warehouse",
            ),
        ),
    ]
