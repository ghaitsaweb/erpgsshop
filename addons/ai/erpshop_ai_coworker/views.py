import hashlib
import secrets
from datetime import timedelta
from functools import wraps
from uuid import uuid4

from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Avg, Count, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from modules.apphub.models import ERPApp

from .forms import (
    ChannelConfigurationForm,
    CompanyCreditPolicyForm,
    CreditAdjustmentForm,
    CoworkerCronForm,
    CoworkerForm,
    CoworkerHeartbeatForm,
    CoworkerMemoryForm,
    CoworkerScheduleForm,
    CoworkerSubagentForm,
    CoworkerTaskForm,
    ModelPricingRegistryForm,
    ModelTemplateRegistryForm,
    ProviderRegistryForm,
    WebhookEndpointForm,
)
from .models import (
    AIAgent,
    AIChannelConfiguration,
    AIChannelInboundEvent,
    AICompanyCredit,
    AICreditLedger,
    AICoworkerChannel,
    AICoworkerCron,
    AICoworkerConnectionCheck,
    AICoworkerHeartbeat,
    AICoworkerMemory,
    AICoworkerSubagent,
    AIChannelSession,
    AIChannelSessionSummary,
    AIModelPricing,
    AIModelTemplate,
    AIModelCatalogAuditEvent,
    AIProvider,
    AITask,
    AITaskEvent,
    AIToolApproval,
    AIUsageEvent,
    AIWorkspace,
    AIWebhookEndpoint,
    AIWebhookEvent,
)
from .services import next_schedule_run, queue_coworker_task, queue_subagent
from .tasks import process_connection_check, process_coworker_task
from .tool_runtime import decide_approval, execute_approved_tool


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


def _schedules(company_id):
    return (
        AICoworkerSchedule.objects.filter(company_id=company_id)
        .select_related("agent", "branch", "requested_by")
        .order_by("active", "next_run_at", "id")
    )


def _runtime_scope(company_id):
    return AIAgent.objects.filter(workspace__company_id=company_id)


def _subagents(company_id):
    return (
        AICoworkerSubagent.objects.filter(agent__workspace__company_id=company_id)
        .select_related("agent", "parent_subagent")
        .order_by("-created_at", "-id")
    )


def _crons(company_id):
    return (
        AICoworkerCron.objects.filter(agent__workspace__company_id=company_id)
        .select_related("agent")
        .order_by("active", "name", "id")
    )


def _heartbeats(company_id):
    return (
        AICoworkerHeartbeat.objects.filter(agent__workspace__company_id=company_id)
        .select_related("agent")
        .order_by("enabled", "name", "id")
    )


def _channels(company_id):
    return (
        AIChannelConfiguration.objects.filter(company_id=company_id)
        .select_related("coworker_link__agent", "owner_user")
        .order_by("name", "pk")
    )


def _webhook_endpoints(company_id):
    return (
        AIWebhookEndpoint.objects.filter(agent__workspace__company_id=company_id)
        .select_related("agent")
        .order_by("agent__name", "name", "pk")
    )


def _usage_totals(queryset):
    totals = queryset.aggregate(
        runs=Count("id"),
        input_tokens=Sum("input_tokens"),
        output_tokens=Sum("output_tokens"),
        total_cost=Sum("total_cost"),
        average_latency_ms=Avg("latency_ms"),
    )
    return {
        "runs": totals["runs"] or 0,
        "input_tokens": totals["input_tokens"] or 0,
        "output_tokens": totals["output_tokens"] or 0,
        "total_cost": totals["total_cost"] or 0,
        "average_latency_ms": round(totals["average_latency_ms"] or 0),
    }


def _catalog_admin_required(request):
    if not request.user.is_superuser:
        raise Http404("AI model catalog is not available.")


def _credit_admin_required(request):
    if not request.user.is_superuser:
        raise Http404("AI credit policy is not available.")


def _render_credit_policy(request, *, company_id, policy_form=None, adjustment_form=None, status=200):
    credit = AICompanyCredit.objects.filter(company_id=company_id).first()
    return render(
        request,
        "erpshop_ai_coworker/credit_policy.html",
        {
            "credit": credit,
            "policy_form": policy_form or CompanyCreditPolicyForm(instance=credit, initial={"currency": "USD"}),
            "adjustment_form": adjustment_form or CreditAdjustmentForm(),
            "ledger_entries": AICreditLedger.objects.filter(company_id=company_id).select_related("created_by").order_by("-id")[:100],
        },
        status=status,
    )


def _record_catalog_event(*, actor, event, obj, details):
    AIModelCatalogAuditEvent.objects.create(
        actor=actor,
        event=event,
        object_type=obj._meta.model_name,
        object_id=obj.pk,
        details=details,
    )


def _render_model_catalog(request, *, provider=None, template=None, pricing=None, provider_form=None, template_form=None, pricing_form=None, status=200):
    providers = AIProvider.objects.order_by("name", "pk")
    templates = AIModelTemplate.objects.select_related("provider").order_by("name", "pk")
    pricings = AIModelPricing.objects.select_related("template", "template__provider").order_by("-effective_from", "id")
    if pricing is not None and template is None:
        template = pricing.template
    if template is not None and provider is None:
        provider = template.provider
    default_template = template or templates.filter(active=True).first() or templates.first()
    return render(
        request,
        "erpshop_ai_coworker/model_catalog.html",
        {
            "providers": providers,
            "templates": templates,
            "pricings": pricings[:100],
            "catalog_events": AIModelCatalogAuditEvent.objects.select_related("actor").order_by("-id")[:50],
            "provider": provider,
            "template": template,
            "pricing": pricing,
            "provider_form": provider_form or ProviderRegistryForm(instance=provider),
            "template_form": template_form or ModelTemplateRegistryForm(instance=template, initial={"provider": provider}),
            "pricing_form": pricing_form or ModelPricingRegistryForm(
                instance=pricing,
                initial={"template": default_template, "effective_from": timezone.localtime().replace(second=0, microsecond=0)},
            ),
        },
        status=status,
    )


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
            "can_model_admin": request.user.is_superuser,
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
        {
            "agent": agent,
            "agents": _agents(company_id),
            "form": CoworkerForm(instance=agent, workspace=workspace),
            "connection_checks": (
                AICoworkerConnectionCheck.objects.filter(agent=agent).select_related("requested_by")[:10]
                if agent
                else []
            ),
        },
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
            {"agent": agent, "agents": _agents(company_id), "form": form, "connection_checks": []},
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
        if saved.owner_user_id is None:
            saved.owner_user = request.user
        saved.full_clean()
        saved.save()
    django_messages.success(request, f"Coworker {saved.name} saved.")
    return redirect(f"{reverse('erpshop_ai_coworker:settings_edit', args=[saved.pk])}")


@require_POST
@coworker_access_required
def test_connection(request, agent_id):
    if not _can_manage(request.user):
        raise Http404("Coworker settings not found.")
    company_id, _branch_id = _active_scope(request)
    agent = get_object_or_404(_agents(company_id), pk=agent_id)
    check = AICoworkerConnectionCheck.objects.create(agent=agent, requested_by=request.user)
    try:
        process_connection_check.delay(check.pk)
    except Exception:
        check.state = AICoworkerConnectionCheck.State.FAILED
        check.safe_error = "The readiness check could not be queued. Verify the worker service and retry."
        check.completed_at = timezone.now()
        check.save(update_fields=["state", "safe_error", "completed_at", "updated_at"])
        django_messages.error(request, "Provider readiness check could not be queued.")
    else:
        django_messages.success(request, "Provider readiness check queued. Refresh this page to see the safe result.")
    return redirect("erpshop_ai_coworker:settings_edit", agent_id=agent.pk)


@require_GET
@coworker_access_required
def runtime(request):
    if not _can_manage(request.user):
        raise Http404("Coworker runtime is not available.")
    company_id, _branch_id = _active_scope(request)
    agents = _runtime_scope(company_id).select_related("workspace").order_by("name", "pk")
    selected_agent = agents.filter(pk=request.GET.get("agent")).first() if request.GET.get("agent") else agents.first()
    memory = None
    if selected_agent:
        memory = AICoworkerMemory.objects.filter(
            agent=selected_agent,
            participant__isnull=True,
            memory_type=AICoworkerMemory.MemoryType.MEMORY,
        ).first()
    return render(
        request,
        "erpshop_ai_coworker/runtime.html",
        {
            "agents": agents,
            "selected_agent": selected_agent,
            "memory_form": CoworkerMemoryForm(
                company_id=company_id,
                initial={"agent": selected_agent, "content": memory.content if memory else ""},
            ),
            "memories": AICoworkerMemory.objects.filter(agent__workspace__company_id=company_id, participant__isnull=True)
            .select_related("agent")
            .order_by("agent__name", "memory_type", "id"),
            "sessions": AIChannelSession.objects.filter(agent__workspace__company_id=company_id)
            .select_related("agent", "participant")
            .order_by("-updated_at", "-id")[:50],
            "summaries": AIChannelSessionSummary.objects.filter(agent__workspace__company_id=company_id)
            .select_related("agent", "session")
            .order_by("-id")[:50],
            "subagents": _subagents(company_id)[:50],
            "crons": _crons(company_id)[:50],
            "heartbeats": _heartbeats(company_id)[:50],
            "can_manage": True,
        },
    )


@require_GET
@coworker_access_required
def operations(request):
    if not _can_manage(request.user):
        raise Http404("AI operations are not available.")
    company_id, _branch_id = _active_scope(request)
    now = timezone.now()
    since_24h, since_7d = now - timedelta(hours=24), now - timedelta(days=7)
    usage = AIUsageEvent.objects.filter(company_id=company_id)
    tasks = AITask.objects.filter(company_id=company_id)
    task_totals = tasks.filter(created_at__gte=since_7d).aggregate(
        total=Count("id"),
        completed=Count("id", filter=Q(status=AITask.Status.COMPLETED)),
        failed=Count("id", filter=Q(status=AITask.Status.FAILED)),
        cancelled=Count("id", filter=Q(status=AITask.Status.CANCELLED)),
        queued=Count("id", filter=Q(status__in=[AITask.Status.QUEUED, AITask.Status.RUNNING])),
    )
    feedback_totals = tasks.filter(feedback_at__gte=since_7d).aggregate(
        total=Count("id"),
        helpful=Count("id", filter=Q(feedback=AITask.Feedback.HELPFUL)),
        unhelpful=Count("id", filter=Q(feedback=AITask.Feedback.UNHELPFUL)),
    )
    approvals = (
        AIToolApproval.objects.filter(
            agent__workspace__company_id=company_id,
            state=AIToolApproval.State.PENDING,
            expires_at__gt=now,
        )
        .select_related("agent", "task")
        .order_by("expires_at", "id")
    )
    return render(
        request,
        "erpshop_ai_coworker/operations.html",
        {
            "credit": AICompanyCredit.objects.filter(company_id=company_id).first(),
            "usage_24h": _usage_totals(usage.filter(created_at__gte=since_24h)),
            "usage_7d": _usage_totals(usage.filter(created_at__gte=since_7d)),
            "task_totals": {key: value or 0 for key, value in task_totals.items()},
            "feedback_totals": {key: value or 0 for key, value in feedback_totals.items()},
            "agent_usage": (
                usage.filter(created_at__gte=since_7d)
                .values("agent__name", "currency")
                .annotate(
                    runs=Count("id"),
                    input_tokens=Sum("input_tokens"),
                    output_tokens=Sum("output_tokens"),
                    total_cost=Sum("total_cost"),
                    average_latency_ms=Avg("latency_ms"),
                )
                .order_by("-total_cost", "agent__name")[:25]
            ),
            "pending_approvals": approvals[:50],
            "recent_usage": usage.select_related("agent", "task").order_by("-id")[:50],
            "recent_failures": tasks.filter(status=AITask.Status.FAILED).select_related("agent").order_by("-id")[:25],
        },
    )


@require_GET
@coworker_access_required
def model_catalog(request):
    _catalog_admin_required(request)
    _company_id, _branch_id = _active_scope(request)
    provider = AIProvider.objects.filter(pk=request.GET.get("provider")).first() if request.GET.get("provider") else None
    template = AIModelTemplate.objects.select_related("provider").filter(pk=request.GET.get("template")).first() if request.GET.get("template") else None
    pricing = AIModelPricing.objects.select_related("template", "template__provider").filter(pk=request.GET.get("pricing")).first() if request.GET.get("pricing") else None
    return _render_model_catalog(request, provider=provider, template=template, pricing=pricing)


@require_POST
@coworker_access_required
def save_provider(request, provider_id=None):
    _catalog_admin_required(request)
    _company_id, _branch_id = _active_scope(request)
    provider = get_object_or_404(AIProvider, pk=provider_id) if provider_id else None
    form = ProviderRegistryForm(request.POST, instance=provider)
    if not form.is_valid():
        return _render_model_catalog(request, provider=provider, provider_form=form, status=400)
    with transaction.atomic():
        saved = form.save(commit=False)
        saved.full_clean()
        saved.save()
        _record_catalog_event(
            actor=request.user,
            event="provider_saved",
            obj=saved,
            details={"name": saved.name, "backend": saved.backend, "active": saved.active},
        )
    django_messages.success(request, f"Provider {saved.display_name} saved. No credential value was stored.")
    return redirect(f"{reverse('erpshop_ai_coworker:model_catalog')}?provider={saved.pk}")


@require_POST
@coworker_access_required
def save_model_template(request, template_id=None):
    _catalog_admin_required(request)
    _company_id, _branch_id = _active_scope(request)
    template = get_object_or_404(AIModelTemplate.objects.select_related("provider"), pk=template_id) if template_id else None
    form = ModelTemplateRegistryForm(request.POST, instance=template)
    if not form.is_valid():
        return _render_model_catalog(request, template=template, template_form=form, status=400)
    with transaction.atomic():
        saved = form.save(commit=False)
        saved.full_clean()
        saved.save()
        _record_catalog_event(
            actor=request.user,
            event="model_template_saved",
            obj=saved,
            details={"name": saved.name, "provider_id": saved.provider_id, "model_name": saved.model_name, "active": saved.active},
        )
    django_messages.success(request, f"Model template {saved.name} saved.")
    return redirect(f"{reverse('erpshop_ai_coworker:model_catalog')}?template={saved.pk}")


@require_POST
@coworker_access_required
def save_model_pricing(request, pricing_id=None):
    _catalog_admin_required(request)
    _company_id, _branch_id = _active_scope(request)
    pricing = get_object_or_404(AIModelPricing.objects.select_related("template", "template__provider"), pk=pricing_id) if pricing_id else None
    form = ModelPricingRegistryForm(request.POST, instance=pricing)
    if not form.is_valid():
        return _render_model_catalog(request, pricing=pricing, pricing_form=form, status=400)
    with transaction.atomic():
        saved = form.save(commit=False)
        saved.full_clean()
        saved.save()
        _record_catalog_event(
            actor=request.user,
            event="model_pricing_saved",
            obj=saved,
            details={"template_id": saved.template_id, "currency": saved.currency, "active": saved.active},
        )
    django_messages.success(request, f"Pricing for {saved.template.name} saved.")
    return redirect(f"{reverse('erpshop_ai_coworker:model_catalog')}?pricing={saved.pk}")


@require_GET
@coworker_access_required
def credit_policy(request):
    _credit_admin_required(request)
    company_id, _branch_id = _active_scope(request)
    return _render_credit_policy(request, company_id=company_id)


@require_POST
@coworker_access_required
def save_credit_policy(request):
    _credit_admin_required(request)
    company_id, _branch_id = _active_scope(request)
    current = AICompanyCredit.objects.filter(company_id=company_id).first()
    form = CompanyCreditPolicyForm(request.POST, instance=current)
    if not form.is_valid():
        return _render_credit_policy(request, company_id=company_id, policy_form=form, status=400)
    with transaction.atomic():
        credit, _created = AICompanyCredit.objects.select_for_update().get_or_create(company_id=company_id)
        locked_form = CompanyCreditPolicyForm(request.POST, instance=credit)
        if not locked_form.is_valid():
            return _render_credit_policy(request, company_id=company_id, policy_form=locked_form, status=400)
        saved = locked_form.save(commit=False)
        saved.full_clean()
        saved.save()
    django_messages.success(request, "AI credit policy saved. The balance was not changed.")
    return redirect(reverse("erpshop_ai_coworker:credit_policy"))


@require_POST
@coworker_access_required
def adjust_credit(request):
    _credit_admin_required(request)
    company_id, _branch_id = _active_scope(request)
    form = CreditAdjustmentForm(request.POST)
    if not form.is_valid():
        return _render_credit_policy(request, company_id=company_id, adjustment_form=form, status=400)
    with transaction.atomic():
        credit = AICompanyCredit.objects.select_for_update().filter(company_id=company_id).first()
        if credit is None:
            django_messages.error(request, "Save the AI credit policy before recording a ledger adjustment.")
            return redirect(reverse("erpshop_ai_coworker:credit_policy"))
        amount = form.cleaned_data["amount"]
        credit.credit_balance += amount
        credit.full_clean()
        credit.save(update_fields=["credit_balance", "updated_at"])
        AICreditLedger.objects.create(
            company_id=company_id,
            entry_type=form.cleaned_data["entry_type"],
            amount=amount,
            balance_after=credit.credit_balance,
            currency=credit.currency,
            description=form.cleaned_data["description"],
            reference=f"manual-credit:{uuid4()}",
            created_by=request.user,
        )
    django_messages.success(request, "AI credit ledger entry recorded.")
    return redirect(reverse("erpshop_ai_coworker:credit_policy"))


@require_POST
@coworker_access_required
def decide_tool_approval(request, approval_id):
    if not _can_manage(request.user):
        raise Http404("AI operations are not available.")
    company_id, _branch_id = _active_scope(request)
    approval = get_object_or_404(
        AIToolApproval.objects.select_related("task", "agent__workspace").filter(agent__workspace__company_id=company_id),
        pk=approval_id,
    )
    decision = request.POST.get("decision")
    if decision not in {"approve", "reject"}:
        raise Http404("Approval decision not found.")
    saved = decide_approval(approval=approval, actor=request.user, approve=decision == "approve")
    tool_label = saved.tool_name.replace("_", " ")
    if saved.state == AIToolApproval.State.APPROVED:
        saved = execute_approved_tool(saved)
        result_status = (saved.result or {}).get("status", "completed")
        django_messages.success(request, f"{tool_label} request approved and processed ({result_status}).")
    elif saved.state == AIToolApproval.State.REJECTED:
        django_messages.info(request, f"{tool_label} request rejected.")
    else:
        django_messages.warning(request, f"{tool_label} request expired before it could be decided.")
    return redirect(reverse("erpshop_ai_coworker:operations"))


@require_POST
@coworker_access_required
def task_feedback(request, task_id):
    task = get_object_or_404(_tasks(request), pk=task_id, status=AITask.Status.COMPLETED)
    feedback = request.POST.get("feedback", "")
    if feedback not in {AITask.Feedback.HELPFUL, AITask.Feedback.UNHELPFUL}:
        return JsonResponse({"ok": False, "message": "Unknown feedback."}, status=400)
    note = request.POST.get("note", "").strip()[:2000]
    task.feedback = feedback
    task.feedback_note = note
    task.feedback_at = timezone.now()
    task.save(update_fields=["feedback", "feedback_note", "feedback_at", "updated_at"])
    AITaskEvent.objects.create(
        task=task,
        actor=request.user,
        event="task_feedback",
        details={"feedback": feedback, "has_note": bool(note)},
    )
    django_messages.success(request, "Task feedback saved. It is included in the company quality signal.")
    return redirect(reverse("erpshop_ai_coworker:task_detail", args=[task.pk]))


@require_POST
@coworker_access_required
def save_memory(request):
    if not _can_manage(request.user):
        raise Http404("Coworker runtime is not available.")
    company_id, _branch_id = _active_scope(request)
    form = CoworkerMemoryForm(request.POST, company_id=company_id)
    if not form.is_valid():
        django_messages.error(request, "Durable memory could not be saved.")
        return redirect(reverse("erpshop_ai_coworker:runtime"))
    agent = form.cleaned_data["agent"]
    with transaction.atomic():
        memory, _created = AICoworkerMemory.objects.update_or_create(
            agent=agent,
            participant=None,
            memory_type=AICoworkerMemory.MemoryType.MEMORY,
            defaults={"content": form.cleaned_data["content"]},
        )
    django_messages.success(request, f"Durable memory for {memory.agent.name} saved.")
    return redirect(f"{reverse('erpshop_ai_coworker:runtime')}?agent={agent.pk}")


@require_GET
@coworker_access_required
def subagents(request):
    if not _can_manage(request.user):
        raise Http404("Coworker subagents are not available.")
    company_id, _branch_id = _active_scope(request)
    return render(
        request,
        "erpshop_ai_coworker/subagents.html",
        {
            "form": CoworkerSubagentForm(company_id=company_id),
            "subagents": _subagents(company_id)[:100],
        },
    )


@require_POST
@coworker_access_required
def start_subagent(request):
    if not _can_manage(request.user):
        raise Http404("Coworker subagents are not available.")
    company_id, _branch_id = _active_scope(request)
    form = CoworkerSubagentForm(request.POST, company_id=company_id)
    if not form.is_valid():
        return render(
            request,
            "erpshop_ai_coworker/subagents.html",
            {"form": form, "subagents": _subagents(company_id)[:100]},
            status=400,
        )
    subagent, task = queue_subagent(
        agent=form.cleaned_data["agent"],
        description=form.cleaned_data["description"],
        prompt=form.cleaned_data["prompt"],
    )
    try:
        process_coworker_task.delay(task.pk)
    except Exception:
        with transaction.atomic():
            task = AITask.objects.select_for_update().get(pk=task.pk)
            task.status, task.error_message, task.completed_at = AITask.Status.FAILED, "The background worker could not be reached.", timezone.now()
            task.save(update_fields=["status", "error_message", "completed_at", "updated_at"])
            subagent = AICoworkerSubagent.objects.select_for_update().get(pk=subagent.pk)
            subagent.state, subagent.error, subagent.completed_at = AICoworkerSubagent.State.FAILED, task.error_message, timezone.now()
            subagent.save(update_fields=["state", "error", "completed_at", "updated_at"])
        django_messages.error(request, "Subagent was not queued because the background worker is unavailable.")
    else:
        django_messages.success(request, f"Subagent {subagent.description} queued.")
    return redirect(reverse("erpshop_ai_coworker:subagents"))


@require_GET
@coworker_access_required
def automation(request):
    if not _can_manage(request.user):
        raise Http404("Coworker automation is not available.")
    company_id, _branch_id = _active_scope(request)
    cron = _crons(company_id).filter(pk=request.GET.get("cron")).first() if request.GET.get("cron") else None
    heartbeat = _heartbeats(company_id).filter(pk=request.GET.get("heartbeat")).first() if request.GET.get("heartbeat") else None
    default_agent = _agents(company_id).filter(active=True).first()
    return render(
        request,
        "erpshop_ai_coworker/automation.html",
        {
            "cron": cron,
            "heartbeat": heartbeat,
            "cron_form": CoworkerCronForm(instance=cron, company_id=company_id, initial={"agent": default_agent}),
            "heartbeat_form": CoworkerHeartbeatForm(instance=heartbeat, company_id=company_id, initial={"agent": default_agent}),
            "crons": _crons(company_id),
            "heartbeats": _heartbeats(company_id),
        },
    )


@require_POST
@coworker_access_required
def save_cron(request, cron_id=None):
    if not _can_manage(request.user):
        raise Http404("Coworker automation is not available.")
    company_id, _branch_id = _active_scope(request)
    cron = get_object_or_404(_crons(company_id), pk=cron_id) if cron_id else None
    form = CoworkerCronForm(request.POST, instance=cron, company_id=company_id)
    if not form.is_valid():
        django_messages.error(request, "Cron rule could not be saved.")
        return redirect(reverse("erpshop_ai_coworker:automation"))
    saved = form.save(commit=False)
    saved.full_clean()
    saved.save()
    django_messages.success(request, f"Cron rule {saved.name} saved.")
    return redirect(f"{reverse('erpshop_ai_coworker:automation')}?cron={saved.pk}")


@require_POST
@coworker_access_required
def save_heartbeat(request, heartbeat_id=None):
    if not _can_manage(request.user):
        raise Http404("Coworker automation is not available.")
    company_id, _branch_id = _active_scope(request)
    heartbeat = get_object_or_404(_heartbeats(company_id), pk=heartbeat_id) if heartbeat_id else None
    form = CoworkerHeartbeatForm(request.POST, instance=heartbeat, company_id=company_id)
    if not form.is_valid():
        django_messages.error(request, "Heartbeat rule could not be saved.")
        return redirect(reverse("erpshop_ai_coworker:automation"))
    saved = form.save(commit=False)
    saved.full_clean()
    saved.save()
    django_messages.success(request, f"Heartbeat rule {saved.name} saved.")
    return redirect(f"{reverse('erpshop_ai_coworker:automation')}?heartbeat={saved.pk}")


@require_GET
@coworker_access_required
def channels(request):
    if not _can_manage(request.user):
        raise Http404("Coworker channels are not available.")
    company_id, _branch_id = _active_scope(request)
    channel = _channels(company_id).filter(pk=request.GET.get("channel")).first() if request.GET.get("channel") else None
    default_agent = _agents(company_id).filter(active=True).first()
    form = ChannelConfigurationForm(
        instance=channel,
        company_id=company_id,
        initial={"agent": default_agent},
    )
    return render(
        request,
        "erpshop_ai_coworker/channels.html",
        {
            "channel": channel,
            "channels": _channels(company_id),
            "form": form,
            "callback_url": request.build_absolute_uri(reverse("erpshop_ai_coworker:channel_webhook", args=[channel.pk])) if channel else "",
            "events": (
                AIChannelInboundEvent.objects.filter(channel=channel).select_related("agent").order_by("-id")[:50]
                if channel
                else []
            ),
        },
    )


@require_POST
@coworker_access_required
def save_channel(request, channel_id=None):
    if not _can_manage(request.user):
        raise Http404("Coworker channels are not available.")
    company_id, _branch_id = _active_scope(request)
    channel = get_object_or_404(_channels(company_id), pk=channel_id) if channel_id else None
    form = ChannelConfigurationForm(request.POST, instance=channel, company_id=company_id)
    if not form.is_valid():
        return render(
            request,
            "erpshop_ai_coworker/channels.html",
            {
                "channel": channel,
                "channels": _channels(company_id),
                "form": form,
                "callback_url": request.build_absolute_uri(reverse("erpshop_ai_coworker:channel_webhook", args=[channel.pk])) if channel else "",
                "events": AIChannelInboundEvent.objects.filter(channel=channel).select_related("agent").order_by("-id")[:50] if channel else [],
            },
            status=400,
        )
    with transaction.atomic():
        saved = form.save(commit=False)
        saved.company_id = company_id
        saved.owner_user = form.cleaned_data["agent"].owner_user
        saved.full_clean()
        saved.save()
        AICoworkerChannel.objects.update_or_create(channel=saved, defaults={"agent": form.cleaned_data["agent"]})
    django_messages.success(request, f"Channel {saved.name} saved. Configure the provider callback URL before enabling it.")
    return redirect(f"{reverse('erpshop_ai_coworker:channels')}?channel={saved.pk}")


@require_GET
@coworker_access_required
def webhooks(request):
    if not _can_manage(request.user):
        raise Http404("Coworker webhooks are not available.")
    company_id, _branch_id = _active_scope(request)
    agents = _agents(company_id).filter(active=True)
    endpoint = _webhook_endpoints(company_id).filter(pk=request.GET.get("endpoint")).first() if request.GET.get("endpoint") else None
    selected_agent = endpoint.agent if endpoint else agents.filter(pk=request.GET.get("agent")).first()
    if selected_agent is None:
        selected_agent = agents.first()
    provisioned_url = request.session.pop("erpshop_ai_coworker_provisioned_webhook", "")
    return render(
        request,
        "erpshop_ai_coworker/webhooks.html",
        {
            "agents": agents,
            "selected_agent": selected_agent,
            "endpoint": endpoint,
            "endpoints": _webhook_endpoints(company_id),
            "form": WebhookEndpointForm(instance=endpoint, company_id=company_id, agent=selected_agent) if selected_agent else None,
            "events": AIWebhookEvent.objects.filter(endpoint=endpoint).order_by("-id")[:50] if endpoint else [],
            "provisioned_url": provisioned_url,
        },
    )


@require_POST
@coworker_access_required
def save_webhook(request, agent_id, endpoint_id=None):
    if not _can_manage(request.user):
        raise Http404("Coworker webhooks are not available.")
    company_id, _branch_id = _active_scope(request)
    agent = get_object_or_404(_agents(company_id).filter(active=True), pk=agent_id)
    endpoint = get_object_or_404(_webhook_endpoints(company_id).filter(agent=agent), pk=endpoint_id) if endpoint_id else None
    form = WebhookEndpointForm(request.POST, instance=endpoint, company_id=company_id, agent=agent)
    if not form.is_valid():
        return render(
            request,
            "erpshop_ai_coworker/webhooks.html",
            {
                "agents": _agents(company_id).filter(active=True),
                "selected_agent": agent,
                "endpoint": endpoint,
                "endpoints": _webhook_endpoints(company_id),
                "form": form,
                "events": AIWebhookEvent.objects.filter(endpoint=endpoint).order_by("-id")[:50] if endpoint else [],
            },
            status=400,
        )
    with transaction.atomic():
        saved = form.save(commit=False)
        saved.agent = agent
        raw_token = ""
        if endpoint is None:
            raw_token = secrets.token_urlsafe(32)
            saved.token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        saved.full_clean()
        saved.save()
    if raw_token:
        request.session["erpshop_ai_coworker_provisioned_webhook"] = request.build_absolute_uri(
            reverse("erpshop_ai_coworker:webhook_receive", args=[raw_token])
        )
        django_messages.success(request, "Webhook endpoint created. Copy the one-time URL below; its token is not shown again.")
    else:
        django_messages.success(request, f"Webhook endpoint {saved.name} saved.")
    return redirect(f"{reverse('erpshop_ai_coworker:webhooks')}?endpoint={saved.pk}")


@require_GET
@coworker_access_required
def schedules(request, schedule_id=None):
    if not _can_manage(request.user):
        raise Http404("Coworker schedules not found.")
    company_id, branch_id = _active_scope(request)
    schedule = get_object_or_404(_schedules(company_id), pk=schedule_id) if schedule_id else None
    form = CoworkerScheduleForm(
        company_id=company_id,
        branch_id=branch_id,
        user=request.user,
        schedule=schedule,
        initial={"agent": _agents(company_id).filter(active=True).first(), "task_type": AITask.TaskType.PLAN},
    )
    return render(
        request,
        "erpshop_ai_coworker/schedules.html",
        {"schedule": schedule, "schedules": _schedules(company_id), "form": form},
    )


@require_POST
@coworker_access_required
def save_schedule(request, schedule_id=None):
    if not _can_manage(request.user):
        raise Http404("Coworker schedules not found.")
    company_id, branch_id = _active_scope(request)
    schedule = get_object_or_404(_schedules(company_id), pk=schedule_id) if schedule_id else None
    form = CoworkerScheduleForm(
        request.POST,
        company_id=company_id,
        branch_id=branch_id,
        user=request.user,
        schedule=schedule,
    )
    if not form.is_valid():
        return render(
            request,
            "erpshop_ai_coworker/schedules.html",
            {"schedule": schedule, "schedules": _schedules(company_id), "form": form},
            status=400,
        )
    data = form.cleaned_data
    with transaction.atomic():
        if schedule is None:
            schedule = AICoworkerSchedule(company_id=company_id, branch_id=branch_id, requested_by=request.user)
        schedule.agent = data["agent"]
        schedule.name = data["name"]
        schedule.task_type = data["task_type"]
        schedule.instruction = data["instruction"]
        schedule.entity = data["entity"]
        schedule.selected_fields = data["selected_fields"]
        schedule.filters = data["filters"]
        schedule.frequency = data["frequency"]
        schedule.weekday = data["weekday"]
        schedule.run_at = data["run_at"]
        schedule.active = data["active"]
        schedule.next_run_at = next_schedule_run(schedule) if schedule.active else None
        schedule.full_clean()
        schedule.save()
    django_messages.success(request, f"Schedule {schedule.name} saved.")
    return redirect(reverse("erpshop_ai_coworker:schedules_edit", args=[schedule.pk]))


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
                "can_model_admin": request.user.is_superuser,
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
                "can_model_admin": request.user.is_superuser,
            },
            status=400,
        )

    data = form.cleaned_data
    task = queue_coworker_task(
        agent=data["agent"],
        company_id=company_id,
        branch_id=branch_id,
        requested_by=request.user,
        title=data["title"],
        task_type=data["task_type"],
        instruction=data["instruction"],
        entity=data["entity"],
        selected_fields=data["selected_fields"],
        filters=data["filters"],
    )
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
