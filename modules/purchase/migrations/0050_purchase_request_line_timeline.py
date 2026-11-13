from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_purchase_order_generated_number"),
        ("gsshop_purchase", "0049_purchase_order_line_expiry"),
    ]

    operations = [
        migrations.AlterField(
            model_name="purchaserequest",
            name="required_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="purchaserequest",
            name="destination_warehouse",
            field=models.ForeignKey(
                blank=True,
                help_text="Default destination warehouse carried into RFQs and purchase orders.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_requests",
                to="core.warehouse",
            ),
        ),
        migrations.AddField(
            model_name="purchaserequestline",
            name="expected_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="purchaserequestline",
            name="destination_warehouse",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_request_lines",
                to="core.warehouse",
            ),
        ),
    ]
