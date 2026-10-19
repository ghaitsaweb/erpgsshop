"""Role and company boundary enforcement for Retail and F&B records."""

from functools import lru_cache

from core.models import Company, Contact, Employee, Product, Warehouse

from .entity_config import POS_ENTITY_CONFIG


POS_MANAGER = "POS Manager"
POS_SUPERVISOR = "POS Supervisor"
POS_CASHIER = "POS Cashier"
POS_ENTITIES = set(POS_ENTITY_CONFIG)
CASHIER_ENTITIES = {
    "pos-orders", "pos-payments", "pos-sessions", "pos-cash-movements", "pos-reservations",
    "restaurant-tables", "kitchen-tickets", "pos-product-availability", "pos-price-check-logs",
    "loyalty-wallets", "loyalty-transactions", "employee-meal-records", "pos-internal-usages",
}

MODEL_COMPANY_LOOKUPS = {
    "posregister": "company_id", "possession": "register__company_id", "posorder": "session__register__company_id",
    "posorderline": "order__session__register__company_id", "restauranttable": "register__company_id",
    "kitchenticket": "order__session__register__company_id", "kitchenticketline": "ticket__order__session__register__company_id",
    "posstation": "profile__company_id", "posdevice": "profile__company_id", "posdevicesequencestate": "device__profile__company_id",
    "pospricelistrule": "pricelist__company_id", "pospromotioncriterion": "promotion__company_id",
    "pospromotionrule": "promotion__company_id", "pospromotionreward": "rule__promotion__company_id",
    "pospromotionrewardaddonline": "reward__rule__promotion__company_id", "customerfacingcampaignslide": "campaign__company_id",
    "customerfacingcampaignrunningtext": "campaign__company_id", "posbomline": "bom__company_id",
    "posbomoption": "bom__company_id", "posbomoptionline": "option__bom__company_id",
    "posprofilepaymentmethodline": "profile__company_id", "pospriceshortcutline": "preset__company_id",
    "posreceiptsection": "template__company_id", "posreceiptfield": "section__template__company_id",
    "posreceipttemplaterevision": "template__company_id", "posreceiptocrjob": "template__company_id",
    "pospayment": "order__session__register__company_id", "poscashmovement": "session__register__company_id",
    "posapprovalotprule": "profile__company_id", "posapprovalotprequest": "rule__profile__company_id",
    "posreceiptprintlog": "profile__company_id", "posreceiptemaildelivery": "order__session__register__company_id",
    "pospricelabelprintlog": "pricelist__company_id", "pospricechecklog": "profile__company_id",
    "loyaltytier": "program__company_id", "loyaltywallet": "program__company_id",
    "loyaltytransaction": "wallet__program__company_id", "loyaltywelcomepoint": "program__company_id",
    "loyaltytierevent": "wallet__program__company_id", "posrfmcalculationrun": "configuration__company_id",
    "posrfmcustomerresult": "run__configuration__company_id", "posrfmbroadcastlog": "result__run__configuration__company_id",
    "posreservation": "profile__company_id", "posreservationline": "reservation__profile__company_id",
    "poscombocategory": "combo__product__company_id", "poscomboline": "category__combo__product__company_id",
    "poscombo": "product__company_id", "posproductavailability": "profile__company_id",
    "poscustomerqueueresetrule": "profile__company_id", "employeemealprogramline": "program__company_id",
    "employeemealprogramrecord": "program__company_id", "employeemealprogramrecordline": "record__program__company_id",
    "posinternalusage": "profile__company_id", "posinternalusageline": "usage__profile__company_id",
    "poscashdrawerlog": "profile__company_id", "posfnbsynccommand": "order__session__register__company_id",
    "paymentmethodmdrcharge": "payment_method__company_id", "posbankpayment": "order__session__register__company_id",
    "posbankpaymentrequestlog": "payment__order__session__register__company_id", "posdepositmovement": "deposit__company_id",
    "posoutletorderline": "order__company_id", "posmembershipappaccount": "member__company_id",
    "posmembershipappotp": "account__member__company_id", "posrealtimechange": "profile__company_id",
    "posproductattributevalue": "attribute__company_id", "posproductvariant": "product__company_id",
    "posproductbundleline": "bundle__company_id", "posbackupdataprofile": "profile__company_id",
    "posbackupdata": "profiles__company_id", "posbackupdataline": "profile__company_id", "posstockdemandline": "demand__company_id",
    "posinventorytransferline": "transfer__company_id",
}


def _group_names(user):
    if not user.is_authenticated:
        return set()
    cached = getattr(user, "_gsshop_pos_group_names", None)
    if cached is None:
        cached = set(user.groups.values_list("name", flat=True))
        user._gsshop_pos_group_names = cached
    return cached


def _company_ids(user):
    cached = getattr(user, "_gsshop_allowed_company_ids", None)
    return cached if cached is not None else user.employee_profiles.values("company_id")


@lru_cache(maxsize=None)
def _field_names(model):
    return {field.name for field in model._meta.get_fields()}


def _company_lookup(model):
    if "company" in _field_names(model):
        return "company_id"
    return MODEL_COMPANY_LOOKUPS.get(model._meta.model_name)


def can_pos_entity_action(user, entity, action):
    if entity not in POS_ENTITIES or user.is_superuser:
        return True
    groups = _group_names(user)
    if POS_MANAGER in groups:
        return True
    if POS_SUPERVISOR in groups:
        return action in {"view", "add", "change"}
    if POS_CASHIER in groups and entity in CASHIER_ENTITIES:
        return action in {"view", "add", "change"}
    return False


def can_pos_workflow_action(user, entity, action):
    if entity not in POS_ENTITIES or user.is_superuser:
        return True
    if action in {"approve", "reject", "refund", "reopen", "publish", "archive", "cancel"}:
        return bool(_group_names(user) & {POS_MANAGER, POS_SUPERVISOR})
    return bool(_group_names(user) & {POS_MANAGER, POS_SUPERVISOR, POS_CASHIER})


def scope_pos_queryset(user, entity, queryset):
    if entity not in POS_ENTITIES or user.is_superuser:
        return queryset
    if entity == "pos-users":
        return queryset.filter(groups__name__in={POS_MANAGER, POS_SUPERVISOR, POS_CASHIER}).distinct()
    lookup = _company_lookup(queryset.model)
    if not lookup:
        return queryset.none()
    return queryset.filter(**{f"{lookup}__in": _company_ids(user)})


def scope_pos_form(form, user, entity):
    if entity not in POS_ENTITIES or user.is_superuser:
        return form
    company_ids = _company_ids(user)
    direct = {Company: "pk", Contact: "company_id", Employee: "company_id", Product: "company_id", Warehouse: "company_id"}
    for field in form.fields.values():
        queryset = getattr(field, "queryset", None)
        if queryset is None:
            continue
        lookup = direct.get(queryset.model) or _company_lookup(queryset.model)
        if lookup:
            field.queryset = queryset.filter(**{f"{lookup}__in": company_ids})
    return form
