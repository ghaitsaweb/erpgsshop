from django.db import migrations, models


STRUCTURED_COST_CENTER_MODELS = (
    "blanketorder",
    "blanketorderline",
    "purchasejournallineanalyticsnapshot",
    "purchaseordercontrol",
    "purchaseorderlinepricingprovenance",
    "purchaserequest",
    "purchaserequestline",
    "purchasetender",
    "purchasetenderline",
)


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0053_purchase_journal_analytic_snapshots"),
    ]

    operations = [
        migrations.AlterField(
            model_name=model_name,
            name="cost_center_mode",
            field=models.CharField(
                blank=True,
                choices=[
                    ("single", "Single Cost Center"),
                    ("distribution", "Distribution"),
                ],
                default="single",
                max_length=16,
            ),
        )
        for model_name in STRUCTURED_COST_CENTER_MODELS
    ]
