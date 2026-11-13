from django.db import migrations, models


PRIMARY_COST_CENTER_MODELS = (
    "blanketorder",
    "blanketorderline",
    "purchaserequest",
    "purchaserequestline",
    "purchasetender",
    "purchasetenderline",
)

STRUCTURED_COST_CENTER_MODELS = (
    "blanketorder",
    "blanketorderline",
    "purchaseordercontrol",
    "purchaseorderlinepricingprovenance",
    "purchaserequest",
    "purchaserequestline",
    "purchasetender",
    "purchasetenderline",
)


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0051_purchase_commercial_term_snapshots"),
    ]

    operations = [
        *[
            migrations.AddField(
                model_name=model_name,
                name="cost_center",
                field=models.CharField(blank=True, max_length=80),
            )
            for model_name in PRIMARY_COST_CENTER_MODELS
        ],
        *[
            operation
            for model_name in STRUCTURED_COST_CENTER_MODELS
            for operation in (
                migrations.AddField(
                    model_name=model_name,
                    name="cost_center_analytic_tag_ids",
                    field=models.JSONField(blank=True, default=list),
                ),
                migrations.AddField(
                    model_name=model_name,
                    name="cost_center_distribution_data",
                    field=models.JSONField(blank=True, default=list),
                ),
                migrations.AddField(
                    model_name=model_name,
                    name="cost_center_distribution_ids",
                    field=models.JSONField(blank=True, default=list),
                ),
                migrations.AddField(
                    model_name=model_name,
                    name="cost_center_mode",
                    field=models.CharField(
                        choices=[
                            ("single", "Single Cost Center"),
                            ("distribution", "Distribution"),
                        ],
                        default="single",
                        max_length=16,
                    ),
                ),
            )
        ],
    ]
