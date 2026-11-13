from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0050_purchase_request_line_timeline"),
    ]

    operations = [
        migrations.AddField(
            model_name="vendorprofile",
            name="payment_terms",
            field=models.TextField(
                blank=True,
                help_text="Default payment terms copied to new Purchase Orders when enabled in Purchase Settings.",
            ),
        ),
        migrations.AddField(
            model_name="purchasesetting",
            name="is_use_vendor_payment_terms",
            field=models.BooleanField(
                default=True,
                verbose_name="Use Vendor Default Payment Terms",
            ),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="payment_terms",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="delivery_terms",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="legal_terms",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="vendor_address",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="bill_address",
            field=models.TextField(blank=True),
        ),
    ]
