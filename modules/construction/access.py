"""Role and company boundary enforcement for Construction records."""

from functools import lru_cache

from core.models import Company, Contact, Employee, Product, PurchaseOrder, PurchaseOrderLine, Warehouse
from modules.finance.models import Account
from modules.operations.models import Asset, ConstructionProject, WorkPackage
from modules.purchase.models import PurchaseRequest, PurchaseRequestLine

from .entity_config import ENTITY_MODELS


CONSTRUCTION_SUPER_USER = "Construction Super User"
CONSTRUCTION_MANAGER = "Construction Manager"
CONSTRUCTION_SUPERVISOR = "Construction Supervisor"
CONSTRUCTION_PUBLIC_USER = "Construction Public User"

CONSTRUCTION_ENTITIES = {"construction-projects", "work-packages", *ENTITY_MODELS}
PUBLIC_ENTITIES = {
    "construction-timesheets",
    "construction-timesheet-lines",
    "construction-risks",
    "construction-issues",
}

MODEL_COMPANY_LOOKUPS = {
    "constructionproject": "company_id",
    "workpackage": "construction_project__company_id",
    "constructionboqtemplateitem": "template__company_id",
    "constructioncontract": "project__company_id",
    "constructioncontractmilestone": "contract__project__company_id",
    "constructionboq": "project__company_id",
    "constructionboqitem": "boq__project__company_id",
    "constructioncostsheet": "project__company_id",
    "constructioncostsheetitem": "cost_sheet__project__company_id",
    "constructionbudgetchangerequest": "project__company_id",
    "constructionbudgetchangeline": "request__project__company_id",
    "constructionjoborder": "project__company_id",
    "constructionprogresslog": "job_order__project__company_id",
    "constructiontaskworker": "job_order__project__company_id",
    "constructionquantitysurvey": "project__company_id",
    "constructionquantitysurveyline": "survey__project__company_id",
    "constructionresourceactualization": "project__company_id",
    "constructionresourceactualizationline": "actualization__project__company_id",
    "constructiontimesheet": "project__company_id",
    "constructiontimesheetline": "timesheet__project__company_id",
    "constructionprogressiveclaim": "project__company_id",
    "constructionprogressiveclaimline": "claim__project__company_id",
    "constructionriskregister": "project__company_id",
    "constructionissuelog": "project__company_id",
    "constructionopeningbalance": "project__company_id",
    "constructionprojectaccountsettings": "project__company_id",
    "constructionmaterialdemand": "project__company_id",
    "constructionmaterialdemandline": "demand__project__company_id",
    "constructionprocurementallocation": "project__company_id",
    "constructionsubcontractjoborder": "project__company_id",
    "constructionboqtemplatecostline": "template_item__template__company_id",
    "constructioncontractdpline": "contract__project__company_id",
    "constructionboqcostline": "boq_item__boq__project__company_id",
    "constructioncostsheetresource": "cost_sheet_item__cost_sheet__project__company_id",
    "constructionequipmentfacility": "job_order__project__company_id",
    "constructionquantitysurveyjosource": "survey_line__survey__project__company_id",
    "constructionprogressiveclaimcumulative": "claim__project__company_id",
    "constructionprogressiveclaimattachment": "claim__project__company_id",
    "constructionprojectprofile": "project__company_id",
    "constructionprojectaccountsettingsline": "settings__project__company_id",
    "constructionprojectaccountsnapshotline": "project__company_id",
    "constructionopeningbalanceimport": "project__company_id",
    "constructionopeningbalanceexportsnapshot": "project__company_id",
    "constructionboqimportlog": "boq__project__company_id",
    "constructionprocurementbudgetacknowledgement": "company_id",
    "constructionprocurementbudgetaudit": "company_id",
    "constructionprocurementline": "project__company_id",
    "constructioninventorytransfer": "project__company_id",
    "constructioninventorytransferline": "transfer__project__company_id",
    "constructionsubcontractprogressline": "subcontract__project__company_id",
    "constructionsubcontractmilestone": "subcontract__project__company_id",
    "constructionaccountingposting": "project__company_id",
}


def _group_names(user):
    if not user.is_authenticated:
        return set()
    cached = getattr(user, "_gsshop_construction_group_names", None)
    if cached is None:
        cached = set(user.groups.values_list("name", flat=True))
        user._gsshop_construction_group_names = cached
    return cached


def _company_ids(user):
    cached = getattr(user, "_gsshop_allowed_company_ids", None)
    return cached if cached is not None else user.employee_profiles.values("company_id")


@lru_cache(maxsize=None)
def _model_field_names(model):
    return {field.name for field in model._meta.get_fields()}


def can_construction_entity_action(user, entity, action):
    if entity not in CONSTRUCTION_ENTITIES or user.is_superuser:
        return True
    groups = _group_names(user)
    if groups & {CONSTRUCTION_SUPER_USER, CONSTRUCTION_MANAGER}:
        return True
    if CONSTRUCTION_SUPERVISOR in groups:
        return action in {"view", "add", "change"}
    if CONSTRUCTION_PUBLIC_USER in groups and entity in PUBLIC_ENTITIES:
        return action in {"view", "add", "change"}
    return False


def can_construction_workflow_action(user, entity, action):
    if entity not in CONSTRUCTION_ENTITIES or user.is_superuser:
        return True
    if action not in {
        "approve", "reject", "certify", "invoice", "pay", "complete", "terminate", "close",
        "create-dp-invoices", "release-retention-1", "release-retention-2",
        "create-purchase-request", "import", "execute", "post",
    }:
        return True
    return bool(_group_names(user) & {CONSTRUCTION_SUPER_USER, CONSTRUCTION_MANAGER})


def _company_lookup(model):
    fields = _model_field_names(model)
    if "company" in fields:
        return "company_id"
    return MODEL_COMPANY_LOOKUPS.get(model._meta.model_name)


def scope_construction_queryset(user, entity, queryset):
    if entity not in CONSTRUCTION_ENTITIES or user.is_superuser:
        return queryset
    lookup = _company_lookup(queryset.model)
    if not lookup:
        return queryset.none()
    return queryset.filter(**{f"{lookup}__in": _company_ids(user)})


def scope_construction_form(form, user, entity):
    if entity not in CONSTRUCTION_ENTITIES or user.is_superuser:
        return form

    company_ids = _company_ids(user)
    directly_scoped = {
        Company: "pk",
        Contact: "company_id",
        Employee: "company_id",
        Product: "company_id",
        Warehouse: "company_id",
        Account: "company_id",
        ConstructionProject: "company_id",
        WorkPackage: "construction_project__company_id",
        Asset: "company_id",
        PurchaseRequest: "company_id",
        PurchaseOrder: "company_id",
        PurchaseRequestLine: "request__company_id",
        PurchaseOrderLine: "order__company_id",
    }
    for field in form.fields.values():
        queryset = getattr(field, "queryset", None)
        if queryset is None:
            continue
        lookup = directly_scoped.get(queryset.model) or _company_lookup(queryset.model)
        if lookup:
            field.queryset = queryset.filter(**{f"{lookup}__in": company_ids})
    return form
