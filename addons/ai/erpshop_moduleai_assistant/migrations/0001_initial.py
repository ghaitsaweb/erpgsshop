# Generated manually for the standalone ERPShop Module AI assistant addon.

import addons.ai.erpshop_moduleai_assistant.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0010_employee_birth_date_employee_blood_type_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="AssistantConfiguration",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("display_name", models.CharField(default="Hashy AI Assistant", max_length=120)),
                ("provider", models.CharField(choices=[("disabled", "Disabled"), ("hashy_os", "Hashy OS gateway"), ("openai_compatible", "OpenAI-compatible gateway")], default="disabled", max_length=32)),
                ("api_base_url", models.URLField(blank=True, help_text="HTTPS base URL, for example https://provider.example/v1")),
                ("credential_env_var", models.CharField(blank=True, help_text="Name only; its secret value is read from the service environment.", max_length=128)),
                ("model_name", models.CharField(blank=True, max_length=160)),
                ("system_prompt", models.TextField(blank=True)),
                ("max_output_tokens", models.PositiveIntegerField(default=1200)),
                ("temperature", models.DecimalField(decimal_places=2, default=0.2, max_digits=3)),
                ("active", models.BooleanField(default=False)),
                ("allow_erp_context", models.BooleanField(default=True)),
                ("allow_file_content", models.BooleanField(default=False)),
                ("company", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="hashy_assistant_configuration", to="core.company")),
            ],
            options={"ordering": ["company__name"], "permissions": [("manage_assistant_configuration", "Can manage Hashy Assistant configuration")]},
        ),
        migrations.CreateModel(
            name="HashyOSConnection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("account_email", models.EmailField(blank=True, max_length=254)),
                ("workspace_identifier", models.CharField(blank=True, max_length=160)),
                ("state", models.CharField(choices=[("disconnected", "Disconnected"), ("pending", "Pending authorization"), ("connected", "Connected"), ("error", "Connection error")], default="disconnected", max_length=16)),
                ("status_detail", models.CharField(blank=True, max_length=300)),
                ("last_verified_at", models.DateTimeField(blank=True, null=True)),
                ("company", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="hashy_os_connection", to="core.company")),
            ],
            options={"ordering": ["company__name"]},
        ),
        migrations.CreateModel(
            name="AssistantSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(default="New conversation", max_length=180)),
                ("status", models.CharField(choices=[("active", "Active"), ("archived", "Archived")], default="active", max_length=16)),
                ("page_context", models.JSONField(blank=True, default=dict)),
                ("last_activity_at", models.DateTimeField(default=timezone.now)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="hashy_assistant_sessions", to="core.company")),
                ("configuration", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sessions", to="erpshop_moduleai_assistant.assistantconfiguration")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="hashy_assistant_sessions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-last_activity_at", "-pk"]},
        ),
        migrations.CreateModel(
            name="AssistantMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("role", models.CharField(choices=[("user", "You"), ("assistant", "Hashy"), ("system", "System")], max_length=16)),
                ("content", models.TextField()),
                ("state", models.CharField(choices=[("completed", "Completed"), ("pending", "Waiting for provider"), ("failed", "Failed")], default="completed", max_length=16)),
                ("input_tokens", models.PositiveIntegerField(default=0)),
                ("output_tokens", models.PositiveIntegerField(default=0)),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                ("provider_message_id", models.CharField(blank=True, max_length=180)),
                ("error_message", models.CharField(blank=True, max_length=500)),
                ("feedback", models.CharField(blank=True, choices=[("helpful", "Helpful"), ("unhelpful", "Unhelpful")], max_length=16)),
                ("feedback_note", models.TextField(blank=True)),
                ("feedback_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="erpshop_moduleai_assistant.assistantsession")),
            ],
            options={"ordering": ["created_at", "pk"]},
        ),
        migrations.CreateModel(
            name="AssistantAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("file", models.FileField(upload_to=addons.ai.erpshop_moduleai_assistant.models.assistant_attachment_path)),
                ("original_name", models.CharField(max_length=255)),
                ("content_type", models.CharField(blank=True, max_length=120)),
                ("byte_size", models.PositiveBigIntegerField(default=0)),
                ("extracted_text", models.TextField(blank=True)),
                ("message", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="erpshop_moduleai_assistant.assistantmessage")),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="erpshop_moduleai_assistant.assistantsession")),
            ],
            options={"ordering": ["created_at", "pk"]},
        ),
        migrations.CreateModel(
            name="AssistantAuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("event", models.CharField(max_length=80)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="hashy_assistant_events", to=settings.AUTH_USER_MODEL)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audit_events", to="erpshop_moduleai_assistant.assistantsession")),
            ],
            options={"ordering": ["-created_at", "-pk"]},
        ),
    ]
