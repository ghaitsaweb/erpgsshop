"""CRUD registry for detailed HR PRD records not covered by the base screens."""

from django.db import models

from . import models as hr_models
from .forms import EmployeeChangeRequestLineForm


ENTITY_MODELS = {
    "employee-lifecycle-tasks": hr_models.EmployeeLifecycleTask,
    "employee-change-request-lines": hr_models.EmployeeChangeRequestLine,
    "employee-change-request-history": hr_models.EmployeeChangeRequestHistory,
    "employee-change-relation-history": hr_models.EmployeeChangeRelationHistory,
    "employee-address-changes": hr_models.EmployeeAddressChange,
    "employee-emergency-contact-changes": hr_models.EmployeeEmergencyContactChange,
    "employee-bank-account-changes": hr_models.EmployeeBankAccountChange,
    "employee-dependent-changes": hr_models.EmployeeDependentChange,
    "employee-education-changes": hr_models.EmployeeEducationChange,
    "employee-emergency-contacts": hr_models.EmployeeEmergencyContact,
    "employee-bank-accounts": hr_models.EmployeeBankAccount,
    "employee-dependents": hr_models.EmployeeDependent,
    "employee-education": hr_models.EmployeeEducation,
    "employee-id-card-templates": hr_models.EmployeeIDCardTemplate,
    "employee-id-cards": hr_models.EmployeeIDCard,
    "multiple-job-assignments": hr_models.MultipleJobAssignment,
    "organization-configurations": hr_models.OrganizationConfiguration,
    "hr-announcements": hr_models.HRAnnouncement,
    "contract-allowance-history": hr_models.ContractAllowanceHistory,
    "employee-document-bulk-uploads": hr_models.EmployeeDocumentBulkUpload,
    "employee-document-history": hr_models.EmployeeDocumentHistory,
    "attendance-recompute-transactions": hr_models.AttendanceRecomputeTransaction,
    "leave-bulk-operations": hr_models.LeaveBulkOperation,
    "payroll-disbursements": hr_models.PayrollDisbursement,
    "payroll-disbursement-lines": hr_models.PayrollDisbursementLine,
    "payroll-accounting-events": hr_models.PayrollAccountingEvent,
    "employee-loan-offers": hr_models.EmployeeLoanOffer,
    "career-transition-change-history": hr_models.CareerTransitionChangeHistory,
    "training-categories": hr_models.TrainingCategory,
    "face-recognition-profiles": hr_models.FaceRecognitionProfile,
    "expense-product-categories": hr_models.ExpenseProductCategory,
    "hr-workflow-audits": hr_models.HRWorkflowAudit,
}

APPROVAL_WORKFLOW = {
    "submit": {"label": "Submit", "from": ["draft"], "to": "submitted", "primary": True},
    "approve": {"label": "Approve", "from": ["submitted"], "to": "approved", "primary": True},
    "reject": {"label": "Reject", "from": ["submitted"], "to": "rejected"},
    "cancel": {"label": "Cancel", "from": ["draft", "submitted"], "to": "cancelled"},
}


def _build_config(entity, model):
    fields = [
        field
        for field in model._meta.concrete_fields
        if field.editable and not field.auto_created and field.name not in {"id", "created_at", "updated_at"}
    ]
    display_fields = [field for field in fields if not isinstance(field, (models.TextField, models.JSONField))][:5]
    status_field = next((field for field in fields if field.name == "status"), None)
    if status_field and status_field not in display_fields:
        display_fields.append(status_field)
    config = {
        "model": model,
        "title": entity.replace("-", " ").title(),
        "singular": str(model._meta.verbose_name).title(),
        "columns": [(field.name, str(field.verbose_name).title()) for field in display_fields],
        "search": [
            field.name
            for field in fields
            if isinstance(field, (models.CharField, models.TextField, models.EmailField, models.URLField))
        ],
        "form_fields": [field.name for field in fields],
        "select_related": [field.name for field in fields if field.many_to_one or field.one_to_one],
    }
    if entity == "leave-bulk-operations":
        config["workflow"] = APPROVAL_WORKFLOW
        config["form_fields"] = [field for field in config["form_fields"] if field != "status"]
    if entity in {
        "employee-change-request-history",
        "employee-change-relation-history",
        "contract-allowance-history",
        "employee-document-history",
        "hr-workflow-audits",
        "payroll-accounting-events",
        "payroll-disbursement-lines",
    }:
        config.update({"creatable": False, "editable": False, "deletable": False})
    return config


HR_DETAIL_ENTITY_CONFIG = {entity: _build_config(entity, model) for entity, model in ENTITY_MODELS.items()}

HR_DETAIL_ENTITY_CONFIG["employee-lifecycle-tasks"].update({
    "title": "Employee Lifecycle Checklist Tasks",
    "singular": "Lifecycle Checklist Task",
    "columns": [
        ("name", "Task"), ("onboarding", "Onboarding"),
        ("offboarding", "Offboarding"), ("requires_evidence", "Evidence Required"),
        ("evidence_document", "Evidence Document"), ("status", "Status"),
    ],
    "select_related": ["onboarding__employee", "offboarding__employee", "evidence_document"],
    "form_fields": [],
    "creatable": False,
    "deletable": False,
    "workflow_locked_statuses": set(hr_models.EmployeeLifecycleTask.Status.values),
    "workflow": {
        "complete": {
            "label": "Complete Checklist Task",
            "from": [hr_models.EmployeeLifecycleTask.Status.PENDING],
            "to": hr_models.EmployeeLifecycleTask.Status.COMPLETED,
            "primary": True,
        },
    },
})

HR_DETAIL_ENTITY_CONFIG["payroll-disbursements"].update({
    "creatable": False,
    "deletable": False,
    "workflow_locked_statuses": set(hr_models.PayrollDisbursement.Status.values),
    "workflow": {
        "confirm-payment": {
            "label": "Confirm Bank Payment",
            "from": [hr_models.PayrollDisbursement.Status.GENERATED],
            "to": hr_models.PayrollDisbursement.Status.COMPLETED,
            "primary": True,
        },
    },
    "columns": [
        ("reference", "Reference"), ("payroll_run", "Payroll Run"),
        ("amount", "Amount"), ("prepared_count", "Payslips"),
        ("payment_reference", "Bank Reference"), ("status", "Status"),
    ],
    "select_related": ["payroll_run"],
})

HR_DETAIL_ENTITY_CONFIG["employee-change-request-lines"].update(
    {
        "title": "Employee Change Request Details",
        "singular": "Employee Change Request Detail",
        "columns": [
            ("request", "Request"),
            ("field_name", "Field"),
            ("current_value", "Current Value"),
            ("requested_value", "Requested Value"),
        ],
        "form": EmployeeChangeRequestLineForm,
        "select_related": ["request", "request__employee"],
        "parent_request_filter": True,
    }
)
HR_DETAIL_ENTITY_CONFIG["employee-change-request-history"].update(
    {
        "title": "Employee Change Request History",
        "singular": "Employee Change Request History",
        "columns": [
            ("request", "Request"),
            ("field_label", "Field"),
            ("value_before", "Before"),
            ("value_after", "After"),
            ("applied_at", "Applied At"),
        ],
        "select_related": ["request", "request__employee", "submitted_by"],
        "parent_request_filter": True,
    }
)

for relation_entity in (
    "employee-address-changes",
    "employee-emergency-contact-changes",
    "employee-bank-account-changes",
    "employee-dependent-changes",
    "employee-education-changes",
):
    HR_DETAIL_ENTITY_CONFIG[relation_entity]["parent_request_filter"] = True

HR_DETAIL_ENTITY_CONFIG["employee-change-relation-history"].update(
    {
        "title": "Employee Relationship Change History",
        "singular": "Employee Relationship Change History",
        "columns": [
            ("request", "Request"),
            ("relation_type", "Relationship"),
            ("submitted_by", "Submitted By"),
            ("applied_at", "Applied At"),
        ],
        "select_related": ["request", "request__employee", "submitted_by"],
        "parent_request_filter": True,
    }
)

HR_DETAIL_ENTITY_CONFIG["payroll-accounting-events"]["workflow"] = {
    "post": {"label": "Post Accounting", "from": ["pending", "failed"], "to": "posted", "primary": True, "service_controls_state": True},
}
