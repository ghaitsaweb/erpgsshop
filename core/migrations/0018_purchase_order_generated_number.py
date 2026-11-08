from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_purchase_receiving_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="purchaseorder",
            name="number",
            field=models.CharField(blank=True, max_length=40),
        ),
    ]
