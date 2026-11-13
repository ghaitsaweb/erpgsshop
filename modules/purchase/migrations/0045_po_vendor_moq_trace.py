from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0044_direct_po_pricing_provenance"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="vendor_moq_warning_message",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="purchaseorderlinepricingprovenance",
            name="applicable_vendor_moq",
            field=models.DecimalField(decimal_places=3, default=0, editable=False, max_digits=18),
        ),
        migrations.AddField(
            model_name="purchaseorderlinepricingprovenance",
            name="is_below_vendor_moq",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="purchaseorderlinepricingprovenance",
            name="moq_warning_message",
            field=models.TextField(blank=True, editable=False),
        ),
    ]
