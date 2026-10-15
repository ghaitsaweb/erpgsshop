"""Idempotent role and model-permission setup for GS Shop procurement."""

from django.contrib.auth.models import Group, Permission

from .access import (
    PURCHASE_BUDGET_MANAGER,
    PURCHASE_MANAGER,
    PURCHASE_USER,
    VENDOR_RELATIONS_OFFICER,
)


VENDOR_MODELS = {
    "vendorcategory",
    "vendorprofile",
    "vendorlegaldocument",
    "vendorprequalification",
    "vendordocumentreview",
    "vendorpricelist",
    "vendorpricelistamendment",
    "vendorperformancetemplate",
    "vendorperformanceevaluation",
}

BUDGET_MODELS = {
    "productbudgetgroup",
    "productbudget",
    "budgetproducttransfer",
    "budgetproductcarryover",
    "budgetproductchangerequest",
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
    "blanketorder",
    "purchaseteam",
    "purchaseteammember",
    "purchasekpitemplate",
    "purchasekpitarget",
    "purchaseordertype",
    "purchaseordertemplate",
    "purchasemilestonetemplate",
    "purchasesetting",
    "purchaseordercontrol",
    "purchaseordermilestone",
    "goodsreceipt",
    "goodsreceiptline",
    "purchasereturnrequest",
    "purchaseaccountingsetting",
    "purchaseaccountmapping",
    "purchasepeppolsubmission",
    "purchaseinsight",
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
    }
    for role_name, permissions in role_permissions.items():
        group, _created = Group.objects.get_or_create(name=role_name)
        group.permissions.add(*permissions)
