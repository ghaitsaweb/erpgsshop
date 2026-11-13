from django.db import migrations, models


PRICE_SOURCE_CHOICES = [
    ("pricelist", "Pricelist"),
    ("purchase_request", "Purchase Request"),
    ("manual", "Manual"),
    ("no_match", "No Match"),
]


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0042_blanket_tax_snapshots"),
    ]

    operations = [
        migrations.AlterField(
            model_name="purchaseorderlinepricingprovenance",
            name="price_source",
            field=models.CharField(blank=True, choices=PRICE_SOURCE_CHOICES, max_length=20),
        ),
        migrations.AlterField(
            model_name="purchasequotationline",
            name="price_source",
            field=models.CharField(blank=True, choices=PRICE_SOURCE_CHOICES, max_length=20),
        ),
    ]
