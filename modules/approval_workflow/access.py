from django.db.models import Q

from modules.organization.context import get_current_context


APPROVAL_ADMIN = "Approval Administrator"
APPROVAL_MANAGER = "Approval Manager"

APPROVAL_ENTITIES = {
    "approval-workflows",
    "approval-perimeters",
    "approval-rules",
    "approval-condition-groups",
    "approval-condition-lines",
    "approval-rule-levels",
    "approval-level-assignees",
    "approval-delegations",
    "approval-instances",
    "approval-actions",
    "approval-timeout-whitelist",
    "approval-outbox",
}
CONFIGURATION_ENTITIES = {
    "approval-workflows",
    "approval-perimeters",
    "approval-rules",
    "approval-condition-groups",
    "approval-condition-lines",
    "approval-rule-levels",
    "approval-level-assignees",
    "approval-timeout-whitelist",
}


def _approval_group_names(user):
    cached = getattr(user, "_gsshop_approval_group_names", None)
    if cached is None:
        cached = frozenset(user.groups.values_list("name", flat=True))
        user._gsshop_approval_group_names = cached
    return cached


def can_approval_entity_action(user, entity, action):
    if entity not in APPROVAL_ENTITIES or user.is_superuser:
        return True
    groups = _approval_group_names(user)
    if APPROVAL_ADMIN in groups:
        return True
    if APPROVAL_MANAGER in groups:
        return action == "view" if entity in CONFIGURATION_ENTITIES else action != "delete"
    return entity == "approval-delegations" and action in {"view", "add", "change"}


def scope_approval_queryset(user, entity, queryset):
    if entity not in APPROVAL_ENTITIES or user.is_superuser:
        return queryset
    groups = _approval_group_names(user)
    if APPROVAL_ADMIN in groups or APPROVAL_MANAGER in groups:
        context = get_current_context()
        if context is None:
            return queryset
        company_ids = context.active_company_ids
        if entity == "approval-instances":
            return queryset.filter(company_id__in=company_ids)
        if entity == "approval-actions":
            return queryset.filter(instance__company_id__in=company_ids)
        if entity == "approval-outbox":
            return queryset.filter(instance__company_id__in=company_ids)
        if entity == "approval-delegations":
            return queryset.filter(Q(company__isnull=True) | Q(company_id__in=company_ids))
        if entity == "approval-rules":
            return queryset.filter(Q(companies__isnull=True) | Q(companies__pk__in=company_ids)).distinct()
        return queryset
    if entity == "approval-delegations":
        return queryset.filter(Q(delegator=user) | Q(delegate=user))
    return queryset.none()


def is_approval_entity(entity):
    return entity in APPROVAL_ENTITIES


def scope_approval_form(form, user, entity):
    if entity not in {"approval-delegations", "approval-rules"} or user.is_superuser:
        return form
    groups = _approval_group_names(user)
    context = get_current_context()
    company_ids = context.active_company_ids if context else ()
    for field_name in ("delegator", "delegate", "master_approver"):
        if field_name not in form.fields:
            continue
        field = form.fields[field_name]
        field.queryset = (
            field.queryset.filter(is_active=True)
            .filter(
                Q(organization_access__all_companies=True)
                | Q(organization_access__companies__pk__in=company_ids)
                | Q(employee_profiles__company_id__in=company_ids)
            )
            .distinct()
        )
    if entity == "approval-delegations" and "delegator" in form.fields and not (
        APPROVAL_ADMIN in groups or APPROVAL_MANAGER in groups
    ):
        form.fields["delegator"].queryset = form.fields["delegator"].queryset.filter(pk=user.pk)
        if not form.is_bound:
            form.initial["delegator"] = user.pk
    return form
