import json
from functools import wraps
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from modules.apphub.models import ERPApp

from .forms import AssistantConfigurationForm, HashyOSConnectionForm
from .models import (
    AssistantAttachment,
    AssistantAuditEvent,
    AssistantConfiguration,
    AssistantMessage,
    AssistantSession,
    HashyOSConnection,
)
from .tasks import process_assistant_message


APP_SLUG = "erpshop-moduleai-assistant"
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 5
EXTRACTABLE_CONTENT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json"}


def _is_json_request(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _active_company(request):
    context = getattr(request, "organization_context", None)
    company_id = getattr(context, "company_id", None)
    if not company_id:
        raise PermissionError("Select an active company before using Hashy AI Assistant.")
    return company_id


def _can_use(user):
    return user.is_superuser or user.has_perm("erpshop_moduleai_assistant.view_assistantsession")


def _can_configure(user):
    return user.is_superuser or user.has_perm("erpshop_moduleai_assistant.manage_assistant_configuration")


def assistant_access_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        app = ERPApp.objects.filter(slug=APP_SLUG, enabled=True).first()
        if app is None:
            messages.info(request, "Install ERPShop Module AI — Hashy Assistant before opening this workspace.")
            return redirect("app_manager")
        if not _can_use(request.user):
            messages.error(request, "Your role does not include Hashy AI Assistant access.")
            return redirect("dashboard")
        try:
            _active_company(request)
        except PermissionError as exc:
            messages.error(request, str(exc))
            return redirect("dashboard")
        request.session["active_erp_app"] = APP_SLUG
        request.session.modified = True
        return view(request, *args, **kwargs)

    return wrapped


def _configuration(company_id):
    return AssistantConfiguration.objects.get_or_create(company_id=company_id)[0]


def _connection(company_id):
    return HashyOSConnection.objects.get_or_create(company_id=company_id)[0]


def _session_queryset(request):
    queryset = AssistantSession.objects.select_related("configuration", "company", "user").filter(
        company_id=_active_company(request)
    )
    if not request.user.is_superuser:
        queryset = queryset.filter(user=request.user)
    return queryset


def _session_for_request(request, session_id, *, allow_archived=False):
    queryset = _session_queryset(request)
    if not allow_archived:
        queryset = queryset.filter(status=AssistantSession.Status.ACTIVE)
    return get_object_or_404(queryset, pk=session_id)


@assistant_access_required
def assistant_home(request):
    company_id = _active_company(request)
    configuration = _configuration(company_id)
    sessions = _session_queryset(request)
    session_id = request.GET.get("session")
    selected_session = None
    if session_id:
        try:
            selected_session = sessions.filter(status=AssistantSession.Status.ACTIVE).get(pk=int(session_id))
        except (AssistantSession.DoesNotExist, ValueError, TypeError):
            raise Http404("Assistant conversation not found.")
    if selected_session is None:
        selected_session = sessions.filter(status=AssistantSession.Status.ACTIVE).first()
    message_rows = []
    if selected_session:
        message_rows = list(
            selected_session.messages.prefetch_related("attachments").order_by("created_at", "pk")
        )
    return render(
        request,
        "erpshop_moduleai_assistant/home.html",
        {
            "configuration": configuration,
            "hashy_connection": _connection(company_id),
            "sessions": sessions.filter(status=AssistantSession.Status.ACTIVE)[:60],
            "selected_session": selected_session,
            "messages": message_rows,
            "can_configure": _can_configure(request.user),
            "can_view_all_sessions": request.user.is_superuser,
        },
    )


@require_POST
@assistant_access_required
def new_session(request):
    company_id = _active_company(request)
    configuration = _configuration(company_id)
    title = request.POST.get("title", "").strip()[:180] or "New conversation"
    raw_context = request.POST.get("page_context", "").strip()
    page_context = {}
    if raw_context:
        try:
            candidate = json.loads(raw_context)
            if isinstance(candidate, dict):
                page_context = candidate
            else:
                raise ValueError
        except ValueError:
            messages.error(request, "Page context must be a JSON object.")
            return redirect(reverse("erpshop_moduleai_assistant:home"))
    session = AssistantSession.objects.create(
        company_id=company_id,
        configuration=configuration,
        user=request.user,
        title=title,
        page_context=page_context,
    )
    AssistantAuditEvent.objects.create(session=session, actor=request.user, event="session_created")
    return redirect(f"{reverse('erpshop_moduleai_assistant:home')}?session={session.pk}")


def _save_attachment(session, message, upload, configuration):
    if upload.size > MAX_ATTACHMENT_BYTES:
        raise ValidationError(f"{upload.name}: maximum attachment size is 10 MB.")
    content_type = (getattr(upload, "content_type", "") or "").lower()[:120]
    original_name = Path(upload.name).name[:255]
    extracted_text = ""
    if configuration.allow_file_content and content_type in EXTRACTABLE_CONTENT_TYPES:
        raw = upload.read(12000)
        extracted_text = raw.decode("utf-8", errors="replace")
        upload.seek(0)
    return AssistantAttachment.objects.create(
        session=session,
        message=message,
        file=upload,
        original_name=original_name,
        content_type=content_type,
        byte_size=upload.size,
        extracted_text=extracted_text,
    )


@require_POST
@assistant_access_required
def send_message(request, session_id):
    session = _session_for_request(request, session_id)
    content = request.POST.get("content", "").strip()
    uploads = request.FILES.getlist("attachments")
    if not content:
        messages.error(request, "Write a message before sending it to Hashy.")
        return redirect(f"{reverse('erpshop_moduleai_assistant:home')}?session={session.pk}")
    if len(uploads) > MAX_ATTACHMENTS_PER_MESSAGE:
        messages.error(request, f"Attach at most {MAX_ATTACHMENTS_PER_MESSAGE} files per message.")
        return redirect(f"{reverse('erpshop_moduleai_assistant:home')}?session={session.pk}")

    with transaction.atomic():
        session = AssistantSession.objects.select_for_update().get(pk=session.pk)
        message = AssistantMessage.objects.create(
            session=session,
            role=AssistantMessage.Role.USER,
            content=content[:50000],
            state=AssistantMessage.State.PENDING,
        )
        try:
            for upload in uploads:
                _save_attachment(session, message, upload, session.configuration)
        except ValidationError as exc:
            transaction.set_rollback(True)
            messages.error(request, "; ".join(exc.messages))
            return redirect(f"{reverse('erpshop_moduleai_assistant:home')}?session={session.pk}")
        if session.title == "New conversation":
            session.title = content.replace("\n", " ")[:177] + ("…" if len(content) > 177 else "")
        session.last_activity_at = timezone.now()
        session.save(update_fields=["title", "last_activity_at", "updated_at"])
        AssistantAuditEvent.objects.create(
            session=session,
            actor=request.user,
            event="message_queued",
            details={"message_id": message.pk, "attachment_count": len(uploads)},
        )
    try:
        process_assistant_message.delay(message.pk)
    except Exception:  # Broker failures must never leave a message apparently in progress.
        message.state = AssistantMessage.State.FAILED
        message.error_message = "The background worker could not be reached."
        message.save(update_fields=["state", "error_message", "updated_at"])
        AssistantMessage.objects.create(
            session=session,
            role=AssistantMessage.Role.ASSISTANT,
            content="Hashy queue is unavailable. Ask an administrator to check the background worker.",
            state=AssistantMessage.State.FAILED,
            error_message="The background worker could not be reached.",
        )
        messages.error(request, "The AI job could not be queued; no provider request was sent.")
    return redirect(f"{reverse('erpshop_moduleai_assistant:home')}?session={session.pk}")


@require_POST
@assistant_access_required
def archive_session(request, session_id):
    session = _session_for_request(request, session_id)
    session.status = AssistantSession.Status.ARCHIVED
    session.save(update_fields=["status", "updated_at"])
    AssistantAuditEvent.objects.create(session=session, actor=request.user, event="session_archived")
    messages.success(request, "Conversation archived.")
    return redirect("erpshop_moduleai_assistant:home")


@require_POST
@assistant_access_required
def message_feedback(request, message_id):
    message = get_object_or_404(
        AssistantMessage.objects.select_related("session"),
        pk=message_id,
        session__in=_session_queryset(request),
        role=AssistantMessage.Role.ASSISTANT,
    )
    feedback = request.POST.get("feedback", "")
    if feedback not in {AssistantMessage.Feedback.HELPFUL, AssistantMessage.Feedback.UNHELPFUL}:
        return JsonResponse({"ok": False, "message": "Unknown feedback."}, status=400)
    message.feedback = feedback
    message.feedback_note = request.POST.get("note", "").strip()[:2000]
    message.feedback_at = timezone.now()
    message.save(update_fields=["feedback", "feedback_note", "feedback_at", "updated_at"])
    AssistantAuditEvent.objects.create(
        session=message.session,
        actor=request.user,
        event="message_feedback",
        details={"message_id": message.pk, "feedback": feedback},
    )
    if _is_json_request(request):
        return JsonResponse({"ok": True, "feedback": feedback})
    return redirect(f"{reverse('erpshop_moduleai_assistant:home')}?session={message.session_id}")


@assistant_access_required
def session_state(request, session_id):
    session = _session_for_request(request, session_id)
    pending = session.messages.filter(state=AssistantMessage.State.PENDING).exists()
    latest = session.messages.order_by("-created_at", "-pk").first()
    return JsonResponse(
        {
            "pending": pending,
            "latest_message_id": latest.pk if latest else None,
            "latest_state": latest.state if latest else None,
        }
    )


@assistant_access_required
def assistant_settings(request):
    if not _can_configure(request.user):
        messages.error(request, "Only an assistant administrator can change provider or Hashy OS configuration.")
        return redirect("erpshop_moduleai_assistant:home")
    company_id = _active_company(request)
    configuration = _configuration(company_id)
    connection = _connection(company_id)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "provider":
            provider_form = AssistantConfigurationForm(request.POST, instance=configuration)
            connection_form = HashyOSConnectionForm(instance=connection)
            if provider_form.is_valid():
                provider_form.save()
                messages.success(request, "Assistant provider configuration saved. Secret values remain outside the database.")
                return redirect("erpshop_moduleai_assistant:settings")
        elif action == "hashy_os":
            provider_form = AssistantConfigurationForm(instance=configuration)
            connection_form = HashyOSConnectionForm(request.POST, instance=connection)
            if connection_form.is_valid():
                connection_form.save()
                messages.success(request, "Hashy OS connection record saved.")
                return redirect("erpshop_moduleai_assistant:settings")
        else:
            raise Http404("Unknown configuration action.")
    else:
        provider_form = AssistantConfigurationForm(instance=configuration)
        connection_form = HashyOSConnectionForm(instance=connection)
    return render(
        request,
        "erpshop_moduleai_assistant/settings.html",
        {
            "configuration": configuration,
            "hashy_connection": connection,
            "provider_form": provider_form,
            "connection_form": connection_form,
        },
    )
