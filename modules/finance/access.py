"""Role and company boundary enforcement for the Accounting workspace."""

from functools import lru_cache

from core.models import Company, Contact, Invoice


ACCOUNTING_SUPER_USER = "Accounting Super User"
ACCOUNTING_MANAGER = "Accounting Manager"
ACCOUNTING_GL_USER = "Accounting GL User"
ACCOUNTING_AR_USER = "Accounting AR User"
ACCOUNTING_AP_USER = "Accounting AP User"
ACCOUNTING_TREASURY_USER = "Accounting Treasury User"
ACCOUNTING_REPORT_VIEWER = "Accounting Report Viewer"
ACCOUNTING_BUDGET_USER = "Accounting Budget User"
ACCOUNTING_BUDGET_MANAGER = "Accounting Budget Manager"
ACCOUNTING_BUDGET_ADMIN = "Accounting Budget Administrator"
ACCOUNTING_BUDGET_REPORT_VIEWER = "Accounting Budget Report Viewer"
ACCOUNTING_PETTY_CASH_VIEWER = "Accounting Petty Cash Viewer"
ACCOUNTING_PETTY_CASH_OFFICER = "Accounting Petty Cash Officer"
ACCOUNTING_PETTY_CASH_MANAGER = "Accounting Petty Cash Manager"

ACCOUNTING_ENTITIES = {
    "accounts", "account-tags", "accounting-cost-centers", "account-journals", "journal-entries", "journal-items", "journal-line-cost-center-allocations",
    "fiscal-years", "fiscal-periods", "fiscal-positions", "account-taxes", "withholding-taxes",
    "payment-terms", "company-bank-accounts", "bank-statements", "bank-statement-lines",
    "reconciliation-rules", "account-payments", "payment-allocations", "account-deposits",
    "account-cheques", "internal-bank-transfers", "recurring-journals", "prepayments",
    "currency-rates", "currency-revaluations", "accounting-settings", "accounting-audit-events",
    "account-types", "journal-entry-templates", "journal-entry-template-lines", "account-payment-methods",
    "other-income-expenses", "other-income-expense-lines", "payment-vouchers", "payment-voucher-lines",
    "multi-payment-batches", "multi-payment-batch-lines", "withholding-tax-transactions",
    "withholding-tax-transaction-lines", "withholding-tax-history", "reconciliation-history",
    "bank-import-configurations", "accounting-import-previews", "currency-revaluation-lines",
    "closing-entries", "opening-balance-lines", "company-account-mappings", "company-account-mapping-lines",
    "financial-ratio-definitions", "cost-center-rules", "product-account-mappings",
    "intercompany-partner-accounts", "soa-reminders", "account-report-definitions",
    "account-tax-distributions", "fiscal-position-account-mappings", "fiscal-position-tax-mappings",
    "commercial-accounting-settings", "partner-accounting-defaults",
    "reconciliation-rule-conditions", "reconciliation-rule-counterparts", "reconciliation-rule-tests", "deposit-applications",
    "prepayment-applications", "recurring-journal-executions", "currency-rate-logs",
    "asset-accounting-settings", "asset-category-accounting-rules", "asset-capitalizations", "asset-capitalization-lines",
    "asset-capitalization-allocations", "asset-capitalization-assets", "asset-depreciation-lines", "asset-revaluations", "asset-disposals",
    "budget-account-groups", "budget-accounts", "budget-transactions", "budget-movement-audits",
    "budget-change-reason-categories", "budget-change-requests", "budget-change-request-lines",
    "budget-transfers", "budget-transfer-lines", "budget-carry-overs", "budget-carry-over-lines",
    "budget-cost-center-distributions",
    "petty-cash-categories", "petty-cash-funds", "petty-cash-vouchers", "petty-cash-voucher-lines",
    "petty-cash-replenishments", "petty-cash-reconciliations", "petty-cash-reconciliation-lines",
    "petty-cash-replenishment-journal-logs", "accounting-integration-events",
    "accounting-source-adapters", "source-accounting-postings", "external-accounting-connectors", "external-invoice-syncs",
    "country-tax-profiles", "compliance-reports", "external-tax-submissions",
    "localized-tax-transaction-lines", "tax-authority-callback-events", "tax-authority-sync-cursors",
    "regulatory-rate-sync-runs", "pos-order-accounting", "sale-order-localizations", "sale-order-localized-tax-lines",
    "bank-statement-parse-jobs", "company-accounting-provisioning-runs", "budgets",
    "account-tax-credit-note-distributions", "account-deposit-lines", "account-deposit-history",
    "account-deposit-conversions", "account-deposit-conversion-lines", "account-payment-lines",
    "payment-unreconcile-logs", "account-cheque-lines", "company-account-mapping-tax-lines",
    "general-centralized-notifications", "soa-reminder-audits", "accounting-intelligence-settings", "accounting-vision-reports",
    "bank-cash-valuations", "bank-cash-valuation-lines", "source-document-cancellations",
    "product-accounting-rules", "ar-reconciliation-sessions", "ar-reconciliation-lines", "ar-reconciliation-allocations",
    "pos-accounting-settings", "pos-payment-account-mappings", "payroll-accounting-settings", "payroll-component-account-mappings", "inventory-accounting-settings",
    "inventory-usage-types", "inventory-accounting-documents", "inventory-accounting-document-lines",
    "expense-advance-accounting-settings", "expense-advance-settlements",
    "rental-accounting-settings", "rental-order-accounting", "rental-deposit-holds",
    "rental-invoice-accounting", "rental-invoice-line-accounting",
    "service-work-order-accounting",
    "marketplace-accounting-settings", "marketplace-accounting-orders",
    "sales-promotion-accounting", "sale-order-invoice-accounting", "sales-invoice-accounting",
    "sales-invoice-line-provenance", "sales-invoice-promotion-splits",
    "school-accounting-settings", "student-accounting-profiles", "school-fee-types",
    "school-fee-structures", "school-fee-items", "student-fee-schedules", "student-fee-schedule-lines",
    "school-invoice-accounting", "school-invoice-line-accounting", "school-billing-runs",
    "school-billing-run-lines", "school-installment-policies", "school-installment-requests",
    "school-installment-plans", "school-installment-lines", "school-installment-reschedule-logs",
    "school-library-fines",
    "customer-portal-access", "portal-document-references",
    "localization-tax-codes", "statutory-report-templates", "statutory-report-lines",
    "tax-authority-documents", "tax-identifier-lookups", "regulatory-exchange-rates",
    "tax-middleware-connectors", "compliance-audit-events",
}

TREASURY_ENTITIES = {
    "company-bank-accounts", "bank-statements", "bank-statement-lines", "reconciliation-rules",
    "account-payments", "payment-allocations", "account-deposits", "account-cheques",
    "internal-bank-transfers", "account-payment-methods", "payment-vouchers", "payment-voucher-lines",
    "multi-payment-batches", "multi-payment-batch-lines", "reconciliation-history", "bank-import-configurations",
    "deposit-applications", "petty-cash-categories", "petty-cash-funds", "petty-cash-vouchers",
    "petty-cash-voucher-lines", "petty-cash-replenishments", "petty-cash-reconciliations",
    "petty-cash-reconciliation-lines", "petty-cash-replenishment-journal-logs", "bank-statement-parse-jobs",
    "account-deposit-lines", "account-deposit-history", "account-deposit-conversions",
    "account-deposit-conversion-lines", "account-payment-lines", "payment-unreconcile-logs",
    "account-cheque-lines", "bank-cash-valuations", "bank-cash-valuation-lines",
    "ar-reconciliation-sessions", "ar-reconciliation-lines", "ar-reconciliation-allocations", "reconciliation-rule-tests",
}
CONFIGURATION_ENTITIES = {
    "accounts", "account-tags", "accounting-cost-centers", "account-journals", "fiscal-years",
    "fiscal-periods", "fiscal-positions", "account-taxes", "withholding-taxes", "payment-terms",
    "currency-rates", "accounting-settings", "reconciliation-rules",
    "account-types", "journal-entry-templates", "journal-entry-template-lines", "account-payment-methods",
    "bank-import-configurations", "company-account-mappings", "company-account-mapping-lines",
    "financial-ratio-definitions", "cost-center-rules", "product-account-mappings",
    "intercompany-partner-accounts", "account-report-definitions",
    "account-tax-distributions", "fiscal-position-account-mappings", "fiscal-position-tax-mappings",
    "commercial-accounting-settings", "partner-accounting-defaults",
    "reconciliation-rule-conditions", "reconciliation-rule-counterparts", "currency-rate-logs",
    "asset-accounting-settings", "asset-category-accounting-rules", "budget-account-groups", "budget-accounts", "budget-change-reason-categories",
    "budget-cost-center-distributions", "petty-cash-categories",
    "account-tax-credit-note-distributions", "company-account-mapping-tax-lines", "product-accounting-rules",
    "pos-accounting-settings", "pos-payment-account-mappings", "payroll-accounting-settings", "payroll-component-account-mappings", "inventory-accounting-settings", "inventory-usage-types",
    "expense-advance-accounting-settings",
    "localization-tax-codes", "statutory-report-templates", "regulatory-exchange-rates",
    "tax-middleware-connectors", "tax-authority-sync-cursors", "regulatory-rate-sync-runs",
    "accounting-intelligence-settings",
    "country-tax-profiles", "company-accounting-provisioning-runs", "accounting-source-adapters", "external-accounting-connectors",
    "rental-accounting-settings", "marketplace-accounting-settings", "sales-promotion-accounting",
    "school-accounting-settings", "school-fee-types", "school-fee-structures", "school-fee-items",
    "school-installment-policies",
    "customer-portal-access",
}
BUDGET_ENTITIES = {
    "budgets", "budget-account-groups", "budget-accounts", "budget-transactions", "budget-movement-audits",
    "budget-change-reason-categories", "budget-change-requests", "budget-change-request-lines",
    "budget-transfers", "budget-transfer-lines", "budget-carry-overs", "budget-carry-over-lines",
    "budget-cost-center-distributions",
}
PETTY_CASH_ENTITIES = {
    "petty-cash-categories", "petty-cash-funds", "petty-cash-vouchers", "petty-cash-voucher-lines",
    "petty-cash-replenishments", "petty-cash-reconciliations", "petty-cash-reconciliation-lines",
    "petty-cash-replenishment-journal-logs",
}

MODEL_COMPANY_LOOKUPS = {
    "journalline": "entry__company_id",
    "journallinecostcenterallocation": "journal_line__entry__company_id",
    "fiscalperiod": "fiscal_year__company_id",
    "bankstatementline": "statement__company_id",
    "paymentallocation": "payment__company_id",
    "journalentrytemplateline": "template__company_id",
    "otherincomeexpenseline": "document__company_id",
    "paymentvoucherline": "voucher__company_id",
    "multipaymentbatchline": "batch__company_id",
    "withholdingtaxtransactionline": "transaction__company_id",
    "withholdingtaxhistory": "transaction__company_id",
    "currencyrevaluationline": "revaluation__company_id",
    "openingbalanceline": "closing_entry__company_id",
    "companyaccountmapping": "parent_company_id",
    "companyaccountmappingline": "mapping__parent_company_id",
    "accounttaxdistribution": "tax__company_id",
    "fiscalpositionaccountmapping": "fiscal_position__company_id",
    "fiscalpositiontaxmapping": "fiscal_position__company_id",
    "reconciliationrulecondition": "rule__company_id",
    "reconciliationrulecounterpart": "rule__company_id",
    "reconciliationruletestrun": "company_id",
    "depositapplication": "deposit__company_id",
    "prepaymentapplication": "prepayment__company_id",
    "recurringjournalexecution": "schedule__company_id",
    "assetdepreciationline": "asset__company_id",
    "assetcapitalizationline": "capitalization__company_id",
    "assetcapitalizationallocation": "capitalization__company_id",
    "assetcapitalizationasset": "capitalization__company_id",
    "assetrevaluation": "asset__company_id",
    "assetdisposal": "asset__company_id",
    "budgetmovementaudit": "transaction_record__company_id",
    "budgetchangerequestline": "request__company_id",
    "budgettransferline": "transfer__company_id",
    "budgetcarryoverline": "carry_over__company_id",
    "budgetcostcenterdistribution": "budget_account__company_id",
    "pettycashvoucher": "fund__company_id",
    "pettycashvoucherline": "voucher__fund__company_id",
    "pettycashreplenishment": "fund__company_id",
    "pettycashreconciliation": "fund__company_id",
    "pettycashreconciliationline": "reconciliation__fund__company_id",
    "pettycashreplenishmentjournallog": "replenishment__fund__company_id",
    "compliancereport": "profile__company_id",
    "externaltaxsubmission": "report__profile__company_id",
    "accounttaxcreditnotedistribution": "tax__company_id",
    "accountdepositline": "deposit__company_id",
    "accountdeposithistory": "deposit__company_id",
    "accountdepositconversion": "deposit__company_id",
    "accountdepositconversionline": "conversion__deposit__company_id",
    "accountpaymentline": "payment__company_id",
    "paymentunreconcilelog": "payment__company_id",
    "accountchequeline": "cheque__company_id",
    "companyaccountmappingtaxline": "mapping__parent_company_id",
    "soareminderaudit": "reminder__company_id",
    "bankcashvaluationline": "valuation__company_id",
    "arreconciliationline": "session__company_id",
    "arreconciliationallocation": "session__company_id",
    "posaccountingsettings": "profile__company_id",
    "pospaymentaccountmapping": "payment_method__company_id",
    "payrollcomponentaccountmapping": "salary_component__company_id",
    "inventoryaccountingdocumentline": "document__company_id",
    "localizationtaxcode": "profile__company_id",
    "statutoryreporttemplate": "profile__company_id",
    "statutoryreportline": "report__profile__company_id",
    "taxauthoritydocument": "profile__company_id",
    "taxidentifierlookup": "profile__company_id",
    "regulatoryexchangerate": "profile__company_id",
    "taxmiddlewareconnector": "profile__company_id",
    "complianceauditevent": "profile__company_id",
    "localizedtaxtransactionline": "profile__company_id",
    "taxauthoritycallbackevent": "profile__company_id",
    "taxauthoritysynccursor": "profile__company_id",
    "regulatoryratesyncrun": "profile__company_id",
    "posorderaccounting": "order__session__register__company_id",
    "saleorderlocalization": "order__company_id",
    "saleorderlocalizedtaxline": "localization__order__company_id",
    "expenseadvancesettlement": "expense__company_id",
    "sourceaccountingposting": "adapter__company_id",
    "externalinvoicesync": "connector__company_id",
    "rentalorderaccounting": "order__company_id",
    "rentaldeposithold": "rental_terms__order__company_id",
    "rentalinvoiceaccounting": "rental_terms__order__company_id",
    "rentalinvoicelineaccounting": "rental_invoice__rental_terms__order__company_id",
    "serviceworkorderaccounting": "work_order__company_id",
    "marketplaceaccountingorder": "accounting_settings__company_id",
    "saleorderinvoiceaccounting": "order__company_id",
    "salesinvoiceaccounting": "order_accounting__order__company_id",
    "salesinvoicelineprovenance": "sales_invoice__order_accounting__order__company_id",
    "salesinvoicepromotionsplit": "invoice_line__invoice__company_id",
    "schoolaccountingsettings": "school__company_id",
    "studentaccountingprofile": "student__school__company_id",
    "schoolfeestructure": "school__company_id",
    "schoolfeeitem": "structure__school__company_id",
    "studentfeeschedule": "student__school__company_id",
    "studentfeescheduleline": "schedule__student__school__company_id",
    "schoolinvoiceaccounting": "school__company_id",
    "schoolinvoicelineaccounting": "school_invoice__school__company_id",
    "schoolbillingrun": "school__company_id",
    "schoolbillingrunline": "billing_run__school__company_id",
    "schoolinstallmentpolicy": "school__company_id",
    "schoolinstallmentrequest": "student__school__company_id",
    "schoolinstallmentplan": "student__school__company_id",
    "schoolinstallmentline": "plan__student__school__company_id",
    "schoolinstallmentreschedulelog": "plan__student__school__company_id",
    "schoollibraryfineaccounting": "book_loan__book__school__company_id",
    "customerportalaccess": "customer__company_id",
}


def _group_names(user):
    if not user.is_authenticated:
        return set()
    cached = getattr(user, "_gsshop_accounting_group_names", None)
    if cached is None:
        cached = set(user.groups.values_list("name", flat=True))
        user._gsshop_accounting_group_names = cached
    return cached


def _company_ids(user):
    cached = getattr(user, "_gsshop_allowed_company_ids", None)
    return cached if cached is not None else user.employee_profiles.values("company_id")


@lru_cache(maxsize=None)
def _model_field_names(model):
    return {field.name for field in model._meta.get_fields()}


def can_accounting_entity_action(user, entity, action):
    if entity not in ACCOUNTING_ENTITIES or user.is_superuser:
        return True
    groups = _group_names(user)
    if groups & {ACCOUNTING_SUPER_USER, ACCOUNTING_MANAGER}:
        return True
    if ACCOUNTING_REPORT_VIEWER in groups:
        return action == "view"
    if entity in BUDGET_ENTITIES:
        if groups & {ACCOUNTING_BUDGET_ADMIN, ACCOUNTING_BUDGET_MANAGER}:
            return action in {"view", "add", "change"}
        if ACCOUNTING_BUDGET_USER in groups:
            return action in {"view", "add", "change"} and entity not in {
                "budget-account-groups", "budget-change-reason-categories", "budget-cost-center-distributions"
            }
        if ACCOUNTING_BUDGET_REPORT_VIEWER in groups:
            return action == "view"
    if entity in PETTY_CASH_ENTITIES:
        if ACCOUNTING_PETTY_CASH_MANAGER in groups:
            return action in {"view", "add", "change"}
        if ACCOUNTING_PETTY_CASH_OFFICER in groups:
            return action in {"view", "add", "change"} and entity not in {"petty-cash-categories"}
        if ACCOUNTING_PETTY_CASH_VIEWER in groups:
            return action == "view"
    if entity in TREASURY_ENTITIES and ACCOUNTING_TREASURY_USER in groups:
        return action in {"view", "add", "change"}
    if entity in CONFIGURATION_ENTITIES:
        return action == "view" and bool(
            groups & {ACCOUNTING_GL_USER, ACCOUNTING_AR_USER, ACCOUNTING_AP_USER, ACCOUNTING_TREASURY_USER}
        )
    if entity in {
        "journal-entries", "journal-items", "journal-line-cost-center-allocations", "recurring-journals", "currency-revaluations", "currency-revaluation-lines",
        "other-income-expenses", "other-income-expense-lines", "closing-entries", "opening-balance-lines",
        "asset-accounting-settings", "asset-category-accounting-rules", "asset-capitalizations", "asset-capitalization-lines",
        "asset-capitalization-allocations", "asset-capitalization-assets", "asset-depreciation-lines", "asset-revaluations", "asset-disposals",
        "accounting-integration-events", "accounting-source-adapters", "source-accounting-postings",
        "external-accounting-connectors", "external-invoice-syncs",
        "company-accounting-provisioning-runs",
        "rental-accounting-settings", "rental-order-accounting", "rental-deposit-holds",
        "rental-invoice-accounting", "rental-invoice-line-accounting",
        "service-work-order-accounting",
        "marketplace-accounting-settings", "marketplace-accounting-orders",
        "sales-promotion-accounting", "sale-order-invoice-accounting", "sales-invoice-accounting",
        "sales-invoice-line-provenance", "sales-invoice-promotion-splits",
        "school-accounting-settings", "student-accounting-profiles", "school-fee-types",
        "school-fee-structures", "school-fee-items", "student-fee-schedules", "student-fee-schedule-lines",
        "school-invoice-accounting", "school-invoice-line-accounting", "school-billing-runs",
        "school-billing-run-lines", "school-installment-policies", "school-installment-requests",
        "school-installment-plans", "school-installment-lines", "school-installment-reschedule-logs",
        "school-library-fines",
        "customer-portal-access", "portal-document-references",
        "accounting-intelligence-settings", "accounting-vision-reports", "source-document-cancellations",
        "inventory-accounting-documents", "inventory-accounting-document-lines", "pos-order-accounting",
        "sale-order-localizations", "sale-order-localized-tax-lines",
    }:
        return action in {"view", "add", "change"} and ACCOUNTING_GL_USER in groups
    if entity in {
        "account-payments", "payment-allocations", "account-deposits", "prepayments",
        "withholding-tax-transactions", "withholding-tax-transaction-lines", "withholding-tax-history",
        "soa-reminders",
        "deposit-applications", "prepayment-applications", "compliance-reports", "external-tax-submissions",
        "general-centralized-notifications", "soa-reminder-audits",
        "statutory-report-lines", "tax-authority-documents", "tax-identifier-lookups", "compliance-audit-events",
        "localized-tax-transaction-lines", "tax-authority-callback-events", "tax-authority-sync-cursors",
        "regulatory-rate-sync-runs",
    }:
        return action in {"view", "add", "change"} and bool(
            groups & {ACCOUNTING_AR_USER, ACCOUNTING_AP_USER}
        )
    return False


def can_accounting_workflow_action(user, entity, action):
    if entity not in ACCOUNTING_ENTITIES or user.is_superuser:
        return True
    if action not in {
        "post", "reverse", "cancel", "close", "lock", "reconcile", "clear", "validate", "generate",
        "activate", "archive", "approve", "reject", "submit", "parse", "run", "apply", "capitalize",
        "return", "pay", "queue", "send", "dispatch", "reopen",
        "issue", "bounce",
    }:
        return True
    groups = _group_names(user)
    if entity in BUDGET_ENTITIES and action in {"approve", "reject"}:
        return bool(groups & {ACCOUNTING_SUPER_USER, ACCOUNTING_MANAGER, ACCOUNTING_BUDGET_MANAGER, ACCOUNTING_BUDGET_ADMIN})
    if entity in BUDGET_ENTITIES and action in {"submit", "cancel"}:
        return bool(groups & {ACCOUNTING_SUPER_USER, ACCOUNTING_MANAGER, ACCOUNTING_BUDGET_USER, ACCOUNTING_BUDGET_MANAGER, ACCOUNTING_BUDGET_ADMIN})
    if entity in PETTY_CASH_ENTITIES and action in {"post", "reverse", "approve", "reconcile"}:
        return bool(groups & {ACCOUNTING_SUPER_USER, ACCOUNTING_MANAGER, ACCOUNTING_PETTY_CASH_MANAGER})
    if entity in PETTY_CASH_ENTITIES and action in {"submit", "cancel"}:
        return bool(groups & {ACCOUNTING_SUPER_USER, ACCOUNTING_MANAGER, ACCOUNTING_PETTY_CASH_OFFICER, ACCOUNTING_PETTY_CASH_MANAGER})
    return bool(groups & {ACCOUNTING_SUPER_USER, ACCOUNTING_MANAGER})


def _company_lookup(model):
    if "company" in _model_field_names(model):
        return "company_id"
    return MODEL_COMPANY_LOOKUPS.get(model._meta.model_name)


def scope_accounting_queryset(user, entity, queryset):
    if entity not in ACCOUNTING_ENTITIES or user.is_superuser:
        return queryset
    lookup = _company_lookup(queryset.model)
    if not lookup:
        return queryset.none()
    return queryset.filter(**{f"{lookup}__in": _company_ids(user)})


def scope_accounting_form(form, user, entity):
    if entity not in ACCOUNTING_ENTITIES or user.is_superuser:
        return form
    company_ids = _company_ids(user)
    for field in form.fields.values():
        queryset = getattr(field, "queryset", None)
        if queryset is None:
            continue
        if queryset.model is Company:
            field.queryset = Company.objects.filter(pk__in=company_ids)
        elif queryset.model is Contact:
            field.queryset = Contact.objects.filter(company_id__in=company_ids)
        elif queryset.model is Invoice:
            field.queryset = Invoice.objects.filter(company_id__in=company_ids)
        else:
            lookup = _company_lookup(queryset.model)
            if lookup:
                field.queryset = queryset.filter(**{f"{lookup}__in": company_ids})
    return form
