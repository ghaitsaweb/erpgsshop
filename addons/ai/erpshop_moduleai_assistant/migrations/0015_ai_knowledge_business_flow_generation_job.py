import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("erpshop_moduleai_assistant", "0014_ai_knowledge_mapping_branch_scope")]

    operations = [
        migrations.CreateModel(
            name="AIKnowledgeBusinessFlowGenerationJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("job_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("overwrite_manual", models.BooleanField(default=False)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="queued",
                        max_length=16,
                    ),
                ),
                ("input_snapshot", models.JSONField(blank=True, default=dict)),
                ("error_reason", models.CharField(blank=True, max_length=500)),
                ("input_tokens", models.PositiveIntegerField(default=0)),
                ("output_tokens", models.PositiveIntegerField(default=0)),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                ("provider_message_id", models.CharField(blank=True, max_length=180)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ai_knowledge_flow_generation_jobs",
                        to="core.company",
                    ),
                ),
                (
                    "configuration",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="knowledge_flow_generation_jobs",
                        to="erpshop_moduleai_assistant.assistantconfiguration",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ai_knowledge_flow_generation_jobs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "result_flow",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="generation_jobs",
                        to="erpshop_moduleai_assistant.aiknowledgebusinessflow",
                    ),
                ),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="aiknowledgebusinessflowgenerationjob",
            index=models.Index(fields=["company", "state", "-created_at"], name="ai_flow_generation_queue"),
        ),
    ]
