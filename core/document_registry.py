"""Generic installed-module extensions for document workflows and options."""

import re
from copy import deepcopy

from django.core.exceptions import ImproperlyConfigured


_ACTION_EXTENSIONS = {}
_OPTION_EXTENSIONS = {}
_HEADER_FIELD_EXTENSIONS = {}
_LINE_FIELD_EXTENSIONS = {}
_TAB_EXTENSIONS = {}


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


def register_document_tab(entity, key, definition, *, after=None, override=False):
    """Register one module-owned tab on a generic document form."""
    if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", key):
        raise ImproperlyConfigured("Document tab keys must use lowercase slug syntax.")
    if not isinstance(definition, dict):
        raise ImproperlyConfigured(f"Document tab '{entity}.{key}' must be a mapping.")
    label = definition.get("label")
    template = definition.get("template")
    if not isinstance(label, str) or not label.strip():
        raise ImproperlyConfigured(f"Document tab '{entity}.{key}' requires a label.")
    if not isinstance(template, str) or not template.strip():
        raise ImproperlyConfigured(f"Document tab '{entity}.{key}' requires a template.")
    field_names = definition.get("field_names", ())
    if not isinstance(field_names, (list, tuple)) or any(
        not isinstance(field, str) or not field for field in field_names
    ):
        raise ImproperlyConfigured(
            f"Document tab '{entity}.{key}' field_names must be a list of field names."
        )
    captures = definition.get("captures", ())
    if not isinstance(captures, (list, tuple)) or any(
        not isinstance(capture, str) or not capture for capture in captures
    ):
        raise ImproperlyConfigured(
            f"Document tab '{entity}.{key}' captures must be a list of content keys."
        )
    visibility = definition.get("is_visible")
    if visibility is not None and not callable(visibility):
        raise ImproperlyConfigured(
            f"Document tab '{entity}.{key}' is_visible must be callable."
        )
    if after is not None and (not isinstance(after, str) or not after):
        raise ImproperlyConfigured(
            f"Document tab '{entity}.{key}' after must be a non-empty tab key."
        )
    registry_key = (entity, key)
    registered = (deepcopy(definition), after, bool(override))
    existing = _TAB_EXTENSIONS.get(registry_key)
    if existing is not None and existing != registered:
        raise ImproperlyConfigured(f"Document tab extension '{entity}.{key}' is already registered.")
    _TAB_EXTENSIONS[registry_key] = registered


def _apply_tab_extensions(document_config):
    by_entity = {}
    for (entity, key), (definition, after, override) in sorted(_TAB_EXTENSIONS.items()):
        if entity not in document_config:
            raise ImproperlyConfigured(f"Document tab targets unknown entity '{entity}'.")
        by_entity.setdefault(entity, {})[key] = (definition, after, override)

    for entity, pending in sorted(by_entity.items()):
        tabs = document_config[entity].setdefault("tabs", [])
        if not isinstance(tabs, list):
            raise ImproperlyConfigured(f"Document tabs for '{entity}' must be a list.")
        existing_keys = []
        for tab in tabs:
            if not isinstance(tab, dict) or not tab.get("key"):
                raise ImproperlyConfigured(f"Document tabs for '{entity}' require stable keys.")
            if tab["key"] in existing_keys:
                raise ImproperlyConfigured(
                    f"Document tab '{entity}.{tab['key']}' is declared more than once."
                )
            existing_keys.append(tab["key"])

        while pending:
            progressed = False
            for key in sorted(tuple(pending)):
                definition, after, override = pending[key]
                existing_index = next(
                    (index for index, tab in enumerate(tabs) if tab.get("key") == key),
                    None,
                )
                if existing_index is not None:
                    if not override:
                        raise ImproperlyConfigured(f"Document tab '{entity}.{key}' already exists.")
                    tabs.pop(existing_index)
                if after is not None and not any(tab.get("key") == after for tab in tabs):
                    continue
                tab = {"key": key, **deepcopy(definition)}
                if after is None:
                    tabs.append(tab)
                else:
                    after_index = next(
                        index for index, item in enumerate(tabs) if item.get("key") == after
                    )
                    tabs.insert(after_index + 1, tab)
                pending.pop(key)
                progressed = True
            if not progressed:
                unresolved = ", ".join(
                    f"{key} after {after!r}"
                    for key, (_definition, after, _override) in sorted(pending.items())
                )
                raise ImproperlyConfigured(
                    f"Document tabs for '{entity}' have missing or cyclic anchors: {unresolved}."
                )


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
    _apply_tab_extensions(document_config)
    for (entity, action), (definition, override) in sorted(_ACTION_EXTENSIONS.items()):
        if entity not in document_config:
            raise ImproperlyConfigured(f"Document action targets unknown entity '{entity}'.")
        actions = document_config[entity].setdefault("actions", {})
        if action in actions and not override:
            raise ImproperlyConfigured(f"Document action '{entity}.{action}' already exists.")
        actions[action] = deepcopy(definition)
    return document_config
