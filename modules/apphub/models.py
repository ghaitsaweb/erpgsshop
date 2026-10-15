from django.conf import settings
from django.db import models
from django.utils import timezone


class ERPApp(models.Model):
    class Status(models.TextChoices):
        STABLE = "stable", "Stable"
        BETA = "beta", "Beta"
        PLANNED = "planned", "Planned"

    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=80)
    summary = models.CharField(max_length=240)
    icon = models.CharField(max_length=8, default="◆")
    version = models.CharField(max_length=20, default="0.1.0")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    enabled = models.BooleanField(default=False)
    is_core = models.BooleanField(default=False)
    sequence = models.PositiveIntegerField(default=100)
    dependencies = models.JSONField(default=list, blank=True)
    route = models.CharField(max_length=120, blank=True)
    enabled_at = models.DateTimeField(blank=True, null=True)
    enabled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="enabled_erp_apps",
    )

    class Meta:
        ordering = ["category", "sequence", "name"]

    def set_enabled(self, enabled, user=None):
        if self.is_core and not enabled:
            return
        if self.status == self.Status.PLANNED and enabled:
            return
        self.enabled = enabled
        self.enabled_by = user if enabled else None
        self.enabled_at = timezone.now() if enabled else None
        self.save(update_fields=["enabled", "enabled_by", "enabled_at"])

    def __str__(self):
        return self.name

