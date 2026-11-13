"""Idempotent role and model-permission setup for GS Shop procurement."""

from django.contrib.auth.models import Group, Permission

from .access import (
    PURCHASE_BUDGET_MANAGER,
    PURCHASE_MANAGER,
    PURCHASE_REQUESTOR_MANAGER,
    PURCHASE_USER,
    VENDOR_RELATIONS_OFFICER,
)


VENDOR_MODELS = {
    "vendorcategory",
    "vendorprofile",
    "vendorchangeproposal",
    "vendorlegaldocument",
    "vendorprequalification",
    "vendordocumentreview",
    "vendorpricelist",
    "vendorpricelistitem",
    "vendorpricelisttier",
    "vendorpricelistamendment",
    "vendorpricelistamendmentline",
    "vendorperformancetemplate",
    "vendorperformanceevaluation",
    "vendorcommunication",
    "vendortransactionhistory",
}

BUDGET_MODELS = {
    "productbudgetgroup",
    "productbudget",
    "budgetproducttransfer",
    "budgetproductcarryover",
    "budgetproductchangerequest",
    "productbudgetmovement",
}

PURCHASE_CORE_MODELS = {"purchaseorder", "purchaseorderline"}
PURCHASE_MODELS = VENDOR_MODELS | BUDGET_MODELS | {
    "purchaserequest",
    "purchaserequestline",
    "purchasequotation",
    "directpurchase",
    "serviceworkorder",
    "awardstrategy",
    "purchasetender",
    "tenderbid",
    "tenderbidline",
    "tenderawardallocation",
    "tenderawardresponse",
    "blanketorder",
    "purchaseteam",
    "purchaseteammember",
    "purchasekpitemplate",
    "purchasekpitarget",
    "purchaseordertype",
    "purchaseordertemplate",
    "purchaseremarktemplate",
    "purchasemilestonetemplate",
    "purchasesetting",
    "purchaseordercontrol",
    "purchaseorderinvoiceaccounting",
    "purchaseordermilestone",
    "goodsreceipt",
    "goodsreceiptline",
    "purchasereturnrequest",
    "purchaseaccountingsetting",
    "purchaseaccountmapping",
    "purchasepeppolsubmission",
    "purchaseinsight",
    "purchasequotationline",
    "directpurchaseline",
    "serviceworkorderline",
    "purchasetenderline",
    "tenderinvitation",
    "tenderdocument",
    "tenderauditlog",
    "blanketorderline",
    "purchaseproductcategory",
    "purchaseproductvariant",
    "purchaseordercommunication",
    "purchasemailtemplate",
    "purchasequotationcommunication",
    "purchaseaccountingdocument",
    "purchaseaccountingline",
    "purchasestockdemand",
    "purchaseinventorytransfer",
    "purchaseinventorytransferline",
    "purchasepeppolevent",
    "purchasecontractterm",
    "purchaseorderrevision",
}


def _permissions(models, actions):
    codenames = {f"{action}_{model}" for model in models for action in actions}
    return Permission.objects.filter(
        content_type__app_label="gsshop_purchase",
        content_type__model__in=models,
        codename__in=codenames,
    )


def _core_purchase_permissions(actions):
    codenames = {f"{action}_{model}" for model in PURCHASE_CORE_MODELS for action in actions}
    return Permission.objects.filter(
        content_type__app_label="core",
        content_type__model__in=PURCHASE_CORE_MODELS,
        codename__in=codenames,
    )


def ensure_purchase_roles(**_kwargs):
    all_actions = {"view", "add", "change", "delete"}
    operational_actions = {"view", "add", "change"}
    all_permissions = _permissions(PURCHASE_MODELS, all_actions) | _core_purchase_permissions(all_actions)
    operational_permissions = _permissions(PURCHASE_MODELS, operational_actions) | _core_purchase_permissions(
        operational_actions
    )
    role_permissions = {
        PURCHASE_MANAGER: all_permissions,
        PURCHASE_USER: operational_permissions,
        VENDOR_RELATIONS_OFFICER: _permissions(VENDOR_MODELS, {"view", "add", "change"}),
        PURCHASE_BUDGET_MANAGER: _permissions(BUDGET_MODELS, {"view", "add", "change", "delete"}),
        PURCHASE_REQUESTOR_MANAGER: _permissions(
            {"purchaserequest", "purchaserequestline"},
            {"view", "add", "change"},
        ),
    }
    role_names = tuple(role_permissions)
    Group.objects.bulk_create(
        [Group(name=role_name) for role_name in role_names],
        ignore_conflicts=True,
        batch_size=100,
    )
    groups = Group.objects.in_bulk(role_names, field_name="name")
    for role_name, permissions in role_permissions.items():
        groups[role_name].permissions.add(*permissions)
