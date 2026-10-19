from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache

from django.db.models import Q

from core.models import Company

from .models import Branch, UserOrganizationAccess


ACTIVE_COMPANY_SESSION_KEY = "active_company_id"
ACTIVE_BRANCH_SESSION_KEY = "active_branch_id"
SELECTED_COMPANIES_SESSION_KEY = "selected_company_ids"
SELECTED_BRANCHES_SESSION_KEY = "selected_branch_ids"


@dataclass(frozen=True)
class OrganizationContext:
    company_id: int | None
    branch_id: int | None
    allowed_company_ids: tuple[int, ...]
    allowed_branch_ids: tuple[int, ...]
    selected_company_ids: tuple[int, ...] = ()
    selected_branch_ids: tuple[int, ...] = ()

    @property
    def active_company_ids(self):
        return self.selected_company_ids or ((self.company_id,) if self.company_id else ())

    @property
    def active_branch_ids(self):
        return self.selected_branch_ids or ((self.branch_id,) if self.branch_id else ())

    @property
    def has_access(self):
        return bool(self.company_id)


_current_context = ContextVar("gsshop_organization_context", default=None)


def set_current_context(context):
    return _current_context.set(context)


def reset_current_context(token):
    _current_context.reset(token)


def get_current_context():
    return _current_context.get()


def _profile_for(user):
    try:
        return user.organization_access
    except UserOrganizationAccess.DoesNotExist:
        return None


def access_scope(user):
    if not user.is_authenticated:
        return (), (), None, None

    profile = _profile_for(user)
    if user.is_superuser or (profile and profile.all_companies):
        companies = Company.objects.filter(active=True)
    elif profile:
        companies = profile.companies.filter(active=True)
    else:
        companies = Company.objects.filter(active=True, employees__user=user).distinct()

    company_ids = tuple(companies.order_by("name", "pk").values_list("pk", flat=True))
    if not company_ids:
        return (), (), None, None

    branches = Branch.objects.filter(active=True, company_id__in=company_ids)
    if not user.is_superuser and profile and not profile.all_branches:
        branches = branches.filter(pk__in=profile.branches.values("pk"))
    branch_ids = tuple(branches.order_by("company__name", "sequence", "name", "pk").values_list("pk", flat=True))

    default_company_id = profile.default_company_id if profile else None
    default_branch_id = profile.default_branch_id if profile else None
    return company_ids, branch_ids, default_company_id, default_branch_id


def resolve_context(user, session):
    company_ids, branch_ids, default_company_id, default_branch_id = access_scope(user)
    selected_company_ids = tuple(
        company_id
        for company_id in _safe_int_tuple(session.get(SELECTED_COMPANIES_SESSION_KEY))
        if company_id in company_ids
    )
    requested_company_id = _safe_int(session.get(ACTIVE_COMPANY_SESSION_KEY))
    if not selected_company_ids:
        fallback_company_id = (
            requested_company_id
            if requested_company_id in company_ids
            else default_company_id if default_company_id in company_ids else (company_ids[0] if company_ids else None)
        )
        selected_company_ids = (fallback_company_id,) if fallback_company_id else ()

    company_id = requested_company_id if requested_company_id in selected_company_ids else None
    if company_id is None:
        company_id = default_company_id if default_company_id in selected_company_ids else (
            selected_company_ids[0] if selected_company_ids else None
        )

    branch_rows = list(
        Branch.objects.filter(pk__in=branch_ids, company_id__in=selected_company_ids, active=True)
        .order_by("company__name", "-is_main", "sequence", "name", "pk")
        .values_list("pk", "company_id")
    )
    branch_company = dict(branch_rows)
    eligible_branch_ids = tuple(branch_id for branch_id, _ in branch_rows)
    selected_branch_ids = tuple(
        branch_id
        for branch_id in _safe_int_tuple(session.get(SELECTED_BRANCHES_SESSION_KEY))
        if branch_id in eligible_branch_ids
    )
    default_selected_branch_ids = _default_selected_branches(
        selected_company_ids,
        branch_rows,
        default_branch_id,
    )
    selected_branch_companies = {branch_company[branch_id] for branch_id in selected_branch_ids}
    selected_branch_ids = (*selected_branch_ids, *(
        branch_id
        for branch_id in default_selected_branch_ids
        if branch_company[branch_id] not in selected_branch_companies
    ))

    requested_branch_id = _safe_int(session.get(ACTIVE_BRANCH_SESSION_KEY))
    branch_id = (
        requested_branch_id
        if requested_branch_id in selected_branch_ids and branch_company.get(requested_branch_id) == company_id
        else None
    )
    if branch_id is None and default_branch_id in selected_branch_ids and branch_company.get(default_branch_id) == company_id:
        branch_id = default_branch_id
    if branch_id is None:
        branch_id = next(
            (selected_id for selected_id in selected_branch_ids if branch_company.get(selected_id) == company_id),
            None,
        )

    session[SELECTED_COMPANIES_SESSION_KEY] = list(selected_company_ids)
    session[SELECTED_BRANCHES_SESSION_KEY] = list(selected_branch_ids)
    if company_id:
        session[ACTIVE_COMPANY_SESSION_KEY] = company_id
    else:
        session.pop(ACTIVE_COMPANY_SESSION_KEY, None)
    if branch_id:
        session[ACTIVE_BRANCH_SESSION_KEY] = branch_id
    else:
        session.pop(ACTIVE_BRANCH_SESSION_KEY, None)

    return OrganizationContext(
        company_id,
        branch_id,
        company_ids,
        branch_ids,
        selected_company_ids,
        selected_branch_ids,
    )


def switch_context(
    user,
    session,
    company_ids,
    branch_ids=None,
    *,
    primary_company_id=None,
    primary_branch_id=None,
):
    allowed_company_ids, allowed_branch_ids, default_company_id, default_branch_id = access_scope(user)
    selected_company_ids = _safe_int_tuple(company_ids)
    selected_branch_ids = _safe_int_tuple(branch_ids)
    if not selected_company_ids:
        raise ValueError("Select at least one company.")
    if any(company_id not in allowed_company_ids for company_id in selected_company_ids):
        raise ValueError("One or more companies are not available to this user.")

    eligible_rows = list(
        Branch.objects.filter(
            pk__in=allowed_branch_ids,
            company_id__in=selected_company_ids,
            active=True,
        )
        .order_by("company__name", "-is_main", "sequence", "name", "pk")
        .values_list("pk", "company_id")
    )
    eligible_branch_ids = {branch_id for branch_id, _ in eligible_rows}
    branch_company = dict(eligible_rows)
    if branch_ids is None:
        selected_branch_ids = _default_selected_branches(
            selected_company_ids,
            eligible_rows,
            default_branch_id,
        )
    if any(branch_id not in eligible_branch_ids for branch_id in selected_branch_ids):
        raise ValueError("One or more branches are not available for the selected companies.")

    companies_with_branches = {company_id for _, company_id in eligible_rows}
    companies_with_selection = {branch_company[branch_id] for branch_id in selected_branch_ids}
    if companies_with_branches - companies_with_selection:
        raise ValueError("Select at least one branch for every selected company that has an active branch.")

    primary_company_id = _safe_int(primary_company_id)
    if primary_company_id not in selected_company_ids:
        primary_company_id = default_company_id if default_company_id in selected_company_ids else selected_company_ids[0]

    primary_branch_id = _safe_int(primary_branch_id)
    if primary_branch_id not in selected_branch_ids or branch_company.get(primary_branch_id) != primary_company_id:
        primary_branch_id = default_branch_id if (
            default_branch_id in selected_branch_ids and branch_company.get(default_branch_id) == primary_company_id
        ) else next(
            (branch_id for branch_id in selected_branch_ids if branch_company.get(branch_id) == primary_company_id),
            None,
        )

    session[SELECTED_COMPANIES_SESSION_KEY] = list(selected_company_ids)
    session[SELECTED_BRANCHES_SESSION_KEY] = list(selected_branch_ids)
    session[ACTIVE_COMPANY_SESSION_KEY] = primary_company_id
    if primary_branch_id:
        session[ACTIVE_BRANCH_SESSION_KEY] = primary_branch_id
    else:
        session.pop(ACTIVE_BRANCH_SESSION_KEY, None)
    session.modified = True
    return resolve_context(user, session)


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_int_tuple(values):
    if values in (None, ""):
        return ()
    if not isinstance(values, (list, tuple, set)):
        values = (values,)
    result = []
    for value in values:
        parsed = _safe_int(value)
        if parsed is not None and parsed not in result:
            result.append(parsed)
    return tuple(result)


def _default_selected_branches(company_ids, branch_rows, default_branch_id=None):
    by_company = {}
    for branch_id, company_id in branch_rows:
        by_company.setdefault(company_id, []).append(branch_id)
    selected = []
    for company_id in company_ids:
        choices = by_company.get(company_id, [])
        if not choices:
            continue
        selected.append(default_branch_id if default_branch_id in choices else choices[0])
    return tuple(selected)


@lru_cache(maxsize=None)
def _relation_lookups(model, target_label, max_depth=4):
    if model._meta.label_lower == target_label.lower():
        return ("pk",)

    queue = [(model, (), frozenset({model._meta.label_lower}), 0)]
    matches = []
    while queue:
        current_model, path, visited, nullable_count = queue.pop(0)
        if len(path) >= max_depth:
            continue
        fields = sorted(current_model._meta.get_fields(), key=lambda field: field.name)
        for field in fields:
            if field.auto_created or not field.is_relation or not (field.many_to_one or field.one_to_one):
                continue
            related_model = field.related_model
            if related_model is None:
                continue
            related_label = related_model._meta.label_lower
            next_path = (*path, field.name)
            next_nullable_count = nullable_count + int(getattr(field, "null", False))
            if related_label == target_label.lower():
                matches.append((next_nullable_count, len(next_path), "__".join(next_path)))
                continue
            if related_label in visited:
                continue
            queue.append((related_model, next_path, visited | {related_label}, next_nullable_count))
    if not matches:
        return ()
    # A single deterministic path keeps SELECT FOR UPDATE compatible with
    # PostgreSQL. Prefer paths that do not cross nullable relations, then the
    # shortest path. Domain models validate that alternate company relations
    # belong to the same company.
    return (min(matches)[2],)


def _filter_by_lookups(queryset, lookups, values):
    values = tuple(values or ())
    if not lookups or not values:
        return queryset
    filters = Q()
    for lookup in lookups:
        filters |= Q(**{f"{lookup}__in": values})
    # Only forward FK/one-to-one paths are traversed, so filtering cannot
    # multiply base rows. Avoid DISTINCT because PostgreSQL rejects
    # SELECT ... FOR UPDATE on a DISTINCT queryset in workflow actions.
    return queryset.filter(filters)


def scope_queryset(queryset, context=None, *, include_branch=True, use_allowed_companies=False):
    context = context or get_current_context()
    if context is None:
        return queryset

    company_lookups = _relation_lookups(queryset.model, "core.company")
    if company_lookups:
        company_ids = context.allowed_company_ids if use_allowed_companies else context.active_company_ids
        if not company_ids:
            return queryset.none()
        queryset = _filter_by_lookups(queryset, company_lookups, company_ids)

    branch_lookups = _relation_lookups(queryset.model, "organization.branch")
    if include_branch and branch_lookups:
        if not context.active_branch_ids:
            return queryset.none()
        queryset = _filter_by_lookups(queryset, branch_lookups, context.active_branch_ids)
    return queryset


def scope_form(form, context=None, *, include_branch=True):
    context = context or get_current_context()
    if context is None:
        return form

    creating = not getattr(form.instance, "pk", None)
    for name, field in form.fields.items():
        queryset = getattr(field, "queryset", None)
        if queryset is None:
            continue
        field.queryset = scope_queryset(queryset, context, include_branch=include_branch)
        if creating and not form.is_bound:
            if queryset.model is Company and context.company_id:
                form.initial[name] = context.company_id
            elif queryset.model is Branch and context.branch_id:
                form.initial[name] = context.branch_id
    return form
