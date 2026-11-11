from importlib import import_module
from importlib.util import find_spec
from types import MappingProxyType

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured


def discover_installed_entity_configs():
    """Load the standard ENTITY_CONFIG contract from installed Django apps."""

    discovered = {}
    owners = {}
    for app_config in sorted(apps.get_app_configs(), key=lambda item: item.name):
        module_name = f"{app_config.name}.entity_config"
        try:
            spec = find_spec(module_name)
        except (ModuleNotFoundError, ValueError):
            spec = None
        if spec is None:
            continue
        module = import_module(module_name)
        entity_config = getattr(module, "ENTITY_CONFIG", None)
        if entity_config is None:
            continue
        if not isinstance(entity_config, dict):
            raise ImproperlyConfigured(f"{module_name}.ENTITY_CONFIG must be a dictionary.")
        for entity, config in sorted(entity_config.items()):
            if not isinstance(config, dict) or "model" not in config:
                raise ImproperlyConfigured(
                    f"{module_name}.ENTITY_CONFIG['{entity}'] must define a model."
                )
            if entity in discovered:
                raise ImproperlyConfigured(
                    f"Entity '{entity}' is registered by both {owners[entity]} and {module_name}."
                )
            discovered[entity] = config
            owners[entity] = module_name
    return MappingProxyType(discovered)


def merge_entity_configs(base_config, discovered_config):
    """Merge installed-module entities while rejecting central key collisions."""

    duplicates = sorted(set(base_config) & set(discovered_config))
    if duplicates:
        raise ImproperlyConfigured(
            "Installed entity config collides with core entities: " + ", ".join(duplicates)
        )
    return {**base_config, **discovered_config}
