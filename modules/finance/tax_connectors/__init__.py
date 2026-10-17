"""Pluggable statutory connector backend registry."""

from importlib import import_module
from pkgutil import iter_modules

from django.core.exceptions import ValidationError


_BACKENDS = {}
_DISCOVERED = False


def register_connector_backend(code, backend_class):
    normalized = code.strip().lower()
    if not normalized:
        raise ValueError("Connector backend code cannot be empty.")
    if normalized in _BACKENDS:
        raise ValueError(f"Connector backend {normalized} is already registered.")
    _BACKENDS[normalized] = backend_class
    return backend_class


def discover_connector_backends():
    global _DISCOVERED
    if _DISCOVERED:
        return
    package_name = __name__
    for module in iter_modules(__path__):
        if not module.name.startswith("_"):
            import_module(f"{package_name}.{module.name}")
    _DISCOVERED = True


def connector_backend(connector):
    discover_connector_backends()
    backend_class = _BACKENDS.get(connector.connector_type.strip().lower())
    if backend_class is None:
        raise ValidationError(
            f"Connector backend {connector.connector_type!r} is not installed. "
            "Install/register the matching localization connector module."
        )
    return backend_class(connector)
