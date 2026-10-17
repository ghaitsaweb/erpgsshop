"""Shared statutory localization registry for HMX country accounting packs."""

from .models import (
    ComplianceAuditEvent,
    LocalizationTaxCode,
    RegulatoryExchangeRate,
    StatutoryReportLine,
    StatutoryReportTemplate,
    TaxAuthorityDocument,
    TaxIdentifierLookup,
    TaxMiddlewareConnector,
)


def _workflow(label, action, from_statuses, to_status, *, primary=True):
    return {
        action: {
            "label": label, "from": from_statuses, "to": to_status, "primary": primary,
            "service_controls_state": True,
        }
    }


LOCALIZATION_ENTITY_CONFIG = {
    "localization-tax-codes": {
        "model": LocalizationTaxCode, "title": "Localization Tax Codes", "singular": "Localization Tax Code",
        "columns": [("profile", "Country Profile"), ("code", "Code"), ("name", "Tax Code"), ("tax_type", "Type"), ("rate", "Rate"), ("active", "Active")],
        "search": ["profile__country_code", "code", "name", "tax_type", "authority_code"],
        "form_fields": ["profile", "code", "name", "tax_type", "rate", "authority_code", "posting_account", "metadata", "active"],
        "select_related": ["profile", "profile__company", "posting_account"],
    },
    "statutory-report-templates": {
        "model": StatutoryReportTemplate, "title": "Statutory Report Templates & Field Mapping", "singular": "Statutory Report Template",
        "columns": [("profile", "Country Profile"), ("form_code", "Form"), ("name", "Template"), ("version", "Version"), ("output_format", "Format"), ("active", "Active")],
        "search": ["profile__country_code", "form_code", "name", "version"],
        "form_fields": ["profile", "form_code", "name", "version", "output_format", "field_mapping", "validation_rules", "active"],
        "select_related": ["profile", "profile__company"],
    },
    "statutory-report-lines": {
        "model": StatutoryReportLine, "title": "Statutory Report Lines", "singular": "Statutory Report Line",
        "columns": [("report", "Report"), ("sequence", "Sequence"), ("field_code", "Field"), ("label", "Label"), ("tax_base", "Tax Base"), ("tax_amount", "Tax Amount"), ("value", "Value")],
        "search": ["report__reference", "field_code", "label", "value"],
        "form_fields": ["report", "sequence", "field_code", "label", "tax_base", "tax_amount", "value", "metadata"],
        "select_related": ["report", "report__profile", "report__profile__company"],
    },
    "tax-authority-documents": {
        "model": TaxAuthorityDocument, "title": "Tax Authority Documents", "singular": "Tax Authority Document",
        "columns": [("issue_date", "Date"), ("profile", "Country Profile"), ("direction", "Direction"), ("document_type", "Type"), ("internal_reference", "Internal Ref"), ("external_reference", "Authority Ref"), ("status", "Status")],
        "search": ["profile__country_code", "document_type", "internal_reference", "external_reference", "error_message"],
        "form_fields": ["profile", "direction", "document_type", "internal_reference", "external_reference", "issue_date", "payload"],
        "select_related": ["profile", "profile__company"],
        "workflow": {
            **_workflow("Queue Submission", "queue", ["draft", "rejected"], "queued"),
            **_workflow("Send to Authority", "send", ["queued"], "sent"),
            "cancel": {"label": "Cancel", "from": ["draft", "queued"], "to": "cancelled"},
        },
    },
    "tax-identifier-lookups": {
        "model": TaxIdentifierLookup, "title": "Tax Identifier / TIN Lookup", "singular": "Tax Identifier Lookup",
        "columns": [("created_at", "Requested"), ("profile", "Country Profile"), ("identifier_type", "Type"), ("identifier", "Identifier"), ("result_name", "Result"), ("status", "Status")],
        "search": ["profile__country_code", "identifier", "result_name", "error_message"],
        "form_fields": ["profile", "identifier", "identifier_type", "requested_by"],
        "select_related": ["profile", "profile__company", "requested_by"],
        "workflow": _workflow("Run TIN Lookup", "lookup", ["pending", "failed"], "found"),
        "workflow_locked_statuses": set(),
    },
    "regulatory-exchange-rates": {
        "model": RegulatoryExchangeRate, "title": "Regulatory / BI Exchange Rates", "singular": "Regulatory Exchange Rate",
        "columns": [("rate_date", "Date"), ("profile", "Country Profile"), ("currency", "Currency"), ("rate", "Rate"), ("source", "Source"), ("reference", "Reference")],
        "search": ["profile__country_code", "currency", "source", "reference"],
        "form_fields": ["profile", "rate_date", "currency", "rate", "source", "reference", "payload"],
        "select_related": ["profile", "profile__company"],
    },
    "tax-middleware-connectors": {
        "model": TaxMiddlewareConnector, "title": "Tax Middleware Connectors", "singular": "Tax Middleware Connector",
        "columns": [("profile", "Country Profile"), ("name", "Connector"), ("connector_type", "Type"), ("endpoint_url", "Endpoint"), ("status", "Status"), ("last_health_check_at", "Last Check")],
        "search": ["profile__country_code", "name", "connector_type", "endpoint_url", "last_error"],
        "form_fields": ["profile", "name", "connector_type", "endpoint_url", "credential_alias", "timeout_seconds", "verify_tls", "configuration"],
        "select_related": ["profile", "profile__company"],
        "workflow": _workflow("Validate Configuration", "validate", ["disabled", "error"], "ready"),
        "workflow_locked_statuses": set(),
    },
    "compliance-audit-events": {
        "model": ComplianceAuditEvent, "title": "Compliance Audit History", "singular": "Compliance Audit Event",
        "columns": [("created_at", "Date"), ("profile", "Country Profile"), ("event_type", "Event"), ("object_type", "Object"), ("reference", "Reference"), ("actor", "Actor")],
        "search": ["profile__country_code", "event_type", "object_type", "reference", "actor__username"],
        "form_fields": [], "select_related": ["profile", "profile__company", "actor"],
        "creatable": False, "editable": False, "deletable": False,
    },
}
