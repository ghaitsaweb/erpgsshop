"""Apply one attendance correction only after an approved, unchanged snapshot."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Attendance, Employee

from .models import AttendanceChangeRequest


ATTENDANCE_CHANGE_TRANSITIONS = {
    "submit": ("draft", "submitted"),
    "approve": ("submitted", "approved"),
    "reject": ("submitted", "rejected"),
    "cancel": ("submitted", "cancelled"),
}


def _attendance_values(attendance):
    if attendance is None:
        return {
            "check_in": None, "check_out": None, "check_in_at": None,
            "check_out_at": None, "from_raw_attendance": False, "status": None,
        }
    return {
        "check_in": attendance.check_in,
        "check_out": attendance.check_out,
        "check_in_at": attendance.check_in_at,
        "check_out_at": attendance.check_out_at,
        "from_raw_attendance": attendance.from_raw_attendance,
        "status": attendance.status,
    }


def _audit_values(values):
    return {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in values.items()
    }


def _proposed_values(change_request, current):
    proposed = {
        "check_in": change_request.requested_check_in if change_request.requested_check_in is not None else current["check_in"],
        "check_out": change_request.requested_check_out if change_request.requested_check_out is not None else current["check_out"],
        "check_in_at": current["check_in_at"],
        "check_out_at": current["check_out_at"],
        "from_raw_attendance": current["from_raw_attendance"],
        "status": Attendance.Status.PRESENT,
    }
    if current["from_raw_attendance"]:
        if not current["check_in_at"] or not current["check_out_at"]:
            raise ValidationError("Raw attendance timestamps are incomplete; this correction cannot be applied safely.")
        if proposed["check_in"] is None or proposed["check_out"] is None:
            raise ValidationError("Raw attendance requires both check-in and check-out times.")
        local_tz = ZoneInfo(settings.TIME_ZONE)
        for field in ("check_in", "check_out"):
            original_at = current[f"{field}_at"]
            local_date = timezone.localtime(original_at, local_tz).date()
            proposed[f"{field}_at"] = timezone.make_aware(
                datetime.combine(local_date, proposed[field]), local_tz
            )
        duration = proposed["check_out_at"] - proposed["check_in_at"]
        if duration <= timedelta(0) or duration > timedelta(days=1):
            raise ValidationError("Corrected raw attendance must have a positive duration of at most 24 hours.")
    return proposed


@transaction.atomic
def transition_attendance_change_request(change_request, action, actor):
    """Transition a request already scoped and row-locked by the caller."""
    transition = ATTENDANCE_CHANGE_TRANSITIONS.get(action)
    if transition is None or change_request.status != transition[0]:
        raise ValidationError("That attendance change action is not available for the current status.")

    if action in {"submit", "approve"}:
        # Locking one employee serializes corrections for the same day without
        # scanning the employee's growing attendance history.
        Employee.objects.select_for_update().get(pk=change_request.employee_id)
        attendance = (
            Attendance.objects.select_for_update()
            .filter(employee_id=change_request.employee_id, attendance_date=change_request.attendance_date)
            .order_by()
            .first()
        )
        current = _attendance_values(attendance)
        if attendance and attendance.status not in {Attendance.Status.PRESENT, Attendance.Status.ABSENT}:
            raise ValidationError("An approved leave or sickness day cannot be changed through attendance correction.")

        if action == "submit":
            if change_request.requested_check_in is None and change_request.requested_check_out is None:
                raise ValidationError("Enter a corrected check-in or check-out time.")
            duplicate = AttendanceChangeRequest.objects.filter(
                employee_id=change_request.employee_id,
                attendance_date=change_request.attendance_date,
                status__in=["draft", "submitted", "approved"],
            ).exclude(pk=change_request.pk).exists()
            if duplicate:
                raise ValidationError("An active attendance correction already exists for this employee and date.")
            proposed = _proposed_values(change_request, current)
            if proposed["check_in"] is None:
                raise ValidationError("A new attendance record needs a check-in time.")
            if proposed == current:
                raise ValidationError("The requested attendance values already match the daily record.")
            change_request.snapshot_captured = True
            change_request.had_attendance = attendance is not None
            change_request.original_check_in = current["check_in"]
            change_request.original_check_out = current["check_out"]
            change_request.original_check_in_at = current["check_in_at"]
            change_request.original_check_out_at = current["check_out_at"]
            change_request.original_from_raw_attendance = current["from_raw_attendance"]
            change_request.original_status = current["status"] or ""
            change_request._workflow_audit_details = {
                "employee_id": change_request.employee_id,
                "attendance_date": change_request.attendance_date.isoformat(),
                "before": _audit_values(current),
                "requested": _audit_values(proposed),
            }
        else:
            if not change_request.snapshot_captured:
                raise ValidationError("This request has no attendance snapshot. Create a new correction request.")
            expected = {
                "check_in": change_request.original_check_in,
                "check_out": change_request.original_check_out,
                "check_in_at": change_request.original_check_in_at,
                "check_out_at": change_request.original_check_out_at,
                "from_raw_attendance": change_request.original_from_raw_attendance,
                "status": change_request.original_status or None,
            }
            if (attendance is not None) != change_request.had_attendance or current != expected:
                raise ValidationError("Attendance changed after submission. Review a new correction request.")
            proposed = _proposed_values(change_request, current)
            if attendance is None:
                _attendance, created = Attendance.objects.get_or_create(
                    employee_id=change_request.employee_id,
                    attendance_date=change_request.attendance_date,
                    defaults={
                        "check_in": proposed["check_in"],
                        "check_out": proposed["check_out"],
                        "status": proposed["status"],
                        "note": f"Approved correction #{change_request.pk}",
                    },
                )
                if not created:
                    raise ValidationError("Attendance changed after submission. Review a new correction request.")
            else:
                attendance.check_in = proposed["check_in"]
                attendance.check_out = proposed["check_out"]
                attendance.check_in_at = proposed["check_in_at"]
                attendance.check_out_at = proposed["check_out_at"]
                attendance.status = proposed["status"]
                attendance.save(update_fields=["check_in", "check_out", "check_in_at", "check_out_at", "status", "updated_at"])
            change_request._workflow_audit_details = {
                "employee_id": change_request.employee_id,
                "attendance_date": change_request.attendance_date.isoformat(),
                "before": _audit_values(current),
                "after": _audit_values(proposed),
            }

    change_request.status = transition[1]
    update_fields = ["status", "updated_at"]
    if action == "submit":
        update_fields.extend([
            "snapshot_captured", "had_attendance", "original_check_in", "original_check_out",
            "original_check_in_at", "original_check_out_at", "original_from_raw_attendance", "original_status",
        ])
    if action in {"approve", "reject"}:
        change_request.approver = actor
        update_fields.append("approver")
    change_request.save(update_fields=update_fields)
    return change_request
