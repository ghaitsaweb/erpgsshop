"""Idempotent role and model-permission setup for GSSHOP HR."""

from django.contrib.auth.models import Group, Permission

from .access import (
    CAREER_ADMIN,
    CAREER_MANAGER,
    CAREER_SUPERVISOR,
    CAREER_USER,
    FINANCE_MANAGER,
    HR_ADMINISTRATOR,
    HR_MANAGER,
    HR_SUPER_USER,
    HR_SUPERVISOR,
    HR_USER,
    PAYROLL_ADMIN,
)


SELF_SERVICE_VIEW_MODELS = {
    "employee",
    "attendance",
    "employmentcontract",
    "leaverequest",
    "overtimerequest",
    "payslip",
    "employeechangerequest",
    "employeedocument",
    "employeeonboarding",
    "employeeoffboarding",
    "leaveallocation",
    "travelrequest",
    "cashadvance",
    "careertransitionrequest",
    "compensationbenefit",
    "expenseclaim",
    "loan",
    "trainingprogram",
    "trainingcourse",
    "trainingtrainer",
    "trainingconduct",
    "trainingconductschedule",
    "trainingconductparticipant",
}
SELF_SERVICE_REQUEST_MODELS = {
    "leaverequest",
    "overtimerequest",
    "employeechangerequest",
    "employeedocument",
    "travelrequest",
    "cashadvance",
    "careertransitionrequest",
    "expenseclaim",
}
PAYROLL_MODELS = {"employee", "employmentcontract", "payslip", "payrollperiod", "salarycomponent", "payrollrun", "compensationbenefit"}
FINANCE_MODELS = {"employee", "loan", "expenseclaim", "cashadvance", "loantype", "loanpolicy", "expenseproduct"}
CAREER_MODELS = {"employee", "careertransitionrequest", "careertransitiontype", "careertransitionletter"}


def _permissions(models, actions):
    codenames = {f"{action}_{model}" for model in models for action in actions}
    return Permission.objects.filter(
        content_type__app_label__in={"core", "gsshop_hr", "finance"},
        codename__in=codenames,
    )


def ensure_hr_roles(**_kwargs):
    all_hr_permissions = Permission.objects.filter(content_type__app_label__in={"core", "gsshop_hr"}) | _permissions(
        FINANCE_MODELS,
        {"view", "add", "change", "delete"},
    )
    self_service_permissions = _permissions(SELF_SERVICE_VIEW_MODELS, {"view"}) | _permissions(
        SELF_SERVICE_REQUEST_MODELS,
        {"add", "change"},
    )
    role_permissions = {
        HR_SUPER_USER: all_hr_permissions,
        HR_MANAGER: all_hr_permissions,
        HR_ADMINISTRATOR: all_hr_permissions,
        HR_SUPERVISOR: self_service_permissions,
        HR_USER: self_service_permissions,
        PAYROLL_ADMIN: _permissions(PAYROLL_MODELS, {"view", "add", "change", "delete"}),
        FINANCE_MANAGER: _permissions(FINANCE_MODELS, {"view", "add", "change", "delete"}),
        CAREER_ADMIN: _permissions(CAREER_MODELS, {"view", "add", "change", "delete"}),
        CAREER_MANAGER: _permissions(CAREER_MODELS, {"view", "add", "change"}),
        CAREER_SUPERVISOR: _permissions(CAREER_MODELS, {"view", "add", "change"}),
        CAREER_USER: _permissions({"employee", "careertransitionrequest"}, {"view", "add", "change"}),
    }
    for role_name, permissions in role_permissions.items():
        group, _created = Group.objects.get_or_create(name=role_name)
        group.permissions.add(*permissions)
