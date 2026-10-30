from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("gsshop_ai", "0002_governed_coworker_runtime"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AICoworkerSchedule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=180)),
                ("task_type", models.CharField(choices=[("plan", "Create an action plan"), ("summarize", "Summarize ERP records"), ("analyze", "Analyze ERP records")], max_length=80)),
                ("instruction", models.TextField()),
                ("entity", models.CharField(blank=True, max_length=80)),
                ("selected_fields", models.JSONField(blank=True, default=list)),
                ("filters", models.JSONField(blank=True, default=list)),
                ("frequency", models.CharField(choices=[("daily", "Daily"), ("weekdays", "Weekdays (Monday–Friday)"), ("weekly", "Weekly")], default="daily", max_length=16)),
                ("weekday", models.PositiveSmallIntegerField(blank=True, help_text="Monday is 0 and Sunday is 6; required only for weekly schedules.", null=True, validators=[MinValueValidator(0), MaxValueValidator(6)])),
                ("run_at", models.TimeField(help_text="Uses the ERP server time zone (Asia/Jakarta).")),
                ("active", models.BooleanField(default=True)),
                ("next_run_at", models.DateTimeField(blank=True, null=True)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("agent", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="schedules", to="gsshop_ai.aiagent")),
                ("branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ai_coworker_schedules", to="organization.branch")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="ai_coworker_schedules", to="core.company")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="ai_coworker_schedules", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["next_run_at", "id"]},
        ),
        migrations.AddIndex(
            model_name="aicoworkerschedule",
            index=models.Index(fields=["active", "next_run_at", "id"], name="ai_schedule_due_scan"),
        ),
        migrations.AddField(
            model_name="aitask",
            name="schedule",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="runs", to="gsshop_ai.aicoworkerschedule"),
        ),
    ]
