from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gsshop_hr", "0007_alter_facerecognitionprofile_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancechangerequest",
            name="snapshot_captured",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="attendancechangerequest",
            name="had_attendance",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="attendancechangerequest",
            name="original_check_in",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="attendancechangerequest",
            name="original_check_out",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="attendancechangerequest",
            name="original_status",
            field=models.CharField(blank=True, max_length=16),
        ),
        migrations.AddIndex(
            model_name="attendancechangerequest",
            index=models.Index(
                fields=["employee", "attendance_date"],
                name="hr_acr_active_day_idx",
                condition=models.Q(status__in=["draft", "submitted", "approved"]),
            ),
        ),
    ]
