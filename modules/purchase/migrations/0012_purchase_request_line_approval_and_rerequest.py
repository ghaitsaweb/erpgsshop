from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("gsshop_purchase", "0011_blanket_rfq_lifecycle_and_direct_bill_setting"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaserequestline",
            name="approval_action",
            field=models.CharField(
                blank=True,
                choices=[("approve", "Approve"), ("reject", "Reject")],
                editable=False,
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="purchaserequestline",
            name="origin_request_line",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="re_requested_lines",
                to="gsshop_purchase.purchaserequestline",
            ),
        ),
        migrations.AddField(
            model_name="purchaserequestline",
            name="re_request_quantity",
            field=models.DecimalField(decimal_places=3, default=0, editable=False, max_digits=18),
        ),
    ]
