from pathlib import Path

from celery import shared_task
from django.utils import timezone

from .models import DatabaseOperation, ManagedDatabase
from .services import (
    bootstrap_database,
    create_backup,
    create_database,
    drop_database,
    duplicate_database,
    restore_backup,
    safe_backup_path,
)


def _update(operation, progress, message):
    operation.progress = progress
    operation.message = message[:500]
    operation.save(using="default", update_fields=("progress", "message"))


@shared_task(bind=True, time_limit=1800, soft_time_limit=1740, name="database_manager.execute")
def execute_database_operation(self, operation_id, payload):
    operation = DatabaseOperation.objects.using("default").get(pk=operation_id)
    operation.task_id = self.request.id or ""
    operation.status = DatabaseOperation.Status.RUNNING
    operation.started_at = timezone.now()
    operation.progress = 3
    operation.message = "Operation started."
    operation.save(
        using="default",
        update_fields=("task_id", "status", "started_at", "progress", "message"),
    )
    created_name = ""
    try:
        kind = operation.kind
        if kind == DatabaseOperation.Kind.CREATE:
            created_name = operation.target_name
            _update(operation, 8, "Creating PostgreSQL database…")
            create_database(created_name)
            _update(operation, 24, "Applying ERP GSSHOP schema and modules…")
            bootstrap_database(
                created_name,
                username=payload.get("admin_username", ""),
                email=payload.get("admin_email", ""),
                password=payload.get("admin_password", ""),
            )
            ManagedDatabase.objects.using("default").create(
                name=created_name,
                label=payload.get("label") or created_name.replace("_", " ").title(),
            )
            _update(operation, 96, "Database is ready for sign-in.")
        elif kind == DatabaseOperation.Kind.BACKUP:
            _update(operation, 12, "Streaming a compressed PostgreSQL backup…")
            path = create_backup(operation.database_name)
            _update(operation, 96, f"Backup {path.name} is ready.")
        elif kind == DatabaseOperation.Kind.DUPLICATE:
            created_name = operation.target_name
            _update(operation, 10, "Copying the source database safely…")
            duplicate_database(operation.database_name, created_name)
            _update(operation, 76, "Upgrading the duplicated schema…")
            bootstrap_database(created_name)
            source = ManagedDatabase.objects.using("default").get(name=operation.database_name)
            ManagedDatabase.objects.using("default").create(
                name=created_name,
                label=payload.get("label") or f"Copy of {source.label}",
            )
            _update(operation, 96, "Duplicate is ready.")
        elif kind == DatabaseOperation.Kind.RESTORE:
            created_name = operation.target_name
            backup_path = safe_backup_path(payload["filename"])
            _update(operation, 10, "Validating and restoring the backup…")
            restore_backup(backup_path, created_name)
            _update(operation, 76, "Bringing the restored schema up to date…")
            bootstrap_database(created_name)
            ManagedDatabase.objects.using("default").create(
                name=created_name,
                label=payload.get("label") or created_name.replace("_", " ").title(),
            )
            _update(operation, 96, "Restored database is ready.")
        elif kind == DatabaseOperation.Kind.UPGRADE:
            _update(operation, 12, "Applying pending migrations and synchronizing catalogs…")
            bootstrap_database(operation.database_name)
            _update(operation, 96, "Schema and application catalogs are current.")
        elif kind == DatabaseOperation.Kind.DROP:
            _update(operation, 25, "Disconnecting clients and dropping the database…")
            drop_database(operation.database_name)
            _update(operation, 96, "Database removed.")
        else:
            raise ValueError("Unsupported database operation.")

        operation.status = DatabaseOperation.Status.SUCCESS
        operation.progress = 100
        operation.finished_at = timezone.now()
        operation.save(using="default", update_fields=("status", "progress", "finished_at"))
        return {"success": True, "operation": str(operation.pk)}
    except Exception as exc:
        operation.status = DatabaseOperation.Status.FAILED
        operation.message = str(exc)[:500]
        operation.finished_at = timezone.now()
        operation.save(using="default", update_fields=("status", "message", "finished_at"))
        raise
