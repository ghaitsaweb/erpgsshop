from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gsshop_hr", "0008_attendance_change_snapshot")]

    operations = [
        migrations.AddIndex(
            model_name="rawattendance",
            index=models.Index(fields=["employee", "captured_at"], name="hr_raw_emp_captured_idx"),
        ),
        migrations.AddIndex(
            model_name="scheduleassignment",
            index=models.Index(fields=["employee", "-date_start"], name="hr_assignment_emp_start_idx"),
        ),
    ]
