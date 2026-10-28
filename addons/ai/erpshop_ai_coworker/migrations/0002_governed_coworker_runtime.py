import uuid

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def backfill_task_company(apps, schema_editor):
    AIAgent = apps.get_model("gsshop_ai", "AIAgent")
    AITask = apps.get_model("gsshop_ai", "AITask")
    database = schema_editor.connection.alias
    company_for_agent = (
        AIAgent.objects.using(database)
        .filter(pk=OuterRef("agent_id"))
        .values("workspace__company_id")[:1]
    )
    AITask.objects.using(database).filter(company_id__isnull=True).update(
        company_id=Subquery(company_for_agent)
    )


class Migration(migrations.Migration):
    dependencies = [
        ("gsshop_ai", "0001_initial"),
        ("organization", "0002_recordorganizationscope"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="aiagent",
            options={
                "ordering": ["workspace", "name"],
                "permissions": [("manage_ai_coworkers", "Can manage AI Coworker profiles")],
            },
        ),
        migrations.AlterModelOptions(
            name="aiconversation",
            options={"ordering": ["-last_activity_at", "-id"]},
        ),
        migrations.AlterModelOptions(
            name="aitask",
            options={"ordering": ["-id"]},
        ),
        migrations.AddField(
            model_name="aiagent",
            name="max_context_records",
            field=models.PositiveSmallIntegerField(
                default=50,
                help_text="Maximum ERP rows included in one read-only task.",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(100),
                ],
            ),
        ),
        migrations.AddField(
            model_name="aiagent",
            name="total_completion_tokens",
            field=models.PositiveBigIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="aiagent",
            name="total_prompt_tokens",
            field=models.PositiveBigIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="aiagent",
            name="total_requests",
            field=models.PositiveBigIntegerField(default=0, editable=False),
        ),
        migrations.AddIndex(
            model_name="aiagent",
            index=models.Index(fields=["workspace", "active", "id"], name="ai_agent_workspace_active"),
        ),
        migrations.AddIndex(
            model_name="aiconversation",
            index=models.Index(
                fields=["agent", "user", "status", "-last_activity_at", "-id"],
                name="ai_conversation_recent",
            ),
        ),
        migrations.AddField(
            model_name="aitask",
            name="branch",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ai_coworker_tasks",
                to="organization.branch",
            ),
        ),
        migrations.AddField(
            model_name="aitask",
            name="cancel_requested",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="aitask",
            name="company",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ai_coworker_tasks",
                to="core.company",
            ),
        ),
        migrations.AddField(
            model_name="aitask",
            name="context_record_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="aitask",
            name="context_truncated",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="aitask",
            name="conversation",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="task",
                to="gsshop_ai.aiconversation",
            ),
        ),
        migrations.AddField(
            model_name="aitask",
            name="entity",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="aitask",
            name="filters",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="aitask",
            name="idempotency_key",
            field=models.UUIDField(blank=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="aitask",
            name="input_tokens",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="aitask",
            name="instruction",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="aitask",
            name="latency_ms",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="aitask",
            name="output_tokens",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="aitask",
            name="requested_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ai_coworker_tasks",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="aitask",
            name="result_text",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="aitask",
            name="selected_fields",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterField(
            model_name="aitask",
            name="task_type",
            field=models.CharField(
                choices=[
                    ("plan", "Create an action plan"),
                    ("summarize", "Summarize ERP records"),
                    ("analyze", "Analyze ERP records"),
                ],
                max_length=80,
            ),
        ),
        migrations.RunPython(backfill_task_company, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="aitask",
            name="company",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ai_coworker_tasks",
                to="core.company",
            ),
        ),
        migrations.AlterField(
            model_name="aitask",
            name="idempotency_key",
            field=models.UUIDField(blank=True, default=uuid.uuid4, editable=False, null=True, unique=True),
        ),
        migrations.AddIndex(
            model_name="aitask",
            index=models.Index(fields=["company", "-id"], name="ai_task_company_recent"),
        ),
        migrations.AddIndex(
            model_name="aitask",
            index=models.Index(fields=["requested_by", "-id"], name="ai_task_user_recent"),
        ),
        migrations.AddIndex(
            model_name="aitask",
            index=models.Index(fields=["agent", "status", "-id"], name="ai_task_agent_status"),
        ),
        migrations.AddIndex(
            model_name="aitask",
            index=models.Index(fields=["status", "id"], name="ai_task_queue_scan"),
        ),
        migrations.CreateModel(
            name="AITaskEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("event", models.CharField(max_length=80)),
                ("details", models.JSONField(blank=True, default=dict)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="ai_coworker_task_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="gsshop_ai.aitask",
                    ),
                ),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.AddIndex(
            model_name="aitaskevent",
            index=models.Index(fields=["task", "id"], name="ai_task_event_stream"),
        ),
    ]
