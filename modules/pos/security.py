"""Idempotent Retail/F&B role and permission setup."""

from django.contrib.auth.models import Group, Permission

from .access import CASHIER_ENTITIES, POS_CASHIER, POS_MANAGER, POS_SUPERVISOR
from .entity_config import POS_ENTITY_CONFIG


POS_MODELS = {
    config["model"]._meta.model_name
    for config in POS_ENTITY_CONFIG.values()
    if config.get("model") and config["model"]._meta.app_label == "gsshop_pos"
}
CASHIER_MODELS = {
    POS_ENTITY_CONFIG[entity]["model"]._meta.model_name
    for entity in CASHIER_ENTITIES
    if entity in POS_ENTITY_CONFIG and POS_ENTITY_CONFIG[entity]["model"]._meta.app_label == "gsshop_pos"
}


def _permissions(models, actions):
    codenames = {f"{action}_{model}" for model in models for action in actions}
    return Permission.objects.filter(
        content_type__app_label="gsshop_pos",
        content_type__model__in=models,
        codename__in=codenames,
    )


def ensure_pos_roles(**_kwargs):
    all_actions = {"view", "add", "change", "delete"}
    operational_actions = {"view", "add", "change"}
    role_permissions = {
        POS_MANAGER: _permissions(POS_MODELS, all_actions),
        POS_SUPERVISOR: _permissions(POS_MODELS, operational_actions),
        POS_CASHIER: _permissions(CASHIER_MODELS, operational_actions),
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
