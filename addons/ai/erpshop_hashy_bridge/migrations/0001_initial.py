from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="BridgeAuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("endpoint", models.CharField(max_length=64)),
                ("run_id", models.CharField(blank=True, max_length=128)),
                ("ok", models.BooleanField(default=False)),
                ("http_status", models.PositiveSmallIntegerField()),
                ("duration_ms", models.PositiveIntegerField(default=0)),
                ("result_count", models.PositiveIntegerField(default=0)),
                ("payload_digest", models.CharField(blank=True, max_length=64)),
                ("error_code", models.CharField(blank=True, max_length=64)),
                ("error_message", models.CharField(blank=True, max_length=300)),
                ("company", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="hashy_bridge_audit_logs", to="core.company")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="hashy_bridge_audit_logs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-pk"],
                "indexes": [
                    models.Index(fields=["company", "-id"], name="hashy_audit_company_recent"),
                    models.Index(fields=["user", "-id"], name="hashy_audit_user_recent"),
                    models.Index(fields=["endpoint", "-id"], name="hashy_audit_endpoint_recent"),
                    models.Index(fields=["run_id", "-id"], name="hashy_audit_run_recent"),
                ],
            },
        ),
    ]
