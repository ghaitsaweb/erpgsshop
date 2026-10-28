"""Clean-room HR row-level access rules for GSSHOP.

The policy mirrors the functional access concepts observed in HMX while using
Django groups, permissions, and querysets rather than HMX implementation code.
"""

from functools import lru_cache

from django.db.models import Q

from core.models import Company, Employee


HR_SUPER_USER = "HR Super User"
HR_MANAGER = "HR Manager"
HR_ADMINISTRATOR = "HR Administrator"
HR_SUPERVISOR = "HR Supervisor"
HR_USER = "HR User"
PAYROLL_ADMIN = "Payroll Admin"
FINANCE_MANAGER = "Finance Manager"
CAREER_ADMIN = "Career Transition Administrator"
CAREER_MANAGER = "Career Transition HR Manager"
CAREER_SUPERVISOR = "Career Transition Supervisor"
CAREER_USER = "Career Transition Self Service"

FULL_HR_GROUPS = {HR_SUPER_USER, HR_MANAGER, HR_ADMINISTRATOR}
PAYROLL_ENTITIES = {
    "contracts",
    "contract-allowances",
    "contract-letter-templates",
    "contract-letters",
    "contract-types",
    "payslips",
    "payroll-periods",
    "payroll-report-templates",
    "payroll-variables",
    "salary-components",
    "salary-packages",
    "payroll-runs",
    "payroll-run-checklist",
    "payroll-run-preview",
    "compensation-benefits",
    "contract-allowance-history",
    "payroll-disbursements",
    "payroll-disbursement-lines",
    "payroll-accounting-events",
}
FINANCE_ENTITIES = {
    "expenses",
    "loans",
    "cash-advances",
    "loan-types",
    "loan-policies",
    "employee-loan-requests",
    "loan-installments",
    "loan-repayments",
    "loan-eligibility",
    "expense-products",
    "advance-cycles",
    "expense-cycles",
    "advance-limit-consumptions",
    "expense-limit-consumptions",
    "employee-loan-offers",
    "expense-product-categories",
}
CAREER_ENTITIES = {
    "career-transition-requests",
    "career-transition-types",
    "career-transition-letters",
    "career-transition-change-history",
}
SELF_SERVICE_ENTITIES = {
    "employees",
    "attendance",
    "contracts",
    "leave-requests",
    "overtime",
    "payslips",
    "employee-change-requests",
    "employee-change-request-lines",
    "employee-change-request-history",
    "employee-change-relation-history",
    "employee-address-changes",
    "employee-emergency-contact-changes",
    "employee-bank-account-changes",
    "employee-dependent-changes",
    "employee-education-changes",
    "employee-emergency-contacts",
    "employee-bank-accounts",
    "employee-dependents",
    "employee-education",
    "employee-documents",
    "employee-onboarding",
    "employee-offboarding",
    "employee-lifecycle-tasks",
    "leave-allocations",
    "travel-requests",
    "cash-advances",
    "career-transition-requests",
    "compensation-benefits",
    "expenses",
    "loans",
    "training-programs",
    "training-courses",
    "training-trainers",
    "training-conducts",
    "employee-addresses",
    "employee-relations",
    "schedule-exchanges",
    "attendance-change-requests",
    "live-tracking-sessions",
    "leave-opening-balances",
    "leave-movements",
    "leave-buckets",
    "leave-bucket-consumptions",
    "leave-encashments",
    "leave-encashment-buckets",
    "employee-loan-requests",
    "loan-installments",
    "loan-repayments",
    "training-requests",
    "training-progress",
    "employee-id-cards",
    "multiple-job-assignments",
    "employee-document-history",
    "employee-loan-offers",
    "face-recognition-profiles",
}
SELF_MUTABLE_ENTITIES = {
    "leave-requests",
    "overtime",
    "employee-change-requests",
    "employee-change-request-lines",
    "employee-address-changes",
    "employee-emergency-contact-changes",
    "employee-bank-account-changes",
    "employee-dependent-changes",
    "employee-education-changes",
    "employee-documents",
    "travel-requests",
    "cash-advances",
    "career-transition-requests",
    "expenses",
    "schedule-exchanges",
    "attendance-change-requests",
    "leave-encashments",
    "employee-loan-requests",
    "training-requests",
}
HR_SCOPED_ENTITIES = {
    "employees",
    "attendance",
    "departments",
    "contracts",
    "leave-requests",
    "overtime",
    "payslips",
    "candidates",
    "appraisals",
    "expenses",
    "loans",
    "business-units",
    "work-locations",
    "job-positions",
    "employee-change-requests",
    "employee-change-request-lines",
    "employee-change-request-history",
    "employee-change-relation-history",
    "employee-address-changes",
    "employee-emergency-contact-changes",
    "employee-bank-account-changes",
    "employee-dependent-changes",
    "employee-education-changes",
    "employee-emergency-contacts",
    "employee-bank-accounts",
    "employee-dependents",
    "employee-education",
    "employee-documents",
    "employee-disciplinary",
    "employee-checklists",
    "employee-onboarding",
    "employee-offboarding",
    "employee-lifecycle-tasks",
    "raw-attendance",
    "shifts",
    "work-schedules",
    "schedule-assignments",
    "leave-types",
    "leave-blackout-periods",
    "leave-allocations",
    "overtime-rules",
    "payroll-periods",
    "salary-components",
    "travel-requests",
    "travel-packages",
    "training-programs",
    "training-courses",
    "training-trainers",
    "training-conducts",
    "cash-advances",
    "hr-settings",
    "hr-years",
    "company-holidays",
    "employee-grades",
    "skills",
    "document-types",
    "loan-types",
    "loan-policies",
    "career-transition-requests",
    "career-transition-types",
    "career-transition-letters",
    "expense-products",
    "payroll-runs",
    "payroll-run-checklist",
    "payroll-run-preview",
    "compensation-benefits",
    "job-classifications",
    "job-experience-levels",
    "employee-categories",
    "employee-addresses",
    "employee-relations",
    "working-calendars",
    "work-patterns",
    "schedule-exchanges",
    "contract-types",
    "contract-allowances",
    "contract-letter-templates",
    "contract-letters",
    "disciplinary-stages",
    "attendance-change-requests",
    "live-tracking-sessions",
    "attendance-recomputes",
    "leave-structures",
    "leave-opening-balances",
    "leave-movements",
    "leave-buckets",
    "leave-bucket-consumptions",
    "leave-encashments",
    "leave-encashment-buckets",
    "payroll-variables",
    "salary-packages",
    "payroll-report-templates",
    "employee-loan-requests",
    "loan-installments",
    "loan-repayments",
    "loan-eligibility",
    "training-requests",
    "training-progress",
    "advance-cycles",
    "expense-cycles",
    "advance-limit-consumptions",
    "expense-limit-consumptions",
    "employee-id-card-templates",
    "employee-id-cards",
    "multiple-job-assignments",
    "organization-configurations",
    "hr-announcements",
    "contract-allowance-history",
    "employee-document-bulk-uploads",
    "employee-document-history",
    "attendance-recompute-transactions",
    "leave-bulk-operations",
    "payroll-disbursements",
    "payroll-disbursement-lines",
    "payroll-accounting-events",
    "employee-loan-offers",
    "career-transition-change-history",
    "training-categories",
    "face-recognition-profiles",
    "expense-product-categories",
    "hr-workflow-audits",
}


def _group_names(user):
    if not user.is_authenticated:
        return set()
    cached = getattr(user, "_gsshop_hr_group_names", None)
    if cached is None:
        cached = set(user.groups.values_list("name", flat=True))
        user._gsshop_hr_group_names = cached
    return cached


def _company_ids(user):
    cached = getattr(user, "_gsshop_allowed_company_ids", None)
    return cached if cached is not None else Employee.objects.filter(user=user).values("company_id")


def can_hr_entity_action(user, entity, action):
    """Apply role policy in addition to Django's model permissions."""
    if entity not in HR_SCOPED_ENTITIES or user.is_superuser:
        return True
    groups = _group_names(user)
    if groups & FULL_HR_GROUPS:
        return True
    if entity in PAYROLL_ENTITIES and PAYROLL_ADMIN in groups:
        return True
    if entity in FINANCE_ENTITIES and FINANCE_MANAGER in groups:
        return True
    if entity in CAREER_ENTITIES and groups & {CAREER_ADMIN, CAREER_MANAGER}:
        return action != "delete" or CAREER_ADMIN in groups
    if groups & {HR_SUPERVISOR, CAREER_SUPERVISOR}:
        if action == "view":
            return entity in SELF_SERVICE_ENTITIES
        return action in {"add", "change"} and entity in SELF_MUTABLE_ENTITIES
    if groups & {HR_USER, CAREER_USER}:
        if action == "view":
            return entity in SELF_SERVICE_ENTITIES
        return action in {"add", "change"} and entity in SELF_MUTABLE_ENTITIES
    return action == "view" and entity in SELF_SERVICE_ENTITIES


def can_hr_workflow_action(user, entity, action, instance):
    """Separate requester transitions from approval authority."""
    if entity == "employee-documents" and action in {"verify", "expire", "replace", "archive", "unarchive"}:
        return user.is_superuser or bool(_group_names(user) & FULL_HR_GROUPS)
    if entity == "leave-opening-balances" and action == "finalize":
        return user.is_superuser or bool(_group_names(user) & FULL_HR_GROUPS)
    if entity == "leave-encashments" and action in {"approve", "reject", "cancel", "assign-payroll"}:
        return user.is_superuser or bool(_group_names(user) & FULL_HR_GROUPS)
    if entity not in HR_SCOPED_ENTITIES or action not in {"approve", "reject"} or user.is_superuser:
        return True
    groups = _group_names(user)
    if groups & FULL_HR_GROUPS:
        return True
    if entity in PAYROLL_ENTITIES and PAYROLL_ADMIN in groups:
        return True
    if entity in FINANCE_ENTITIES and FINANCE_MANAGER in groups:
        return True
    if entity in CAREER_ENTITIES and groups & {CAREER_ADMIN, CAREER_MANAGER}:
        return True
    if groups & {HR_SUPERVISOR, CAREER_SUPERVISOR} and hasattr(instance, "employee_id"):
        cache_key = f"_gsshop_supervises_{user.pk}"
        allowed = getattr(instance, cache_key, None)
        if allowed is None:
            allowed = Employee.objects.filter(pk=instance.employee_id, supervisor__user=user).exists()
            setattr(instance, cache_key, allowed)
        return allowed
    return False


def accessible_employees(user, entity=None):
    """Return employees visible to a user without query-per-employee growth."""
    base = Employee.objects.all()
    if user.is_superuser:
        return base

    groups = _group_names(user)
    company_ids = _company_ids(user)
    if groups & FULL_HR_GROUPS:
        return base.filter(company_id__in=company_ids)
    if entity in PAYROLL_ENTITIES and PAYROLL_ADMIN in groups:
        return base.filter(company_id__in=company_ids)
    if entity in FINANCE_ENTITIES and FINANCE_MANAGER in groups:
        return base.filter(company_id__in=company_ids)
    if entity in CAREER_ENTITIES and groups & {CAREER_ADMIN, CAREER_MANAGER}:
        return base.filter(company_id__in=company_ids)
    if HR_SUPERVISOR in groups or CAREER_SUPERVISOR in groups:
        return base.filter(Q(user=user) | Q(supervisor__user=user)).distinct()
    return base.filter(user=user)


@lru_cache(maxsize=None)
def _model_field_names(model):
    return {field.name for field in model._meta.get_fields()}


def scope_hr_queryset(user, entity, queryset):
    """Apply company and employee record rules to an HR-facing queryset."""
    if entity not in HR_SCOPED_ENTITIES or user.is_superuser:
        return queryset

    model = queryset.model
    fields = _model_field_names(model)
    employee_ids = accessible_employees(user, entity=entity).values("pk")
    company_ids = _company_ids(user)

    if model is Employee:
        return queryset.filter(pk__in=employee_ids)
    if "employee" in fields:
        queryset = queryset.filter(employee_id__in=employee_ids)
        if entity == "payslips":
            groups = _group_names(user)
            elevated = groups & (FULL_HR_GROUPS | {HR_SUPERVISOR, PAYROLL_ADMIN})
            if not elevated:
                queryset = queryset.filter(status__in={"confirmed", "paid"})
        return queryset
    if "contract" in fields:
        return queryset.filter(contract__employee_id__in=employee_ids)
    if "loan" in fields:
        return queryset.filter(loan__employee_id__in=employee_ids)
    if model._meta.model_name == "employeelifecycletask":
        return queryset.filter(
            Q(onboarding__employee_id__in=employee_ids)
            | Q(offboarding__employee_id__in=employee_ids)
        )
    model_lookup = {
        "employeechangerequestline": ("request__employee_id", employee_ids),
        "employeechangerequesthistory": ("request__employee_id", employee_ids),
        "employeechangerelationhistory": ("request__employee_id", employee_ids),
        "employeeaddresschange": ("request__employee_id", employee_ids),
        "employeeemergencycontactchange": ("request__employee_id", employee_ids),
        "employeebankaccountchange": ("request__employee_id", employee_ids),
        "employeedependentchange": ("request__employee_id", employee_ids),
        "employeeeducationchange": ("request__employee_id", employee_ids),
        "employeedocumenthistory": ("document__employee_id", employee_ids),
        "payrolldisbursement": ("payroll_run__company_id", company_ids),
        "payrolldisbursementline": ("disbursement__payroll_run__company_id", company_ids),
        "payrollaccountingevent": ("payroll_run__company_id", company_ids),
        "payrollrunchecklistitem": ("run__company_id", company_ids),
        "payrollrunpreviewline": ("run__company_id", company_ids),
        "careertransitionchangehistory": ("request__employee_id", employee_ids),
        "leavebucketconsumption": ("leave_request__employee_id", employee_ids),
        "leaveencashmentbucket": ("encashment__employee_id", employee_ids),
        "leaveblackoutperiod": ("leave_type__company_id", company_ids),
    }.get(model._meta.model_name)
    if model_lookup:
        lookup, values = model_lookup
        return queryset.filter(**{f"{lookup}__in": values})
    if "company" in fields:
        return queryset.filter(company_id__in=company_ids)
    if "course" in fields:
        return queryset.filter(course__company_id__in=company_ids)
    if "schedule" in fields:
        return queryset.filter(schedule__company_id__in=company_ids)
    return queryset.none()


def scope_hr_form(form, user, entity):
    """Restrict relational choices using the same record-rule boundary."""
    if entity not in HR_SCOPED_ENTITIES or user.is_superuser:
        return form

    if entity == "employee-documents" and not (_group_names(user) & FULL_HR_GROUPS):
        for name in ("verified", "status", "replaced_by"):
            if name in form.fields:
                form.fields[name].disabled = True
        if form.instance.pk and form.instance.verified:
            for field in form.fields.values():
                field.disabled = True

    employee_queryset = accessible_employees(user, entity=entity)
    company_ids = _company_ids(user)
    # scale-guard: allow SG001 — form fields are fixed by schema and never grow with tenant records.
    for field in form.fields.values():
        queryset = getattr(field, "queryset", None)
        if queryset is None:
            continue
        related_model = queryset.model
        related_fields = _model_field_names(related_model)
        if related_model is Employee:
            field.queryset = employee_queryset
        elif related_model is Company:
            field.queryset = Company.objects.filter(pk__in=company_ids)
        elif "company" in related_fields:
            field.queryset = queryset.filter(company_id__in=company_ids)
        elif "employee" in related_fields:
            field.queryset = queryset.filter(employee_id__in=employee_queryset.values("pk"))
        elif "contract" in related_fields:
            field.queryset = queryset.filter(contract__employee_id__in=employee_queryset.values("pk"))
        elif "loan" in related_fields:
            field.queryset = queryset.filter(loan__employee_id__in=employee_queryset.values("pk"))
    return form
