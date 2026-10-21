"""Apply career transitions to employee organization data with audit history."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Employee

from .models import (
    ApprovalStatus,
    CareerTransitionChangeHistory,
    CareerTransitionRequest,
    CareerTransitionType,
    EmploymentContract,
)


EXIT_CATEGORIES = {
    CareerTransitionType.Category.RESIGNATION,
    CareerTransitionType.Category.TERMINATION,
    CareerTransitionType.Category.RETIREMENT,
}


def _display(value):
    if value is None:
        return ""
    return str(value)


@transaction.atomic
def transition_career_request(request_record, action, actor):
    """Advance one request and apply its employee change once on approval."""
    transitions = {
        "submit": ({ApprovalStatus.DRAFT}, ApprovalStatus.SUBMITTED),
        "approve": ({ApprovalStatus.SUBMITTED}, ApprovalStatus.APPROVED),
        "reject": ({ApprovalStatus.SUBMITTED}, ApprovalStatus.REJECTED),
        "cancel": ({ApprovalStatus.DRAFT, ApprovalStatus.SUBMITTED}, ApprovalStatus.CANCELLED),
        "reset": ({ApprovalStatus.REJECTED, ApprovalStatus.CANCELLED}, ApprovalStatus.DRAFT),
    }
    transition = transitions.get(action)
    if transition is None or request_record.status not in transition[0]:
        raise ValidationError("That career transition action is not available for the current status.")

    request_record.full_clean()
    if action != "approve":
        request_record.status = transition[1]
        update_fields = ["status", "updated_at"]
        if action == "reject":
            request_record.approver = actor
            update_fields.append("approver")
        request_record.save(update_fields=update_fields)
        return request_record

    if request_record.applied_at:
        raise ValidationError("This career transition has already been applied.")
    if request_record.effective_date > timezone.localdate():
        raise ValidationError("A career transition cannot be applied before its effective date.")

    employee = Employee.objects.select_for_update(of=("self",)).select_related(
        "branch",
        "department_record",
        "job_position",
        "supervisor",
    ).get(pk=request_record.employee_id)
    category = request_record.transition_type.category
    changes = []
    employee_update_fields = []

    def change(field_name, value, label=None):
        previous = getattr(employee, field_name)
        previous_id = getattr(employee, f"{field_name}_id", None)
        value_id = getattr(value, "pk", None)
        if previous_id == value_id:
            return
        setattr(employee, field_name, value)
        employee_update_fields.append(field_name)
        changes.append((label or field_name, _display(previous), _display(value)))

    if category in EXIT_CATEGORIES:
        if employee.status != Employee.Status.ACTIVE:
            raise ValidationError("Only an active employee can complete an exit transition.")
        changes.extend(
            [
                ("status", employee.status, Employee.Status.INACTIVE),
                ("inactive_date", _display(employee.inactive_date), _display(request_record.effective_date)),
                ("inactive_reason", employee.inactive_reason, request_record.reason),
            ]
        )
        employee.status = Employee.Status.INACTIVE
        employee.inactive_date = request_record.effective_date
        employee.inactive_reason = request_record.reason
        employee_update_fields.extend(["status", "inactive_date", "inactive_reason"])
        terminated_contracts = EmploymentContract.objects.filter(
            employee=employee,
            status=EmploymentContract.Status.ACTIVE,
        ).update(status=EmploymentContract.Status.TERMINATED, updated_at=timezone.now())
    else:
        if employee.status != Employee.Status.ACTIVE:
            raise ValidationError("An inactive employee cannot receive an organization transition.")
        if request_record.target_branch_id:
            change("branch", request_record.target_branch)
        target_department = request_record.target_department
        if request_record.target_job_position_id and target_department is None:
            target_department = request_record.target_job_position.department
        if target_department is not None:
            change("department_record", target_department, "department")
            if employee.department != target_department.name:
                changes.append(("department_name", employee.department, target_department.name))
                employee.department = target_department.name
                employee_update_fields.append("department")
        if request_record.target_job_position_id:
            change("job_position", request_record.target_job_position, "job_position")
            if employee.position != request_record.target_job_position.name:
                changes.append(("position_name", employee.position, request_record.target_job_position.name))
                employee.position = request_record.target_job_position.name
                employee_update_fields.append("position")
        if request_record.target_supervisor_id:
            change("supervisor", request_record.target_supervisor)
        if not employee_update_fields:
            raise ValidationError("The approved transition does not change the employee organization assignment.")
        terminated_contracts = 0

    employee.full_clean()
    employee.save(update_fields=[*dict.fromkeys(employee_update_fields), "updated_at"])
    CareerTransitionChangeHistory.objects.bulk_create(
        [
            CareerTransitionChangeHistory(
                request=request_record,
                field_name=field_name,
                previous_value=previous,
                new_value=new,
                effective_date=request_record.effective_date,
                changed_by=actor,
                note=request_record.reason,
            )
            for field_name, previous, new in changes
        ]
    )

    request_record.status = ApprovalStatus.APPROVED
    request_record.approver = actor
    request_record.applied_by = actor
    request_record.applied_at = timezone.now()
    request_record._workflow_audit_details = {
        "employee_id": employee.pk,
        "changed_fields": [field_name for field_name, _previous, _new in changes],
        "terminated_contract_count": terminated_contracts,
    }
    request_record.save(update_fields=["status", "approver", "applied_by", "applied_at", "updated_at"])
    return request_record
