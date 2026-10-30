import uuid
from datetime import UTC, datetime

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
    SUPPORTED_TOOLS = {"erp_read", "web_search", "fetch", "filesystem", "mcp", "sandbox", "erp_write", "message"}
    class Provider(models.TextChoices):
        OPENAI_COMPATIBLE = "openai_compatible", "OpenAI-compatible"
        ANTHROPIC = "anthropic", "Anthropic"
        LOCAL = "local", "Local Model"

    workspace = models.ForeignKey(AIWorkspace, on_delete=models.CASCADE, related_name="agents")
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120, blank=True)
    role = models.CharField(max_length=160)
    provider = models.CharField(max_length=24, choices=Provider.choices, default=Provider.OPENAI_COMPATIBLE)
    model_name = models.CharField(max_length=100, blank=True)
    model_template = models.ForeignKey(
        "AIModelTemplate",
        on_delete=models.PROTECT,
        related_name="agents",
        blank=True,
        null=True,
    )
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="owned_ai_coworkers",
        blank=True,
        null=True,
    )
    run_as_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="run_as_ai_coworkers",
        blank=True,
        null=True,
    )
    system_prompt = models.TextField(blank=True)
    allowed_tools = models.JSONField(default=list, blank=True)
    max_context_records = models.PositiveSmallIntegerField(
        default=50,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text="Maximum ERP rows included in one read-only task.",
    )
    total_prompt_tokens = models.PositiveBigIntegerField(default=0, editable=False)
    total_completion_tokens = models.PositiveBigIntegerField(default=0, editable=False)
    total_cached_tokens = models.PositiveBigIntegerField(default=0, editable=False)
    total_requests = models.PositiveBigIntegerField(default=0, editable=False)
    total_cost = models.DecimalField(max_digits=16, decimal_places=8, default=0, editable=False)
    currency = models.CharField(max_length=3, default="IDR", editable=False)
    max_inbound_queue_size = models.PositiveIntegerField(default=500)
    max_concurrent_dispatches = models.PositiveSmallIntegerField(default=10)
    dream_enabled = models.BooleanField(default=True)
    dream_interval_hours = models.PositiveSmallIntegerField(default=2)
    dream_max_batch_size = models.PositiveSmallIntegerField(default=20)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["workspace", "name"]
        constraints = [
            models.UniqueConstraint(fields=["workspace", "name"], name="unique_ai_agent_per_workspace"),
            models.UniqueConstraint(
                fields=["workspace", "slug"],
                condition=models.Q(slug__gt=""),
                name="unique_ai_agent_slug_per_workspace",
            ),
        ]
        indexes = [models.Index(fields=["workspace", "active", "id"], name="ai_agent_workspace_active")]
        permissions = [("manage_ai_coworkers", "Can manage AI Coworker profiles")]

    def clean(self):
        super().clean()
        if not isinstance(self.allowed_tools, list):
            raise ValidationError({"allowed_tools": "Allowed tools must be a list."})
        normalized = [str(item) for item in self.allowed_tools]
        if len(normalized) != len(set(normalized)) or any(item not in self.SUPPORTED_TOOLS for item in normalized):
            raise ValidationError({"allowed_tools": "One or more configured tools are unsupported."})
        if self.model_template_id and not self.model_template.active:
            raise ValidationError({"model_template": "The selected model template is inactive."})

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


class AICoworkerSchedule(TimeStampedModel):
    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKDAYS = "weekdays", "Weekdays (Monday–Friday)"
        WEEKLY = "weekly", "Weekly"

    agent = models.ForeignKey(AIAgent, on_delete=models.PROTECT, related_name="schedules")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_coworker_schedules")
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="ai_coworker_schedules",
        blank=True,
        null=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ai_coworker_schedules",
    )
    name = models.CharField(max_length=180)
    task_type = models.CharField(max_length=80, choices=(("plan", "Create an action plan"), ("summarize", "Summarize ERP records"), ("analyze", "Analyze ERP records")))
    instruction = models.TextField()
    entity = models.CharField(max_length=80, blank=True)
    selected_fields = models.JSONField(default=list, blank=True)
    filters = models.JSONField(default=list, blank=True)
    frequency = models.CharField(max_length=16, choices=Frequency.choices, default=Frequency.DAILY)
    weekday = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(0), MaxValueValidator(6)],
        help_text="Monday is 0 and Sunday is 6; required only for weekly schedules.",
    )
    run_at = models.TimeField(help_text="Uses the ERP server time zone (Asia/Jakarta).")
    active = models.BooleanField(default=True)
    next_run_at = models.DateTimeField(blank=True, null=True)
    last_run_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["next_run_at", "id"]
        indexes = [models.Index(fields=["active", "next_run_at", "id"], name="ai_schedule_due_scan")]

    def clean(self):
        super().clean()
        errors = {}
        if self.agent_id and self.company_id and self.agent.workspace.company_id != self.company_id:
            errors["agent"] = "Coworker must belong to the schedule company."
        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            errors["branch"] = "Schedule branch must belong to the schedule company."
        if self.task_type in {"summarize", "analyze"}:
            if not self.entity:
                errors["entity"] = "An ERP entity is required for this schedule type."
            elif self.agent_id and "erp_read" not in (self.agent.allowed_tools or []):
                errors["agent"] = "This coworker is not allowed to read ERP context."
        elif self.task_type == "plan" and self.entity:
            errors["entity"] = "Planning schedules do not accept ERP record context."
        if not self.instruction.strip():
            errors["instruction"] = "Instruction is required."
        if not isinstance(self.selected_fields, list) or len(self.selected_fields) > 12:
            errors["selected_fields"] = "Select at most 12 ERP fields."
        if not isinstance(self.filters, list) or len(self.filters) > 20:
            errors["filters"] = "Use at most 20 ERP filters."
        if self.frequency == self.Frequency.WEEKLY and self.weekday is None:
            errors["weekday"] = "Choose a weekday for a weekly schedule."
        elif self.frequency != self.Frequency.WEEKLY and self.weekday is not None:
            errors["weekday"] = "A weekday is only used by weekly schedules."
        if self.active and self.next_run_at is None:
            errors["next_run_at"] = "An active schedule requires its next run time."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name


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
    schedule = models.ForeignKey(
        AICoworkerSchedule,
        on_delete=models.SET_NULL,
        related_name="runs",
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


class AIProvider(TimeStampedModel):
    """Administrator-managed provider endpoint; credentials remain in environment variables."""

    class Backend(models.TextChoices):
        OPENAI_COMPAT = "openai_compat", "OpenAI-compatible"
        ANTHROPIC = "anthropic", "Anthropic"
        AZURE_OPENAI = "azure_openai", "Azure OpenAI"

    name = models.CharField(max_length=100, unique=True)
    display_name = models.CharField(max_length=200)
    backend = models.CharField(max_length=32, choices=Backend.choices, default=Backend.OPENAI_COMPAT)
    api_base_url = models.URLField(blank=True)
    credential_env_var = models.CharField(max_length=128, blank=True)
    extra_headers = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        permissions = [("manage_ai_providers", "Can manage AI providers")]

    def clean(self):
        super().clean()
        errors = {}
        if self.api_base_url and not self.api_base_url.startswith("https://"):
            errors["api_base_url"] = "Provider endpoints must use HTTPS."
        if self.credential_env_var and not self.credential_env_var.replace("_", "").isalnum():
            errors["credential_env_var"] = "Use an environment variable name only."
        if not isinstance(self.extra_headers, dict):
            errors["extra_headers"] = "Extra headers must be a JSON object."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.display_name


class AIModelTemplate(TimeStampedModel):
    class ReasoningEffort(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        ADAPTIVE = "adaptive", "Adaptive"

    name = models.CharField(max_length=160, unique=True)
    description = models.TextField(blank=True)
    provider = models.ForeignKey(AIProvider, on_delete=models.PROTECT, related_name="templates")
    model_name = models.CharField(max_length=160)
    max_output_tokens = models.PositiveIntegerField(default=8192)
    context_window_tokens = models.PositiveIntegerField(default=65536)
    temperature = models.DecimalField(max_digits=3, decimal_places=2, default=0.10)
    reasoning_effort = models.CharField(max_length=16, choices=ReasoningEffort.choices, blank=True)
    max_tool_iterations = models.PositiveSmallIntegerField(default=200)
    max_tool_result_chars = models.PositiveIntegerField(default=16000)
    max_concurrent_interactive = models.PositiveSmallIntegerField(default=5)
    max_concurrent_background = models.PositiveSmallIntegerField(default=2)
    concurrent_queue_timeout_seconds = models.PositiveSmallIntegerField(default=120)
    workspace_retention_days = models.PositiveSmallIntegerField(default=7)
    subagent_max_depth = models.PositiveSmallIntegerField(default=3)
    subagent_max_children_per_parent = models.PositiveSmallIntegerField(default=5)
    subagent_timeout_seconds = models.PositiveSmallIntegerField(default=300)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def clean(self):
        super().clean()
        errors = {}
        if not 64 <= self.max_output_tokens <= 32000:
            errors["max_output_tokens"] = "Choose a response limit from 64 to 32,000 tokens."
        if self.context_window_tokens < self.max_output_tokens:
            errors["context_window_tokens"] = "The context window must cover the response limit."
        if self.temperature < 0 or self.temperature > 2:
            errors["temperature"] = "Temperature must be between 0 and 2."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name


class AIModelPricing(TimeStampedModel):
    """Effective-dated, non-overlapping price tiers in the provider's currency."""

    template = models.ForeignKey(AIModelTemplate, on_delete=models.PROTECT, related_name="pricing_entries")
    min_context_tokens = models.PositiveIntegerField(default=0)
    max_context_tokens = models.PositiveIntegerField(blank=True, null=True)
    input_price_per_mtok = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    output_price_per_mtok = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    cache_read_price_per_mtok = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    cache_write_5m_price_per_mtok = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    cache_write_1h_price_per_mtok = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    currency = models.CharField(max_length=3, default="USD")
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(blank=True, null=True)
    source = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-effective_from", "min_context_tokens"]
        indexes = [models.Index(fields=["template", "effective_from"], name="ai_price_template_time")]

    def clean(self):
        super().clean()
        errors = {}
        if self.max_context_tokens is not None and self.max_context_tokens <= self.min_context_tokens:
            errors["max_context_tokens"] = "The maximum context must be greater than the minimum."
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            errors["effective_to"] = "The end must be after the effective start."
        if self.active and self.template_id and not errors:
            candidate_max = self.max_context_tokens or 10**12
            candidate_end = self.effective_to or datetime.max.replace(tzinfo=UTC)
            for other in AIModelPricing.objects.filter(template_id=self.template_id, active=True).exclude(pk=self.pk):
                other_max = other.max_context_tokens or 10**12
                other_end = other.effective_to or datetime.max.replace(tzinfo=UTC)
                if self.min_context_tokens < other_max and other.min_context_tokens < candidate_max and self.effective_from < other_end and other.effective_from < candidate_end:
                    errors["effective_from"] = "This pricing tier overlaps an active price tier."
                    break
        if errors:
            raise ValidationError(errors)


class AICompanyCredit(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="ai_company_credit")
    credit_balance = models.DecimalField(max_digits=16, decimal_places=8, default=0)
    credit_limit = models.DecimalField(max_digits=16, decimal_places=8, default=0)
    enforce_credits = models.BooleanField(default=False)
    currency = models.CharField(max_length=3, default="USD")
    low_balance_threshold = models.DecimalField(max_digits=16, decimal_places=8, default=0)

    def __str__(self):
        return f"{self.company} · {self.credit_balance} {self.currency}"


class AICreditLedger(TimeStampedModel):
    class EntryType(models.TextChoices):
        GRANT = "grant", "Grant"
        CONSUME = "consume", "Consume"
        REFUND = "refund", "Refund"
        ADJUSTMENT = "adjustment", "Adjustment"
        EXPIRE = "expire", "Expiration"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_credit_ledger_entries")
    entry_type = models.CharField(max_length=16, choices=EntryType.choices)
    amount = models.DecimalField(max_digits=16, decimal_places=8)
    balance_after = models.DecimalField(max_digits=16, decimal_places=8)
    currency = models.CharField(max_length=3)
    description = models.CharField(max_length=500, blank=True)
    reference = models.CharField(max_length=255, blank=True, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    expires_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["company", "-id"], name="ai_credit_company_recent")]


class AIUsageEvent(TimeStampedModel):
    class Trigger(models.TextChoices):
        INTERACTIVE = "interactive", "Interactive"
        CRON = "cron", "Cron"
        HEARTBEAT = "heartbeat", "Heartbeat"
        DREAM = "dream", "Dream"
        SUBAGENT = "subagent", "Subagent"

    agent = models.ForeignKey(AIAgent, on_delete=models.PROTECT, related_name="usage_events")
    task = models.OneToOneField(AITask, on_delete=models.SET_NULL, blank=True, null=True, related_name="usage_event")
    template = models.ForeignKey(AIModelTemplate, on_delete=models.SET_NULL, blank=True, null=True, related_name="usage_events")
    pricing = models.ForeignKey(AIModelPricing, on_delete=models.SET_NULL, blank=True, null=True, related_name="usage_events")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_usage_events")
    provider = models.CharField(max_length=100)
    model_name = models.CharField(max_length=160)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cached_tokens = models.PositiveIntegerField(default=0)
    reasoning_tokens = models.PositiveIntegerField(default=0)
    input_cost = models.DecimalField(max_digits=16, decimal_places=8, default=0)
    output_cost = models.DecimalField(max_digits=16, decimal_places=8, default=0)
    cache_cost = models.DecimalField(max_digits=16, decimal_places=8, default=0)
    total_cost = models.DecimalField(max_digits=16, decimal_places=8, default=0)
    currency = models.CharField(max_length=3, default="USD")
    trigger = models.CharField(max_length=16, choices=Trigger.choices, default=Trigger.INTERACTIVE)
    iterations = models.PositiveSmallIntegerField(default=0)
    finish_reason = models.CharField(max_length=64, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    request_id = models.CharField(max_length=128, unique=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["agent", "-id"], name="ai_usage_agent_recent")]


class AICoworkerParticipant(TimeStampedModel):
    """A durable external-channel identity scoped to exactly one coworker."""

    agent = models.ForeignKey(AIAgent, on_delete=models.CASCADE, related_name="participants")
    channel_type = models.CharField(max_length=32)
    external_user_id = models.CharField(max_length=255)
    external_chat_id = models.CharField(max_length=255)
    external_thread_id = models.CharField(max_length=255, blank=True)
    username = models.CharField(max_length=255, blank=True)
    display_name = models.CharField(max_length=255, blank=True)
    profile = models.TextField(blank=True)
    last_profile_update_at = models.DateTimeField(blank=True, null=True)
    total_prompt_tokens = models.PositiveBigIntegerField(default=0)
    total_completion_tokens = models.PositiveBigIntegerField(default=0)
    total_cached_tokens = models.PositiveBigIntegerField(default=0)
    total_requests = models.PositiveBigIntegerField(default=0)
    total_cost = models.DecimalField(max_digits=16, decimal_places=8, default=0)
    currency = models.CharField(max_length=3, default="IDR")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "channel_type", "external_user_id"],
                name="unique_ai_participant_identity",
            )
        ]
        indexes = [models.Index(fields=["agent", "channel_type", "external_chat_id"], name="ai_participant_chat")]

    def __str__(self):
        return self.display_name or self.username or self.external_user_id


class AIChannelSession(TimeStampedModel):
    agent = models.ForeignKey(AIAgent, on_delete=models.CASCADE, related_name="channel_sessions")
    participant = models.ForeignKey(
        AICoworkerParticipant,
        on_delete=models.SET_NULL,
        related_name="sessions",
        blank=True,
        null=True,
    )
    key = models.CharField(max_length=500, db_index=True)
    channel_type = models.CharField(max_length=32)
    chat_id = models.CharField(max_length=255)
    previous_session = models.ForeignKey(
        "self", on_delete=models.SET_NULL, related_name="continued_sessions", blank=True, null=True
    )
    last_consolidated_cursor = models.PositiveBigIntegerField(default=0)
    runtime_checkpoint = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["agent", "key"], name="unique_ai_channel_session_key")]
        indexes = [models.Index(fields=["agent", "active", "-id"], name="ai_channel_session_recent")]

    def clean(self):
        super().clean()
        if self.participant_id and self.participant.agent_id != self.agent_id:
            raise ValidationError({"participant": "The participant belongs to another coworker."})


class AIChannelMessage(TimeStampedModel):
    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"
        TOOL = "tool", "Tool"
        SYSTEM = "system", "System"

    session = models.ForeignKey(AIChannelSession, on_delete=models.CASCADE, related_name="messages")
    participant = models.ForeignKey(
        AICoworkerParticipant, on_delete=models.SET_NULL, blank=True, null=True, related_name="messages"
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField(blank=True)
    tool_calls = models.JSONField(default=list, blank=True)
    tool_call_id = models.CharField(max_length=255, blank=True)
    media = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["session", "id"], name="ai_channel_message_stream")]

    def clean(self):
        super().clean()
        if self.participant_id and self.participant.agent_id != self.session.agent_id:
            raise ValidationError({"participant": "The participant belongs to another coworker."})


class AICoworkerMemory(TimeStampedModel):
    class MemoryType(models.TextChoices):
        MEMORY = "memory", "Long-term memory"
        AGENTS = "agents", "Agent instructions"

    agent = models.ForeignKey(AIAgent, on_delete=models.CASCADE, related_name="memories")
    participant = models.ForeignKey(
        AICoworkerParticipant, on_delete=models.CASCADE, related_name="memories", blank=True, null=True
    )
    memory_type = models.CharField(max_length=16, choices=MemoryType.choices)
    content = models.TextField()

    class Meta:
        ordering = ["memory_type", "id"]
        constraints = [
            models.UniqueConstraint(fields=["agent", "participant", "memory_type"], name="unique_ai_participant_memory")
        ]

    def clean(self):
        super().clean()
        if self.participant_id and self.participant.agent_id != self.agent_id:
            raise ValidationError({"participant": "The participant belongs to another coworker."})


class AIChannelSessionSummary(TimeStampedModel):
    class SummaryType(models.TextChoices):
        COMPACTION = "compaction", "Compaction"
        HANDOFF = "handoff", "Handoff"
        DREAM = "dream", "Dream"

    agent = models.ForeignKey(AIAgent, on_delete=models.CASCADE, related_name="session_summaries")
    session = models.ForeignKey(AIChannelSession, on_delete=models.CASCADE, related_name="summaries")
    participant = models.ForeignKey(AICoworkerParticipant, on_delete=models.SET_NULL, blank=True, null=True)
    summary_type = models.CharField(max_length=16, choices=SummaryType.choices)
    content = models.TextField()
    from_message_id = models.PositiveBigIntegerField(blank=True, null=True)
    to_message_id = models.PositiveBigIntegerField(blank=True, null=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["session", "-id"], name="ai_session_summary_recent")]

    def clean(self):
        super().clean()
        if self.session_id and self.agent_id and self.session.agent_id != self.agent_id:
            raise ValidationError({"session": "The session belongs to another coworker."})


class AICoworkerSubagent(TimeStampedModel):
    class Role(models.TextChoices):
        READ_ONLY = "read_only", "Read only"
        DELEGATE = "delegate", "Delegate"

    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    agent = models.ForeignKey(AIAgent, on_delete=models.CASCADE, related_name="subagents")
    parent_session = models.ForeignKey(AIChannelSession, on_delete=models.SET_NULL, blank=True, null=True)
    parent_subagent = models.ForeignKey("self", on_delete=models.SET_NULL, blank=True, null=True, related_name="children")
    participant = models.ForeignKey(AICoworkerParticipant, on_delete=models.SET_NULL, blank=True, null=True)
    runtime_id = models.CharField(max_length=128, unique=True)
    description = models.CharField(max_length=255)
    prompt = models.TextField()
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.READ_ONLY)
    tool_whitelist = models.JSONField(default=list, blank=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED)
    depth = models.PositiveSmallIntegerField(default=0)
    iterations = models.PositiveSmallIntegerField(default=0)
    result = models.TextField(blank=True)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    duration_ms = models.PositiveIntegerField(default=0)
    usage = models.JSONField(default=dict, blank=True)
    cost = models.DecimalField(max_digits=16, decimal_places=8, default=0)
    currency = models.CharField(max_length=3, default="IDR")

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["agent", "state", "-id"], name="ai_subagent_queue")]


class AICoworkerCron(TimeStampedModel):
    class ScheduleType(models.TextChoices):
        AT = "at", "One time"
        EVERY = "every", "Interval"
        CRONTAB = "crontab", "Crontab"

    class Delivery(models.TextChoices):
        ALWAYS = "always", "Always deliver"
        AUTO = "auto", "Deliver when actionable"
        NEVER = "never", "Never deliver"

    name = models.CharField(max_length=255)
    agent = models.ForeignKey(AIAgent, on_delete=models.CASCADE, related_name="crons")
    schedule_type = models.CharField(max_length=16, choices=ScheduleType.choices, default=ScheduleType.EVERY)
    at_datetime = models.DateTimeField(blank=True, null=True)
    crontab_expr = models.CharField(max_length=80, blank=True)
    interval_seconds = models.PositiveIntegerField(blank=True, null=True)
    timezone_name = models.CharField(max_length=64, default="Asia/Jakarta")
    prompt = models.TextField()
    deliver = models.CharField(max_length=16, choices=Delivery.choices, default=Delivery.AUTO)
    delete_after_run = models.BooleanField(default=False)
    target_channel_type = models.CharField(max_length=32, blank=True)
    target_chat_id = models.CharField(max_length=255, blank=True)
    target_thread_id = models.CharField(max_length=255, blank=True)
    target_participant = models.ForeignKey(AICoworkerParticipant, on_delete=models.SET_NULL, blank=True, null=True)
    target_is_group = models.BooleanField(default=False)
    target_mentions = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(blank=True, null=True)
    run_count = models.PositiveIntegerField(default=0)
    last_run_window_key = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["name", "id"]
        indexes = [models.Index(fields=["active", "schedule_type", "last_run_at"], name="ai_cron_due_scan")]

    def clean(self):
        super().clean()
        errors = {}
        if self.schedule_type == self.ScheduleType.AT and self.at_datetime is None:
            errors["at_datetime"] = "A one-time schedule requires its run time."
        if self.schedule_type == self.ScheduleType.EVERY and not self.interval_seconds:
            errors["interval_seconds"] = "An interval schedule requires seconds."
        if self.schedule_type == self.ScheduleType.CRONTAB and len(self.crontab_expr.split()) != 5:
            errors["crontab_expr"] = "Use a five-field crontab expression."
        if self.target_participant_id and self.target_participant.agent_id != self.agent_id:
            errors["target_participant"] = "The participant belongs to another coworker."
        if errors:
            raise ValidationError(errors)


class AICoworkerHeartbeat(TimeStampedModel):
    class NotifyPolicy(models.TextChoices):
        ALWAYS = "always", "Always"
        ON_ACTION = "on_action", "On action"
        ON_ERROR = "on_error", "On error"

    class LastStatus(models.TextChoices):
        IDLE = "idle", "Idle"
        SKIPPED = "skipped", "Skipped"
        EXECUTED = "executed", "Executed"
        ERROR = "error", "Error"

    name = models.CharField(max_length=255)
    agent = models.ForeignKey(AIAgent, on_delete=models.CASCADE, related_name="heartbeats")
    instruction = models.TextField()
    target_channel_type = models.CharField(max_length=32, blank=True)
    target_chat_id = models.CharField(max_length=255, blank=True)
    target_thread_id = models.CharField(max_length=255, blank=True)
    target_participant = models.ForeignKey(AICoworkerParticipant, on_delete=models.SET_NULL, blank=True, null=True)
    target_is_group = models.BooleanField(default=False)
    target_mentions = models.JSONField(default=list, blank=True)
    interval_seconds = models.PositiveIntegerField(default=300)
    notify_policy = models.CharField(max_length=16, choices=NotifyPolicy.choices, default=NotifyPolicy.ON_ACTION)
    enabled = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(blank=True, null=True)
    last_status = models.CharField(max_length=16, choices=LastStatus.choices, default=LastStatus.IDLE)
    last_summary = models.TextField(blank=True)

    class Meta:
        ordering = ["agent", "name", "id"]
        indexes = [models.Index(fields=["enabled", "last_run_at"], name="ai_heartbeat_due_scan")]

    def clean(self):
        super().clean()
        if self.target_participant_id and self.target_participant.agent_id != self.agent_id:
            raise ValidationError({"target_participant": "The participant belongs to another coworker."})


class AIChannelConfiguration(TimeStampedModel):
    class ChannelType(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        WHATSAPP_CLOUD = "whatsapp_cloud", "WhatsApp Cloud"
        WHATSAPP_WEB = "whatsapp_web", "WhatsApp Web"

    class WebSessionStatus(models.TextChoices):
        UNPAIRED = "unpaired", "Unpaired"
        PAIRING = "pairing", "Pairing"
        CONNECTED = "connected", "Connected"
        ERROR = "error", "Error"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_channel_configurations")
    name = models.CharField(max_length=180)
    owner_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    channel_type = models.CharField(max_length=32, choices=ChannelType.choices)
    credential_env_var = models.CharField(max_length=128, blank=True)
    app_secret_env_var = models.CharField(max_length=128, blank=True)
    config = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=False)
    web_session_status = models.CharField(max_length=16, choices=WebSessionStatus.choices, default=WebSessionStatus.UNPAIRED)
    web_session_error = models.TextField(blank=True)
    web_phone_id = models.CharField(max_length=64, blank=True)
    web_force_reauth_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["company", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_ai_channel_config_name")]
        indexes = [models.Index(fields=["company", "enabled", "channel_type"], name="ai_channel_active_type")]

    def clean(self):
        super().clean()
        errors = {}
        if self.enabled and self.channel_type != self.ChannelType.WHATSAPP_WEB and not self.credential_env_var:
            errors["credential_env_var"] = "An environment credential name is required for an enabled channel."
        if not isinstance(self.config, dict):
            errors["config"] = "Channel configuration must be a JSON object."
        for field in ("credential_env_var", "app_secret_env_var"):
            value = getattr(self, field)
            if value and not value.replace("_", "").isalnum():
                errors[field] = "Use an environment variable name only."
        if errors:
            raise ValidationError(errors)


class AICoworkerChannel(TimeStampedModel):
    agent = models.ForeignKey(AIAgent, on_delete=models.CASCADE, related_name="channel_links")
    channel = models.OneToOneField(AIChannelConfiguration, on_delete=models.CASCADE, related_name="coworker_link")
    is_primary = models.BooleanField(default=True)
    sequence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sequence", "id"]

    def clean(self):
        super().clean()
        if self.agent_id and self.channel_id and self.agent.workspace.company_id != self.channel.company_id:
            raise ValidationError({"channel": "The channel must belong to the coworker company."})
        if self.agent_id and self.channel_id and self.channel.owner_user_id and self.agent.owner_user_id:
            if self.channel.owner_user_id != self.agent.owner_user_id:
                raise ValidationError({"channel": "A channel may only be linked to its coworker owner's profile."})


class AIChannelInboundEvent(TimeStampedModel):
    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        DELIVERED = "delivered", "Delivered"
        ERRORED = "errored", "Errored"
        IGNORED = "ignored", "Ignored"

    channel = models.ForeignKey(AIChannelConfiguration, on_delete=models.CASCADE, related_name="inbound_events")
    agent = models.ForeignKey(AIAgent, on_delete=models.CASCADE, blank=True, null=True, related_name="channel_inbound_events")
    external_message_id = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(default=timezone.now)
    dispatch_state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    delivered_at = models.DateTimeField(blank=True, null=True)
    error = models.TextField(blank=True)
    remote_ip = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-received_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "external_message_id"],
                condition=models.Q(external_message_id__gt=""),
                name="unique_ai_channel_message_id",
            )
        ]
        indexes = [models.Index(fields=["dispatch_state", "received_at"], name="ai_inbound_dispatch_scan")]


class AIWebhookEndpoint(TimeStampedModel):
    agent = models.ForeignKey(AIAgent, on_delete=models.CASCADE, related_name="webhook_endpoints")
    name = models.CharField(max_length=180)
    token_hash = models.CharField(max_length=128, unique=True)
    hmac_secret_env_var = models.CharField(max_length=128, blank=True)
    hmac_header = models.CharField(max_length=100, default="X-Hub-Signature-256")
    allowed_ips = models.CharField(max_length=500, blank=True)
    rate_limit_per_minute = models.PositiveSmallIntegerField(default=60)
    max_body_bytes = models.PositiveIntegerField(default=65536)
    active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    total_events = models.PositiveBigIntegerField(default=0)
    last_event_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["agent", "name"]
        constraints = [models.UniqueConstraint(fields=["agent", "name"], name="unique_ai_webhook_endpoint_name")]

    def clean(self):
        super().clean()
        if self.hmac_secret_env_var and not self.hmac_secret_env_var.replace("_", "").isalnum():
            raise ValidationError({"hmac_secret_env_var": "Use an environment variable name only."})


class AIWebhookEvent(TimeStampedModel):
    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"
        REJECTED = "rejected", "Rejected"

    endpoint = models.ForeignKey(AIWebhookEndpoint, on_delete=models.CASCADE, related_name="events")
    received_at = models.DateTimeField(default=timezone.now)
    method = models.CharField(max_length=10, default="POST")
    path = models.CharField(max_length=500, blank=True)
    query = models.TextField(blank=True)
    headers = models.JSONField(default=dict, blank=True)
    body = models.TextField(blank=True)
    remote_ip = models.CharField(max_length=64, blank=True)
    response_status = models.PositiveSmallIntegerField(blank=True, null=True)
    response_body = models.TextField(blank=True)
    handler_duration_ms = models.PositiveIntegerField(default=0)
    dispatch_state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-received_at", "-id"]
        indexes = [models.Index(fields=["endpoint", "-id"], name="ai_webhook_event_recent")]


class AIToolApproval(TimeStampedModel):
    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"
        EXECUTED = "executed", "Executed"

    agent = models.ForeignKey(AIAgent, on_delete=models.PROTECT, related_name="tool_approvals")
    task = models.ForeignKey(AITask, on_delete=models.CASCADE, related_name="tool_approvals")
    tool_name = models.CharField(max_length=64)
    request_digest = models.CharField(max_length=64)
    request_preview = models.JSONField(default=dict)
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    decided_at = models.DateTimeField(blank=True, null=True)
    expires_at = models.DateTimeField()
    result = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["state", "expires_at"], name="ai_tool_approval_due")]
        constraints = [models.UniqueConstraint(fields=["task", "tool_name", "request_digest"], name="unique_ai_tool_approval_request")]

    def clean(self):
        super().clean()
        if self.agent_id and self.task_id and self.agent_id != self.task.agent_id:
            raise ValidationError({"task": "The approval task belongs to another coworker."})
