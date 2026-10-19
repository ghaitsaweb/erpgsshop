import uuid

from django.db import models


class ManagedDatabase(models.Model):
    name = models.CharField(max_length=63, unique=True)
    label = models.CharField(max_length=120)
    is_primary = models.BooleanField(default=False)
    is_protected = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.label or self.name


class DatabaseOperation(models.Model):
    class Kind(models.TextChoices):
        CREATE = "create", "Create database"
        BACKUP = "backup", "Create backup"
        RESTORE = "restore", "Restore backup"
        DUPLICATE = "duplicate", "Duplicate database"
        DROP = "drop", "Drop database"
        UPGRADE = "upgrade", "Upgrade schema"
        PROTECT = "protect", "Protect database"
        UNPROTECT = "unprotect", "Remove protection"
        DELETE_BACKUP = "delete_backup", "Delete backup"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Successful"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_id = models.CharField(max_length=64, blank=True)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    database_name = models.CharField(max_length=63, blank=True)
    target_name = models.CharField(max_length=63, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    progress = models.PositiveSmallIntegerField(default=0)
    message = models.CharField(max_length=500, blank=True)
    requested_by = models.CharField(max_length=150)
    client_ip = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(blank=True, null=True)
    finished_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("status", "created_at"), name="dbop_status_created_idx")]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.database_name or self.target_name}"
