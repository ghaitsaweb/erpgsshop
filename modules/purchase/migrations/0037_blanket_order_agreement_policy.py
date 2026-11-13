from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0036_purchase_approval_workflow_parity"),
    ]

    operations = [
        migrations.AddField(
            model_name="blanketorder",
            name="agreement_policy",
            field=models.CharField(
                choices=[
                    ("quantity_period", "Quantity and Period"),
                    ("period_only", "Period Only"),
                ],
                default="quantity_period",
                max_length=32,
            ),
        ),
    ]
