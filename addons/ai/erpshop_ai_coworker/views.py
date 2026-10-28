from functools import wraps

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from modules.apphub.models import ERPApp

from .forms import CoworkerForm, CoworkerTaskForm
from .models import AIAgent, AIConversation, AIMessage, AITask, AITaskEvent, AIWorkspace
from .tasks import process_coworker_task


APP_SLUG = "ai-coworker"
TASK_PAGE_SIZE = 50


def _active_scope(request):
    context = getattr(request, "organization_context", None)
    company_id = getattr(context, "company_id", None)
    branch_id = getattr(context, "branch_id", None)
    if not company_id:
        raise PermissionError("Select an active company before using AI Coworker.")
    return company_id, branch_id


def _can_manage(user):
    return user.is_superuser or user.has_perm("gsshop_ai.manage_ai_coworkers")


def _can_use(user):
    return user.is_superuser or (
        user.has_perm("gsshop_ai.view_aiagent") and user.has_perm("gsshop_ai.add_aitask")
    )


def coworker_access_required(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        app = ERPApp.objects.filter(slug=APP_SLUG, enabled=True).first()
        if app is None:
            django_messages.info(request, "Install ERPShop AI Coworker before opening this workspace.")
            return redirect("app_manager")
        if not _can_use(request.user):
            django_messages.error(request, "Your role does not include AI Coworker access.")
            return redirect("dashboard")
        try:
            _active_scope(request)
        except PermissionError as exc:
            django_messages.error(request, str(exc))
            return redirect("dashboard")
        request.session["active_erp_app"] = APP_SLUG
        request.session.modified = True
        return view(request, *args, **kwargs)

    return wrapped


def _agents(company_id):
    return (
        AIAgent.objects.filter(workspace__company_id=company_id)
        .select_related("workspace")
        .order_by("-active", "name", "pk")
    )


def _tasks(request):
    company_id, _branch_id = _active_scope(request)
    queryset = AITask.objects.filter(company_id=company_id).select_related(
        "agent", "requested_by", "branch"
    )
    if not _can_manage(request.user):
        queryset = queryset.filter(requested_by=request.user)
    return queryset


@require_GET
@coworker_access_required
def home(request):
    company_id, _branch_id = _active_scope(request)
    agents = _agents(company_id)
    selected_agent = None
    raw_agent = request.GET.get("agent")
    if raw_agent:
        try:
            selected_agent = agents.get(pk=int(raw_agent))
        except (AIAgent.DoesNotExist, TypeError, ValueError) as exc:
            raise Http404("Coworker not found.") from exc
    if selected_agent is None:
        selected_agent = agents.filter(active=True).first()
    task_form = CoworkerTaskForm(
        company_id=company_id,
        user=request.user,
        initial={"agent": selected_agent, "task_type": AITask.TaskType.PLAN},
    )
    return render(
        request,
        "erpshop_ai_coworker/home.html",
        {
            "agents": agents,
            "selected_agent": selected_agent,
            "tasks": _tasks(request)[:TASK_PAGE_SIZE],
            "task_form": task_form,
            "can_manage": _can_manage(request.user),
        },
    )


@require_GET
@coworker_access_required
def settings(request, agent_id=None):
    if not _can_manage(request.user):
        raise Http404("Coworker settings not found.")
    company_id, _branch_id = _active_scope(request)
    agent = get_object_or_404(_agents(company_id), pk=agent_id) if agent_id else None
    workspace = agent.workspace if agent else AIWorkspace.objects.filter(company_id=company_id, name="ERP Coworkers").first()
    return render(
        request,
        "erpshop_ai_coworker/settings.html",
        {"agent": agent, "agents": _agents(company_id), "form": CoworkerForm(instance=agent, workspace=workspace)},
    )


@require_POST
@coworker_access_required
def save_agent(request, agent_id=None):
    if not _can_manage(request.user):
        raise Http404("Coworker settings not found.")
    company_id, _branch_id = _active_scope(request)
    agent = get_object_or_404(_agents(company_id), pk=agent_id) if agent_id else None
    workspace = agent.workspace if agent else AIWorkspace.objects.filter(company_id=company_id, name="ERP Coworkers").first()
    form = CoworkerForm(request.POST, instance=agent, workspace=workspace)
    if not form.is_valid():
        return render(
            request,
            "erpshop_ai_coworker/settings.html",
            {"agent": agent, "agents": _agents(company_id), "form": form},
            status=400,
        )
    with transaction.atomic():
        if workspace is None:
            workspace = AIWorkspace.objects.create(
                company_id=company_id,
                name="ERP Coworkers",
                description="Governed AI coworkers for this company.",
            )
        saved = form.save(commit=False)
        saved.workspace = workspace
        saved.full_clean()
        saved.save()
    django_messages.success(request, f"Coworker {saved.name} saved.")
    return redirect(f"{reverse('erpshop_ai_coworker:settings_edit', args=[saved.pk])}")


@require_POST
@coworker_access_required
def create_task(request):
    company_id, branch_id = _active_scope(request)
    form = CoworkerTaskForm(request.POST, company_id=company_id, user=request.user)
    if not form.is_valid():
        return render(
            request,
            "erpshop_ai_coworker/home.html",
            {
                "agents": _agents(company_id),
                "selected_agent": form.cleaned_data.get("agent"),
                "tasks": _tasks(request)[:TASK_PAGE_SIZE],
                "task_form": form,
                "can_manage": _can_manage(request.user),
            },
            status=400,
        )
    if form.cleaned_data["task_type"] != AITask.TaskType.PLAN and not branch_id:
        form.add_error("entity", "Select an active branch before using ERP record context.")
        return render(
            request,
            "erpshop_ai_coworker/home.html",
            {
                "agents": _agents(company_id),
                "selected_agent": form.cleaned_data["agent"],
                "tasks": _tasks(request)[:TASK_PAGE_SIZE],
                "task_form": form,
                "can_manage": _can_manage(request.user),
            },
            status=400,
        )

    data = form.cleaned_data
    with transaction.atomic():
        conversation = AIConversation.objects.create(
            agent=data["agent"],
            user=request.user,
            title=data["title"],
        )
        AIMessage.objects.create(
            conversation=conversation,
            role=AIMessage.Role.USER,
            content=data["instruction"],
            metadata={"task_type": data["task_type"], "entity": data["entity"]},
        )
        task = AITask(
            agent=data["agent"],
            company_id=company_id,
            branch_id=branch_id,
            requested_by=request.user,
            conversation=conversation,
            title=data["title"],
            task_type=data["task_type"],
            instruction=data["instruction"],
            entity=data["entity"],
            selected_fields=data["selected_fields"],
            filters=data["filters"],
        )
        task.full_clean()
        task.save()
        AITaskEvent.objects.create(task=task, actor=request.user, event="queued")
    try:
        process_coworker_task.delay(task.pk)
    except Exception:
        with transaction.atomic():
            task = AITask.objects.select_for_update().get(pk=task.pk)
            task.status = AITask.Status.FAILED
            task.error_message = "The background worker could not be reached."
            task.completed_at = timezone.now()
            task.save(update_fields=["status", "error_message", "completed_at", "updated_at"])
            AITaskEvent.objects.create(task=task, event="queue_failed")
        django_messages.error(request, "The AI task could not be queued; no provider request was sent.")
    return redirect(reverse("erpshop_ai_coworker:task_detail", args=[task.pk]))


@require_GET
@coworker_access_required
def task_detail(request, task_id):
    task = get_object_or_404(_tasks(request).select_related("conversation", "company"), pk=task_id)
    return render(
        request,
        "erpshop_ai_coworker/task_detail.html",
        {
            "task": task,
            "events": task.events.select_related("actor").order_by("id")[:100],
            "conversation_messages": task.conversation.messages.order_by("id")[:100] if task.conversation_id else [],
        },
    )


@require_GET
@coworker_access_required
def task_state(request, task_id):
    task = get_object_or_404(_tasks(request).only("id", "status", "completed_at", "updated_at"), pk=task_id)
    return JsonResponse(
        {
            "id": task.pk,
            "status": task.status,
            "terminal": task.status in {AITask.Status.COMPLETED, AITask.Status.FAILED, AITask.Status.CANCELLED},
            "updated_at": task.updated_at.isoformat(),
        }
    )


@require_POST
@coworker_access_required
def cancel_task(request, task_id):
    with transaction.atomic():
        task = get_object_or_404(_tasks(request).select_for_update(of=("self",)), pk=task_id)
        if task.status == AITask.Status.QUEUED:
            task.status = AITask.Status.CANCELLED
            task.cancel_requested = True
            task.completed_at = timezone.now()
            task.save(update_fields=["status", "cancel_requested", "completed_at", "updated_at"])
            event = "cancelled"
        elif task.status == AITask.Status.RUNNING:
            task.cancel_requested = True
            task.save(update_fields=["cancel_requested", "updated_at"])
            event = "cancel_requested"
        else:
            django_messages.info(request, "This task is already in a terminal state.")
            return redirect(reverse("erpshop_ai_coworker:task_detail", args=[task.pk]))
        AITaskEvent.objects.create(task=task, actor=request.user, event=event)
    django_messages.success(request, "Cancellation recorded.")
    return redirect(reverse("erpshop_ai_coworker:task_detail", args=[task.pk]))
