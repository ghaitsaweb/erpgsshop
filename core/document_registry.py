"""Generic installed-module extensions for document workflows and options."""

from copy import deepcopy

from django.core.exceptions import ImproperlyConfigured


_ACTION_EXTENSIONS = {}
_OPTION_EXTENSIONS = {}


def register_document_action(entity, action, definition, *, override=False):
    key = (entity, action)
    value = (deepcopy(definition), bool(override))
    existing = _ACTION_EXTENSIONS.get(key)
    if existing is not None and existing != value:
        raise ImproperlyConfigured(f"Document action extension '{entity}.{action}' is already registered.")
    _ACTION_EXTENSIONS[key] = value


def register_document_option(entity, option, value, *, override=False):
    key = (entity, option)
    registered = (deepcopy(value), bool(override))
    existing = _OPTION_EXTENSIONS.get(key)
    if existing is not None and existing != registered:
        raise ImproperlyConfigured(f"Document option extension '{entity}.{option}' is already registered.")
    _OPTION_EXTENSIONS[key] = registered


def apply_document_extensions(document_config):
    for (entity, option), (value, override) in sorted(_OPTION_EXTENSIONS.items()):
        if entity not in document_config:
            raise ImproperlyConfigured(f"Document extension targets unknown entity '{entity}'.")
        if option in document_config[entity] and not override:
            raise ImproperlyConfigured(f"Document option '{entity}.{option}' already exists.")
        document_config[entity][option] = deepcopy(value)
    for (entity, action), (definition, override) in sorted(_ACTION_EXTENSIONS.items()):
        if entity not in document_config:
            raise ImproperlyConfigured(f"Document action targets unknown entity '{entity}'.")
        actions = document_config[entity].setdefault("actions", {})
        if action in actions and not override:
            raise ImproperlyConfigured(f"Document action '{entity}.{action}' already exists.")
        actions[action] = deepcopy(definition)
    return document_config
