from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Company, TimeStampedModel


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
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["workspace", "name"]
        constraints = [models.UniqueConstraint(fields=["workspace", "name"], name="unique_ai_agent_per_workspace")]

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
        ordering = ["-last_activity_at"]

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
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    agent = models.ForeignKey(AIAgent, on_delete=models.PROTECT, related_name="tasks")
    title = models.CharField(max_length=180)
    task_type = models.CharField(max_length=80)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    input_data = models.JSONField(default=dict, blank=True)
    result_data = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

