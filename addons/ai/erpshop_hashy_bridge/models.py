from django.conf import settings
from django.db import models

from core.models import Company, TimeStampedModel


class BridgeAuditLog(TimeStampedModel):
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="hashy_bridge_audit_logs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="hashy_bridge_audit_logs",
    )
    endpoint = models.CharField(max_length=64)
    run_id = models.CharField(max_length=128, blank=True)
    ok = models.BooleanField(default=False)
    http_status = models.PositiveSmallIntegerField()
    duration_ms = models.PositiveIntegerField(default=0)
    result_count = models.PositiveIntegerField(default=0)
    payload_digest = models.CharField(max_length=64, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    error_message = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-pk"]
        indexes = [
            models.Index(fields=["company", "-id"], name="hashy_audit_company_recent"),
            models.Index(fields=["user", "-id"], name="hashy_audit_user_recent"),
            models.Index(fields=["endpoint", "-id"], name="hashy_audit_endpoint_recent"),
            models.Index(fields=["run_id", "-id"], name="hashy_audit_run_recent"),
        ]

    def __str__(self):
        return f"{self.endpoint} · {self.http_status} · {self.created_at:%Y-%m-%d %H:%M:%S}"
