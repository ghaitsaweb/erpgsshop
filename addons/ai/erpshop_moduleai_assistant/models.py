import hashlib
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import Company, TimeStampedModel


ENVIRONMENT_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


def assistant_attachment_path(instance, filename):
    safe_name = Path(filename).name[:120]
    return f"ai_assistant/{instance.session_id}/{uuid.uuid4().hex}-{safe_name}"


def knowledge_document_path(instance, filename):
    safe_name = Path(filename).name[:120]
    return f"ai_knowledge/{instance.company_id}/{uuid.uuid4().hex}-{safe_name}"


class AssistantConfiguration(TimeStampedModel):
    """One company-scoped provider configuration.

    Credentials intentionally never live in the database.  The administrator
    supplies the name of an environment variable mounted into web and worker.
    """

    class Provider(models.TextChoices):
        DISABLED = "disabled", "Disabled"
        HASHY_OS = "hashy_os", "Hashy OS gateway"
        OPENAI_COMPATIBLE = "openai_compatible", "OpenAI-compatible gateway"
        ANTHROPIC = "anthropic", "Anthropic Messages API"

    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="hashy_assistant_configuration")
    display_name = models.CharField(max_length=120, default="Hashy AI Assistant")
    provider = models.CharField(max_length=32, choices=Provider.choices, default=Provider.DISABLED)
    api_base_url = models.URLField(blank=True, help_text="HTTPS base URL, for example https://provider.example/v1")
    credential_env_var = models.CharField(
        max_length=128,
        blank=True,
        help_text="Name only; its secret value is read from the service environment.",
    )
    model_name = models.CharField(max_length=160, blank=True)
    system_prompt = models.TextField(blank=True)
    max_output_tokens = models.PositiveIntegerField(default=1200)
    temperature = models.DecimalField(max_digits=3, decimal_places=2, default=0.20)
    active = models.BooleanField(default=False)
    allow_erp_context = models.BooleanField(default=True)
    allow_file_content = models.BooleanField(default=False)
    allow_knowledge_context = models.BooleanField(
        default=True,
        help_text="Include bounded active company knowledge documents in assistant context.",
    )

    class Meta:
        ordering = ["company__name"]
        permissions = [("manage_assistant_configuration", "Can manage Hashy Assistant configuration")]

    def __str__(self):
        return f"{self.company} · {self.display_name}"

    @property
    def credential_is_available(self):
        return bool(self.credential_env_var and os.getenv(self.credential_env_var))

    @property
    def is_ready(self):
        return bool(
            self.active
            and self.provider != self.Provider.DISABLED
            and self.api_base_url
            and self.model_name
            and self.credential_is_available
        )

    def clean(self):
        super().clean()
        errors = {}
        if self.credential_env_var and not ENVIRONMENT_NAME_RE.fullmatch(self.credential_env_var):
            errors["credential_env_var"] = "Use an uppercase environment-variable name, e.g. ERPGSSHOP_HASHY_API_KEY."
        if self.provider != self.Provider.DISABLED:
            if not self.api_base_url:
                errors["api_base_url"] = "A provider endpoint is required when the assistant is enabled."
            if not self.model_name:
                errors["model_name"] = "A provider model name is required when the assistant is enabled."
            if not self.credential_env_var:
                errors["credential_env_var"] = "A credential environment-variable name is required when the assistant is enabled."
        if self.api_base_url:
            parsed = urlparse(self.api_base_url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                errors["api_base_url"] = "Provider endpoints must use HTTPS and cannot contain embedded credentials."
        if self.max_output_tokens < 64 or self.max_output_tokens > 8000:
            errors["max_output_tokens"] = "Choose a response limit from 64 to 8,000 tokens."
        if self.temperature < 0 or self.temperature > 1:
            errors["temperature"] = "Temperature must be between 0 and 1."
        if errors:
            raise ValidationError(errors)


class HashyOSConnection(TimeStampedModel):
    class State(models.TextChoices):
        DISCONNECTED = "disconnected", "Disconnected"
        PENDING = "pending", "Pending authorization"
        CONNECTED = "connected", "Connected"
        ERROR = "error", "Connection error"

    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="hashy_os_connection")
    account_email = models.EmailField(blank=True)
    workspace_identifier = models.CharField(max_length=160, blank=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.DISCONNECTED)
    status_detail = models.CharField(max_length=300, blank=True)
    last_verified_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["company__name"]

    def __str__(self):
        return f"{self.company} · Hashy OS"


class AssistantSession(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="hashy_assistant_sessions")
    configuration = models.ForeignKey(AssistantConfiguration, on_delete=models.PROTECT, related_name="sessions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="hashy_assistant_sessions")
    title = models.CharField(max_length=180, default="New conversation")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    page_context = models.JSONField(default=dict, blank=True)
    last_activity_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-last_activity_at", "-pk"]
        indexes = [
            models.Index(
                fields=["company", "user", "status", "-last_activity_at", "-id"],
                name="ai_session_owner_recent",
            ),
            models.Index(
                fields=["company", "status", "-last_activity_at", "-id"],
                name="ai_session_company_recent",
            ),
        ]

    def clean(self):
        super().clean()
        if self.configuration_id and self.company_id and self.configuration.company_id != self.company_id:
            raise ValidationError({"configuration": "The assistant configuration must belong to this session company."})

    def __str__(self):
        return self.title


class AssistantMessage(TimeStampedModel):
    class Role(models.TextChoices):
        USER = "user", "You"
        ASSISTANT = "assistant", "Hashy"
        SYSTEM = "system", "System"

    class State(models.TextChoices):
        COMPLETED = "completed", "Completed"
        PENDING = "pending", "Waiting for provider"
        FAILED = "failed", "Failed"

    class Feedback(models.TextChoices):
        HELPFUL = "helpful", "Helpful"
        UNHELPFUL = "unhelpful", "Unhelpful"

    session = models.ForeignKey(AssistantSession, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField()
    state = models.CharField(max_length=16, choices=State.choices, default=State.COMPLETED)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    provider_message_id = models.CharField(max_length=180, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    feedback = models.CharField(max_length=16, choices=Feedback.choices, blank=True)
    feedback_note = models.TextField(blank=True)
    feedback_at = models.DateTimeField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [
            models.Index(fields=["session", "-id"], name="ai_message_session_recent"),
            models.Index(
                fields=["session", "state", "-created_at", "-id"],
                name="ai_message_context_recent",
            ),
        ]

    def __str__(self):
        return f"{self.session} · {self.role}"


class AssistantAttachment(TimeStampedModel):
    session = models.ForeignKey(AssistantSession, on_delete=models.CASCADE, related_name="attachments")
    message = models.ForeignKey(AssistantMessage, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=assistant_attachment_path)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120, blank=True)
    byte_size = models.PositiveBigIntegerField(default=0)
    extracted_text = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def clean(self):
        super().clean()
        if self.message_id and self.session_id and self.message.session_id != self.session_id:
            raise ValidationError({"message": "Attachment message must belong to the same conversation."})

    def __str__(self):
        return self.original_name


class AssistantAuditEvent(TimeStampedModel):
    session = models.ForeignKey(AssistantSession, on_delete=models.CASCADE, related_name="audit_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="hashy_assistant_events")
    event = models.CharField(max_length=80)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.session} · {self.event}"


class AIWorkEpisode(TimeStampedModel):
    """A bounded, auditable unit of AI-assisted ERP work.

    This is the Django counterpart of HMX ``aiworkepisode``.  It intentionally
    stores only an allow-listed summary and references, never raw ERP records
    or provider credentials.
    """

    class State(models.TextChoices):
        STARTED = "started", "Started"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    episode_id = models.CharField(max_length=128, unique=True, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_work_episodes")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="ai_work_episodes",
        blank=True,
        null=True,
    )
    session = models.ForeignKey(
        AssistantSession,
        on_delete=models.SET_NULL,
        related_name="work_episodes",
        blank=True,
        null=True,
    )
    workflow_scope = models.CharField(max_length=128, db_index=True)
    semantic_bucket = models.CharField(max_length=64, blank=True, db_index=True)
    source_ref = models.CharField(max_length=180, blank=True, db_index=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.STARTED, db_index=True)
    summary = models.CharField(max_length=500, blank=True)
    context = models.JSONField(default=dict, blank=True)
    outcome = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    completed_at = models.DateTimeField(blank=True, null=True, db_index=True)

    class Meta:
        ordering = ["-started_at", "-id"]
        indexes = [
            models.Index(fields=["company", "actor", "workflow_scope", "-started_at"], name="ai_episode_actor_scope"),
            models.Index(fields=["company", "state", "-started_at"], name="ai_episode_company_state"),
        ]

    def save(self, *args, **kwargs):
        if not self.episode_id:
            seed = f"{self.company_id}:{self.actor_id or ''}:{self.workflow_scope}:{uuid.uuid4()}"
            self.episode_id = f"erpgsshop_work_v1:{hashlib.sha256(seed.encode()).hexdigest()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.summary or self.workflow_scope


class AIWorkEpisodeEvent(TimeStampedModel):
    class Type(models.TextChoices):
        INPUT = "input", "Input"
        SUGGESTION = "suggestion", "Suggestion"
        TOOL = "tool", "Tool"
        OUTCOME = "outcome", "Outcome"
        FEEDBACK = "feedback", "Feedback"

    episode = models.ForeignKey(AIWorkEpisode, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=24, choices=Type.choices)
    semantic_bucket = models.CharField(max_length=64, blank=True, db_index=True)
    selected = models.BooleanField(default=False)
    confidence = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["occurred_at", "id"]
        indexes = [models.Index(fields=["episode", "event_type", "id"], name="ai_episode_event_stream")]


class AIWorkflowInteractionAggregate(TimeStampedModel):
    """Pre-aggregated selection evidence used to derive soft work-profile hints."""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_interaction_aggregates")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="ai_interaction_aggregates",
        blank=True,
        null=True,
    )
    workflow_scope = models.CharField(max_length=128)
    semantic_bucket = models.CharField(max_length=64)
    episode_count = models.PositiveIntegerField(default=0)
    selection_count = models.PositiveIntegerField(default=0)
    average_confidence = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    evidence_episode_ids = models.JSONField(default=list, blank=True)
    last_event_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-last_event_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "actor", "workflow_scope", "semantic_bucket"],
                name="unique_ai_interaction_aggregate",
            )
        ]
        indexes = [models.Index(fields=["company", "workflow_scope", "semantic_bucket"], name="ai_interaction_scope")]


class AIWorkflowScopeAggregate(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_scope_aggregates")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="ai_scope_aggregates",
        blank=True,
        null=True,
    )
    workflow_scope = models.CharField(max_length=128)
    episode_count = models.PositiveIntegerField(default=0)
    completed_episode_count = models.PositiveIntegerField(default=0)
    total_event_count = models.PositiveIntegerField(default=0)
    decision_completed_count = models.PositiveIntegerField(default=0)
    approved_outcome_count = models.PositiveIntegerField(default=0)
    rejected_outcome_count = models.PositiveIntegerField(default=0)
    last_episode_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-last_episode_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["company", "actor", "workflow_scope"], name="unique_ai_scope_aggregate")
        ]
        indexes = [models.Index(fields=["company", "workflow_scope", "-last_episode_at"], name="ai_scope_recent")]


class AIWorkProfileSignal(TimeStampedModel):
    """A derived, non-decisive familiarity signal for the provider context."""

    signal_id = models.CharField(max_length=160, unique=True, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_work_profile_signals")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="ai_work_profile_signals",
        blank=True,
        null=True,
    )
    workflow_scope = models.CharField(max_length=128, db_index=True)
    signal_key = models.CharField(max_length=128, db_index=True)
    signal_type = models.CharField(max_length=64, default="erp_workflow_behavior_pattern", db_index=True)
    semantic_bucket = models.CharField(max_length=64, blank=True, db_index=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    evidence_strength = models.CharField(max_length=16, default="low")
    evidence_episode_count = models.PositiveIntegerField(default=0)
    evidence_selection_count = models.PositiveIntegerField(default=0)
    coverage_ratio = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    summary = models.CharField(max_length=500, blank=True)
    profile_payload = models.JSONField(default=dict, blank=True)
    evidence_episode_ids = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True, db_index=True)
    last_derived_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-last_derived_at", "-id"]
        indexes = [
            models.Index(fields=["company", "actor", "active", "workflow_scope"], name="ai_profile_active_scope"),
        ]

    def save(self, *args, **kwargs):
        if not self.signal_id:
            seed = f"{self.company_id}:{self.actor_id or ''}:{self.workflow_scope}:{self.signal_key}"
            self.signal_id = f"erpgsshop_profile_v1:{hashlib.sha256(seed.encode()).hexdigest()}"
        super().save(*args, **kwargs)


class AIWorkSignalOutbox(TimeStampedModel):
    """Privacy-filtered local event outbox; delivery is dry-run by default."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT_MOCK = "sent_mock", "Sent (mock)"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    event_id = models.CharField(max_length=160, unique=True, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_work_signal_outbox")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="ai_work_signal_outbox",
        blank=True,
        null=True,
    )
    episode = models.ForeignKey(AIWorkEpisode, on_delete=models.SET_NULL, related_name="outbox_events", blank=True, null=True)
    event_type = models.CharField(max_length=64, db_index=True)
    canonical_action = models.CharField(max_length=64, blank=True, db_index=True)
    workflow_scope = models.CharField(max_length=128, blank=True, db_index=True)
    source_ref = models.CharField(max_length=180, blank=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempt_count = models.PositiveIntegerField(default=0)
    sent_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [models.Index(fields=["status", "occurred_at", "id"], name="ai_work_outbox_pending")]

    def save(self, *args, **kwargs):
        if not self.event_id:
            seed = f"{self.company_id}:{self.episode_id or ''}:{self.event_type}:{self.source_ref}:{uuid.uuid4()}"
            self.event_id = f"erpgsshop_signal_v1:{hashlib.sha256(seed.encode()).hexdigest()}"
        super().save(*args, **kwargs)


class AIWorkProfileRefreshSetting(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="ai_work_profile_refresh_setting")
    enabled = models.BooleanField(default=True)
    interval_minutes = models.PositiveIntegerField(default=60)
    batch_size = models.PositiveSmallIntegerField(default=100)
    last_run_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["company__name"]

    def clean(self):
        super().clean()
        if not 5 <= self.interval_minutes <= 1440:
            raise ValidationError({"interval_minutes": "Choose an interval from 5 minutes to 24 hours."})
        if not 1 <= self.batch_size <= 500:
            raise ValidationError({"batch_size": "Choose a batch size from 1 to 500."})


class AIWorkProfileRefreshRun(TimeStampedModel):
    class Status(models.TextChoices):
        STARTED = "started", "Started"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    setting = models.ForeignKey(AIWorkProfileRefreshSetting, on_delete=models.CASCADE, related_name="runs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.STARTED)
    scanned_episodes = models.PositiveIntegerField(default=0)
    updated_aggregates = models.PositiveIntegerField(default=0)
    upserted_signals = models.PositiveIntegerField(default=0)
    details = models.JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class AIHashyInteractionSignalOutbox(TimeStampedModel):
    """Local-only Hashy interaction signal queue with no raw message content."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT_MOCK = "sent_mock", "Sent (mock)"
        FAILED = "failed", "Failed"

    event_id = models.CharField(max_length=160, unique=True, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_hashy_signal_outbox")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="ai_hashy_signal_outbox",
        blank=True,
        null=True,
    )
    session = models.ForeignKey(AssistantSession, on_delete=models.SET_NULL, related_name="interaction_signals", blank=True, null=True)
    event_type = models.CharField(max_length=64, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "id"], name="ai_hashy_signal_pending")]

    def save(self, *args, **kwargs):
        if not self.event_id:
            seed = f"{self.company_id}:{self.session_id or ''}:{self.event_type}:{uuid.uuid4()}"
            self.event_id = f"erpgsshop_hashy_v1:{hashlib.sha256(seed.encode()).hexdigest()}"
        super().save(*args, **kwargs)


class AIDigestCategory(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_digest_categories")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_ai_digest_category")]

    def __str__(self):
        return self.name


class AIDigestRule(TimeStampedModel):
    class Trigger(models.TextChoices):
        EVENT = "event", "Event"
        PERIODIC = "periodic", "Periodic"
        BOTH = "both", "Both"

    class Frequency(models.TextChoices):
        HOURLY = "hourly", "Hourly"
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_digest_rules")
    name = models.CharField(max_length=100)
    categories = models.ManyToManyField(AIDigestCategory, related_name="rules", blank=True)
    description = models.TextField(blank=True)
    trigger_type = models.CharField(max_length=16, choices=Trigger.choices, default=Trigger.BOTH)
    schedule_frequency = models.CharField(max_length=16, choices=Frequency.choices, blank=True)
    run_at = models.TimeField(blank=True, null=True)
    provider_instruction = models.TextField(blank=True)
    monitor_config = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_ai_digest_rule")]
        indexes = [models.Index(fields=["company", "active", "last_run_at"], name="ai_digest_rule_due")]

    def clean(self):
        super().clean()
        if self.trigger_type in {self.Trigger.PERIODIC, self.Trigger.BOTH} and not self.schedule_frequency:
            raise ValidationError({"schedule_frequency": "Periodic digest rules require a schedule frequency."})
        if self.trigger_type == self.Trigger.EVENT and self.schedule_frequency:
            raise ValidationError({"schedule_frequency": "Event-only rules do not use a schedule frequency."})

    def __str__(self):
        return self.name


class AIDigestRuleSource(TimeStampedModel):
    rule = models.ForeignKey(AIDigestRule, on_delete=models.CASCADE, related_name="sources")
    module_key = models.CharField(max_length=128)
    module_label = models.CharField(max_length=255, blank=True)
    model_key = models.CharField(max_length=128)
    model_label = models.CharField(max_length=255, blank=True)
    sequence = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)
    validity_state = models.CharField(max_length=32, default="unknown")
    validity_message = models.TextField(blank=True)

    class Meta:
        ordering = ["rule", "sequence", "id"]
        constraints = [models.UniqueConstraint(fields=["rule", "model_key"], name="unique_ai_digest_rule_source")]

    def __str__(self):
        return self.model_label or self.model_key


class AIDigestRuleSourceAction(TimeStampedModel):
    source = models.ForeignKey(AIDigestRuleSource, on_delete=models.CASCADE, related_name="actions")
    canonical_action_key = models.CharField(max_length=255)
    method_name = models.CharField(max_length=128)
    friendly_label = models.CharField(max_length=255)
    source_type = models.CharField(max_length=64, default="model_method")
    source_module = models.CharField(max_length=128, blank=True)
    sequence = models.PositiveIntegerField(default=10)
    validity_state = models.CharField(max_length=32, default="unknown")
    validity_message = models.TextField(blank=True)

    class Meta:
        ordering = ["source", "sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "canonical_action_key"],
                name="unique_ai_digest_source_action",
            )
        ]

    def __str__(self):
        return self.friendly_label or self.method_name


class AIDigestRecord(TimeStampedModel):
    class Urgency(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_digest_records")
    rule = models.ForeignKey(AIDigestRule, on_delete=models.SET_NULL, related_name="records", blank=True, null=True)
    source_ref = models.CharField(max_length=180, blank=True, db_index=True)
    action = models.CharField(max_length=128, blank=True)
    summary = models.TextField(blank=True)
    narrative = models.TextField(blank=True)
    diagnosis = models.TextField(blank=True)
    evidence = models.JSONField(default=list, blank=True)
    timeline = models.JSONField(default=list, blank=True)
    urgency = models.CharField(max_length=16, choices=Urgency.choices, default=Urgency.LOW)
    urgency_score = models.PositiveSmallIntegerField(default=0)
    confidence_score = models.DecimalField(max_digits=5, decimal_places=4, default=0)
    recommended_actions = models.JSONField(default=list, blank=True)
    trace_id = models.CharField(max_length=128, blank=True, db_index=True)
    job_id = models.CharField(max_length=128, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["company", "rule", "-created_at"], name="ai_digest_company_recent")]

    def __str__(self):
        return self.summary or self.source_ref or "AI digest"


class AIKnowledgeDocument(TimeStampedModel):
    """Company-scoped base knowledge, stored locally until an explicit sync is configured."""

    class Status(models.TextChoices):
        READY = "ready", "Ready"
        PENDING = "pending", "Pending extraction"
        FAILED = "failed", "Extraction failed"
        ARCHIVED = "archived", "Archived"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ai_knowledge_documents")
    title = models.CharField(max_length=255)
    category = models.CharField(max_length=100, blank=True)
    file = models.FileField(upload_to=knowledge_document_path, blank=True)
    original_name = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=120, blank=True)
    byte_size = models.PositiveBigIntegerField(default=0)
    extracted_text = models.TextField(blank=True)
    content_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    error_message = models.CharField(max_length=500, blank=True)
    active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["category", "title", "id"]
        constraints = [models.UniqueConstraint(fields=["company", "title"], name="unique_ai_knowledge_title")]
        indexes = [models.Index(fields=["company", "active", "status", "id"], name="ai_knowledge_context")]

    def clean(self):
        super().clean()
        if len(self.extracted_text or "") > 200_000:
            raise ValidationError({"extracted_text": "Extracted knowledge text must be at most 200,000 characters."})
        if self.status == self.Status.READY and not (self.extracted_text or self.file):
            raise ValidationError({"status": "Ready knowledge requires extracted text or a source file."})

    def __str__(self):
        return self.title
