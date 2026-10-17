"""Source-audited HMX accounting surfaces that supplement the main registry."""

from .models import (
    AccountChequeLine,
    AccountDepositConversion,
    AccountDepositConversionLine,
    AccountDepositHistory,
    AccountDepositLine,
    AccountPaymentLine,
    AccountTaxCreditNoteDistribution,
    AccountingVisionReport,
    AccountingIntelligenceSettings,
    ARReconciliationLine,
    ARReconciliationAllocation,
    ARReconciliationSession,
    BankCashValuation,
    BankCashValuationLine,
    CompanyAccountMappingTaxLine,
    GeneralCentralizedNotification,
    PaymentUnreconcileLog,
    ProductAccountingRule,
    SOAReminderAudit,
    SourceDocumentCancellation,
)


def _workflow(label, action, from_statuses, to_status, *, primary=True):
    return {
        action: {
            "label": label,
            "from": from_statuses,
            "to": to_status,
            "primary": primary,
            "service_controls_state": True,
        }
    }


CORE_PARITY_ENTITY_CONFIG = {
    "accounting-intelligence-settings": {
        "model": AccountingIntelligenceSettings, "title": "Accounting Intelligence Settings", "singular": "Accounting Intelligence Settings",
        "columns": [("company", "Company"), ("risk_thresholds", "Risk Thresholds"), ("governance", "Governance"), ("routing", "Routing")],
        "search": ["company__code", "company__name"],
        "form_fields": ["company", "risk_thresholds", "health_scoring", "forecast_settings", "governance", "routing", "source_coverage"],
        "select_related": ["company"],
    },
    "account-tax-credit-note-distributions": {
        "model": AccountTaxCreditNoteDistribution, "title": "Credit Note Tax Distributions", "singular": "Credit Note Tax Distribution",
        "columns": [("tax", "Tax"), ("account", "Account"), ("percentage", "Percentage"), ("sequence", "Sequence")],
        "search": ["tax__code", "tax__name", "account__code"],
        "form_fields": ["tax", "account", "percentage", "sequence"], "select_related": ["tax", "tax__company", "account"],
    },
    "account-deposit-lines": {
        "model": AccountDepositLine, "title": "Deposit Lines", "singular": "Deposit Line",
        "columns": [("deposit", "Deposit"), ("account", "Account"), ("description", "Description"), ("amount", "Amount")],
        "search": ["deposit__number", "account__code", "description"],
        "form_fields": ["deposit", "account", "description", "amount"], "select_related": ["deposit", "deposit__company", "account"],
    },
    "account-deposit-history": {
        "model": AccountDepositHistory, "title": "Deposit History", "singular": "Deposit History",
        "columns": [("created_at", "Date"), ("deposit", "Deposit"), ("action", "Action"), ("amount", "Amount"), ("previous_status", "Previous"), ("new_status", "New"), ("actor", "Actor")],
        "search": ["deposit__number", "action", "actor__username"], "form_fields": [],
        "select_related": ["deposit", "deposit__company", "actor"], "creatable": False, "editable": False, "deletable": False,
    },
    "account-deposit-conversions": {
        "model": AccountDepositConversion, "title": "Deposit Conversions", "singular": "Deposit Conversion",
        "columns": [("number", "Number"), ("conversion_date", "Date"), ("deposit", "Deposit"), ("total_amount", "Total"), ("status", "Status")],
        "search": ["number", "deposit__number", "deposit__partner__name"],
        "form_fields": ["deposit", "number", "conversion_date", "note"],
        "select_related": ["deposit", "deposit__company", "deposit__partner", "journal_entry"], "admin_detail": True,
    },
    "account-deposit-conversion-lines": {
        "model": AccountDepositConversionLine, "title": "Deposit Conversion Lines", "singular": "Deposit Conversion Line",
        "columns": [("conversion", "Conversion"), ("invoice", "Invoice"), ("amount", "Amount")],
        "search": ["conversion__number", "invoice__number"], "form_fields": ["conversion", "invoice", "amount"],
        "select_related": ["conversion", "conversion__deposit__company", "invoice"],
    },
    "account-payment-lines": {
        "model": AccountPaymentLine, "title": "Payment Lines", "singular": "Payment Line",
        "columns": [("payment", "Payment"), ("account", "Account"), ("invoice", "Invoice"), ("description", "Description"), ("amount", "Amount")],
        "search": ["payment__number", "account__code", "invoice__number", "description"],
        "form_fields": ["payment", "account", "invoice", "description", "amount"],
        "select_related": ["payment", "payment__company", "account", "invoice"],
    },
    "payment-unreconcile-logs": {
        "model": PaymentUnreconcileLog, "title": "Payment Unreconcile Log", "singular": "Payment Unreconcile Log",
        "columns": [("created_at", "Date"), ("payment", "Payment"), ("allocation", "Allocation"), ("amount", "Amount"), ("actor", "Actor"), ("reason", "Reason")],
        "search": ["payment__number", "reason", "actor__username"], "form_fields": [],
        "select_related": ["payment", "payment__company", "allocation", "actor"], "creatable": False, "editable": False, "deletable": False,
    },
    "account-cheque-lines": {
        "model": AccountChequeLine, "title": "Cheque Payment Lines", "singular": "Cheque Payment Line",
        "columns": [("cheque", "Cheque"), ("account", "Account"), ("description", "Description"), ("amount", "Amount")],
        "search": ["cheque__cheque_number", "account__code", "description"],
        "form_fields": ["cheque", "account", "description", "amount"], "select_related": ["cheque", "cheque__company", "account"],
    },
    "company-account-mapping-tax-lines": {
        "model": CompanyAccountMappingTaxLine, "title": "Consolidation Tax Mappings", "singular": "Consolidation Tax Mapping",
        "columns": [("mapping", "Mapping"), ("subsidiary_tax", "Subsidiary Tax"), ("parent_tax", "Parent Tax")],
        "search": ["mapping__name", "subsidiary_tax__code", "parent_tax__code"],
        "form_fields": ["mapping", "subsidiary_tax", "parent_tax"],
        "select_related": ["mapping", "mapping__parent_company", "subsidiary_tax", "parent_tax"],
    },
    "general-centralized-notifications": {
        "model": GeneralCentralizedNotification, "title": "Centralized Accounting Notifications", "singular": "Accounting Notification",
        "columns": [("scheduled_at", "Scheduled"), ("notification_type", "Type"), ("recipient", "Recipient"), ("subject", "Subject"), ("channel", "Channel"), ("status", "Status")],
        "search": ["recipient", "subject", "message", "partner__name"],
        "form_fields": ["company", "partner", "notification_type", "channel", "recipient", "subject", "message", "scheduled_at", "payload"],
        "select_related": ["company", "partner"],
    },
    "soa-reminder-audits": {
        "model": SOAReminderAudit, "title": "Statement Reminder Audit", "singular": "Statement Reminder Audit",
        "columns": [("created_at", "Date"), ("reminder", "Reminder"), ("action", "Action"), ("previous_status", "Previous"), ("new_status", "New"), ("actor", "Actor")],
        "search": ["reminder__partner__name", "action", "actor__username"], "form_fields": [],
        "select_related": ["reminder", "reminder__company", "actor"], "creatable": False, "editable": False, "deletable": False,
    },
    "accounting-vision-reports": {
        "model": AccountingVisionReport, "title": "Accounting Vision & Intelligence", "singular": "Accounting Vision Report",
        "columns": [("name", "Report"), ("period_start", "From"), ("period_end", "To"), ("health_score", "Health Score"), ("risk_level", "Risk"), ("status", "Status")],
        "search": ["name", "risk_level"],
        "form_fields": ["company", "name", "period_start", "period_end", "generated_by"],
        "select_related": ["company", "generated_by"], "workflow": _workflow("Generate", "generate", ["draft"], "generated"),
    },
    "bank-cash-valuations": {
        "model": BankCashValuation, "title": "Bank / Cash Multi-Currency Valuation", "singular": "Bank / Cash Valuation",
        "columns": [("number", "Number"), ("valuation_date", "Date"), ("currency", "Currency"), ("rate", "Rate"), ("total_gain_loss", "Gain / Loss"), ("status", "Status")],
        "search": ["number", "currency"], "form_fields": ["company", "number", "valuation_date", "currency", "rate"],
        "select_related": ["company", "journal_entry"], "admin_detail": True,
    },
    "bank-cash-valuation-lines": {
        "model": BankCashValuationLine, "title": "Bank / Cash Valuation Lines", "singular": "Bank / Cash Valuation Line",
        "columns": [("valuation", "Valuation"), ("bank_account", "Bank Account"), ("foreign_balance", "Foreign Balance"), ("book_value", "Book Value"), ("revalued_value", "Revalued"), ("gain_loss", "Gain / Loss")],
        "search": ["valuation__number", "bank_account__bank_name", "bank_account__account_number"],
        "form_fields": ["valuation", "bank_account", "foreign_balance", "book_value", "revalued_value", "gain_loss"],
        "select_related": ["valuation", "valuation__company", "bank_account"],
    },
    "source-document-cancellations": {
        "model": SourceDocumentCancellation, "title": "Source Document Cancellation", "singular": "Source Document Cancellation",
        "columns": [("created_at", "Requested"), ("source_model", "Source Model"), ("source_reference", "Reference"), ("requested_by", "Requester"), ("status", "Status")],
        "search": ["source_model", "source_reference", "reason", "requested_by__username"],
        "form_fields": ["company", "source_model", "source_id", "source_reference", "reason", "requested_by"],
        "select_related": ["company", "requested_by", "approved_by", "reversal_entry"],
        "workflow": _workflow("Approve & Cancel Source", "approve", ["requested"], "completed"),
    },
    "product-accounting-rules": {
        "model": ProductAccountingRule, "title": "Product / Category / Variant Accounting Rules", "singular": "Product Accounting Rule",
        "columns": [("scope", "Scope"), ("reference", "Reference"), ("expense_account", "Expense"), ("revenue_account", "Revenue"), ("prepayment_account", "Prepayment"), ("active", "Active")],
        "search": ["reference", "expense_account__code", "revenue_account__code", "prepayment_account__code"],
        "form_fields": ["company", "scope", "reference", "expense_account", "revenue_account", "prepayment_account", "active"],
        "select_related": ["company", "expense_account", "revenue_account", "prepayment_account"],
    },
    "ar-reconciliation-sessions": {
        "model": ARReconciliationSession, "title": "AR Reconciliation", "singular": "AR Reconciliation Session",
        "columns": [("reference", "Reference"), ("reconciliation_date", "Date"), ("partner", "Partner"), ("total_debit", "Debit"), ("total_credit", "Credit"), ("status", "Status")],
        "search": ["reference", "partner__name", "partner__code"],
        "form_fields": ["company", "reference", "partner", "reconciliation_date", "actor"],
        "select_related": ["company", "partner", "actor"], "admin_detail": True,
    },
    "ar-reconciliation-lines": {
        "model": ARReconciliationLine, "title": "AR Reconciliation Lines", "singular": "AR Reconciliation Line",
        "columns": [("session", "Session"), ("journal_line", "Journal Item"), ("amount", "Amount")],
        "search": ["session__reference", "journal_line__entry__number", "journal_line__description"],
        "form_fields": ["session", "journal_line", "amount"],
        "select_related": ["session", "session__company", "journal_line", "journal_line__entry"],
    },
    "ar-reconciliation-allocations": {
        "model": ARReconciliationAllocation, "title": "AR Reconciliation Allocations", "singular": "AR Reconciliation Allocation",
        "columns": [("reconciliation_date", "Date"), ("session", "Session"), ("debit_line", "Debit Line"), ("credit_line", "Credit Line"), ("amount", "Amount"), ("status", "Status")],
        "search": ["session__reference", "debit_line__entry__number", "credit_line__entry__number"],
        "form_fields": [],
        "select_related": ["session", "debit_line", "debit_line__entry", "credit_line", "credit_line__entry", "actor", "reversed_by"],
        "creatable": False, "editable": False, "deletable": False,
    },
}


CORE_PARITY_DOCUMENT_CONFIG = {
    "account-deposit-conversions": {
        "line_model": AccountDepositConversionLine, "line_relation": "lines",
        "header_fields": ["deposit", "number", "conversion_date", "note"],
        "line_fields": ["invoice", "amount"], "line_title": "Invoice Applications",
        "total_fields": [("Total", "total_amount")], "editable_statuses": ["draft"],
        "actions": {
            **_workflow("Post Conversion", "post", ["draft"], "posted"),
            **_workflow("Reverse", "reverse", ["posted"], "reversed", primary=False),
            "cancel": {"label": "Cancel", "from": ["draft"], "to": "cancelled"},
        },
    },
    "bank-cash-valuations": {
        "line_model": BankCashValuationLine, "line_relation": "lines",
        "header_fields": ["company", "number", "valuation_date", "currency", "rate"],
        "line_fields": ["bank_account", "foreign_balance", "book_value", "revalued_value", "gain_loss"],
        "line_title": "Valuation Lines", "total_fields": [("Gain / Loss", "total_gain_loss")],
        "editable_statuses": ["draft", "generated"],
        "actions": {
            **_workflow("Generate", "generate", ["draft"], "generated"),
            **_workflow("Post", "post", ["generated"], "posted"),
            **_workflow("Reverse", "reverse", ["posted"], "reversed", primary=False),
        },
    },
    "ar-reconciliation-sessions": {
        "line_model": ARReconciliationLine, "line_relation": "lines",
        "header_fields": ["company", "reference", "partner", "reconciliation_date", "actor"],
        "line_fields": ["journal_line", "amount"], "line_title": "Journal Items",
        "total_fields": [("Debit", "total_debit"), ("Credit", "total_credit")], "editable_statuses": ["draft"],
        "actions": {
            **_workflow("Suggest Open Items", "suggest", ["draft"], "draft", primary=False),
            **_workflow("Reconcile", "reconcile", ["draft"], "reconciled"),
            **_workflow("Reverse", "reverse", ["reconciled"], "reversed", primary=False),
        },
    },
}
