from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import ApprovalActionForm
from .models import ApprovalDelegation, ApprovalInstance, ApprovalNotification
from .adapters import apply_approval_result
from .services import can_user_act, delegation_available, perform_approval_action


INBOX_SCAN_LIMIT = 500
MAX_MASS_SELECTION = 100


def _company_scope(user):
    if user.is_superuser:
        return Q()
    access = getattr(user, "organization_access", None)
    if access and access.all_companies:
        return Q()
    return (
        Q(company__organization_users__user=user)
        | Q(company__employees__user=user)
        | Q(rule__master_approver=user)
    )


def _visible_pending(user):
    queryset = (
        ApprovalInstance.objects.filter(status=ApprovalInstance.Status.PENDING)
        .filter(_company_scope(user))
        .select_related("workflow__content_type", "rule", "company", "submitted_by")
        .distinct()
        .order_by("submitted_at", "pk")[:INBOX_SCAN_LIMIT]
    )
    instances = list(queryset)
    if user.is_superuser:
        return instances
    now = timezone.now()
    delegation_rows = list(
        ApprovalDelegation.objects.filter(delegate=user, active=True, valid_from__lte=now)
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now))
        .values("delegator_id", "while_away")
    )
    delegator_ids = {row["delegator_id"] for row in delegation_rows}
    immediate_delegator_ids = {row["delegator_id"] for row in delegation_rows if row["while_away"]}
    visible = []
    for instance in instances:
        candidate_ids = set((instance.active_level or {}).get("resolved_user_ids") or [])
        if (
            instance.rule.master_approver_id == user.pk
            or user.pk in candidate_ids
            or candidate_ids & immediate_delegator_ids
            or (delegation_available(instance, now) and candidate_ids & delegator_ids)
        ):
            visible.append(instance)
    return visible


@login_required
def approval_inbox(request):
    instances = _visible_pending(request.user)
    notifications = list(
        ApprovalNotification.objects.filter(recipient=request.user)
        .select_related("instance")
        .order_by("-created_at", "-pk")[:50]
    )
    return render(
        request,
        "approval_workflow/inbox.html",
        {"instances": instances, "notifications": notifications},
    )


@require_POST
@login_required
def approval_mass_action(request):
    action = request.POST.get("action", "").strip()
    reason = request.POST.get("reason", "").strip()
    raw_ids = request.POST.getlist("approval_ids")
    if action not in {"approve", "reject"}:
        messages.error(request, "Choose Approve or Reject for the selected transactions.")
        return redirect("approval_inbox")
    if action == "reject" and not reason:
        messages.error(request, "A rejection reason is required.")
        return redirect("approval_inbox")
    try:
        selected_ids = sorted({int(value) for value in raw_ids})
    except (TypeError, ValueError):
        messages.error(request, "The approval selection is invalid.")
        return redirect("approval_inbox")
    if not selected_ids:
        messages.error(request, "Select at least one approval transaction.")
        return redirect("approval_inbox")
    if len(selected_ids) > MAX_MASS_SELECTION:
        messages.error(request, f"Select no more than {MAX_MASS_SELECTION} approval transactions at once.")
        return redirect("approval_inbox")
    visible = {
        instance.pk: instance
        for instance in _visible_pending(request.user)
        if instance.pk in selected_ids and instance.workflow.enable_mass_approval
    }
    if set(selected_ids) != set(visible):
        messages.error(request, "One or more transactions are unavailable or do not allow mass approval.")
        return redirect("approval_inbox")
    from .tasks import process_mass_approval_item

    for instance_id in selected_ids:
        process_mass_approval_item.delay(instance_id, request.user.pk, action, reason)
    messages.success(request, f"Queued {len(selected_ids)} approval transaction(s) for {action}.")
    return redirect("approval_inbox")


@login_required
def approval_detail(request, pk):
    instance = get_object_or_404(
        ApprovalInstance.objects.select_related(
            "workflow__content_type", "rule", "company", "submitted_by"
        ).prefetch_related("actions__performed_by", "actions__effective_approver", "actions__delegated_from"),
        pk=pk,
    )
    if not request.user.is_superuser and not can_user_act(instance, request.user) and instance.submitted_by_id != request.user.pk:
        messages.error(request, "You do not have access to this approval transaction.")
        return redirect("approval_inbox")
    return render(
        request,
        "approval_workflow/detail.html",
        {
            "approval": instance,
            "can_act": can_user_act(instance, request.user),
            "approve_form": ApprovalActionForm(initial={"action": "approve"}, prefix="approve"),
            "reject_form": ApprovalActionForm(initial={"action": "reject"}, prefix="reject"),
        },
    )


@require_POST
@login_required
def approval_action(request, pk):
    instance = get_object_or_404(ApprovalInstance.objects.select_related("content_type", "rule"), pk=pk)
    action = request.POST.get("action")
    form = ApprovalActionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "The approval action is invalid.")
        return redirect("approval_detail", pk=pk)
    target = instance.target
    if target is None:
        messages.error(request, "The approval target is no longer available.")
        return redirect("approval_detail", pk=pk)

    try:
        outcome = perform_approval_action(
            instance,
            request.user,
            action,
            reason=form.cleaned_data["reason"],
            apply_result=lambda target, result_action: apply_approval_result(target, result_action, request.user),
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        if outcome.completed:
            messages.success(request, f"Approval {outcome.instance.get_status_display().lower()}.")
        else:
            messages.success(request, "Approval recorded and advanced to the next level.")
    return redirect("approval_detail", pk=pk)
