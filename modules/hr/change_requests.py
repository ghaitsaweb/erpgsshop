"""Apply typed employee profile changes through an auditable approval."""

from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Employee

from .employee_change_fields import CHANGE_REQUEST_FIELDS
from .models import (
    EmployeeAddress,
    EmployeeAddressChange,
    EmployeeBankAccount,
    EmployeeBankAccountChange,
    EmployeeChangeRelationHistory,
    EmployeeChangeRequestHistory,
    EmployeeChangeRequestLine,
    EmployeeDependent,
    EmployeeDependentChange,
    EmployeeEducation,
    EmployeeEducationChange,
    EmployeeEmergencyContact,
    EmployeeEmergencyContactChange,
    HRSetting,
)


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


def _validated_changes(employee, rows, *, allow_empty=False):
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
    if not changes and not allow_empty:
        raise ValidationError("No employee profile differences were detected.")
    return changes


def _date_value(value):
    return value.isoformat() if value else None


def _decimal_value(value):
    return format(value, "f") if value is not None else None


RELATION_SPECS = (
    {
        "type": EmployeeChangeRelationHistory.RelationType.ADDRESS,
        "master": EmployeeAddress,
        "draft": EmployeeAddressChange,
        "source": "source_address_id",
        "serialize_master": lambda row: {
            "source_id": row.pk,
            "address_type": row.address_type,
            "address": row.address,
            "city": row.city,
            "province": row.province,
            "postal_code": row.postal_code,
            "phone": row.phone,
            "primary": row.primary,
        },
        "serialize_draft": lambda row: {
            "source_id": row.source_address_id,
            "address_type": row.address_type,
            "address": row.address,
            "city": row.city,
            "province": row.province,
            "postal_code": row.postal_code,
            "phone": row.phone,
            "primary": row.primary,
        },
        "draft_values": lambda row: {
            "source_address_id": row.pk,
            "address_type": row.address_type,
            "address": row.address,
            "city": row.city,
            "province": row.province,
            "postal_code": row.postal_code,
            "phone": row.phone,
            "primary": row.primary,
        },
        "master_values": lambda item: {
            "address_type": item["address_type"],
            "address": item["address"],
            "city": item["city"],
            "province": item["province"],
            "postal_code": item["postal_code"],
            "phone": item["phone"],
            "primary": item.get("primary", False),
        },
    },
    {
        "type": EmployeeChangeRelationHistory.RelationType.EMERGENCY_CONTACT,
        "master": EmployeeEmergencyContact,
        "draft": EmployeeEmergencyContactChange,
        "source": "source_contact_id",
        "serialize_master": lambda row: {
            "source_id": row.pk,
            "name": row.name,
            "phone": row.phone,
            "relationship": row.relationship,
            "address": row.address,
        },
        "serialize_draft": lambda row: {
            "source_id": row.source_contact_id,
            "name": row.name,
            "phone": row.phone,
            "relationship": row.relationship,
            "address": row.address,
        },
        "draft_values": lambda row: {
            "source_contact_id": row.pk,
            "name": row.name,
            "phone": row.phone,
            "relationship": row.relationship,
            "address": row.address,
        },
        "master_values": lambda item: {
            "name": item["name"],
            "phone": item["phone"],
            "relationship": item["relationship"],
            "address": item["address"],
        },
    },
    {
        "type": EmployeeChangeRelationHistory.RelationType.BANK_ACCOUNT,
        "master": EmployeeBankAccount,
        "draft": EmployeeBankAccountChange,
        "source": "source_account_id",
        "serialize_master": lambda row: {
            "source_id": row.pk,
            "bank_name": row.bank_name,
            "bic": row.bic,
            "bank_unit": row.bank_unit,
            "account_number": row.account_number,
            "account_holder": row.account_holder,
            "primary": row.primary,
        },
        "serialize_draft": lambda row: {
            "source_id": row.source_account_id,
            "bank_name": row.bank_name,
            "bic": row.bic,
            "bank_unit": row.bank_unit,
            "account_number": row.account_number,
            "account_holder": row.account_holder,
            "primary": row.primary,
        },
        "draft_values": lambda row: {
            "source_account_id": row.pk,
            "bank_name": row.bank_name,
            "bic": row.bic,
            "bank_unit": row.bank_unit,
            "account_number": row.account_number,
            "account_holder": row.account_holder,
            "primary": row.primary,
        },
        "master_values": lambda item: {
            "bank_name": item["bank_name"],
            "bic": item["bic"],
            "bank_unit": item["bank_unit"],
            "account_number": item["account_number"],
            "account_holder": item["account_holder"],
            "primary": item["primary"],
        },
    },
    {
        "type": EmployeeChangeRelationHistory.RelationType.DEPENDENT,
        "master": EmployeeDependent,
        "draft": EmployeeDependentChange,
        "source": "source_dependent_id",
        "serialize_master": lambda row: {
            "source_id": row.pk,
            "name": row.name,
            "relationship": row.relationship,
            "gender": row.gender,
            "contact": row.contact,
            "birth_date": _date_value(row.birth_date),
            "age": row.age,
            "education": row.education,
            "occupation": row.occupation,
        },
        "serialize_draft": lambda row: {
            "source_id": row.source_dependent_id,
            "name": row.name,
            "relationship": row.relationship,
            "gender": row.gender,
            "contact": row.contact,
            "birth_date": _date_value(row.birth_date),
            "age": row.age,
            "education": row.education,
            "occupation": row.occupation,
        },
        "draft_values": lambda row: {
            "source_dependent_id": row.pk,
            "name": row.name,
            "relationship": row.relationship,
            "gender": row.gender,
            "contact": row.contact,
            "birth_date": row.birth_date,
            "age": row.age,
            "education": row.education,
            "occupation": row.occupation,
        },
        "master_values": lambda item: {
            "name": item["name"],
            "relationship": item["relationship"],
            "gender": item["gender"],
            "contact": item["contact"],
            "birth_date": date.fromisoformat(item["birth_date"]) if item.get("birth_date") else None,
            "age": item.get("age"),
            "education": item["education"],
            "occupation": item["occupation"],
        },
    },
    {
        "type": EmployeeChangeRelationHistory.RelationType.EDUCATION,
        "master": EmployeeEducation,
        "draft": EmployeeEducationChange,
        "source": "source_education_id",
        "serialize_master": lambda row: {
            "source_id": row.pk,
            "certificate": row.certificate,
            "study_field": row.study_field,
            "school": row.school,
            "city": row.city,
            "graduation_year": row.graduation_year,
            "gpa": _decimal_value(row.gpa),
        },
        "serialize_draft": lambda row: {
            "source_id": row.source_education_id,
            "certificate": row.certificate,
            "study_field": row.study_field,
            "school": row.school,
            "city": row.city,
            "graduation_year": row.graduation_year,
            "gpa": _decimal_value(row.gpa),
        },
        "draft_values": lambda row: {
            "source_education_id": row.pk,
            "certificate": row.certificate,
            "study_field": row.study_field,
            "school": row.school,
            "city": row.city,
            "graduation_year": row.graduation_year,
            "gpa": row.gpa,
        },
        "master_values": lambda item: {
            "certificate": item["certificate"],
            "study_field": item["study_field"],
            "school": item["school"],
            "city": item["city"],
            "graduation_year": item["graduation_year"],
            "gpa": Decimal(item["gpa"]) if item.get("gpa") is not None else None,
        },
    },
)


def _sorted_payload(rows, serializer):
    import json

    payload = [serializer(row) for row in rows]
    return sorted(payload, key=lambda item: (item.get("source_id") is None, item.get("source_id") or 0, json.dumps(item, sort_keys=True)))


@transaction.atomic
def initialize_employee_relation_drafts(change_request):
    """Clone current employee relation rows into a new draft in fixed queries."""
    if change_request.status != "draft":
        raise ValidationError("Employee relationship details can only be initialized on a draft request.")
    for spec in RELATION_SPECS:
        if spec["draft"].objects.filter(request=change_request).exists():
            continue
        current = list(spec["master"].objects.filter(employee_id=change_request.employee_id).order_by("pk"))
        spec["draft"].objects.bulk_create(
            [spec["draft"](request=change_request, **spec["draft_values"](row)) for row in current]
        )


def _capture_relation_changes(change_request, actor):
    histories = []
    for spec in RELATION_SPECS:
        current = list(spec["master"].objects.filter(employee_id=change_request.employee_id).order_by("pk"))
        draft = list(spec["draft"].objects.select_for_update().filter(request=change_request).order_by("pk"))
        before = _sorted_payload(current, spec["serialize_master"])
        after = _sorted_payload(draft, spec["serialize_draft"])
        if before != after:
            histories.append(
                EmployeeChangeRelationHistory(
                    request=change_request,
                    relation_type=spec["type"],
                    value_before=before,
                    value_after=after,
                    submitted_by=actor,
                )
            )
    if histories:
        EmployeeChangeRelationHistory.objects.bulk_create(histories)
    return histories


def _apply_relation_history(change_request, histories):
    history_by_type = {history.relation_type: history for history in histories}
    for spec in RELATION_SPECS:
        history = history_by_type.get(spec["type"])
        if history is None:
            continue
        current_rows = list(
            spec["master"].objects.select_for_update().filter(employee_id=change_request.employee_id).order_by("pk")
        )
        current_payload = _sorted_payload(current_rows, spec["serialize_master"])
        if current_payload != history.value_before:
            raise ValidationError(
                f"The employee's {history.get_relation_type_display().lower()} changed after submission. Create a new request."
            )
        current_by_id = {row.pk: row for row in current_rows}
        retained_ids = set()
        updates = []
        additions = []
        update_fields = None
        for item in history.value_after:
            values = spec["master_values"](item)
            source_id = item.get("source_id")
            row = current_by_id.get(source_id)
            if row is None:
                additions.append(spec["master"](employee_id=change_request.employee_id, **values))
                continue
            retained_ids.add(row.pk)
            for field_name, value in values.items():
                setattr(row, field_name, value)
            updates.append(row)
            update_fields = list(values)
        if updates:
            spec["master"].objects.bulk_update(updates, update_fields)
        if additions:
            spec["master"].objects.bulk_create(additions)
        removed_ids = set(current_by_id) - retained_ids
        if removed_ids:
            spec["master"].objects.filter(employee_id=change_request.employee_id, pk__in=removed_ids).delete()


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
        employee = Employee.objects.select_for_update(of=("self",)).select_related(
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
        changes = _validated_changes(employee, _collect_draft_changes(change_request), allow_empty=True)
        relation_histories = _capture_relation_changes(change_request, actor)
        if not changes and not relation_histories:
            raise ValidationError("No employee profile differences were detected.")
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
        relation_histories = list(
            EmployeeChangeRelationHistory.objects.select_for_update()
            .filter(request=change_request)
            .order_by("pk")
        )
        legacy_snapshot_created = not histories and not relation_histories
        if legacy_snapshot_created:
            # Requests submitted before immutable snapshots were introduced must
            # remain approvable after an additive deployment. The approving actor
            # is recorded as the legacy snapshot owner because the old schema did
            # not retain the original submitter.
            changes = _validated_changes(employee, _collect_draft_changes(change_request), allow_empty=True)
            relation_histories = _capture_relation_changes(change_request, actor)
            if not changes and not relation_histories:
                raise ValidationError("No employee profile differences were detected.")
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
        _apply_relation_history(change_request, relation_histories)
        employee.full_clean()
        employee.save(update_fields=[*update_fields, "updated_at"])
        applied_at = timezone.now()
        EmployeeChangeRequestHistory.objects.filter(request=change_request).update(applied_at=applied_at)
        EmployeeChangeRelationHistory.objects.filter(request=change_request).update(applied_at=applied_at)
        change_request.approved_at = applied_at
        change_request._workflow_audit_details = {
            "employee_id": employee.pk,
            "changed_fields": update_fields,
            "changed_relations": [history.relation_type for history in relation_histories],
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
