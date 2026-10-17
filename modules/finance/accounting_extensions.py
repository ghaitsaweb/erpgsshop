"""Built-in accounting feature blueprints and company provisioners.

Each feature owns its required accounts, journals, and settings.  The central
company provisioning service only materializes registered blueprints.
"""

from .models import (
    Account,
    AccountJournal,
    AccountingIntelligenceSettings,
    CommercialAccountingSettings,
    ExpenseAdvanceAccountingSettings,
    InventoryAccountingSettings,
    PayrollAccountingSettings,
)
from .provisioning import (
    AccountDefinition,
    JournalDefinition,
    register_accounting_blueprint,
    register_company_accounting_provisioner,
)


def provision_commercial_accounting(*, company, accounts, journals, actor=None):
    _settings, created = CommercialAccountingSettings.objects.get_or_create(
        company=company,
        defaults={
            "sales_journal": journals["SAL"],
            "purchase_journal": journals["PUR"],
            "sales_downpayment_account": accounts["2100"],
            "purchase_downpayment_account": accounts["1300"],
        },
    )
    return {"extension": "commercial_accounting", "settings_created": created}


def provision_inventory_accounting(*, company, accounts, journals, actor=None):
    _settings, created = InventoryAccountingSettings.objects.get_or_create(
        company=company,
        defaults={
            "journal": journals["GEN"],
            "inventory_account": accounts["1200"],
            "cogs_account": accounts["5000"],
            "variance_account": accounts["5200"],
            "usage_expense_account": accounts["5000"],
            "landed_cost_clearing_account": accounts["2100"],
        },
    )
    return {"extension": "inventory_accounting", "settings_created": created}


def provision_payroll_accounting(*, company, accounts, journals, actor=None):
    _settings, created = PayrollAccountingSettings.objects.get_or_create(
        company=company,
        defaults={
            "journal": journals["GEN"],
            "salary_expense_account": accounts["5300"],
            "payroll_payable_account": accounts["2300"],
            "bank_account": accounts["1000"],
        },
    )
    return {"extension": "payroll_accounting", "settings_created": created}


def provision_expense_advance_accounting(*, company, accounts, journals, actor=None):
    _settings, created = ExpenseAdvanceAccountingSettings.objects.get_or_create(
        company=company,
        defaults={
            "journal": journals["GEN"],
            "advance_account": accounts["1300"],
            "expense_account": accounts["5000"],
            "payable_account": accounts["2100"],
            "bank_account": accounts["1000"],
        },
    )
    return {"extension": "expense_advance_accounting", "settings_created": created}


def provision_accounting_intelligence(*, company, accounts, journals, actor=None):
    _settings, created = AccountingIntelligenceSettings.objects.get_or_create(
        company=company,
        defaults={
            "risk_thresholds": {
                "ar_overdue_high_share": 0.20,
                "ap_overdue_high_share": 0.20,
                "net_margin_warning": 0.10,
                "budget_expense_ratio_warning": 0.85,
            },
            "health_scoring": {
                "critical_penalty": 30,
                "high_penalty": 15,
                "medium_penalty": 8,
                "low_penalty": 3,
            },
            "forecast_settings": {"default_method": "simple_average", "horizon_months": 12},
            "governance": {"ai_mode": "review_first", "require_approval_for_high_risk": True},
            "routing": {
                "ar_owner": "AR Lead", "ap_owner": "AP Lead",
                "accounting_owner": "Accounting Manager", "budget_owner": "Budget Owner",
            },
            "source_coverage": {
                "ar_invoice": True, "ap_bill": True, "journal_entry": True,
                "bank_statement": True, "budget": True, "tax_vat": False,
            },
        },
    )
    return {"extension": "accounting_intelligence", "settings_created": created}


def register_builtin_accounting_extensions():
    register_accounting_blueprint(
        accounts=(
            AccountDefinition("1300", "Prepayments and Advances", Account.AccountType.ASSET),
        ),
        journals=(
            JournalDefinition("SAL", "Sales Journal", AccountJournal.JournalType.SALES, "1100", "4000"),
            JournalDefinition("PUR", "Purchase Journal", AccountJournal.JournalType.PURCHASE, "5000", "2100"),
        ),
    )
    register_company_accounting_provisioner(provision_commercial_accounting)

    register_accounting_blueprint(accounts=(
        AccountDefinition("1200", "Inventory", Account.AccountType.ASSET),
    ))
    register_company_accounting_provisioner(provision_inventory_accounting)

    register_accounting_blueprint(accounts=(
        AccountDefinition("2300", "Payroll Payable", Account.AccountType.LIABILITY),
        AccountDefinition("5300", "Salary Expense", Account.AccountType.EXPENSE),
    ))
    register_company_accounting_provisioner(provision_payroll_accounting)

    register_accounting_blueprint(accounts=(
        AccountDefinition("1300", "Prepayments and Advances", Account.AccountType.ASSET),
    ))
    register_company_accounting_provisioner(provision_expense_advance_accounting)
    register_company_accounting_provisioner(provision_accounting_intelligence)
