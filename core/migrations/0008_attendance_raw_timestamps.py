from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0007_warehouse_branch")]

    operations = [
        migrations.AddField(
            model_name="attendance", name="check_in_at", field=models.DateTimeField(blank=True, null=True)
        ),
        migrations.AddField(
            model_name="attendance", name="check_out_at", field=models.DateTimeField(blank=True, null=True)
        ),
        migrations.AddField(
            model_name="attendance", name="from_raw_attendance", field=models.BooleanField(default=False)
        ),
    ]
