"""Idempotent accounting role and permission provisioning."""

from django.contrib.auth.models import Group, Permission

from .access import (
    ACCOUNTING_AP_USER,
    ACCOUNTING_AR_USER,
    ACCOUNTING_GL_USER,
    ACCOUNTING_MANAGER,
    ACCOUNTING_REPORT_VIEWER,
    ACCOUNTING_SUPER_USER,
    ACCOUNTING_TREASURY_USER,
    ACCOUNTING_BUDGET_ADMIN,
    ACCOUNTING_BUDGET_MANAGER,
    ACCOUNTING_BUDGET_REPORT_VIEWER,
    ACCOUNTING_BUDGET_USER,
    ACCOUNTING_PETTY_CASH_MANAGER,
    ACCOUNTING_PETTY_CASH_OFFICER,
    ACCOUNTING_PETTY_CASH_VIEWER,
)


FINANCE_MODELS = {
    "account", "accounttag", "accountingcostcenter", "accountjournal", "journalentry", "journalline", "journallinecostcenterallocation",
    "fiscalyear", "fiscalperiod", "fiscalposition", "accounttax", "withholdingtax", "paymentterm",
    "companybankaccount", "bankstatement", "bankstatementline", "reconciliationrule", "accountpayment",
    "paymentallocation", "accountdeposit", "accountcheque", "internalbanktransfer", "recurringjournal",
    "prepayment", "currencyrate", "currencyrevaluation", "accountingsettings", "accountingauditevent",
    "accounttypedefinition", "journalentrytemplate", "journalentrytemplateline", "accountpaymentmethod",
    "otherincomeexpense", "otherincomeexpenseline", "paymentvoucher", "paymentvoucherline",
    "multipaymentbatch", "multipaymentbatchline", "withholdingtaxtransaction",
    "withholdingtaxtransactionline", "withholdingtaxhistory", "reconciliationhistory",
    "bankimportconfiguration", "accountingimportpreview", "currencyrevaluationline", "closingentry",
    "openingbalanceline", "companyaccountmapping", "companyaccountmappingline", "financialratiodefinition",
    "costcenterrule", "productaccountmapping", "intercompanypartneraccount", "soareminder",
    "accountreportdefinition",
    "commercialaccountingsettings", "partneraccountingdefault",
    "accounttaxdistribution", "fiscalpositionaccountmapping", "fiscalpositiontaxmapping",
    "reconciliationrulecondition", "reconciliationrulecounterpart", "reconciliationruletestrun", "depositapplication",
    "prepaymentapplication", "recurringjournalexecution", "currencyratelog", "assetaccountingsettings", "assetcategoryaccountingrule",
    "assetcapitalization", "assetcapitalizationline", "assetcapitalizationallocation", "assetcapitalizationasset",
    "assetdepreciationline", "assetrevaluation", "assetdisposal", "budgetaccountgroup", "budgetaccount",
    "budgettransaction", "budgetmovementaudit", "budgetchangereasoncategory", "budgetchangerequest",
    "budgetchangerequestline", "budgettransfer", "budgettransferline", "budgetcarryover",
    "budgetcarryoverline", "budgetcostcenterdistribution", "pettycashcategory", "pettycashfund", "pettycashvoucher",
    "pettycashvoucherline", "pettycashreplenishment", "pettycashreconciliation",
    "pettycashreconciliationline", "pettycashreplenishmentjournallog",
    "accountingintegrationevent", "accountingsourceadapter", "sourceaccountingposting",
    "externalaccountingconnector", "externalinvoicesync",
    "rentalaccountingsettings", "rentalorderaccounting", "rentaldeposithold",
    "rentalinvoiceaccounting", "rentalinvoicelineaccounting",
    "serviceworkorderaccounting",
    "marketplaceaccountingsettings", "marketplaceaccountingorder",
    "salespromotionaccounting", "saleorderinvoiceaccounting", "salesinvoiceaccounting",
    "salesinvoicelineprovenance", "salesinvoicepromotionsplit",
    "schoolaccountingsettings", "studentaccountingprofile", "schoolfeetype", "schoolfeestructure",
    "schoolfeeitem", "studentfeeschedule", "studentfeescheduleline", "schoolinvoiceaccounting",
    "schoolinvoicelineaccounting", "schoolbillingrun", "schoolbillingrunline", "schoolinstallmentpolicy",
    "schoolinstallmentrequest", "schoolinstallmentplan", "schoolinstallmentline",
    "schoolinstallmentreschedulelog", "schoollibraryfineaccounting",
    "customerportalaccess", "portaldocumentreference",
    "countrytaxprofile", "compliancereport", "externaltaxsubmission",
    "bankstatementparsejob", "companyaccountingprovisioningrun",
    "accounttaxcreditnotedistribution", "accountdepositline", "accountdeposithistory",
    "accountdepositconversion", "accountdepositconversionline", "accountpaymentline",
    "paymentunreconcilelog", "accountchequeline", "companyaccountmappingtaxline",
    "generalcentralizednotification", "soareminderaudit", "accountingvisionreport",
    "accountingintelligencesettings",
    "bankcashvaluation", "bankcashvaluationline", "sourcedocumentcancellation",
    "productaccountingrule", "arreconciliationsession", "arreconciliationline",
    "posaccountingsettings", "pospaymentaccountmapping", "payrollaccountingsettings", "payrollcomponentaccountmapping", "inventoryaccountingsettings",
    "inventoryusagetype", "inventoryaccountingdocument", "inventoryaccountingdocumentline",
    "expenseadvanceaccountingsettings", "expenseadvancesettlement",
    "localizationtaxcode", "statutoryreporttemplate", "statutoryreportline", "taxauthoritydocument",
    "taxidentifierlookup", "regulatoryexchangerate", "taxmiddlewareconnector", "complianceauditevent",
}
TREASURY_MODELS = {
    "companybankaccount", "bankstatement", "bankstatementline", "reconciliationrule", "accountpayment",
    "paymentallocation", "accountdeposit", "accountcheque", "internalbanktransfer",
    "accountpaymentmethod", "paymentvoucher", "paymentvoucherline", "multipaymentbatch",
    "multipaymentbatchline", "reconciliationhistory", "bankimportconfiguration",
    "depositapplication", "reconciliationruletestrun", "pettycashcategory", "pettycashfund", "pettycashvoucher",
    "pettycashvoucherline", "pettycashreplenishment", "pettycashreconciliation",
    "pettycashreconciliationline", "pettycashreplenishmentjournallog", "bankstatementparsejob",
    "accountdepositline", "accountdeposithistory", "accountdepositconversion",
    "accountdepositconversionline", "accountpaymentline", "paymentunreconcilelog", "accountchequeline",
    "bankcashvaluation", "bankcashvaluationline", "arreconciliationsession", "arreconciliationline",
}
GL_MODELS = {
    "account", "accounttag", "accountingcostcenter", "accountjournal", "journalentry", "journalline", "journallinecostcenterallocation",
    "fiscalyear", "fiscalperiod", "fiscalposition", "currencyrate", "currencyrevaluation",
    "currencyrevaluationline", "accounttypedefinition", "journalentrytemplate", "journalentrytemplateline",
    "otherincomeexpense", "otherincomeexpenseline", "closingentry", "openingbalanceline",
    "companyaccountmapping", "companyaccountmappingline", "financialratiodefinition", "costcenterrule",
    "productaccountmapping", "intercompanypartneraccount", "accountreportdefinition",
    "commercialaccountingsettings", "partneraccountingdefault",
    "accounttaxdistribution", "fiscalpositionaccountmapping", "fiscalpositiontaxmapping",
    "reconciliationrulecondition", "reconciliationrulecounterpart", "recurringjournalexecution",
    "currencyratelog", "assetaccountingsettings", "assetcategoryaccountingrule", "assetcapitalization",
    "assetcapitalizationline", "assetcapitalizationallocation", "assetcapitalizationasset", "assetdepreciationline", "assetrevaluation",
    "assetdisposal", "budgetaccountgroup", "budgetaccount", "budgettransaction", "budgetmovementaudit",
    "budgetchangereasoncategory", "budgetchangerequest", "budgetchangerequestline", "budgettransfer",
    "budgettransferline", "budgetcarryover", "budgetcarryoverline", "budgetcostcenterdistribution",
    "accountingintegrationevent", "accountingsourceadapter", "sourceaccountingposting",
    "externalaccountingconnector", "externalinvoicesync",
    "rentalaccountingsettings", "rentalorderaccounting", "rentaldeposithold",
    "rentalinvoiceaccounting", "rentalinvoicelineaccounting",
    "serviceworkorderaccounting",
    "marketplaceaccountingsettings", "marketplaceaccountingorder",
    "salespromotionaccounting", "saleorderinvoiceaccounting", "salesinvoiceaccounting",
    "salesinvoicelineprovenance", "salesinvoicepromotionsplit",
    "schoolaccountingsettings", "studentaccountingprofile", "schoolfeetype", "schoolfeestructure",
    "schoolfeeitem", "studentfeeschedule", "studentfeescheduleline", "schoolinvoiceaccounting",
    "schoolinvoicelineaccounting", "schoolbillingrun", "schoolbillingrunline", "schoolinstallmentpolicy",
    "schoolinstallmentrequest", "schoolinstallmentplan", "schoolinstallmentline",
    "schoolinstallmentreschedulelog", "schoollibraryfineaccounting",
    "customerportalaccess", "portaldocumentreference",
    "companyaccountingprovisioningrun",
    "accountingintelligencesettings", "accountingvisionreport", "sourcedocumentcancellation", "companyaccountmappingtaxline",
    "productaccountingrule",
    "posaccountingsettings", "pospaymentaccountmapping", "payrollaccountingsettings", "payrollcomponentaccountmapping", "inventoryaccountingsettings",
    "inventoryusagetype", "inventoryaccountingdocument", "inventoryaccountingdocumentline",
    "expenseadvanceaccountingsettings", "expenseadvancesettlement",
    "localizationtaxcode", "statutoryreporttemplate", "regulatoryexchangerate", "taxmiddlewareconnector",
}
AR_AP_MODELS = {
    "accountpayment", "paymentallocation", "accountdeposit", "prepayment", "paymentterm",
    "multipaymentbatch", "multipaymentbatchline", "withholdingtaxtransaction",
    "withholdingtaxtransactionline", "withholdingtaxhistory", "soareminder",
    "depositapplication", "prepaymentapplication", "countrytaxprofile", "compliancereport",
    "externaltaxsubmission", "generalcentralizednotification", "soareminderaudit", "statutoryreportline",
    "taxauthoritydocument", "taxidentifierlookup", "complianceauditevent",
}
BUDGET_MODELS = {
    "budget", "budgetaccountgroup", "budgetaccount", "budgettransaction", "budgetmovementaudit",
    "budgetchangereasoncategory", "budgetchangerequest", "budgetchangerequestline", "budgettransfer",
    "budgettransferline", "budgetcarryover", "budgetcarryoverline", "budgetcostcenterdistribution",
}
PETTY_CASH_MODELS = {
    "pettycashcategory", "pettycashfund", "pettycashvoucher", "pettycashvoucherline",
    "pettycashreplenishment", "pettycashreconciliation", "pettycashreconciliationline",
    "pettycashreplenishmentjournallog",
}


def _permissions(model_names, actions):
    codenames = {f"{action}_{model}" for model in model_names for action in actions}
    return Permission.objects.filter(
        content_type__app_label="gsshop_finance",
        content_type__model__in=model_names,
        codename__in=codenames,
    )


def ensure_accounting_roles(**_kwargs):
    all_actions = {"view", "add", "change", "delete"}
    operational = {"view", "add", "change"}
    all_permissions = _permissions(FINANCE_MODELS, all_actions)
    role_permissions = {
        ACCOUNTING_SUPER_USER: all_permissions,
        ACCOUNTING_MANAGER: all_permissions,
        ACCOUNTING_GL_USER: _permissions(GL_MODELS, operational),
        ACCOUNTING_AR_USER: _permissions(AR_AP_MODELS, operational),
        ACCOUNTING_AP_USER: _permissions(AR_AP_MODELS, operational),
        ACCOUNTING_TREASURY_USER: _permissions(TREASURY_MODELS, operational),
        ACCOUNTING_REPORT_VIEWER: _permissions(FINANCE_MODELS, {"view"}),
        ACCOUNTING_BUDGET_USER: _permissions(BUDGET_MODELS - {"budgetaccountgroup", "budgetchangereasoncategory", "budgetcostcenterdistribution"}, operational),
        ACCOUNTING_BUDGET_MANAGER: _permissions(BUDGET_MODELS, operational),
        ACCOUNTING_BUDGET_ADMIN: _permissions(BUDGET_MODELS, all_actions),
        ACCOUNTING_BUDGET_REPORT_VIEWER: _permissions(BUDGET_MODELS, {"view"}),
        ACCOUNTING_PETTY_CASH_VIEWER: _permissions(PETTY_CASH_MODELS, {"view"}),
        ACCOUNTING_PETTY_CASH_OFFICER: _permissions(PETTY_CASH_MODELS - {"pettycashcategory"}, operational),
        ACCOUNTING_PETTY_CASH_MANAGER: _permissions(PETTY_CASH_MODELS, operational),
    }
    for role_name, permissions in role_permissions.items():
        group, _created = Group.objects.get_or_create(name=role_name)
        group.permissions.add(*permissions)
