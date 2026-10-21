"""Apply typed employee profile changes through an auditable approval."""

from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Employee

from .employee_change_fields import CHANGE_REQUEST_FIELDS
from .models import EmployeeChangeRequestHistory, EmployeeChangeRequestLine, HRSetting


CHANGE_REQUEST_TRANSITIONS = {
    "submit": ("draft", "submitted"),
    "approve": ("submitted", "approved"),
    "reject": ("submitted", "rejected"),
    "cancel": ("draft", "cancelled"),
}


def serialize_employee_value(value):
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def clean_requested_value(employee, field_name, raw_value):
    if field_name not in CHANGE_REQUEST_FIELDS:
        raise ValidationError("This employee field is not supported by change requests.")
    model_field = Employee._meta.get_field(field_name)
    return model_field.clean(raw_value, employee)


def _collect_draft_changes(change_request):
    rows = []
    if change_request.field_name:
        rows.append(
            (
                change_request.field_name,
                change_request.current_value,
                change_request.requested_value,
            )
        )
    rows.extend(
        EmployeeChangeRequestLine.objects.select_for_update()
        .filter(request=change_request)
        .order_by("pk")
        .values_list("field_name", "current_value", "requested_value")
    )
    seen = set()
    for field_name, _current, _requested in rows:
        if field_name in seen:
            raise ValidationError(f"The {CHANGE_REQUEST_FIELDS.get(field_name, field_name)} change is duplicated.")
        seen.add(field_name)
    return rows


def _validated_changes(employee, rows):
    changes = []
    for field_name, current_value, requested_value in rows:
        typed_requested = clean_requested_value(employee, field_name, requested_value)
        actual_value = serialize_employee_value(getattr(employee, field_name))
        if actual_value != current_value:
            raise ValidationError(
                f"The {CHANGE_REQUEST_FIELDS[field_name]} value changed since this request was saved. Review a new draft."
            )
        normalized_requested = serialize_employee_value(typed_requested)
        if normalized_requested == actual_value:
            continue
        changes.append((field_name, actual_value, normalized_requested, typed_requested))
    if not changes:
        raise ValidationError("No employee profile differences were detected.")
    return changes


@transaction.atomic
def transition_employee_change_request(change_request, action, actor):
    """Transition a locked request and apply its values exactly on approval.

    The caller scopes the request by the actor's access rights. Both the request
    and its employee are locked so a concurrent approval cannot apply stale data.
    """
    transition = CHANGE_REQUEST_TRANSITIONS.get(action)
    if transition is None or change_request.status != transition[0]:
        raise ValidationError("That employee change action is not available for the current status.")

    employee = None
    if action in {"submit", "approve"}:
        employee = Employee.objects.select_for_update().select_related(
            "branch",
            "department_record",
            "job_position",
            "work_location",
            "grade",
            "supervisor",
        ).get(pk=change_request.employee_id)

    if action == "submit":
        attachment_required = (
            HRSetting.objects.filter(company_id=employee.company_id)
            .values_list("employee_change_request_attachment", flat=True)
            .first()
            or False
        )
        if attachment_required and not change_request.attachment_content:
            raise ValidationError("Attachment is required for Employee Change Request.")
        changes = _validated_changes(employee, _collect_draft_changes(change_request))
        EmployeeChangeRequestHistory.objects.bulk_create(
            [
                EmployeeChangeRequestHistory(
                    request=change_request,
                    field_name=field_name,
                    field_label=CHANGE_REQUEST_FIELDS[field_name],
                    value_before=value_before,
                    value_after=value_after,
                    submitted_by=actor,
                )
                for field_name, value_before, value_after, _typed_value in changes
            ]
        )
        change_request.submitted_at = timezone.now()

    if action == "approve":
        if change_request.effective_date > timezone.localdate():
            raise ValidationError("This change cannot be applied before its effective date.")
        histories = list(
            EmployeeChangeRequestHistory.objects.select_for_update()
            .filter(request=change_request)
            .order_by("pk")
        )
        legacy_snapshot_created = not histories
        if legacy_snapshot_created:
            # Requests submitted before immutable snapshots were introduced must
            # remain approvable after an additive deployment. The approving actor
            # is recorded as the legacy snapshot owner because the old schema did
            # not retain the original submitter.
            changes = _validated_changes(employee, _collect_draft_changes(change_request))
            histories = EmployeeChangeRequestHistory.objects.bulk_create(
                [
                    EmployeeChangeRequestHistory(
                        request=change_request,
                        field_name=field_name,
                        field_label=CHANGE_REQUEST_FIELDS[field_name],
                        value_before=value_before,
                        value_after=value_after,
                        submitted_by=actor,
                    )
                    for field_name, value_before, value_after, _typed_value in changes
                ]
            )
        update_fields = []
        for history in histories:
            if history.field_name not in CHANGE_REQUEST_FIELDS:
                raise ValidationError("This employee field is not supported by change requests.")
            actual_value = serialize_employee_value(getattr(employee, history.field_name))
            if actual_value != history.value_before:
                raise ValidationError(
                    f"The {history.field_label} value changed after submission. Create a new request."
                )
            typed_value = clean_requested_value(employee, history.field_name, history.value_after)
            setattr(employee, history.field_name, typed_value)
            update_fields.append(history.field_name)
        employee.full_clean()
        employee.save(update_fields=[*update_fields, "updated_at"])
        applied_at = timezone.now()
        EmployeeChangeRequestHistory.objects.filter(request=change_request).update(applied_at=applied_at)
        change_request.approved_at = applied_at
        change_request._workflow_audit_details = {
            "employee_id": employee.pk,
            "changed_fields": update_fields,
            "attachment_name": change_request.attachment_name,
            "legacy_snapshot_created": legacy_snapshot_created,
        }

    change_request.status = transition[1]
    update_fields = ["status", "updated_at"]
    if action == "submit":
        update_fields.append("submitted_at")
    if action in {"approve", "reject"}:
        change_request.approver = actor
        update_fields.append("approver")
    if action == "approve":
        update_fields.append("approved_at")
    change_request.save(update_fields=update_fields)
    return change_request
