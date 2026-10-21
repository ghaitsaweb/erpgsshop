from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    ApprovalAction,
    ApprovalDelegation,
    ApprovalInstance,
    ApprovalOutbox,
    ApprovalRule,
    ApprovalWorkflow,
    timeout_delta,
)


MAX_RULE_CANDIDATES = 200
MAX_LEVEL_APPROVERS = 1000
MAX_APPROVAL_LEVELS = 20
MAX_LEVEL_GROUPS = 200


@dataclass(frozen=True)
class ApprovalOutcome:
    instance: ApprovalInstance
    completed: bool
    approved: bool


def _json_safe(value):
    return DjangoJSONEncoder().encode(value)


def _normalized_json(value):
    import json

    return json.loads(_json_safe(value))


def _resolve_path(record, path):
    value = record
    for part in path.split("."):
        value = getattr(value, part, None)
        if value is None:
            break
    if hasattr(value, "pk"):
        return value.pk
    return value


def _compare(actual, operator, expected):
    if isinstance(actual, Decimal):
        try:
            expected = Decimal(str(expected))
        except (TypeError, ValueError):
            return False
    if operator in {"=", "==", "eq"}:
        return actual == expected
    if operator in {"!=", "ne"}:
        return actual != expected
    if operator in {">", "gt"}:
        return actual is not None and actual > expected
    if operator in {">=", "gte"}:
        return actual is not None and actual >= expected
    if operator in {"<", "lt"}:
        return actual is not None and actual < expected
    if operator in {"<=", "lte"}:
        return actual is not None and actual <= expected
    if operator == "in":
        return actual in (expected or [])
    if operator == "not_in":
        return actual not in (expected or [])
    if operator == "contains":
        return expected in actual if actual is not None else False
    if operator == "is_empty":
        return actual in (None, "", [], ())
    if operator == "is_not_empty":
        return actual not in (None, "", [], ())
    return False


def _condition_matches(record, condition):
    if not isinstance(condition, dict):
        return False
    nested = condition.get("conditions")
    if isinstance(nested, list):
        matches = [_condition_matches(record, item) for item in nested]
        return any(matches) if str(condition.get("operator", "and")).lower() == "or" else all(matches)
    field = condition.get("field") or condition.get("field_name")
    if not field:
        return False
    return _compare(_resolve_path(record, field), condition.get("operator", "eq"), condition.get("value"))


def _rule_matches(record, rule):
    conditions = rule.trigger_conditions or []
    if isinstance(conditions, dict):
        return _condition_matches(record, conditions)
    return all(_condition_matches(record, item) for item in conditions)


def _company_id_for(record):
    direct = getattr(record, "company_id", None)
    if direct:
        return direct
    for path in ("employee.company_id", "request.employee.company_id", "contract.employee.company_id"):
        value = _resolve_path(record, path)
        if value:
            return value
    raise ValidationError("The approval record does not resolve to a company.")


def resolve_workflow_and_rule(record, company_id=None):
    content_type = ContentType.objects.get_for_model(record, for_concrete_model=False)
    workflow = (
        ApprovalWorkflow.objects.select_related("content_type")
        .filter(content_type=content_type, active=True)
        .first()
    )
    if workflow is None:
        raise ValidationError("No active Approval Workflow is configured for this record type.")
    company_id = company_id or _company_id_for(record)
    rules = list(
        ApprovalRule.objects.filter(workflow=workflow, state=ApprovalRule.State.ASSOCIATED)
        .filter(Q(companies__isnull=True) | Q(companies__pk=company_id))
        .select_related("master_approver", "workflow", "perimeter")
        .distinct()
        .order_by("priority", "pk")[: MAX_RULE_CANDIDATES + 1]
    )
    if len(rules) > MAX_RULE_CANDIDATES:
        raise ValidationError("Approval rule resolution exceeded the configured safe bound.")
    matching = [rule for rule in rules if _rule_matches(record, rule)]
    if not matching:
        raise ValidationError("No associated Approval Rule matches this transaction.")
    return workflow, matching[0], company_id


def _record_user_ids(record, field_path):
    value = _resolve_path(record, field_path)
    if value in (None, ""):
        return set()
    if isinstance(value, (list, tuple, set)):
        return {int(item) for item in value if item}
    return {int(value)}


def _level_group_references(level):
    names = set(level.get("group_names") or [])
    ids = set(level.get("group_ids") or level.get("selected_role_ids") or [])
    for source in level.get("approvers") or []:
        if source.get("type") in {"group", "access_profile", "functional_hierarchy"}:
            names.update(source.get("group_names") or [])
            ids.update(source.get("group_ids") or [])
    return names, {int(group_id) for group_id in ids}


def _configured_ids(values):
    result = set()
    for value in values or []:
        if isinstance(value, dict):
            value = value.get("id") or value.get("value") or value.get("user_id")
        if value not in (None, ""):
            result.add(int(value))
    return result


def _level_delegate_references(level):
    names = set(level.get("delegate_group_names") or [])
    ids = _configured_ids(level.get("delegate_group_ids") or level.get("selected_delegate_group_ids"))
    return names, ids


def _level_direct_delegate_ids(level):
    ids = _configured_ids(
        level.get("delegate_user_ids")
        or level.get("selected_delegate_ids")
        or level.get("selected_delegates")
    )
    for source in level.get("delegates") or []:
        if source.get("type") == "user":
            ids.update(_configured_ids(source.get("user_ids") or source.get("users")))
    return ids


def _resolve_levels(record, levels, company_id):
    if len(levels) > MAX_APPROVAL_LEVELS:
        raise ValidationError(f"Approval rules support at most {MAX_APPROVAL_LEVELS} levels.")
    all_group_names = set()
    all_group_ids = set()
    record_paths = set()
    direct_ids = set()
    for level in levels:
        names, ids = _level_group_references(level)
        delegate_names, delegate_ids = _level_delegate_references(level)
        all_group_names.update(names)
        all_group_names.update(delegate_names)
        all_group_ids.update(ids)
        all_group_ids.update(delegate_ids)
        direct_ids.update(_configured_ids(level.get("user_ids") or level.get("selected_user_ids")))
        direct_ids.update(_level_direct_delegate_ids(level))
        for source in level.get("approvers") or []:
            if source.get("type") == "user":
                direct_ids.update(_configured_ids(source.get("user_ids")))
            elif source.get("type") == "record_supervisor":
                record_paths.add("employee.supervisor.user_id")
            elif source.get("type") in {"record_field", "hierarchy"} and source.get("field"):
                record_paths.add(source["field"])
    group_filter = Q(pk__in=all_group_ids) | Q(name__in=all_group_names)
    group_rows = list(
        Group.objects.filter(group_filter)
        .order_by("pk")
        .values_list("pk", "name")[: MAX_LEVEL_GROUPS + 1]
    ) if all_group_ids or all_group_names else []
    if len(group_rows) > MAX_LEVEL_GROUPS:
        raise ValidationError("Approval levels reference too many access profiles.")
    group_ids_by_name = {name: group_id for group_id, name in group_rows}
    resolved_group_ids = {group_id for group_id, _name in group_rows}
    membership_rows = list(
        get_user_model().objects.filter(groups__pk__in=resolved_group_ids, is_active=True)
        .filter(
            Q(organization_access__all_companies=True)
            | Q(organization_access__companies__pk=company_id)
            | Q(employee_profiles__company_id=company_id)
        )
        .order_by("groups__pk", "pk")
        .values_list("groups__pk", "pk")[: MAX_LEVEL_GROUPS * MAX_LEVEL_APPROVERS + 1]
    )
    if len(membership_rows) > MAX_LEVEL_GROUPS * MAX_LEVEL_APPROVERS:
        raise ValidationError("Approval access profiles resolve to too many users.")
    users_by_group = {}
    for group_id, user_id in membership_rows:
        users_by_group.setdefault(group_id, set()).add(user_id)
    record_users_by_path = {path: _record_user_ids(record, path) for path in record_paths}
    direct_ids.update(user_id for ids in record_users_by_path.values() for user_id in ids)
    active_direct_ids = set(
        get_user_model().objects.filter(pk__in=direct_ids, is_active=True).values_list("pk", flat=True)
    )
    fallback_ids = set(
        get_user_model().objects.filter(is_active=True, is_superuser=True)
        .order_by("pk")
        .values_list("pk", flat=True)[:MAX_LEVEL_APPROVERS]
    )
    snapshots = []
    for level in levels:
        snapshot = dict(level)
        user_ids = _configured_ids(level.get("user_ids") or level.get("selected_user_ids")) & active_direct_ids
        names, ids = _level_group_references(level)
        ids.update(group_ids_by_name[name] for name in names if name in group_ids_by_name)
        for group_id in ids:
            user_ids.update(users_by_group.get(group_id, ()))
        for source in level.get("approvers") or []:
            if source.get("type") == "user":
                user_ids.update(_configured_ids(source.get("user_ids")) & active_direct_ids)
            elif source.get("type") == "record_supervisor":
                user_ids.update(record_users_by_path.get("employee.supervisor.user_id", set()) & active_direct_ids)
            elif source.get("type") in {"record_field", "hierarchy"} and source.get("field"):
                user_ids.update(record_users_by_path.get(source["field"], set()) & active_direct_ids)
        delegate_user_ids = _level_direct_delegate_ids(level) & active_direct_ids
        delegate_names, delegate_group_ids = _level_delegate_references(level)
        delegate_group_ids.update(
            group_ids_by_name[name] for name in delegate_names if name in group_ids_by_name
        )
        for group_id in delegate_group_ids:
            delegate_user_ids.update(users_by_group.get(group_id, ()))
        overlap = user_ids & delegate_user_ids
        if overlap:
            raise ValidationError("An approval level cannot use the same user as approver and delegate.")
        minimum = int(level.get("minimum_approvals", level.get("min_approval", 1)))
        if len(user_ids) < minimum:
            user_ids.update(fallback_ids)
        if len(user_ids) > MAX_LEVEL_APPROVERS:
            raise ValidationError("An approval level resolves to too many users; narrow its access profiles.")
        if len(user_ids) < minimum:
            raise ValidationError(
                f"Approval level {level.get('name') or level.get('layer_name') or 'unnamed'} does not have enough active approvers."
            )
        snapshot["resolved_user_ids"] = sorted(user_ids)
        snapshot["resolved_delegate_user_ids"] = sorted(delegate_user_ids)
        snapshot["minimum_approvals"] = minimum
        snapshots.append(snapshot)
    return snapshots


def _trigger_snapshot(record, workflow):
    return {
        field_name: _normalized_json(_resolve_path(record, field_name))
        for field_name in workflow.primary_perimeter
    }


def _queue_event(instance, event, recipient_ids=()):
    if (instance.notification_snapshot or {}).get(event, True) is False:
        return
    ApprovalOutbox.objects.create(
        instance=instance,
        event=event,
        recipient_ids=sorted(set(recipient_ids)),
        payload={"instance_id": instance.pk, "reference": instance.reference, "entity": instance.entity},
    )


def delegation_available(instance, now=None):
    return bool(instance.delegate_due_at and (now or timezone.now()) >= instance.delegate_due_at)


def _rule_runtime_snapshot(rule, started_at):
    timeout = timeout_delta(rule.timeout_value, rule.timeout_period)
    delegate = timeout_delta(rule.delegate_after, rule.delegate_period) if rule.use_delegate else None
    return {
        "timeout_action_snapshot": rule.timeout_action,
        "timeout_value_snapshot": rule.timeout_value,
        "timeout_period_snapshot": rule.timeout_period,
        "delegate_after_snapshot": rule.delegate_after if rule.use_delegate else None,
        "delegate_period_snapshot": rule.delegate_period if rule.use_delegate else "",
        "timeout_due_at": started_at + timeout if rule.timeout_action != rule.TimeoutAction.NONE and timeout else None,
        "delegate_due_at": started_at + delegate if delegate else None,
    }


@transaction.atomic
def submit_for_approval(record, actor, *, entity, apply_submit):
    """Submit a domain record and freeze its approval rule and approvers."""
    model = type(record)
    locked = model._default_manager.select_for_update().get(pk=record.pk)
    content_type = ContentType.objects.get_for_model(locked, for_concrete_model=False)
    existing = ApprovalInstance.objects.select_for_update().filter(
        content_type=content_type,
        object_id=locked.pk,
        status=ApprovalInstance.Status.PENDING,
    ).first()
    if existing:
        raise ValidationError("This record already has a pending approval.")
    workflow, rule, company_id = resolve_workflow_and_rule(locked)
    levels = _resolve_levels(locked, rule.approval_levels, company_id)
    apply_submit(locked)
    submitted_at = timezone.now()
    instance = ApprovalInstance.objects.create(
        workflow=workflow,
        rule=rule,
        content_type=content_type,
        object_id=locked.pk,
        company_id=company_id,
        entity=entity,
        reference=str(locked),
        submitted_by=actor,
        submitted_at=submitted_at,
        level_started_at=submitted_at,
        approval_levels_snapshot=levels,
        trigger_snapshot=_trigger_snapshot(locked, workflow),
        notification_snapshot=rule.notification_settings or {},
        **_rule_runtime_snapshot(rule, submitted_at),
    )
    ApprovalAction.objects.create(
        instance=instance,
        action=ApprovalAction.Action.SUBMIT,
        performed_by=actor,
        reason="",
    )
    _queue_event(instance, "submitted", levels[0]["resolved_user_ids"])
    return instance


@transaction.atomic
def ensure_legacy_pending_approval(record, actor, *, entity):
    """Backfill an approval instance for a submitted pre-module transaction."""
    content_type = ContentType.objects.get_for_model(record, for_concrete_model=False)
    existing = ApprovalInstance.objects.filter(
        content_type=content_type,
        object_id=record.pk,
        status=ApprovalInstance.Status.PENDING,
    ).first()
    if existing:
        return existing
    workflow, rule, company_id = resolve_workflow_and_rule(record)
    levels = _resolve_levels(record, rule.approval_levels, company_id)
    submitted_at = timezone.now()
    instance = ApprovalInstance.objects.create(
        workflow=workflow,
        rule=rule,
        content_type=content_type,
        object_id=record.pk,
        company_id=company_id,
        entity=entity,
        reference=str(record),
        submitted_by=actor,
        submitted_at=submitted_at,
        level_started_at=submitted_at,
        approval_levels_snapshot=levels,
        trigger_snapshot=_trigger_snapshot(record, workflow),
        notification_snapshot=rule.notification_settings or {},
        **_rule_runtime_snapshot(rule, submitted_at),
    )
    ApprovalAction.objects.create(
        instance=instance,
        action=ApprovalAction.Action.SUBMIT,
        performed_by=actor,
        metadata={"legacy_backfill": True},
    )
    return instance


def _delegated_from(instance, actor_id, candidate_ids):
    now = timezone.now()
    delegations = (
        ApprovalDelegation.objects.filter(
            delegate_id=actor_id,
            delegator_id__in=candidate_ids,
            active=True,
            valid_from__lte=now,
        )
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now))
        .filter(Q(company__isnull=True) | Q(company_id=instance.company_id))
    )
    if not delegation_available(instance, now):
        delegations = delegations.filter(while_away=True)
    delegation = delegations.order_by("delegator_id", "pk").first()
    return delegation.delegator_id if delegation else None


def can_user_act(instance, user):
    if instance.status != ApprovalInstance.Status.PENDING or not user.is_active:
        return False
    if user.is_superuser or instance.rule.master_approver_id == user.pk:
        return True
    level = instance.active_level or {}
    candidates = set(level.get("resolved_user_ids") or [])
    configured_delegates = set(level.get("resolved_delegate_user_ids") or [])
    return (
        user.pk in candidates
        or (delegation_available(instance) and user.pk in configured_delegates)
        or _delegated_from(instance, user.pk, candidates) is not None
    )


@transaction.atomic
def perform_approval_action(instance, actor, action, *, reason="", apply_result):
    """Apply one locked approval action and call the domain only on completion."""
    if action not in {"approve", "reject"}:
        raise ValidationError("Unsupported approval action.")
    instance = (
        ApprovalInstance.objects.select_for_update()
        .select_related("rule", "workflow", "content_type", "company")
        .get(pk=instance.pk)
    )
    if instance.status != ApprovalInstance.Status.PENDING:
        raise ValidationError("This approval is no longer pending.")
    target_model = instance.content_type.model_class()
    target = target_model._default_manager.select_for_update().get(pk=instance.object_id)
    level = instance.active_level
    if level is None:
        raise ValidationError("The approval instance has no active level.")
    candidate_ids = set(level.get("resolved_user_ids") or [])
    configured_delegate_ids = set(level.get("resolved_delegate_user_ids") or [])
    master = instance.rule.master_approver_id == actor.pk
    delegated_from = None
    if not actor.is_superuser and not master and actor.pk not in candidate_ids:
        delegated_from = _delegated_from(instance, actor.pk, candidate_ids)
        configured_delegate = delegation_available(instance) and actor.pk in configured_delegate_ids
        if delegated_from is None and not configured_delegate:
            raise ValidationError("You are not an approver for the active approval level.")
    reason = (reason or "").strip()
    if action == "reject" and not reason:
        raise ValidationError("A rejection reason is required.")
    if action == "approve" and (master or instance.rule.require_approve_reason) and not reason:
        raise ValidationError("An approval reason is required.")
    if master:
        action_code = ApprovalAction.Action.MASTER_APPROVE if action == "approve" else ApprovalAction.Action.MASTER_REJECT
    else:
        action_code = ApprovalAction.Action.APPROVE if action == "approve" else ApprovalAction.Action.REJECT
    effective_approver_id = delegated_from or actor.pk
    duplicate = ApprovalAction.objects.filter(
        instance=instance,
        level=instance.current_level,
        action__in=[ApprovalAction.Action.APPROVE, ApprovalAction.Action.MASTER_APPROVE],
        effective_approver_id=effective_approver_id,
    ).exists()
    if action == "approve" and duplicate:
        raise ValidationError("This approver has already approved the active level.")
    ApprovalAction.objects.create(
        instance=instance,
        action=action_code,
        level=instance.current_level,
        performed_by=actor,
        effective_approver_id=effective_approver_id,
        delegated_from_id=delegated_from,
        reason=reason,
    )
    completed = action == "reject" or master
    if action == "approve" and not completed:
        approval_count = (
            ApprovalAction.objects.filter(
                instance=instance,
                level=instance.current_level,
                action=ApprovalAction.Action.APPROVE,
            )
            .values("effective_approver_id")
            .distinct()
            .count()
        )
        if approval_count >= int(level.get("minimum_approvals", 1)):
            completed = instance.current_level + 1 >= len(instance.approval_levels_snapshot)
            if not completed:
                instance.current_level += 1
                instance.level_started_at = timezone.now()
                instance.delegate_activated_at = None
                timeout = timeout_delta(instance.timeout_value_snapshot, instance.timeout_period_snapshot)
                delegate = timeout_delta(instance.delegate_after_snapshot, instance.delegate_period_snapshot)
                instance.timeout_due_at = instance.level_started_at + timeout if timeout else None
                instance.delegate_due_at = instance.level_started_at + delegate if delegate else None
                instance.save(
                    update_fields=[
                        "current_level",
                        "level_started_at",
                        "delegate_activated_at",
                        "timeout_due_at",
                        "delegate_due_at",
                        "updated_at",
                    ]
                )
                _queue_event(
                    instance,
                    "level_advanced",
                    instance.active_level.get("resolved_user_ids", []),
                )
    if completed:
        apply_result(target, action)
        instance.status = ApprovalInstance.Status.APPROVED if action == "approve" else ApprovalInstance.Status.REJECTED
        instance.completed_at = timezone.now()
        if action == "reject":
            instance.rejection_reason = reason
        instance.save(update_fields=["status", "completed_at", "rejection_reason", "updated_at"])
        _queue_event(instance, instance.status, [instance.submitted_by_id])
    return ApprovalOutcome(instance=instance, completed=completed, approved=completed and action == "approve")


def pending_for_user(user, queryset=None):
    """Return a bounded, company-scoped pending inbox for one user."""
    base = queryset if queryset is not None else ApprovalInstance.objects.all()
    base = base.filter(status=ApprovalInstance.Status.PENDING).select_related(
        "workflow__content_type", "rule", "company", "submitted_by"
    )
    if user.is_superuser:
        return base
    access = getattr(user, "organization_access", None)
    if access and access.all_companies:
        return base
    company_filter = (
        Q(company__organization_users__user=user)
        | Q(company__employees__user=user)
    )
    direct_filter = Q(rule__master_approver=user) | Q(approval_levels_snapshot__contains=[{"resolved_user_ids": [user.pk]}])
    # JSON containment cannot express membership inside every possible layer
    # portably, so company scoping happens in SQL and the active layer is checked
    # against a bounded result set in the inbox view.
    return base.filter(company_filter | direct_filter).distinct()
