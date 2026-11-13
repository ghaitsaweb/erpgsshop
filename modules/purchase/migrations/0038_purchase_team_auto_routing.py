import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0037_blanket_order_agreement_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaserequest",
            name="team",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="requests",
                to="gsshop_purchase.purchaseteam",
            ),
        ),
    ]
