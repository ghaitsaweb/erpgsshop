"""Generic installed-module extensions for document workflows and options."""

from copy import deepcopy

from django.core.exceptions import ImproperlyConfigured


_ACTION_EXTENSIONS = {}
_OPTION_EXTENSIONS = {}
_HEADER_FIELD_EXTENSIONS = {}
_LINE_FIELD_EXTENSIONS = {}


def register_document_action(entity, action, definition, *, override=False):
    handler = definition.get("handler")
    if handler is not None and not callable(handler):
        raise ImproperlyConfigured(f"Document action handler '{entity}.{action}' must be callable.")
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


def register_document_header_field(entity, field, *, after=None):
    key = (entity, field)
    existing = _HEADER_FIELD_EXTENSIONS.get(key)
    if existing is not None and existing != after:
        raise ImproperlyConfigured(f"Document header field extension '{entity}.{field}' is already registered.")
    _HEADER_FIELD_EXTENSIONS[key] = after


def register_document_line_field(entity, field, *, after=None):
    """Add one field to a document's primary line section without owning core config."""
    key = (entity, field)
    existing = _LINE_FIELD_EXTENSIONS.get(key)
    if existing is not None and existing != after:
        raise ImproperlyConfigured(f"Document line field extension '{entity}.{field}' is already registered.")
    _LINE_FIELD_EXTENSIONS[key] = after


def apply_document_extensions(document_config):
    for (entity, option), (value, override) in sorted(_OPTION_EXTENSIONS.items()):
        if entity not in document_config:
            raise ImproperlyConfigured(f"Document extension targets unknown entity '{entity}'.")
        if option in document_config[entity] and not override:
            raise ImproperlyConfigured(f"Document option '{entity}.{option}' already exists.")
        document_config[entity][option] = deepcopy(value)
    for (entity, field), after in sorted(_HEADER_FIELD_EXTENSIONS.items()):
        if entity not in document_config:
            raise ImproperlyConfigured(f"Document header extension targets unknown entity '{entity}'.")
        fields = document_config[entity].get("header_fields")
        if fields is None:
            continue
        if field in fields:
            continue
        if after is None:
            fields.append(field)
            continue
        if after not in fields:
            raise ImproperlyConfigured(
                f"Document header field '{entity}.{field}' references missing field '{after}'."
            )
        fields.insert(fields.index(after) + 1, field)
    for (entity, field), after in sorted(_LINE_FIELD_EXTENSIONS.items()):
        if entity not in document_config:
            raise ImproperlyConfigured(f"Document line extension targets unknown entity '{entity}'.")
        fields = document_config[entity].get("line_fields")
        if fields is None:
            continue
        if field in fields:
            continue
        if after is None:
            fields.append(field)
            continue
        if after not in fields:
            raise ImproperlyConfigured(
                f"Document line field '{entity}.{field}' references missing field '{after}'."
            )
        fields.insert(fields.index(after) + 1, field)
    for (entity, action), (definition, override) in sorted(_ACTION_EXTENSIONS.items()):
        if entity not in document_config:
            raise ImproperlyConfigured(f"Document action targets unknown entity '{entity}'.")
        actions = document_config[entity].setdefault("actions", {})
        if action in actions and not override:
            raise ImproperlyConfigured(f"Document action '{entity}.{action}' already exists.")
        actions[action] = deepcopy(definition)
    return document_config
