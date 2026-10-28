import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import Company, TimeStampedModel
from modules.organization.models import Branch


class AIWorkspace(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_workspaces")
    name = models.CharField(max_length=140)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_ai_workspace_per_company")]

    def __str__(self):
        return self.name


class AIAgent(TimeStampedModel):
    class Provider(models.TextChoices):
        OPENAI_COMPATIBLE = "openai_compatible", "OpenAI-compatible"
        ANTHROPIC = "anthropic", "Anthropic"
        LOCAL = "local", "Local Model"

    workspace = models.ForeignKey(AIWorkspace, on_delete=models.CASCADE, related_name="agents")
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=160)
    provider = models.CharField(max_length=24, choices=Provider.choices, default=Provider.OPENAI_COMPATIBLE)
    model_name = models.CharField(max_length=100, blank=True)
    system_prompt = models.TextField(blank=True)
    allowed_tools = models.JSONField(default=list, blank=True)
    max_context_records = models.PositiveSmallIntegerField(
        default=50,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text="Maximum ERP rows included in one read-only task.",
    )
    total_prompt_tokens = models.PositiveBigIntegerField(default=0, editable=False)
    total_completion_tokens = models.PositiveBigIntegerField(default=0, editable=False)
    total_requests = models.PositiveBigIntegerField(default=0, editable=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["workspace", "name"]
        constraints = [models.UniqueConstraint(fields=["workspace", "name"], name="unique_ai_agent_per_workspace")]
        indexes = [models.Index(fields=["workspace", "active", "id"], name="ai_agent_workspace_active")]
        permissions = [("manage_ai_coworkers", "Can manage AI Coworker profiles")]

    def clean(self):
        super().clean()
        if not isinstance(self.allowed_tools, list):
            raise ValidationError({"allowed_tools": "Allowed tools must be a list."})
        normalized = [str(item) for item in self.allowed_tools]
        if len(normalized) != len(set(normalized)) or any(item not in {"erp_read"} for item in normalized):
            raise ValidationError({"allowed_tools": "Only the governed erp_read tool is available."})

    def __str__(self):
        return self.name


class AIConversation(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    agent = models.ForeignKey(AIAgent, on_delete=models.PROTECT, related_name="conversations")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ai_conversations")
    title = models.CharField(max_length=180)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    last_activity_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-last_activity_at", "-id"]
        indexes = [
            models.Index(fields=["agent", "user", "status", "-last_activity_at", "-id"], name="ai_conversation_recent")
        ]

    def __str__(self):
        return self.title


class AIMessage(TimeStampedModel):
    class Role(models.TextChoices):
        SYSTEM = "system", "System"
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        TOOL = "tool", "Tool"

    conversation = models.ForeignKey(AIConversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.conversation} · {self.role}"


class AITask(TimeStampedModel):
    class TaskType(models.TextChoices):
        PLAN = "plan", "Create an action plan"
        SUMMARIZE = "summarize", "Summarize ERP records"
        ANALYZE = "analyze", "Analyze ERP records"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    agent = models.ForeignKey(AIAgent, on_delete=models.PROTECT, related_name="tasks")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_coworker_tasks")
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="ai_coworker_tasks",
        blank=True,
        null=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ai_coworker_tasks",
        blank=True,
        null=True,
    )
    conversation = models.OneToOneField(
        AIConversation,
        on_delete=models.SET_NULL,
        related_name="task",
        blank=True,
        null=True,
    )
    title = models.CharField(max_length=180)
    task_type = models.CharField(max_length=80, choices=TaskType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    instruction = models.TextField(blank=True)
    entity = models.CharField(max_length=80, blank=True)
    selected_fields = models.JSONField(default=list, blank=True)
    filters = models.JSONField(default=list, blank=True)
    input_data = models.JSONField(default=dict, blank=True)
    result_data = models.JSONField(default=dict, blank=True)
    result_text = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    cancel_requested = models.BooleanField(default=False)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    context_record_count = models.PositiveSmallIntegerField(default=0)
    context_truncated = models.BooleanField(default=False)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, blank=True, null=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["company", "-id"], name="ai_task_company_recent"),
            models.Index(fields=["requested_by", "-id"], name="ai_task_user_recent"),
            models.Index(fields=["agent", "status", "-id"], name="ai_task_agent_status"),
            models.Index(fields=["status", "id"], name="ai_task_queue_scan"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.agent_id and self.company_id and self.agent.workspace.company_id != self.company_id:
            errors["agent"] = "Coworker must belong to the task company."
        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            errors["branch"] = "Task branch must belong to the task company."
        if self.task_type in {self.TaskType.SUMMARIZE, self.TaskType.ANALYZE} and not self.entity:
            errors["entity"] = "An ERP entity is required for this task type."
        if self.task_type == self.TaskType.PLAN and self.entity:
            errors["entity"] = "Planning tasks do not accept ERP record context."
        if not isinstance(self.selected_fields, list) or len(self.selected_fields) > 12:
            errors["selected_fields"] = "Select at most 12 ERP fields."
        if not isinstance(self.filters, list) or len(self.filters) > 20:
            errors["filters"] = "Use at most 20 ERP filters."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.title


class AITaskEvent(TimeStampedModel):
    task = models.ForeignKey(AITask, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="ai_coworker_task_events",
        blank=True,
        null=True,
    )
    event = models.CharField(max_length=80)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["task", "id"], name="ai_task_event_stream")]

    def __str__(self):
        return f"{self.task_id} · {self.event}"
