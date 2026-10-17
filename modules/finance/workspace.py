"""Accounting-owned workspace contributions.

Keeping these declarations with Finance mirrors the HMX module ownership model:
the app hub does not need to know which accounting extensions are installed.
"""

from modules.apphub.workspaces import register_workspace_contribution

from .localization_packs import registered_country_packs


def register_accounting_workspace():
    register_workspace_contribution(
        "accounting",
        group_label="Accounting Integrations",
        group_icon="I",
        items=[
            {"label": "Source Adapter Registry", "entity": "accounting-source-adapters"},
            {"label": "Source Posting Requests", "entity": "source-accounting-postings"},
            {"label": "External Accounting Connectors", "entity": "external-accounting-connectors"},
            {"label": "External Invoice Sync", "entity": "external-invoice-syncs"},
            {"label": "Rental Order Accounting", "entity": "rental-order-accounting"},
            {"label": "Rental Deposit Holds", "entity": "rental-deposit-holds"},
            {"label": "Rental Invoice Provenance", "entity": "rental-invoice-accounting"},
            {"label": "Rental Invoice Line Provenance", "entity": "rental-invoice-line-accounting"},
            {"label": "Rental Accounting Settings", "entity": "rental-accounting-settings"},
            {"label": "Service Work Order Vendor Bills", "entity": "service-work-order-accounting"},
            {"label": "Marketplace Accounting Settings", "entity": "marketplace-accounting-settings"},
            {"label": "Marketplace Order Accounting", "entity": "marketplace-accounting-orders"},
            {"label": "Sales Promotion Accounting", "entity": "sales-promotion-accounting"},
            {"label": "Sales Order Invoice Accounting", "entity": "sale-order-invoice-accounting"},
            {"label": "Sales Invoice Provenance", "entity": "sales-invoice-accounting"},
            {"label": "Promotion Expense Splits", "entity": "sales-invoice-promotion-splits"},
            {"label": "POS Order Invoice, Deposit & MDR", "entity": "pos-order-accounting"},
            {"label": "Localized Sales Tax", "entity": "sale-order-localizations"},
            {"label": "Localized Sales Tax Lines", "entity": "sale-order-localized-tax-lines"},
            {"label": "Customer Portal Access", "entity": "customer-portal-access"},
            {"label": "Portal Document References", "entity": "portal-document-references"},
            {"label": "Accounting Intelligence Settings", "entity": "accounting-intelligence-settings"},
        ],
    )
    localization_items = []
    for pack in registered_country_packs():
        for report in pack.reports:
            localization_items.append({
                "label": f"{pack.country_code} · {report.name}",
                "entity": report.menu_entity,
            })
    register_workspace_contribution(
        "accounting",
        group_label="Localization & Compliance",
        group_icon="L",
        items=[
            {"label": "Localized Document Tax Lines", "entity": "localized-tax-transaction-lines"},
            {"label": "Tax Authority Callbacks", "entity": "tax-authority-callback-events"},
            {"label": "Received Document Sync", "entity": "tax-authority-sync-cursors"},
            {"label": "Regulatory Rate Sync", "entity": "regulatory-rate-sync-runs"},
            *localization_items,
        ],
    )
    register_workspace_contribution(
        "accounting",
        group_label="School Billing",
        group_icon="S",
        items=[
            {"label": "School Accounting Settings", "entity": "school-accounting-settings"},
            {"label": "Student Billing Profiles", "entity": "student-accounting-profiles"},
            {"label": "Fee Types", "entity": "school-fee-types"},
            {"label": "Fee Structures", "entity": "school-fee-structures"},
            {"label": "Fee Items", "entity": "school-fee-items"},
            {"label": "Student Fee Schedules", "entity": "student-fee-schedules"},
            {"label": "Fee Schedule Lines", "entity": "student-fee-schedule-lines"},
            {"label": "Billing Runs", "entity": "school-billing-runs"},
            {"label": "Billing Run Lines", "entity": "school-billing-run-lines"},
            {"label": "School Invoice Provenance", "entity": "school-invoice-accounting"},
            {"label": "School Invoice Line Provenance", "entity": "school-invoice-line-accounting"},
            {"label": "Library Fines", "entity": "school-library-fines"},
        ],
    )
    register_workspace_contribution(
        "accounting",
        group_label="School Installments",
        group_icon="I",
        items=[
            {"label": "Installment Policies", "entity": "school-installment-policies"},
            {"label": "Installment Requests", "entity": "school-installment-requests"},
            {"label": "Installment Plans", "entity": "school-installment-plans"},
            {"label": "Installment Lines", "entity": "school-installment-lines"},
            {"label": "Reschedule Audit", "entity": "school-installment-reschedule-logs"},
        ],
    )
