"""Idempotent Sales roles for HMX-compatible ownership and approvals."""

from django.contrib.auth.models import Group, Permission


SALES_SUPER_USER = "Sales Super User"
SALES_MANAGER = "Sales Manager"
SALES_ADMINISTRATOR = "Sales Administrator"
SALES_USER = "Sales User"


def _sales_permissions(actions):
    model_names = {
        "saleorder",
        "saleorderline",
        "salequotationemail",
        "companysalesetting",
        "saleordertype",
        "saleordertypesequence",
    }
    codenames = {
        f"{action}_{model_name}"
        for action in actions
        for model_name in model_names
    }
    return Permission.objects.filter(
        content_type__app_label="core",
        content_type__model__in=model_names,
        codename__in=codenames,
    )


def ensure_sales_roles(**_kwargs):
    all_permissions = _sales_permissions({"view", "add", "change", "delete"})
    operational_model_names = {"saleorder", "saleorderline", "salequotationemail"}
    operational_codenames = {
        f"{action}_{model_name}"
        for action in {"view", "add", "change"}
        for model_name in operational_model_names
    }
    operational_permissions = Permission.objects.filter(
        content_type__app_label="core",
        content_type__model__in=operational_model_names,
        codename__in=operational_codenames,
    )
    order_type_view = Permission.objects.filter(
        content_type__app_label="core",
        content_type__model="saleordertype",
        codename="view_saleordertype",
    )
    operational_permissions = operational_permissions | order_type_view
    role_permissions = {
        SALES_SUPER_USER: all_permissions,
        SALES_MANAGER: all_permissions,
        SALES_ADMINISTRATOR: all_permissions,
        SALES_USER: operational_permissions,
    }
    role_names = tuple(role_permissions)
    Group.objects.bulk_create(
        [Group(name=role_name) for role_name in role_names],
        ignore_conflicts=True,
        batch_size=100,
    )
    groups = Group.objects.in_bulk(role_names, field_name="name")
    for role_name, permissions in role_permissions.items():
        groups[role_name].permissions.add(*permissions)
