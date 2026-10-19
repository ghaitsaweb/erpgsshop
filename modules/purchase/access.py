"""Company-scoped access rules for the GS Shop procurement workspace."""

from functools import lru_cache

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse


PURCHASE_MANAGER = "Purchase Manager"
PURCHASE_USER = "Purchase User"
VENDOR_RELATIONS_OFFICER = "Vendor Relations Officer"
PURCHASE_BUDGET_MANAGER = "Purchase Budget Manager"

PURCHASE_ENTITIES = {
    "purchases",
    "purchase-requests",
    "purchase-request-lines",
    "purchase-quotations",
    "direct-purchases",
    "service-work-orders",
    "award-strategies",
    "purchase-tenders",
    "tender-bids",
    "blanket-orders",
    "vendor-categories",
    "vendor-profiles",
    "vendor-legal-documents",
    "vendor-prequalifications",
    "vendor-document-reviews",
    "vendor-pricelists",
    "vendor-pricelist-amendments",
    "vendor-performance-templates",
    "vendor-performance-evaluations",
    "purchase-teams",
    "purchase-team-members",
    "purchase-kpi-templates",
    "purchase-kpi-targets",
    "purchase-order-types",
    "purchase-order-templates",
    "purchase-milestone-templates",
    "purchase-settings",
    "purchase-order-controls",
    "purchase-order-milestones",
    "product-budget-groups",
    "product-budgets",
    "budget-product-transfers",
    "budget-product-carry-overs",
    "budget-product-change-requests",
    "goods-receipts",
    "goods-receipt-lines",
    "purchase-return-requests",
    "purchase-accounting-settings",
    "purchase-account-mappings",
    "purchase-peppol-submissions",
    "purchase-insights",
    "purchase-quotation-lines",
    "direct-purchase-lines",
    "service-work-order-lines",
    "purchase-tender-lines",
    "tender-invitations",
    "tender-documents",
    "tender-audit-logs",
    "blanket-order-lines",
    "purchase-product-categories",
    "purchase-product-variants",
    "vendor-communications",
    "vendor-transaction-history",
    "purchase-order-communications",
    "product-budget-movements",
    "purchase-accounting-documents",
    "purchase-accounting-lines",
    "purchase-stock-demands",
    "purchase-inventory-transfers",
    "purchase-inventory-transfer-lines",
    "purchase-peppol-events",
    "purchase-contract-terms",
    "purchase-order-revisions",
}

VENDOR_ENTITIES = {
    "vendor-categories",
    "vendor-profiles",
    "vendor-legal-documents",
    "vendor-prequalifications",
    "vendor-document-reviews",
    "vendor-pricelists",
    "vendor-pricelist-amendments",
    "vendor-performance-templates",
    "vendor-performance-evaluations",
    "vendor-communications",
    "vendor-transaction-history",
}

BUDGET_ENTITIES = {
    "product-budget-groups",
    "product-budgets",
    "budget-product-transfers",
    "budget-product-carry-overs",
    "budget-product-change-requests",
    "product-budget-movements",
}


def _group_names(user):
    if not user.is_authenticated:
        return set()
    cached = getattr(user, "_gsshop_purchase_group_names", None)
    if cached is None:
        cached = set(user.groups.values_list("name", flat=True))
        user._gsshop_purchase_group_names = cached
    return cached


def _company_ids(user):
    cached = getattr(user, "_gsshop_allowed_company_ids", None)
    return cached if cached is not None else user.employee_profiles.values("company_id")


def can_purchase_entity_action(user, entity, action):
    if entity not in PURCHASE_ENTITIES or user.is_superuser:
        return True
    groups = _group_names(user)
    if PURCHASE_MANAGER in groups:
        return True
    if entity in VENDOR_ENTITIES and VENDOR_RELATIONS_OFFICER in groups:
        return action in {"view", "add", "change"}
    if entity in BUDGET_ENTITIES and PURCHASE_BUDGET_MANAGER in groups:
        return True
    if PURCHASE_USER in groups:
        return action in {"view", "add", "change"}
    return False


PRIVILEGED_WORKFLOW_ACTIONS = {
    "approve",
    "confirm",
    "reject",
    "publish",
    "open-submission",
    "evaluate",
    "award",
    "shortlist",
    "select",
    "mark-ordered",
    "close",
}


def can_purchase_workflow_action(user, entity, action):
    """Keep requester transitions separate from purchasing approval authority."""
    if entity not in PURCHASE_ENTITIES or action not in PRIVILEGED_WORKFLOW_ACTIONS or user.is_superuser:
        return True
    groups = _group_names(user)
    if PURCHASE_MANAGER in groups:
        return True
    if entity in VENDOR_ENTITIES and VENDOR_RELATIONS_OFFICER in groups:
        return True
    if entity in BUDGET_ENTITIES and PURCHASE_BUDGET_MANAGER in groups:
        return True
    return False


@lru_cache(maxsize=None)
def _model_field_names(model):
    return {field.name for field in model._meta.get_fields()}


MODEL_COMPANY_LOOKUPS = {
    "purchaserequestline": "request__company_id",
    "tenderbid": "tender__company_id",
    "vendorlegaldocument": "vendor__company_id",
    "vendorprequalification": "vendor__company_id",
    "vendordocumentreview": "prequalification__vendor__company_id",
    "vendorpricelistamendment": "pricelist__company_id",
    "vendorperformanceevaluation": "vendor__company_id",
    "purchaseteammember": "team__company_id",
    "purchasekpitarget": "team__company_id",
    "purchasemilestonetemplate": "order_template__company_id",
    "purchaseordercontrol": "order__company_id",
    "purchaseordermilestone": "order__company_id",
    "budgetproducttransfer": "source_budget__company_id",
    "budgetproductcarryover": "source_budget__company_id",
    "budgetproductchangerequest": "budget__company_id",
    "goodsreceiptline": "receipt__company_id",
    "purchasepeppolsubmission": "order__company_id",
    "purchasequotationline": "quotation__company_id",
    "directpurchaseline": "direct_purchase__company_id",
    "serviceworkorderline": "work_order__company_id",
    "purchasetenderline": "tender__company_id",
    "tenderinvitation": "tender__company_id",
    "tenderdocument": "tender__company_id",
    "tenderauditlog": "tender__company_id",
    "blanketorderline": "blanket_order__company_id",
    "purchaseproductvariant": "product__company_id",
    "vendorcommunication": "vendor__company_id",
    "vendortransactionhistory": "vendor__company_id",
    "purchaseordercommunication": "order__company_id",
    "productbudgetmovement": "budget__company_id",
    "purchaseaccountingdocument": "order__company_id",
    "purchaseaccountingline": "document__order__company_id",
    "purchasestockdemand": "request_line__request__company_id",
    "purchaseinventorytransfer": "source_warehouse__company_id",
    "purchaseinventorytransferline": "transfer__source_warehouse__company_id",
    "purchasepeppolevent": "submission__order__company_id",
    "purchasecontractterm": "order_template__company_id",
    "purchaseorderrevision": "order__company_id",
}


def scope_purchase_queryset(user, entity, queryset):
    if entity not in PURCHASE_ENTITIES or user.is_superuser:
        return queryset

    company_ids = _company_ids(user)
    fields = _model_field_names(queryset.model)
    if "company" in fields:
        return queryset.filter(company_id__in=company_ids)
    lookup = MODEL_COMPANY_LOOKUPS.get(queryset.model._meta.model_name)
    if lookup:
        return queryset.filter(**{f"{lookup}__in": company_ids})
    return queryset.none()


def scope_purchase_form(form, user, entity):
    if entity not in PURCHASE_ENTITIES or user.is_superuser:
        return form

    company_ids = _company_ids(user)
    for field in form.fields.values():
        queryset = getattr(field, "queryset", None)
        if queryset is None:
            continue
        model = queryset.model
        fields = _model_field_names(model)
        if model is Company:
            field.queryset = Company.objects.filter(pk__in=company_ids)
        elif model is Contact:
            field.queryset = Contact.objects.filter(company_id__in=company_ids)
        elif model is Product:
            field.queryset = Product.objects.filter(company_id__in=company_ids)
        elif model is Warehouse:
            field.queryset = Warehouse.objects.filter(company_id__in=company_ids)
        elif model is PurchaseOrder:
            field.queryset = PurchaseOrder.objects.filter(company_id__in=company_ids)
        elif model is PurchaseOrderLine:
            field.queryset = PurchaseOrderLine.objects.filter(order__company_id__in=company_ids)
        elif "company" in fields:
            field.queryset = queryset.filter(company_id__in=company_ids)
        else:
            lookup = MODEL_COMPANY_LOOKUPS.get(model._meta.model_name)
            if lookup:
                field.queryset = queryset.filter(**{f"{lookup}__in": company_ids})
    return form
