"""Discoverable outbound accounting connector registry."""

from importlib import import_module
from pkgutil import iter_modules

from django.core.exceptions import ValidationError


_BACKENDS = {}
_DISCOVERED = False


def register_external_connector_backend(code, backend_class):
    normalized = code.strip().lower()
    if not normalized:
        raise ValueError("External connector backend code cannot be empty.")
    if normalized in _BACKENDS:
        raise ValueError(f"External connector backend {normalized} is already registered.")
    _BACKENDS[normalized] = backend_class


def discover_external_connector_backends():
    global _DISCOVERED
    if _DISCOVERED:
        return
    for module in iter_modules(__path__):
        if not module.name.startswith("_"):
            import_module(f"{__name__}.{module.name}")
    _DISCOVERED = True


def external_connector_backend(connector):
    discover_external_connector_backends()
    backend_class = _BACKENDS.get(connector.backend.strip().lower())
    if backend_class is None:
        raise ValidationError(f"External accounting connector backend {connector.backend!r} is not installed.")
    return backend_class(connector)
