"""Discoverable country accounting packs.

Each country lives in its own module and registers metadata through the small
contract below. Finance never branches on a country code; adding a pack only
requires adding/importing an installed pack module.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from importlib import import_module
from pkgutil import iter_modules

from django.utils import timezone


GENERAL_LEDGER_MAPPING = {
    "total_debit": "debit",
    "total_credit": "credit",
    "withholding_gross": "withholding_gross",
    "withholding_tax": "withholding_tax",
}
BASE_VALIDATION = {
    "required_fields": ["total_debit", "total_credit"],
    "non_negative_fields": ["total_debit", "total_credit", "withholding_gross", "withholding_tax"],
    "equal_fields": [["total_debit", "total_credit"]],
    "registration_required": True,
}


@dataclass(frozen=True)
class ReportDefinition:
    code: str
    name: str
    output_format: str
    field_mapping: dict = field(default_factory=lambda: dict(GENERAL_LEDGER_MAPPING))
    validation_rules: dict = field(default_factory=lambda: dict(BASE_VALIDATION))
    version: str = "1.0"
    menu_entity: str = "compliance-reports"


@dataclass(frozen=True)
class TaxDefinition:
    code: str
    name: str
    tax_type: str
    rate: Decimal
    authority_code: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CountryPack:
    country_code: str
    reports: tuple[ReportDefinition, ...]
    taxes: tuple[TaxDefinition, ...]


_PACKS = {}
_DISCOVERED = False


def register_country_pack(pack):
    code = pack.country_code.strip().upper()
    if len(code) != 2:
        raise ValueError("Country accounting pack codes must use ISO alpha-2 format.")
    if code in _PACKS:
        raise ValueError(f"Country accounting pack {code} is already registered.")
    _PACKS[code] = pack
    return pack


def discover_country_packs():
    global _DISCOVERED
    if _DISCOVERED:
        return
    package_name = __name__
    for module in iter_modules(__path__):
        if not module.name.startswith("_"):
            import_module(f"{package_name}.{module.name}")
    _DISCOVERED = True


def country_pack(country_code):
    discover_country_packs()
    return _PACKS.get((country_code or "").strip().upper())


def registered_country_packs():
    discover_country_packs()
    return tuple(_PACKS[code] for code in sorted(_PACKS))


def provision_country_profile(profile):
    """Idempotently synchronize the selected profile with its registered pack."""
    from ..models import LocalizationTaxCode, StatutoryReportTemplate

    pack = country_pack(profile.country_code)
    if pack is None:
        return {"templates_created": 0, "templates_updated": 0, "taxes_created": 0, "taxes_updated": 0}

    now = timezone.now()
    existing_templates = {
        (row.form_code, row.version): row
        for row in StatutoryReportTemplate.objects.filter(profile=profile)
    }
    template_creates = []
    template_updates = []
    for definition in pack.reports:
        key = (definition.code, definition.version)
        values = {
            "name": definition.name,
            "output_format": definition.output_format,
            "field_mapping": definition.field_mapping,
            "validation_rules": definition.validation_rules,
            "active": True,
        }
        current = existing_templates.get(key)
        if current is None:
            template_creates.append(StatutoryReportTemplate(
                profile=profile, form_code=definition.code, version=definition.version, **values
            ))
        else:
            for field_name, value in values.items():
                setattr(current, field_name, value)
            current.updated_at = now
            template_updates.append(current)
    StatutoryReportTemplate.objects.bulk_create(template_creates)
    if template_updates:
        StatutoryReportTemplate.objects.bulk_update(
            template_updates,
            ["name", "output_format", "field_mapping", "validation_rules", "active", "updated_at"],
        )

    existing_taxes = {row.code: row for row in LocalizationTaxCode.objects.filter(profile=profile)}
    tax_creates = []
    tax_updates = []
    for definition in pack.taxes:
        values = {
            "name": definition.name,
            "tax_type": definition.tax_type,
            "rate": definition.rate,
            "authority_code": definition.authority_code or definition.code,
            "metadata": definition.metadata,
            "active": True,
        }
        current = existing_taxes.get(definition.code)
        if current is None:
            tax_creates.append(LocalizationTaxCode(profile=profile, code=definition.code, **values))
        else:
            for field_name, value in values.items():
                setattr(current, field_name, value)
            current.updated_at = now
            tax_updates.append(current)
    LocalizationTaxCode.objects.bulk_create(tax_creates)
    if tax_updates:
        LocalizationTaxCode.objects.bulk_update(
            tax_updates, ["name", "tax_type", "rate", "authority_code", "metadata", "active", "updated_at"]
        )
    return {
        "templates_created": len(template_creates),
        "templates_updated": len(template_updates),
        "taxes_created": len(tax_creates),
        "taxes_updated": len(tax_updates),
    }
