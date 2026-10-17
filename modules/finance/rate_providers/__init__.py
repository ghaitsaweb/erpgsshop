"""Regulatory exchange-rate provider registry."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from importlib import import_module
from pkgutil import iter_modules

from django.core.exceptions import ValidationError


@dataclass(frozen=True)
class RegulatoryRateResult:
    rates: list[dict]
    payload: dict = field(default_factory=dict)


_PROVIDERS = {}
_DISCOVERED = False


def register_rate_provider(code, provider_class):
    normalized = str(code).strip().lower()
    if not normalized or normalized in _PROVIDERS:
        raise ValueError("Regulatory rate provider code must be non-empty and unique.")
    _PROVIDERS[normalized] = provider_class
    return provider_class


def discover_rate_providers():
    global _DISCOVERED
    if _DISCOVERED:
        return
    for module in iter_modules(__path__):
        if not module.name.startswith("_"):
            import_module(f"{__name__}.{module.name}")
    _DISCOVERED = True


def rate_provider(run):
    discover_rate_providers()
    provider_class = _PROVIDERS.get(run.source.strip().lower())
    if provider_class is None:
        raise ValidationError(f"Regulatory rate provider {run.source!r} is not installed.")
    return provider_class(run)
