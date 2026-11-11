"""Company-scoped access rules for the GS Shop procurement workspace."""

from functools import lru_cache

from django.utils import timezone
from django.utils.dateparse import parse_date

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse


PURCHASE_MANAGER = "Purchase Manager"
PURCHASE_USER = "Purchase User"
VENDOR_RELATIONS_OFFICER = "Vendor Relations Officer"
PURCHASE_BUDGET_MANAGER = "Purchase Budget Manager"

PURCHASE_ENTITIES = {
    "purchases",
    "purchase-order-lines",
    "purchase-requests",
    "purchase-request-lines",
    "purchase-quotations",
    "direct-purchases",
    "service-work-orders",
    "award-strategies",
    "purchase-tenders",
    "tender-bids",
    "tender-bid-lines",
    "tender-award-allocations",
    "tender-award-responses",
    "blanket-orders",
    "vendor-categories",
    "purchase-legal-documents",
    "purchase-legal-document-attachments",
    "vendor-profiles",
    "vendor-legal-documents",
    "vendor-prequalifications",
    "vendor-document-reviews",
    "vendor-pricelists",
    "vendor-pricelist-items",
    "vendor-pricelist-tiers",
    "vendor-pricelist-amendments",
    "vendor-pricelist-amendment-lines",
    "vendor-pricelist-imports",
    "vendor-pricelist-import-previews",
    "vendor-performance-templates",
    "vendor-performance-template-lines",
    "vendor-performance-evaluations",
    "vendor-performance-evaluation-lines",
    "vendor-performance-score-history",
    "purchase-teams",
    "purchase-team-members",
    "purchase-kpi-templates",
    "purchase-kpi-template-lines",
    "purchase-kpi-targets",
    "purchase-kpi-target-lines",
    "purchase-order-types",
    "purchase-order-type-sequences",
    "purchase-order-templates",
    "purchase-order-template-lines",
    "purchase-remark-templates",
    "purchase-milestone-templates",
    "purchase-milestone-template-lines",
    "purchase-settings",
    "purchase-order-controls",
    "purchase-three-way-matches",
    "purchase-order-billing-plans",
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
    "tender-document-completion-matrices",
    "tender-document-completion-lines",
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
    "purchase-legal-documents",
    "purchase-legal-document-attachments",
    "vendor-profiles",
    "vendor-legal-documents",
    "vendor-prequalifications",
    "vendor-document-reviews",
    "vendor-pricelists",
    "vendor-pricelist-items",
    "vendor-pricelist-tiers",
    "vendor-pricelist-amendments",
    "vendor-pricelist-amendment-lines",
    "vendor-pricelist-imports",
    "vendor-pricelist-import-previews",
    "vendor-performance-templates",
    "vendor-performance-template-lines",
    "vendor-performance-evaluations",
    "vendor-performance-evaluation-lines",
    "vendor-performance-score-history",
    "vendor-communications",
    "vendor-transaction-history",
}

READ_ONLY_PURCHASE_CONFIGURATION_ENTITIES = {
    "purchase-legal-documents",
    "purchase-legal-document-attachments",
}

SYSTEM_GENERATED_PURCHASE_ENTITIES = {
    "tender-document-completion-matrices",
    "tender-document-completion-lines",
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
    if entity in SYSTEM_GENERATED_PURCHASE_ENTITIES:
        return action == "view" and bool(
            groups & {PURCHASE_MANAGER, PURCHASE_USER, VENDOR_RELATIONS_OFFICER}
        )
    if PURCHASE_MANAGER in groups:
        return True
    if entity in READ_ONLY_PURCHASE_CONFIGURATION_ENTITIES:
        return action == "view" and bool(
            groups & {PURCHASE_USER, VENDOR_RELATIONS_OFFICER}
        )
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
    "submit-decision",
    "approve-decision",
    "reject-decision",
    "reset-decision",
    "reopen-award",
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
    if entity == "vendor-document-reviews" and action in {"approve", "reject", "revision"}:
        return PURCHASE_MANAGER in groups
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
    "purchaseorderline": "order__company_id",
    "purchaserequestline": "request__company_id",
    "tenderbid": "tender__company_id",
    "tenderbidline": "bid__tender__company_id",
    "tenderawardallocation": "tender__company_id",
    "tenderawardresponse": "tender__company_id",
    "vendorlegaldocument": "vendor__company_id",
    "purchaselegaldocumentattachment": "legal_document__company_id",
    "vendorprequalification": "vendor__company_id",
    "vendordocumentreview": "prequalification__vendor__company_id",
    "vendorpricelistamendment": "pricelist__company_id",
    "vendorpricelistitem": "pricelist__company_id",
    "vendorpricelisttier": "item__pricelist__company_id",
    "vendorpricelistamendmentline": "amendment__pricelist__company_id",
    "vendorpricelistimportpreview": "import_log__company_id",
    "vendorperformanceevaluation": "vendor__company_id",
    "vendorperformancetemplateline": "template__company_id",
    "vendorperformanceevaluationline": "evaluation__company_id",
    "vendorperformancescorehistory": "vendor__company_id",
    "purchaseteammember": "team__company_id",
    "purchasekpitarget": "company_id",
    "purchasekpitemplateline": "template__company_id",
    "purchasekpitargetline": "target__company_id",
    "purchaseordertypesequenceline": "company_id",
    "purchaseordertemplateline": "order_template__company_id",
    "purchasemilestonetemplateline": "template__company_id",
    "purchasemilestonetemplate": "company_id",
    "purchaseordercontrol": "order__company_id",
    "purchaseorderinvoiceaccounting": "order__company_id",
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
    "tenderdocumentcompletionmatrix": "tender__company_id",
    "tenderdocumentcompletionline": "tender__company_id",
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
    if entity == "purchases" and "product" in form.fields:
        order = getattr(form.instance, "_state", None)
        order = order.fields_cache.get("order") if order is not None else None
        data = getattr(form, "data", None)
        company_value = data.get("company") if data else None
        vendor_value = data.get("vendor") if data else None
        date_value = data.get("order_date") if data else None
        company_id = int(company_value) if str(company_value or "").isdigit() else getattr(order, "company_id", None)
        vendor_id = int(vendor_value) if str(vendor_value or "").isdigit() else getattr(order, "vendor_id", None)
        transaction_date = parse_date(str(date_value)) if date_value else getattr(order, "order_date", None)
        transaction_date = transaction_date or timezone.localdate()
        if company_id and vendor_id:
            cache = getattr(user, "_purchase_product_eligibility_cache", None)
            if cache is None:
                cache = {}
                user._purchase_product_eligibility_cache = cache
            cache_key = (company_id, vendor_id, transaction_date)
            if cache_key not in cache:
                from .models import PurchaseSetting
                from .vendor_pricelist_eligibility import eligible_vendor_pricelist_product_ids

                restricted = (
                    PurchaseSetting.objects.filter(company_id=company_id)
                    .values_list("restrict_product_by_vendor_pricelist", flat=True)
                    .first()
                )
                cache[cache_key] = (
                    eligible_vendor_pricelist_product_ids(
                        company_id=company_id,
                        vendor_id=vendor_id,
                        transaction_date=transaction_date,
                    )
                    if restricted
                    else None
                )
            eligible_ids = cache[cache_key]
            if eligible_ids is not None:
                current_product_id = getattr(form.instance, "product_id", None)
                if current_product_id:
                    eligible_ids = set(eligible_ids) | {current_product_id}
                form.fields["product"].queryset = form.fields["product"].queryset.filter(pk__in=eligible_ids)
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
