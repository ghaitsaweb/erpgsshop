from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .adapters import apply_approval_result
from .models import (
    ApprovalAction,
    ApprovalDelegation,
    ApprovalInstance,
    ApprovalNotification,
    ApprovalOutbox,
    ApprovalRule,
    ApprovalTimeoutWhitelist,
)
from .services import _queue_event


RUNTIME_SCAN_LIMIT = 500
OUTBOX_BATCH_SIZE = 200


def due_instance_ids(now=None, limit=RUNTIME_SCAN_LIMIT):
    """Return a bounded work list; record processing happens in per-record tasks."""
    now = now or timezone.now()
    return list(
        ApprovalInstance.objects.filter(status=ApprovalInstance.Status.PENDING)
        .filter(
            Q(timeout_due_at__isnull=False, timeout_due_at__lte=now)
            | Q(delegate_due_at__isnull=False, delegate_due_at__lte=now, delegate_activated_at__isnull=True)
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:limit]
    )


def _system_actor(instance):
    if instance.rule.master_approver_id:
        master = get_user_model().objects.filter(pk=instance.rule.master_approver_id, is_active=True).first()
        if master:
            return master
    actor = get_user_model().objects.filter(is_active=True, is_superuser=True).order_by("pk").first()
    return actor or instance.submitted_by


@transaction.atomic
def activate_due_delegation(instance_id, now=None):
    now = now or timezone.now()
    instance = (
        ApprovalInstance.objects.select_for_update()
        .select_related("rule", "submitted_by")
        .get(pk=instance_id)
    )
    if (
        instance.status != ApprovalInstance.Status.PENDING
        or instance.delegate_activated_at is not None
        or instance.delegate_due_at is None
        or instance.delegate_due_at > now
    ):
        return False
    candidate_ids = set((instance.active_level or {}).get("resolved_user_ids") or [])
    delegations = list(
        ApprovalDelegation.objects.filter(
            delegator_id__in=candidate_ids,
            active=True,
            while_away=False,
            valid_from__lte=now,
        )
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now))
        .filter(Q(company__isnull=True) | Q(company_id=instance.company_id))
        .values("delegator_id", "delegate_id")
        .order_by("delegator_id", "delegate_id")
    )
    instance.delegate_activated_at = now
    instance.save(update_fields=["delegate_activated_at", "updated_at"])
    if not delegations:
        return True
    delegate_ids = sorted({item["delegate_id"] for item in delegations})
    ApprovalAction.objects.create(
        instance=instance,
        action=ApprovalAction.Action.AUTO_DELEGATE,
        level=instance.current_level,
        performed_by=_system_actor(instance),
        reason="Delegation delay elapsed.",
        metadata={"delegations": delegations},
    )
    _queue_event(instance, "delegated", delegate_ids)
    return True


@transaction.atomic
def complete_due_timeout(instance_id, now=None):
    now = now or timezone.now()
    instance = (
        ApprovalInstance.objects.select_for_update()
        .select_related("rule", "content_type", "submitted_by")
        .get(pk=instance_id)
    )
    if (
        instance.status != ApprovalInstance.Status.PENDING
        or instance.timeout_action_snapshot == ApprovalRule.TimeoutAction.NONE
        or instance.timeout_due_at is None
        or instance.timeout_due_at > now
    ):
        return False
    if not ApprovalTimeoutWhitelist.objects.filter(content_type_id=instance.content_type_id).exists():
        return False
    target_model = instance.content_type.model_class()
    if target_model is None:
        raise ValidationError("The approval target model is unavailable.")
    target = target_model._default_manager.select_for_update().get(pk=instance.object_id)
    action = instance.timeout_action_snapshot
    actor = _system_actor(instance)
    reason = "Automatic approval timeout elapsed."
    apply_approval_result(target, action, actor)
    ApprovalAction.objects.create(
        instance=instance,
        action=(ApprovalAction.Action.AUTO_APPROVE if action == "approve" else ApprovalAction.Action.AUTO_REJECT),
        level=instance.current_level,
        performed_by=actor,
        effective_approver=actor,
        reason=reason,
        metadata={"timeout_due_at": instance.timeout_due_at.isoformat()},
    )
    instance.status = ApprovalInstance.Status.APPROVED if action == "approve" else ApprovalInstance.Status.REJECTED
    instance.completed_at = now
    if action == "reject":
        instance.rejection_reason = reason
    instance.save(update_fields=["status", "completed_at", "rejection_reason", "updated_at"])
    _queue_event(instance, instance.status, [instance.submitted_by_id])
    return True


@transaction.atomic
def drain_approval_outbox(now=None, batch_size=OUTBOX_BATCH_SIZE):
    now = now or timezone.now()
    rows = list(
        ApprovalOutbox.objects.select_for_update(skip_locked=True)
        .filter(status=ApprovalOutbox.Status.PENDING, available_at__lte=now)
        .select_related("instance__company")
        .order_by("available_at", "pk")[:batch_size]
    )
    if not rows:
        return 0
    requested_ids = {
        int(user_id)
        for row in rows
        for user_id in (row.recipient_ids or [])
        if str(user_id).isdigit()
    }
    active_ids = set(
        get_user_model().objects.filter(pk__in=requested_ids, is_active=True).values_list("pk", flat=True)
    )
    notifications = []
    for row in rows:
        row_recipient_ids = {
            int(value)
            for value in (row.recipient_ids or [])
            if str(value).isdigit()
        }
        for recipient_id in sorted(active_ids.intersection(row_recipient_ids)):
            notifications.append(
                ApprovalNotification(
                    outbox=row,
                    instance=row.instance,
                    recipient_id=recipient_id,
                    event=row.event,
                    title=f"Approval {row.event.replace('_', ' ').title()}: {row.instance.reference}",
                    message=f"{row.instance.entity} · {row.instance.company} · {row.instance.reference}",
                )
            )
        row.status = ApprovalOutbox.Status.SENT
        row.sent_at = now
        row.attempts += 1
        row.last_error = ""
        row.updated_at = now
    ApprovalNotification.objects.bulk_create(notifications, ignore_conflicts=True)
    ApprovalOutbox.objects.bulk_update(rows, ["status", "sent_at", "attempts", "last_error", "updated_at"])
    return len(rows)
