# Generated for ERP GSSHOP database lifecycle management.
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ManagedDatabase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=63, unique=True)),
                ("label", models.CharField(max_length=120)),
                ("is_primary", models.BooleanField(default=False)),
                ("is_protected", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ("name",)},
        ),
        migrations.CreateModel(
            name="DatabaseOperation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("task_id", models.CharField(blank=True, max_length=64)),
                ("kind", models.CharField(choices=[("create", "Create database"), ("backup", "Create backup"), ("restore", "Restore backup"), ("duplicate", "Duplicate database"), ("drop", "Drop database"), ("upgrade", "Upgrade schema"), ("protect", "Protect database"), ("unprotect", "Remove protection"), ("delete_backup", "Delete backup")], max_length=24)),
                ("database_name", models.CharField(blank=True, max_length=63)),
                ("target_name", models.CharField(blank=True, max_length=63)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("running", "Running"), ("success", "Successful"), ("failed", "Failed")], default="pending", max_length=16)),
                ("progress", models.PositiveSmallIntegerField(default=0)),
                ("message", models.CharField(blank=True, max_length=500)),
                ("requested_by", models.CharField(max_length=150)),
                ("client_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.AddIndex(
            model_name="databaseoperation",
            index=models.Index(fields=["status", "created_at"], name="dbop_status_created_idx"),
        ),
    ]
