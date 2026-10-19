from functools import lru_cache

from django.contrib.contenttypes.models import ContentType

from core.models import Company

from .models import Branch, RecordOrganizationScope


EXCLUDED_MODEL_LABELS = {
    "core.company",
    "organization.branch",
    "organization.recordorganizationscope",
    "organization.userorganizationaccess",
}


@lru_cache(maxsize=None)
def direct_company_field_name(model):
    """Return the concrete FK/one-to-one field that owns a record's company."""
    for field in model._meta.concrete_fields:
        if not field.is_relation or not (field.many_to_one or field.one_to_one):
            continue
        related_model = field.related_model
        if related_model and related_model._meta.label_lower == Company._meta.label_lower:
            return field.name
    return None


@lru_cache(maxsize=None)
def has_direct_branch_field(model):
    return any(
        field.is_relation
        and (field.many_to_one or field.one_to_one)
        and field.related_model
        and field.related_model._meta.label_lower == Branch._meta.label_lower
        for field in model._meta.concrete_fields
    )


def default_branch_for_company(company_id, context=None):
    if not company_id:
        return None

    selected_ids = tuple(getattr(context, "active_branch_ids", ()) or ())
    if selected_ids:
        branches = Branch.objects.filter(
            pk__in=selected_ids,
            company_id=company_id,
            active=True,
        ).in_bulk()
        for branch_id in selected_ids:
            if branch_id in branches:
                return branches[branch_id]

    return (
        Branch.objects.filter(company_id=company_id, active=True)
        .order_by("-is_main", "sequence", "name", "pk")
        .first()
        or Branch.objects.filter(company_id=company_id)
        .order_by("-is_main", "sequence", "name", "pk")
        .first()
    )


def record_scope_for(instance):
    if not getattr(instance, "pk", None):
        return None
    content_type = ContentType.objects.get_for_model(instance, for_concrete_model=False)
    return (
        RecordOrganizationScope.objects.select_related("company", "branch")
        .filter(content_type=content_type, object_id=str(instance.pk))
        .first()
    )


def assign_record_scope(instance, branch_id=None, context=None):
    """Create or update one record's stable company/branch ownership."""
    model = instance.__class__
    if (
        not getattr(instance, "pk", None)
        or model._meta.label_lower in EXCLUDED_MODEL_LABELS
        or model._meta.proxy
        or not model._meta.managed
        or has_direct_branch_field(model)
    ):
        return None

    company_field = direct_company_field_name(model)
    if not company_field:
        return None
    company_id = getattr(instance, model._meta.get_field(company_field).attname, None)
    if not company_id:
        return None

    requested_branch_id = branch_id or getattr(instance, "_gsshop_organization_branch_id", None)
    branch = None
    if requested_branch_id:
        branch = Branch.objects.filter(pk=requested_branch_id, company_id=company_id).first()
    if branch is None:
        branch = default_branch_for_company(company_id, context)
    if branch is None:
        return None

    content_type = ContentType.objects.get_for_model(instance, for_concrete_model=False)
    scope, _created = RecordOrganizationScope.objects.update_or_create(
        content_type=content_type,
        object_id=str(instance.pk),
        defaults={"company_id": company_id, "branch_id": branch.pk},
    )
    return scope


def delete_record_scope(instance):
    model = instance.__class__
    if (
        not getattr(instance, "pk", None)
        or model._meta.label_lower in EXCLUDED_MODEL_LABELS
        or not direct_company_field_name(model)
        or has_direct_branch_field(model)
    ):
        return
    content_type = ContentType.objects.get_for_model(instance, for_concrete_model=False)
    RecordOrganizationScope.objects.filter(
        content_type=content_type,
        object_id=str(instance.pk),
    ).delete()
