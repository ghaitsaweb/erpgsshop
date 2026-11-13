import hashlib
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
from django.views.decorators.http import require_GET, require_POST

from modules.apphub.models import ERPApp

from .forms import (
    AIKnowledgeCategoryForm,
    AIKnowledgeBusinessFlowForm,
    AIKnowledgeCompanyProfileForm,
    AIKnowledgeDocumentForm,
    AIKnowledgeRecordModelMappingForm,
    AssistantConfigurationForm,
    HashyOSConnectionForm,
)
from .models import (
    AssistantAttachment,
    AssistantAuditEvent,
    AssistantConfiguration,
    AIKnowledgeCategory,
    AIKnowledgeBusinessFlow,
    AIKnowledgeBusinessFlowGenerationJob,
    AIKnowledgeCompanyProfile,
    AIKnowledgeDocument,
    AIKnowledgeRecordModelMapping,
    AssistantMessage,
    AssistantSession,
    HashyOSConnection,
)
from .flow_generation import FlowGenerationError, flow_generation_prerequisites, reason_about_business_flows
from .greeting import resolve_proactive_greeting
from .knowledge import rebuild_document_chunks
from .knowledge_graph import (
    build_knowledge_graph,
    graph_neighbors,
    graph_node,
    graph_overview,
    graph_path,
    graph_search,
    graph_trace,
)
from .tasks import generate_business_flow as queue_business_flow_generation
from .tasks import process_assistant_message


APP_SLUG = "erpshop-moduleai-assistant"
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 5
MESSAGE_PAGE_SIZE = 100
EXTRACTABLE_CONTENT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json"}
MAX_KNOWLEDGE_TEXT_BYTES = 200_000


def _is_json_request(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _json_body(request, *, max_bytes=12_000):
    raw_body = request.body or b""
    if len(raw_body) > max_bytes:
        raise ValueError("Request data must be at most 12 KB.")
    if not raw_body:
        return {}
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Request data must be a JSON object.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request data must be a JSON object.")
    return payload


def _active_company(request):
    context = getattr(request, "organization_context", None)
    company_id = getattr(context, "company_id", None)
    if not company_id:
        raise PermissionError("Select an active company before using GashyAI.")
    return company_id


def _active_branch_id(request):
    return getattr(getattr(request, "organization_context", None), "branch_id", None)


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
            messages.info(request, "Install ERPShop Module AI — GashyAI before opening this workspace.")
            return redirect("app_manager")
        if not _can_use(request.user):
            messages.error(request, "Your role does not include GashyAI access.")
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
    queryset = AssistantSession.objects.select_related("configuration", "company", "branch", "user").filter(
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
    has_older_messages = False
    older_page = False
    if selected_session:
        message_queryset = selected_session.messages.order_by("-pk")
        before = request.GET.get("before")
        if before is not None:
            try:
                before_id = int(before)
            except (TypeError, ValueError) as exc:
                raise Http404("Invalid message cursor.") from exc
            if before_id < 1:
                raise Http404("Invalid message cursor.")
            message_queryset = message_queryset.filter(pk__lt=before_id)
            older_page = True
        page = list(message_queryset.prefetch_related("attachments")[: MESSAGE_PAGE_SIZE + 1])
        has_older_messages = len(page) > MESSAGE_PAGE_SIZE
        message_rows = list(reversed(page[:MESSAGE_PAGE_SIZE]))
    return render(
        request,
        "erpshop_moduleai_assistant/home.html",
        {
            "configuration": configuration,
            "sessions": sessions.filter(status=AssistantSession.Status.ACTIVE)[:60],
            "archived_sessions": sessions.filter(status=AssistantSession.Status.ARCHIVED)[:20],
            "selected_session": selected_session,
            "messages": message_rows,
            "has_older_messages": has_older_messages,
            "older_page": older_page,
            "oldest_message_id": message_rows[0].pk if message_rows else None,
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
        if len(raw_context.encode("utf-8")) > 12000:
            if _is_json_request(request):
                return JsonResponse({"ok": False, "message": "Page context must be at most 12 KB."}, status=400)
            messages.error(request, "Page context must be at most 12 KB.")
            return redirect(reverse("erpshop_moduleai_assistant:home"))
        try:
            candidate = json.loads(raw_context)
            if isinstance(candidate, dict):
                page_context = candidate
            else:
                raise ValueError
        except ValueError:
            if _is_json_request(request):
                return JsonResponse({"ok": False, "message": "Page context must be a JSON object."}, status=400)
            messages.error(request, "Page context must be a JSON object.")
            return redirect(reverse("erpshop_moduleai_assistant:home"))
    session = AssistantSession.objects.create(
        company_id=company_id,
        branch_id=_active_branch_id(request),
        configuration=configuration,
        user=request.user,
        title=title,
        page_context=page_context,
    )
    AssistantAuditEvent.objects.create(session=session, actor=request.user, event="session_created")
    if _is_json_request(request):
        return JsonResponse({"ok": True, "session_id": session.pk}, status=201)
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
        if _is_json_request(request):
            return JsonResponse({"ok": False, "message": "Tulis pesan sebelum mengirim ke GashyAI."}, status=400)
        messages.error(request, "Write a message before sending it to GashyAI.")
        return redirect(f"{reverse('erpshop_moduleai_assistant:home')}?session={session.pk}")
    if len(uploads) > MAX_ATTACHMENTS_PER_MESSAGE:
        if _is_json_request(request):
            return JsonResponse(
                {"ok": False, "message": f"Attach at most {MAX_ATTACHMENTS_PER_MESSAGE} files per message."},
                status=400,
            )
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
            if _is_json_request(request):
                return JsonResponse({"ok": False, "message": "; ".join(exc.messages)}, status=400)
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
            content="GashyAI queue is unavailable. Ask an administrator to check the background worker.",
            state=AssistantMessage.State.FAILED,
            error_message="The background worker could not be reached.",
        )
        if _is_json_request(request):
            return JsonResponse(
                {"ok": False, "message": "Worker AI tidak dapat dijangkau; tidak ada request provider yang dikirim."},
                status=503,
            )
        messages.error(request, "The AI job could not be queued; no provider request was sent.")
    if _is_json_request(request):
        return JsonResponse({"ok": True, "session_id": session.pk, "message_id": message.pk}, status=202)
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
def restore_session(request, session_id):
    session = _session_for_request(request, session_id, allow_archived=True)
    if session.status == AssistantSession.Status.ACTIVE:
        messages.info(request, "Conversation is already active.")
    else:
        session.status = AssistantSession.Status.ACTIVE
        session.save(update_fields=["status", "updated_at"])
        AssistantAuditEvent.objects.create(session=session, actor=request.user, event="session_restored")
        messages.success(request, "Conversation restored.")
    return redirect(f"{reverse('erpshop_moduleai_assistant:home')}?session={session.pk}")


@require_POST
@assistant_access_required
def rename_session(request, session_id):
    session = _session_for_request(request, session_id, allow_archived=True)
    title = request.POST.get("title", "").strip()[:180]
    if not title:
        messages.error(request, "Conversation name cannot be empty.")
        return redirect("erpshop_moduleai_assistant:home")
    if session.title != title:
        previous_title = session.title
        session.title = title
        session.save(update_fields=["title", "updated_at"])
        AssistantAuditEvent.objects.create(
            session=session,
            actor=request.user,
            event="session_renamed",
            details={"previous_title": previous_title, "title": title},
        )
        messages.success(request, "Conversation renamed.")
    target = reverse("erpshop_moduleai_assistant:home")
    if session.status == AssistantSession.Status.ACTIVE:
        target = f"{target}?session={session.pk}"
    return redirect(target)


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


@require_GET
@assistant_access_required
def launcher_state(request):
    """Return a bounded chat snapshot only after the floating panel is opened."""
    sessions_queryset = _session_queryset(request).filter(status=AssistantSession.Status.ACTIVE)
    recent_sessions = list(sessions_queryset[:12])
    requested_session_id = request.GET.get("session")
    selected_session = None
    if requested_session_id:
        try:
            parsed_session_id = int(requested_session_id)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "message": "Percakapan tidak valid."}, status=400)
        selected_session = next((item for item in recent_sessions if item.pk == parsed_session_id), None)
        if selected_session is None:
            selected_session = sessions_queryset.filter(pk=parsed_session_id).first()
        if selected_session is None:
            return JsonResponse({"ok": False, "message": "Percakapan tidak ditemukan pada scope aktif."}, status=404)
    elif recent_sessions:
        selected_session = recent_sessions[0]

    message_rows = []
    pending = False
    configuration = selected_session.configuration if selected_session else None
    if selected_session:
        message_rows = list(
            selected_session.messages.order_by("-pk").values("id", "role", "content", "state", "created_at")[:50]
        )
        message_rows.reverse()
        pending = selected_session.messages.filter(state=AssistantMessage.State.PENDING).exists()
    if configuration is None:
        configuration = AssistantConfiguration.objects.filter(company_id=_active_company(request)).first()

    workspace_url = reverse("erpshop_moduleai_assistant:home")
    if selected_session:
        workspace_url = f"{workspace_url}?session={selected_session.pk}"
    return JsonResponse(
        {
            "ok": True,
            "selected_session_id": selected_session.pk if selected_session else None,
            "sessions": [
                {
                    "id": item.pk,
                    "title": item.title,
                    "last_activity_at": item.last_activity_at.isoformat(),
                }
                for item in recent_sessions
            ],
            "messages": [
                {
                    "id": item["id"],
                    "role": item["role"],
                    "content": item["content"],
                    "state": item["state"],
                    "time": timezone.localtime(item["created_at"]).strftime("%H:%M"),
                }
                for item in message_rows
            ],
            "pending": pending,
            "configuration_ready": bool(configuration and configuration.is_ready),
            "send_url": (
                reverse("erpshop_moduleai_assistant:send_message", args=[selected_session.pk])
                if selected_session
                else ""
            ),
            "workspace_url": workspace_url,
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
                messages.success(request, "Hashy OS account reference saved; provider access has not been verified.")
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


@require_POST
@assistant_access_required
def proactive_greeting(request):
    if len(request.body or b"") > 12_000:
        return JsonResponse({"display": False, "reason_code": "INVALID_PAGE_CONTEXT"}, status=400)
    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"display": False, "reason_code": "INVALID_PAGE_CONTEXT"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"display": False, "reason_code": "INVALID_PAGE_CONTEXT"}, status=400)
    context = getattr(request, "organization_context", None)
    response = resolve_proactive_greeting(
        user=request.user,
        page_context=payload.get("page_context"),
        company_id=getattr(context, "company_id", None),
        branch_id=getattr(context, "branch_id", None),
        language=payload.get("language", "id"),
    )
    return JsonResponse(response)


def _knowledge_access_required(request):
    if not _can_configure(request.user):
        messages.error(request, "Only an assistant administrator can manage company knowledge.")
        return False
    return True


def _extract_knowledge_upload(upload):
    """Return bounded plain text and a source digest when a file is safely readable."""
    content_type = (getattr(upload, "content_type", "") or "").lower()[:120]
    if content_type not in EXTRACTABLE_CONTENT_TYPES:
        return "", "", content_type
    raw = upload.read(MAX_KNOWLEDGE_TEXT_BYTES + 1)
    upload.seek(0)
    if len(raw) > MAX_KNOWLEDGE_TEXT_BYTES:
        raise ValidationError("Plain-text knowledge files must be at most 200 KB after extraction.")
    return raw.decode("utf-8", errors="replace"), hashlib.sha256(raw).hexdigest(), content_type


def _knowledge_flow_for_company(queryset, raw_flow_id):
    try:
        flow_id = int(raw_flow_id)
    except (TypeError, ValueError):
        raise Http404("Knowledge business flow not found.")
    return get_object_or_404(queryset, pk=flow_id)


def _knowledge_mapping_for_company(queryset, raw_mapping_id):
    try:
        mapping_id = int(raw_mapping_id)
    except (TypeError, ValueError):
        raise Http404("Knowledge record-model mapping not found.")
    return get_object_or_404(queryset, pk=mapping_id)


@assistant_access_required
def knowledge_documents(request):
    """Company-scoped local base-knowledge management."""
    if not _knowledge_access_required(request):
        return redirect("erpshop_moduleai_assistant:home")
    company_id = _active_company(request)
    documents = AIKnowledgeDocument.objects.filter(company_id=company_id).select_related("branch")
    profile, _ = AIKnowledgeCompanyProfile.objects.get_or_create(company_id=company_id)
    categories = AIKnowledgeCategory.objects.filter(company_id=company_id)
    flows = AIKnowledgeBusinessFlow.objects.filter(company_id=company_id)
    mappings = AIKnowledgeRecordModelMapping.objects.filter(company_id=company_id).select_related("branch", "business_flow")
    document_form = AIKnowledgeDocumentForm(company_id=company_id, initial={"branch": _active_branch_id(request)})
    profile_form = AIKnowledgeCompanyProfileForm(instance=profile)
    category_form = AIKnowledgeCategoryForm()
    flow_form = AIKnowledgeBusinessFlowForm()
    mapping_form = AIKnowledgeRecordModelMappingForm(company_id=company_id, initial={"branch": _active_branch_id(request)})
    editing_flow = None
    editing_mapping = None
    if request.method == "GET" and request.GET.get("flow"):
        editing_flow = _knowledge_flow_for_company(flows, request.GET.get("flow"))
        flow_form = AIKnowledgeBusinessFlowForm(instance=editing_flow)
    if request.method == "GET" and request.GET.get("mapping"):
        editing_mapping = _knowledge_mapping_for_company(mappings, request.GET.get("mapping"))
        mapping_form = AIKnowledgeRecordModelMappingForm(instance=editing_mapping, company_id=company_id)
    if request.method == "POST":
        action = request.POST.get("action", "document")
        if action == "profile":
            profile_form = AIKnowledgeCompanyProfileForm(request.POST, instance=profile)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Company knowledge profile saved.")
                return redirect("erpshop_moduleai_assistant:knowledge_documents")
        elif action == "category":
            category_form = AIKnowledgeCategoryForm(request.POST)
            if category_form.is_valid():
                category = category_form.save(commit=False)
                category.company_id = company_id
                try:
                    category.full_clean()
                    category.save()
                except ValidationError as exc:
                    category_form.add_error(None, exc)
                else:
                    messages.success(request, "Knowledge category saved.")
                    return redirect("erpshop_moduleai_assistant:knowledge_documents")
        elif action in {"flow", "flow_update"}:
            if action == "flow_update":
                editing_flow = _knowledge_flow_for_company(flows, request.POST.get("flow_id"))
            flow_form = AIKnowledgeBusinessFlowForm(request.POST, instance=editing_flow)
            if flow_form.is_valid():
                flow = flow_form.save(commit=False)
                flow.company_id = company_id
                try:
                    flow.full_clean()
                    flow.save()
                except ValidationError as exc:
                    flow_form.add_error(None, exc)
                else:
                    messages.success(request, "Business-flow reference updated." if editing_flow else "Business-flow reference saved.")
                    return redirect("erpshop_moduleai_assistant:knowledge_documents")
        elif action in {"mapping", "mapping_update"}:
            if action == "mapping_update":
                editing_mapping = _knowledge_mapping_for_company(mappings, request.POST.get("mapping_id"))
            mapping_form = AIKnowledgeRecordModelMappingForm(request.POST, instance=editing_mapping, company_id=company_id)
            if mapping_form.is_valid():
                mapping = mapping_form.save(commit=False)
                mapping.company_id = company_id
                try:
                    mapping.full_clean()
                    mapping.save()
                except ValidationError as exc:
                    mapping_form.add_error(None, exc)
                else:
                    messages.success(request, "ERP record-model mapping updated." if editing_mapping else "ERP record-model mapping saved.")
                    return redirect("erpshop_moduleai_assistant:knowledge_documents")
        elif action == "document":
            document_form = AIKnowledgeDocumentForm(request.POST, request.FILES, company_id=company_id)
            form = document_form
        else:
            raise Http404("Unknown knowledge action.")
        if action == "document" and form.is_valid():
            title = form.cleaned_data["title"].strip()
            if documents.filter(title=title).exists():
                form.add_error("title", "A company knowledge document already uses this title.")
            else:
                uploaded_file = form.cleaned_data.get("file")
                extracted_text = (form.cleaned_data.get("extracted_text") or "").strip()
                content_type = ""
                content_sha256 = ""
                try:
                    if uploaded_file:
                        file_text, file_sha256, content_type = _extract_knowledge_upload(uploaded_file)
                        if not extracted_text:
                            extracted_text = file_text
                        content_sha256 = file_sha256
                except ValidationError as exc:
                    form.add_error("file", exc.message)
                if not form.errors:
                    if extracted_text:
                        content_sha256 = content_sha256 or hashlib.sha256(extracted_text.encode("utf-8")).hexdigest()
                    document = AIKnowledgeDocument(
                        company_id=company_id,
                        branch=form.cleaned_data.get("branch"),
                        title=title,
                        category=(form.cleaned_data.get("category") or "").strip(),
                        file=uploaded_file,
                        original_name=Path(uploaded_file.name).name[:255] if uploaded_file else "",
                        content_type=content_type,
                        byte_size=uploaded_file.size if uploaded_file else 0,
                        extracted_text=extracted_text,
                        content_sha256=content_sha256,
                        status=AIKnowledgeDocument.Status.READY if extracted_text else AIKnowledgeDocument.Status.PENDING,
                        metadata={"ingestion": "local", "created_by_user_id": request.user.pk},
                    )
                    document.full_clean()
                    document.save()
                    rebuild_document_chunks(document)
                    messages.success(
                        request,
                        "Knowledge document saved and ready for assistant context."
                        if extracted_text
                        else "Knowledge file saved as pending because its content is not locally extractable.",
                    )
                    return redirect("erpshop_moduleai_assistant:knowledge_documents")
    return render(
        request,
        "erpshop_moduleai_assistant/knowledge_documents.html",
        {
            "documents": documents[:200],
            "profile": profile,
            "categories": categories[:100],
            "flows": flows[:100],
            "mappings": mappings[:100],
            "form": document_form,
            "profile_form": profile_form,
            "category_form": category_form,
            "flow_form": flow_form,
            "editing_flow": editing_flow,
            "editing_mapping": editing_mapping,
            "mapping_form": mapping_form,
            "flow_generation_prerequisites_url": reverse(
                "erpshop_moduleai_assistant:knowledge_flow_generation_prerequisites"
            ),
            "flow_generation_url": reverse("erpshop_moduleai_assistant:generate_knowledge_flow"),
            "flow_generation_job_url_template": reverse(
                "erpshop_moduleai_assistant:knowledge_flow_generation_job",
                kwargs={"job_id": "00000000-0000-0000-0000-000000000000"},
            ),
            "flow_reason_url": reverse("erpshop_moduleai_assistant:reason_knowledge_flow"),
        },
    )


@require_POST
@assistant_access_required
def set_knowledge_document_state(request, document_id):
    if not _knowledge_access_required(request):
        return redirect("erpshop_moduleai_assistant:home")
    document = get_object_or_404(AIKnowledgeDocument, pk=document_id, company_id=_active_company(request))
    action = request.POST.get("action")
    if action == "archive":
        document.active = False
        document.status = AIKnowledgeDocument.Status.ARCHIVED
        message = "Knowledge document archived. It is no longer included in AI context."
    elif action == "restore":
        document.active = True
        document.status = AIKnowledgeDocument.Status.READY if document.extracted_text else AIKnowledgeDocument.Status.PENDING
        message = "Knowledge document restored."
    else:
        raise Http404("Unknown knowledge action.")
    document.save(update_fields=["active", "status", "updated_at"])
    messages.success(request, message)
    return redirect("erpshop_moduleai_assistant:knowledge_documents")


@require_POST
@assistant_access_required
def set_knowledge_category_state(request, category_id):
    if not _knowledge_access_required(request):
        return redirect("erpshop_moduleai_assistant:home")
    category = get_object_or_404(AIKnowledgeCategory, pk=category_id, company_id=_active_company(request))
    action = request.POST.get("action")
    if action == "archive":
        category.active = False
        message = "Knowledge category archived. Existing document labels are retained."
    elif action == "activate":
        category.active = True
        message = "Knowledge category activated."
    else:
        raise Http404("Unknown knowledge action.")
    category.save(update_fields=["active", "updated_at"])
    messages.success(request, message)
    return redirect("erpshop_moduleai_assistant:knowledge_documents")


@require_POST
@assistant_access_required
def set_knowledge_flow_state(request, flow_id):
    if not _knowledge_access_required(request):
        return redirect("erpshop_moduleai_assistant:home")
    flow = get_object_or_404(AIKnowledgeBusinessFlow, pk=flow_id, company_id=_active_company(request))
    action = request.POST.get("action")
    if action == "archive":
        flow.status = AIKnowledgeBusinessFlow.Status.ARCHIVED
        message = "Business-flow reference archived. It is no longer included in Assistant context."
    elif action == "activate":
        flow.status = AIKnowledgeBusinessFlow.Status.ACTIVE
        message = "Business-flow reference activated."
    else:
        raise Http404("Unknown knowledge action.")
    flow.save(update_fields=["status", "updated_at"])
    messages.success(request, message)
    return redirect("erpshop_moduleai_assistant:knowledge_documents")


@require_GET
@assistant_access_required
def knowledge_flow_detail(request, flow_id):
    if not _knowledge_access_required(request):
        return JsonResponse({"detail": "Assistant administrator access is required."}, status=403)
    flow = get_object_or_404(AIKnowledgeBusinessFlow, pk=flow_id, company_id=_active_company(request))
    return JsonResponse(
        {
            "id": flow.pk,
            "name": flow.name,
            "trigger": flow.trigger,
            "description": flow.description,
            "steps": flow.steps,
            "edges": flow.edges,
            "status": flow.status,
        }
    )


@require_GET
@assistant_access_required
def knowledge_flow_map(request, flow_id):
    """Return the reviewed flow in the node/edge shape used by HMX flow maps."""
    if not _knowledge_access_required(request):
        return JsonResponse({"detail": "Assistant administrator access is required."}, status=403)
    flow = get_object_or_404(AIKnowledgeBusinessFlow, pk=flow_id, company_id=_active_company(request))
    nodes_by_sequence = {}
    for position, raw_step in enumerate(flow.steps if isinstance(flow.steps, list) else [], start=1):
        if not isinstance(raw_step, dict):
            continue
        try:
            sequence = int(raw_step.get("sequence", position))
        except (TypeError, ValueError):
            sequence = position
        description = str(raw_step.get("description", "")).strip()
        if description:
            nodes_by_sequence[sequence] = {
                "label": description,
                "description": description,
                "sort_order": sequence,
                "metadata": {"sequence": sequence},
            }
    configured_edges = flow.edges if isinstance(flow.edges, list) else []
    if not configured_edges:
        sequences = sorted(nodes_by_sequence)
        configured_edges = [
            {"from_sequence": source, "to_sequence": target, "label": "next"}
            for source, target in zip(sequences, sequences[1:])
        ]
    edges = []
    for raw_edge in configured_edges:
        try:
            source_sequence = int(raw_edge.get("from_sequence"))
            target_sequence = int(raw_edge.get("to_sequence"))
        except (AttributeError, TypeError, ValueError):
            continue
        source_node = nodes_by_sequence.get(source_sequence)
        target_node = nodes_by_sequence.get(target_sequence)
        if source_node and target_node:
            edges.append(
                {
                    "source_node_label": source_node["label"],
                    "target_node_label": target_node["label"],
                    "label": str(raw_edge.get("label") or "next")[:160],
                    "metadata": {"source_sequence": source_sequence, "target_sequence": target_sequence},
                }
            )
    return JsonResponse(
        {
            "id": flow.pk,
            "name": flow.name,
            "description": flow.description,
            "nodes": [nodes_by_sequence[sequence] for sequence in sorted(nodes_by_sequence)],
            "edges": edges,
        }
    )


def _flow_generation_job_payload(job):
    return {
        "job_id": str(job.job_id),
        "status": job.state,
        "error_reason": job.error_reason,
        "business_flow_map_id": job.result_flow_id,
        "flow_id": job.result_flow_id,
    }


@require_GET
@assistant_access_required
def knowledge_flow_generation_prerequisites(request):
    if not _knowledge_access_required(request):
        return JsonResponse({"detail": "Assistant administrator access is required."}, status=403)
    company_id = _active_company(request)
    return JsonResponse(flow_generation_prerequisites(company_id, _configuration(company_id)))


@require_POST
@assistant_access_required
def generate_knowledge_flow(request):
    if not _knowledge_access_required(request):
        return JsonResponse({"detail": "Assistant administrator access is required."}, status=403)
    try:
        payload = _json_body(request)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    overwrite_manual = payload.get("overwriteManual", payload.get("overwrite_manual", False))
    if not isinstance(overwrite_manual, bool):
        return JsonResponse({"detail": "overwriteManual must be a boolean."}, status=400)
    company_id = _active_company(request)
    configuration = _configuration(company_id)
    prerequisites = flow_generation_prerequisites(company_id, configuration)
    if not prerequisites["can_generate"]:
        return JsonResponse({"detail": "Business-flow generation is not ready.", "prerequisites": prerequisites}, status=409)
    job = AIKnowledgeBusinessFlowGenerationJob(
        company_id=company_id,
        configuration=configuration,
        requested_by=request.user,
        overwrite_manual=overwrite_manual,
        input_snapshot={"requested_via": "knowledge_flow_generation", "references": prerequisites["references"]},
    )
    job.full_clean()
    job.save()
    try:
        queue_business_flow_generation.delay(str(job.job_id))
    except Exception:
        job.state = AIKnowledgeBusinessFlowGenerationJob.State.FAILED
        job.error_reason = "The background worker could not be reached. No provider request was sent."
        job.completed_at = timezone.now()
        job.save(update_fields=["state", "error_reason", "completed_at", "updated_at"])
        return JsonResponse(_flow_generation_job_payload(job), status=503)
    return JsonResponse(_flow_generation_job_payload(job), status=202)


@require_GET
@assistant_access_required
def knowledge_flow_generation_job(request, job_id):
    if not _knowledge_access_required(request):
        return JsonResponse({"detail": "Assistant administrator access is required."}, status=403)
    job = get_object_or_404(
        AIKnowledgeBusinessFlowGenerationJob.objects.select_related("result_flow"),
        company_id=_active_company(request),
        job_id=job_id,
    )
    return JsonResponse(_flow_generation_job_payload(job))


@require_POST
@assistant_access_required
def reason_knowledge_flow(request):
    if not _knowledge_access_required(request):
        return JsonResponse({"detail": "Assistant administrator access is required."}, status=403)
    try:
        payload = _json_body(request)
        query = payload.get("query", "")
        if not isinstance(query, str):
            raise ValueError("query must be text.")
        result = reason_about_business_flows(_active_company(request), query)
    except (FlowGenerationError, ValueError) as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    return JsonResponse(result)


@require_POST
@assistant_access_required
def set_knowledge_mapping_state(request, mapping_id):
    if not _knowledge_access_required(request):
        return redirect("erpshop_moduleai_assistant:home")
    mapping = get_object_or_404(AIKnowledgeRecordModelMapping, pk=mapping_id, company_id=_active_company(request))
    action = request.POST.get("action")
    if action == "archive":
        mapping.active = False
        message = "ERP record-model mapping archived. It is retained for auditability."
    elif action == "activate":
        mapping.active = True
        message = "ERP record-model mapping activated."
    else:
        raise Http404("Unknown knowledge action.")
    mapping.save(update_fields=["active", "updated_at"])
    messages.success(request, message)
    return redirect("erpshop_moduleai_assistant:knowledge_documents")


def _knowledge_graph_access(request):
    if _knowledge_access_required(request):
        return True
    return False


@require_GET
@assistant_access_required
def knowledge_graph_page(request):
    if not _knowledge_graph_access(request):
        return redirect("erpshop_moduleai_assistant:knowledge_documents")
    return render(
        request,
        "erpshop_moduleai_assistant/knowledge_graph.html",
        {
            "graph_view_url": reverse("erpshop_moduleai_assistant:knowledge_graph_view"),
            "graph_overview_url": reverse("erpshop_moduleai_assistant:knowledge_graph_overview"),
            "graph_search_url": reverse("erpshop_moduleai_assistant:knowledge_graph_search"),
            "graph_neighbors_url": reverse("erpshop_moduleai_assistant:knowledge_graph_neighbors"),
        },
    )


@require_GET
@assistant_access_required
def knowledge_graph_status(request):
    if not _knowledge_graph_access(request):
        return JsonResponse({"detail": "Assistant administrator access is required."}, status=403)
    company_id = _active_company(request)
    return JsonResponse({"status": "ready", "overview": graph_overview(company_id, branch_id=_active_branch_id(request))})


@require_GET
@assistant_access_required
def knowledge_graph_overview(request):
    if not _knowledge_graph_access(request):
        return JsonResponse({"detail": "Assistant administrator access is required."}, status=403)
    return JsonResponse(graph_overview(_active_company(request), branch_id=_active_branch_id(request)))


@require_GET
@assistant_access_required
def knowledge_graph_view(request):
    if not _knowledge_graph_access(request):
        return JsonResponse({"detail": "Assistant administrator access is required."}, status=403)
    return JsonResponse(
        build_knowledge_graph(
            _active_company(request),
            branch_id=_active_branch_id(request),
            limit=request.GET.get("limit"),
            source=request.GET.get("source", "all"),
            node_type=request.GET.get("type", ""),
        )
    )


@require_GET
@assistant_access_required
def knowledge_graph_search(request):
    if not _knowledge_graph_access(request):
        return JsonResponse({"detail": "Assistant administrator access is required."}, status=403)
    return JsonResponse(
        graph_search(
            _active_company(request),
            request.GET.get("q", ""),
            branch_id=_active_branch_id(request),
            limit=request.GET.get("limit"),
            source=request.GET.get("source", "all"),
            node_type=request.GET.get("type", ""),
        )
    )


@require_GET
@assistant_access_required
def knowledge_graph_node(request, node_id):
    if not _knowledge_graph_access(request):
        return JsonResponse({"detail": "Assistant administrator access is required."}, status=403)
    node = graph_node(_active_company(request), node_id[:180], branch_id=_active_branch_id(request))
    if node is None:
        return JsonResponse({"detail": "Knowledge graph node not found."}, status=404)
    return JsonResponse({"node": node})


@require_GET
@assistant_access_required
def knowledge_graph_neighbors(request):
    if not _knowledge_graph_access(request):
        return JsonResponse({"detail": "Assistant administrator access is required."}, status=403)
    result = graph_neighbors(
        _active_company(request),
        request.GET.get("node_id", "")[:180],
        branch_id=_active_branch_id(request),
        depth=request.GET.get("depth", 1),
        limit=request.GET.get("limit"),
    )
    if result is None:
        return JsonResponse({"detail": "Knowledge graph node not found."}, status=404)
    return JsonResponse(result)


@require_GET
@assistant_access_required
def knowledge_graph_path(request):
    if not _knowledge_graph_access(request):
        return JsonResponse({"detail": "Assistant administrator access is required."}, status=403)
    result = graph_path(
        _active_company(request),
        request.GET.get("from_node_id", "")[:180],
        request.GET.get("to_node_id", "")[:180],
        branch_id=_active_branch_id(request),
        max_length=request.GET.get("max_length", 6),
    )
    if result is None:
        return JsonResponse({"detail": "One or more knowledge graph nodes were not found."}, status=404)
    return JsonResponse(result)


@require_GET
@assistant_access_required
def knowledge_graph_trace(request):
    if not _knowledge_graph_access(request):
        return JsonResponse({"detail": "Assistant administrator access is required."}, status=403)
    result = graph_trace(
        _active_company(request),
        request.GET.get("target_type", ""),
        request.GET.get("target_id", "")[:120],
        branch_id=_active_branch_id(request),
        limit=request.GET.get("limit"),
    )
    if result is None:
        return JsonResponse({"detail": "Knowledge graph target was not found."}, status=404)
    return JsonResponse(result)
