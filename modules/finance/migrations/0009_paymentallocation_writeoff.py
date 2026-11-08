from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("gsshop_finance", "0008_salesinvoicelineprovenance_sale_order_line"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentallocation",
            name="writeoff_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=18),
        ),
        migrations.AddField(
            model_name="paymentallocation",
            name="writeoff_account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payment_allocation_writeoffs",
                to="gsshop_finance.account",
            ),
        ),
    ]
