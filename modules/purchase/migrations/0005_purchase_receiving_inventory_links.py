from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_purchase_receiving_fields"),
        ("gsshop_purchase", "0004_productbudgetmovement_approved_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchasesetting",
            name="receiving_behavior",
            field=models.CharField(
                choices=[
                    ("manual", "Create goods receipts manually"),
                    ("auto", "Prepare draft goods receipts on PO approval"),
                ],
                default="manual",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="goodsreceiptline",
            name="return_stock_move",
            field=models.OneToOneField(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_return_line",
                to="core.stockmove",
            ),
        ),
        migrations.AddField(
            model_name="goodsreceiptline",
            name="returned_quantity",
            field=models.DecimalField(decimal_places=3, default=0, editable=False, max_digits=18),
        ),
        migrations.AddField(
            model_name="goodsreceiptline",
            name="stock_move",
            field=models.OneToOneField(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_receipt_line",
                to="core.stockmove",
            ),
        ),
    ]
