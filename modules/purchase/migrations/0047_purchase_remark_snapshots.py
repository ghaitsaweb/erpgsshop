from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0046_purchase_rental_controls"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaserequest",
            name="remark_template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="purchase_requests",
                to="gsshop_purchase.purchaseremarktemplate",
            ),
        ),
        migrations.AddField(
            model_name="purchaserequest",
            name="remark_template_code",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="purchaserequest",
            name="remark_template_name",
            field=models.CharField(blank=True, editable=False, max_length=255),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="remark_template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="purchase_orders",
                to="gsshop_purchase.purchaseremarktemplate",
            ),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="remark_template_code",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="remark_template_name",
            field=models.CharField(blank=True, editable=False, max_length=255),
        ),
    ]
