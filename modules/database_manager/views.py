import time
from datetime import datetime, timezone

from celery import current_app
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import FileResponse, JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .models import DatabaseOperation, ManagedDatabase
from .runtime import primary_database_name, validate_database_name
from .services import (
    backup_directory,
    database_exists,
    list_backups,
    list_database_inventory,
    registered_database,
    safe_backup_path,
)
from .tasks import execute_database_operation


OPERATOR_SESSION_KEY = "erp_database_manager_operator"
OPERATOR_UNLOCKED_AT_KEY = "erp_database_manager_unlocked_at"
OPERATOR_FAILURES_KEY = "erp_database_manager_auth_failures"
OPERATOR_BLOCKED_UNTIL_KEY = "erp_database_manager_blocked_until"
OPERATOR_MAX_FAILURES = 5
OPERATOR_BLOCK_SECONDS = 60


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",", 1)[0].strip() or request.META.get("REMOTE_ADDR") or None)


def _operator(request):
    user_id = request.session.get(OPERATOR_SESSION_KEY)
    unlocked_at = request.session.get(OPERATOR_UNLOCKED_AT_KEY, 0)
    if not user_id or time.time() - float(unlocked_at) > settings.ERP_DATABASE_MANAGER_SESSION_SECONDS:
        request.session.pop(OPERATOR_SESSION_KEY, None)
        request.session.pop(OPERATOR_UNLOCKED_AT_KEY, None)
        return None
    return (
        get_user_model()._default_manager.using("default")
        .filter(pk=user_id, is_active=True, is_superuser=True)
        .first()
    )


def _authenticate_operator(username, password):
    user = (
        get_user_model()._default_manager.using("default")
        .filter(username=(username or "").strip(), is_active=True, is_superuser=True)
        .first()
    )
    if user and user.check_password(password or ""):
        return user
    return None


def _operator_rate_limited(request):
    return float(request.session.get(OPERATOR_BLOCKED_UNTIL_KEY, 0)) > time.time()


def _record_operator_failure(request):
    failures = int(request.session.get(OPERATOR_FAILURES_KEY, 0)) + 1
    request.session[OPERATOR_FAILURES_KEY] = failures
    if failures >= OPERATOR_MAX_FAILURES:
        request.session[OPERATOR_BLOCKED_UNTIL_KEY] = time.time() + OPERATOR_BLOCK_SECONDS
        request.session[OPERATOR_FAILURES_KEY] = 0


def _clear_operator_failures(request):
    request.session.pop(OPERATOR_FAILURES_KEY, None)
    request.session.pop(OPERATOR_BLOCKED_UNTIL_KEY, None)


def _require_operator(request, *, step_up=False):
    operator = _operator(request)
    if operator is None:
        return None, JsonResponse({"success": False, "message": "Operator unlock required."}, status=401)
    if step_up:
        if _operator_rate_limited(request):
            return None, JsonResponse({"success": False, "message": "Too many attempts. Try again in one minute."}, status=429)
        if not operator.check_password(request.POST.get("operator_password", "")):
            _record_operator_failure(request)
            return None, JsonResponse({"success": False, "message": "Operator password is incorrect."}, status=403)
        _clear_operator_failures(request)
    return operator, None


def _operation_payload(operation):
    return {
        "id": str(operation.pk),
        "task_id": operation.task_id,
        "kind": operation.kind,
        "kind_label": operation.get_kind_display(),
        "database": operation.database_name,
        "target": operation.target_name,
        "status": operation.status,
        "progress": operation.progress,
        "message": operation.message,
        "requested_by": operation.requested_by,
        "created_at": operation.created_at.isoformat(),
        "started_at": operation.started_at.isoformat() if operation.started_at else None,
        "finished_at": operation.finished_at.isoformat() if operation.finished_at else None,
    }


@ensure_csrf_cookie
def index(request):
    try:
        databases = list_database_inventory()
        inventory_error = ""
    except Exception as exc:
        databases = []
        inventory_error = str(exc)
    return render(
        request,
        "database_manager/index.html",
        {
            "databases": databases,
            "inventory_error": inventory_error,
            "current_database": request.COOKIES.get(settings.ERP_DATABASE_COOKIE, primary_database_name()),
            "database_prefix": f"{settings.ERP_DATABASE_PREFIX}_",
            "operator_unlocked": _operator(request) is not None,
        },
    )


@require_GET
def databases(request):
    try:
        return JsonResponse({"success": True, "databases": list_database_inventory()})
    except Exception as exc:
        return JsonResponse({"success": False, "message": str(exc)}, status=503)


@require_POST
def choose_database(request):
    try:
        name = validate_database_name(request.POST.get("database"))
        registered_database(name)
    except ValueError as exc:
        return JsonResponse({"success": False, "message": str(exc)}, status=404)
    response = JsonResponse({"success": True, "database": name, "redirect": "/login/"})
    response.set_cookie(
        settings.ERP_DATABASE_COOKIE,
        name,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="Lax",
        secure=settings.SESSION_COOKIE_SECURE,
    )
    return response


@require_GET
def operator_status(request):
    operator = _operator(request)
    return JsonResponse(
        {
            "success": True,
            "unlocked": operator is not None,
            "operator": operator.username if operator else "",
        }
    )


@require_POST
def unlock(request):
    if _operator_rate_limited(request):
        return JsonResponse({"success": False, "message": "Too many attempts. Try again in one minute."}, status=429)
    user = _authenticate_operator(request.POST.get("username"), request.POST.get("password"))
    if user is None:
        _record_operator_failure(request)
        return JsonResponse({"success": False, "message": "Invalid system administrator credentials."}, status=403)
    _clear_operator_failures(request)
    request.session[OPERATOR_SESSION_KEY] = user.pk
    request.session[OPERATOR_UNLOCKED_AT_KEY] = time.time()
    request.session.cycle_key()
    return JsonResponse({"success": True, "operator": user.username})


@require_POST
def lock(request):
    request.session.pop(OPERATOR_SESSION_KEY, None)
    request.session.pop(OPERATOR_UNLOCKED_AT_KEY, None)
    return JsonResponse({"success": True})


def _validated_target(raw_name):
    name = validate_database_name(raw_name, allow_primary=False)
    if ManagedDatabase.objects.using("default").filter(name=name).exists() or database_exists(name):
        raise ValueError("A database with this name already exists.")
    return name


def _queue_operation(request, operator, kind, *, database_name="", target_name="", payload=None):
    operation = DatabaseOperation.objects.using("default").create(
        kind=kind,
        database_name=database_name,
        target_name=target_name,
        requested_by=operator.username,
        client_ip=_client_ip(request),
        message="Queued for the database worker.",
    )
    try:
        result = execute_database_operation.delay(str(operation.pk), payload or {})
    except Exception as exc:
        operation.status = DatabaseOperation.Status.FAILED
        operation.message = f"Unable to queue the database task: {exc}"[:500]
        operation.finished_at = datetime.now(timezone.utc)
        operation.save(using="default", update_fields=("status", "message", "finished_at"))
        return JsonResponse({"success": False, "message": "The database worker queue is unavailable."}, status=503)
    operation.task_id = result.id or ""
    operation.save(using="default", update_fields=("task_id",))
    return JsonResponse({"success": True, "operation": _operation_payload(operation)}, status=202)


@require_POST
def create(request):
    operator, error = _require_operator(request, step_up=True)
    if error:
        return error
    try:
        target = _validated_target(request.POST.get("name"))
        label = (request.POST.get("label") or "").strip()[:120]
        admin_username = (request.POST.get("admin_username") or "").strip()
        admin_email = (request.POST.get("admin_email") or "").strip()
        admin_password = request.POST.get("admin_password") or ""
        if not label or not admin_username or not admin_password:
            raise ValueError("Display name, administrator username, and administrator password are required.")
        validate_password(admin_password)
    except (ValueError, ValidationError) as exc:
        messages = exc.messages if isinstance(exc, ValidationError) else [str(exc)]
        return JsonResponse({"success": False, "message": " ".join(messages)}, status=400)
    return _queue_operation(
        request,
        operator,
        DatabaseOperation.Kind.CREATE,
        target_name=target,
        payload={
            "label": label,
            "admin_username": admin_username,
            "admin_email": admin_email,
            "admin_password": admin_password,
        },
    )


@require_POST
def backup(request):
    operator, error = _require_operator(request, step_up=True)
    if error:
        return error
    try:
        source = registered_database(request.POST.get("database")).name
    except ValueError as exc:
        return JsonResponse({"success": False, "message": str(exc)}, status=400)
    return _queue_operation(request, operator, DatabaseOperation.Kind.BACKUP, database_name=source)


@require_POST
def duplicate(request):
    operator, error = _require_operator(request, step_up=True)
    if error:
        return error
    try:
        source = registered_database(request.POST.get("source")).name
        target = _validated_target(request.POST.get("target"))
        label = (request.POST.get("label") or "").strip()[:120]
    except ValueError as exc:
        return JsonResponse({"success": False, "message": str(exc)}, status=400)
    return _queue_operation(
        request,
        operator,
        DatabaseOperation.Kind.DUPLICATE,
        database_name=source,
        target_name=target,
        payload={"label": label},
    )


@require_POST
def restore(request):
    operator, error = _require_operator(request, step_up=True)
    if error:
        return error
    try:
        target = _validated_target(request.POST.get("target"))
        label = (request.POST.get("label") or "").strip()[:120]
        upload = request.FILES.get("backup_file")
        existing = request.POST.get("existing_file", "")
        if upload:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = safe_backup_path(f"{target}-{stamp}.dump", must_exist=False)
            with path.open("wb") as destination:
                for chunk in upload.chunks():
                    destination.write(chunk)
        elif existing:
            path = safe_backup_path(existing)
        else:
            raise ValueError("Choose an uploaded or existing backup file.")
    except ValueError as exc:
        return JsonResponse({"success": False, "message": str(exc)}, status=400)
    return _queue_operation(
        request,
        operator,
        DatabaseOperation.Kind.RESTORE,
        target_name=target,
        payload={"label": label, "filename": path.name},
    )


@require_POST
def upgrade(request):
    operator, error = _require_operator(request, step_up=True)
    if error:
        return error
    try:
        source = registered_database(request.POST.get("database")).name
    except ValueError as exc:
        return JsonResponse({"success": False, "message": str(exc)}, status=400)
    return _queue_operation(request, operator, DatabaseOperation.Kind.UPGRADE, database_name=source)


@require_POST
def drop(request):
    operator, error = _require_operator(request, step_up=True)
    if error:
        return error
    try:
        record = registered_database(request.POST.get("database"))
        if request.POST.get("confirmation") != record.name:
            raise ValueError("Type the complete database name to confirm deletion.")
        if record.is_primary:
            raise ValueError("The primary ERP GSSHOP database cannot be dropped.")
        if record.is_protected:
            raise ValueError("Remove protection before dropping this database.")
    except ValueError as exc:
        return JsonResponse({"success": False, "message": str(exc)}, status=400)
    return _queue_operation(request, operator, DatabaseOperation.Kind.DROP, database_name=record.name)


@require_POST
def protection(request):
    operator, error = _require_operator(request, step_up=True)
    if error:
        return error
    try:
        record = registered_database(request.POST.get("database"))
        protect = request.POST.get("protect") == "true"
        if record.is_primary and not protect:
            raise ValueError("Protection cannot be removed from the primary database.")
        record.is_protected = protect
        record.save(using="default", update_fields=("is_protected", "updated_at"))
        DatabaseOperation.objects.using("default").create(
            kind=DatabaseOperation.Kind.PROTECT if protect else DatabaseOperation.Kind.UNPROTECT,
            database_name=record.name,
            status=DatabaseOperation.Status.SUCCESS,
            progress=100,
            message="Protection enabled." if protect else "Protection removed.",
            requested_by=operator.username,
            client_ip=_client_ip(request),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
    except ValueError as exc:
        return JsonResponse({"success": False, "message": str(exc)}, status=400)
    return JsonResponse({"success": True, "database": record.name, "protected": protect})


@require_GET
def backups(request):
    _operator_user, error = _require_operator(request)
    if error:
        return error
    return JsonResponse({"success": True, "backups": list_backups()})


@require_GET
def download_backup(request, filename):
    _operator_user, error = _require_operator(request)
    if error:
        return error
    try:
        path = safe_backup_path(filename)
    except ValueError as exc:
        return JsonResponse({"success": False, "message": str(exc)}, status=404)
    return FileResponse(path.open("rb"), as_attachment=True, filename=path.name)


@require_POST
def delete_backup(request, filename):
    operator, error = _require_operator(request, step_up=True)
    if error:
        return error
    try:
        path = safe_backup_path(filename)
        path.unlink()
        DatabaseOperation.objects.using("default").create(
            kind=DatabaseOperation.Kind.DELETE_BACKUP,
            status=DatabaseOperation.Status.SUCCESS,
            progress=100,
            message=f"Deleted backup {filename}.",
            requested_by=operator.username,
            client_ip=_client_ip(request),
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
    except ValueError as exc:
        return JsonResponse({"success": False, "message": str(exc)}, status=404)
    return JsonResponse({"success": True})


@require_GET
def operations(request):
    _operator_user, error = _require_operator(request)
    if error:
        return error
    queryset = DatabaseOperation.objects.using("default").all()[:100]
    return JsonResponse({"success": True, "operations": [_operation_payload(item) for item in queryset]})


@require_GET
def operation_status(request, operation_id):
    _operator_user, error = _require_operator(request)
    if error:
        return error
    try:
        operation = DatabaseOperation.objects.using("default").get(pk=operation_id)
    except (DatabaseOperation.DoesNotExist, ValidationError):
        return JsonResponse({"success": False, "message": "Operation not found."}, status=404)
    return JsonResponse({"success": True, "operation": _operation_payload(operation)})


@require_GET
def worker_status(request):
    try:
        replies = current_app.control.ping(timeout=0.6) or []
        online = bool(replies)
    except Exception:
        online = False
    return JsonResponse({"success": True, "worker": online, "backup_dir": str(backup_directory())})
