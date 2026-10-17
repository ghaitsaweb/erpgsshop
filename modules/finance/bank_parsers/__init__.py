"""Pluggable bank-statement parser registry."""

from dataclasses import dataclass, field
from decimal import Decimal
from importlib import import_module
from pkgutil import iter_modules

from django.core.exceptions import ValidationError


@dataclass(frozen=True)
class BankParseResult:
    rows: list[dict]
    confidence: Decimal
    raw_result: dict = field(default_factory=dict)


_PARSERS = {}
_DISCOVERED = False


def register_bank_parser(code, parser_class):
    normalized = str(code).strip().lower()
    if not normalized:
        raise ValueError("Bank parser code cannot be empty.")
    if normalized in _PARSERS:
        raise ValueError(f"Bank parser {normalized} is already registered.")
    _PARSERS[normalized] = parser_class
    return parser_class


def discover_bank_parsers():
    global _DISCOVERED
    if _DISCOVERED:
        return
    for module in iter_modules(__path__):
        if not module.name.startswith("_"):
            import_module(f"{__name__}.{module.name}")
    _DISCOVERED = True


def bank_parser(job):
    discover_bank_parsers()
    parser_class = _PARSERS.get((job.parser_provider or "builtin").strip().lower())
    if parser_class is None:
        raise ValidationError(f"Bank parser provider {job.parser_provider!r} is not installed.")
    return parser_class(job)
