from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_attendance_raw_timestamps"),
        ("gsshop_hr", "0009_raw_attendance_day_lookup"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancechangerequest",
            name="original_check_in_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="attendancechangerequest",
            name="original_check_out_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="attendancechangerequest",
            name="original_from_raw_attendance",
            field=models.BooleanField(default=False),
        ),
    ]
