from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0043_rfq_purchase_request_price_source"),
    ]

    operations = [
        migrations.AlterField(
            model_name="purchaseorderlinepricingprovenance",
            name="quotation_line",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_order_pricing_provenance",
                to="gsshop_purchase.purchasequotationline",
            ),
        ),
    ]
