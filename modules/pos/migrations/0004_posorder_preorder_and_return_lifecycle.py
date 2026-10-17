from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("gsshop_pos", "0003_posbackupdata_posbackupdataline_posinventorytransfer_and_more")]

    operations = [
        migrations.AddField(
            model_name="posorder",
            name="fulfillment_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="posorder",
            name="is_preorder",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="posorder",
            name="preorder_status",
            field=models.CharField(blank=True, choices=[("ordered", "Ordered"), ("ready", "Ready"), ("completed", "Completed"), ("cancelled", "Cancelled")], default="ordered", max_length=20),
        ),
        migrations.AddField(
            model_name="posorder",
            name="ready_date",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="posorder",
            name="return_state",
            field=models.CharField(blank=True, choices=[("partially_exchanged", "Partially Exchanged"), ("partially_returned", "Partially Returned"), ("voided", "Voided"), ("returned", "Returned"), ("exchanged", "Exchanged")], max_length=20),
        ),
        migrations.AddField(
            model_name="posorder",
            name="return_type",
            field=models.CharField(blank=True, choices=[("void", "Void"), ("return", "Return"), ("exchange", "Exchange")], max_length=20),
        ),
        migrations.AddField(
            model_name="posorder",
            name="source_order",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="return_orders", to="gsshop_pos.posorder"),
        ),
    ]
