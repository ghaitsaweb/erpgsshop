"""Idempotent Construction role and model-permission setup."""

from django.contrib.auth.models import Group, Permission

from .access import (
    CONSTRUCTION_MANAGER,
    CONSTRUCTION_PUBLIC_USER,
    CONSTRUCTION_SUPERVISOR,
    CONSTRUCTION_SUPER_USER,
    PUBLIC_ENTITIES,
)
from .entity_config import ENTITY_MODELS


CONSTRUCTION_MODELS = {model._meta.model_name for model in ENTITY_MODELS.values()}
OPERATIONS_MODELS = {"constructionproject", "workpackage"}
PUBLIC_MODELS = {
    ENTITY_MODELS[entity]._meta.model_name
    for entity in PUBLIC_ENTITIES
    if entity in ENTITY_MODELS
}


def _permissions(app_label, models, actions):
    codenames = {f"{action}_{model}" for model in models for action in actions}
    return Permission.objects.filter(
        content_type__app_label=app_label,
        content_type__model__in=models,
        codename__in=codenames,
    )


def ensure_construction_roles(**_kwargs):
    all_actions = {"view", "add", "change", "delete"}
    operational_actions = {"view", "add", "change"}
    all_permissions = _permissions("gsshop_construction", CONSTRUCTION_MODELS, all_actions) | _permissions(
        "gsshop_operations", OPERATIONS_MODELS, all_actions
    )
    operational_permissions = _permissions(
        "gsshop_construction", CONSTRUCTION_MODELS, operational_actions
    ) | _permissions("gsshop_operations", OPERATIONS_MODELS, operational_actions)
    role_permissions = {
        CONSTRUCTION_SUPER_USER: all_permissions,
        CONSTRUCTION_MANAGER: all_permissions,
        CONSTRUCTION_SUPERVISOR: operational_permissions,
        CONSTRUCTION_PUBLIC_USER: _permissions("gsshop_construction", PUBLIC_MODELS, operational_actions),
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
