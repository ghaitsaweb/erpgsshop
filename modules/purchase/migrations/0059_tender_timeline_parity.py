from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0019_company_logo"),
        ("gsshop_purchase", "0058_vendor_transaction_history_parity"),
    ]

    operations = [
        migrations.AlterField(
            model_name="purchasetender",
            name="close_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="purchasetender",
            name="expiry_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="purchasetenderline",
            name="destination_warehouse",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_tender_lines",
                to="core.warehouse",
            ),
        ),
        migrations.AddField(
            model_name="purchasetenderline",
            name="expiry_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
