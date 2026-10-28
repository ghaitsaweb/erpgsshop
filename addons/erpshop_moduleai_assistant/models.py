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


class AssistantConfiguration(TimeStampedModel):
    """One company-scoped provider configuration.

    Credentials intentionally never live in the database.  The administrator
    supplies the name of an environment variable mounted into web and worker.
    """

    class Provider(models.TextChoices):
        DISABLED = "disabled", "Disabled"
        HASHY_OS = "hashy_os", "Hashy OS gateway"
        OPENAI_COMPATIBLE = "openai_compatible", "OpenAI-compatible gateway"

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
