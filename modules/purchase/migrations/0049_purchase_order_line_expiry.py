from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0048_vendor_category_description"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseorderlinepricingprovenance",
            name="expiry_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
