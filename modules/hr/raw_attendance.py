"""Conservative, single-workday raw punch processing for GSSHOP."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import Attendance, Employee

from .models import AttendanceChangeRequest, RawAttendance, ScheduleAssignment


WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@transaction.atomic
def process_raw_attendance(raw, *, expected_company_id=None):
    """Build one daily record from exactly one in/out pair; never replace a day.

    The employee lock serializes processing with attendance-correction approval.
    A bounded 24-hour work window supports an overnight checkout while refusing
    ambiguous extra punches rather than silently choosing a pair.
    """
    employee = Employee.objects.select_for_update().get(pk=raw.employee_id)
    if expected_company_id is not None and employee.company_id != expected_company_id:
        raise ValidationError("Employee company changed after this punch was selected. Reload the record.")
    current_raw = RawAttendance.objects.select_for_update().get(pk=raw.pk)
    if current_raw.employee_id != employee.pk:
        raise ValidationError("Raw punch employee changed. Reload the record.")
    if current_raw.processed:
        raise ValidationError("This raw punch has already been processed.")

    anchor = current_raw
    if current_raw.event_type == RawAttendance.EventType.CHECK_OUT:
        prior_ins = list(
            RawAttendance.objects.filter(
                employee_id=employee.pk,
                event_type=RawAttendance.EventType.CHECK_IN,
                processed=False,
                captured_at__gte=current_raw.captured_at - timedelta(days=1),
                captured_at__lt=current_raw.captured_at,
            ).order_by("-captured_at", "-pk")[:2]
        )
        if len(prior_ins) != 1:
            raise ValidationError("Exactly one unprocessed check-in must precede this check-out within 24 hours.")
        anchor = prior_ins[0]
    elif current_raw.event_type != RawAttendance.EventType.CHECK_IN:
        raise ValidationError("Unsupported raw punch event type.")

    local_tz = ZoneInfo(settings.TIME_ZONE)
    work_date = timezone.localtime(anchor.captured_at, local_tz).date()
    assignments = list(
        ScheduleAssignment.objects.filter(
            employee_id=employee.pk,
            active=True,
            date_start__lte=work_date,
        )
        .filter(Q(date_end__isnull=True) | Q(date_end__gte=work_date))
        .select_related("schedule__shift")
        .order_by("-date_start", "-pk")[:2]
    )
    if len(assignments) != 1:
        raise ValidationError("Exactly one active schedule assignment is required for this work date.")
    schedule = assignments[0].schedule
    shift = schedule.shift
    if schedule.company_id != employee.company_id or shift.company_id != employee.company_id:
        raise ValidationError("The schedule and shift must belong to the employee company.")
    if not schedule.active or not shift.active:
        raise ValidationError("The assigned schedule and shift must be active.")
    days = [part.strip() for part in schedule.working_days.split(",")]
    if not days or any(day not in WEEKDAYS for day in days) or WEEKDAYS[work_date.weekday()] not in days:
        raise ValidationError("This date is not a valid working day for the assigned schedule.")
    if shift.end_time == shift.start_time:
        raise ValidationError("A 24-hour shift needs an explicit shift duration and cannot be processed yet.")
    overnight = shift.end_time < shift.start_time

    start = timezone.make_aware(datetime.combine(work_date, time.min), local_tz)
    end = (
        anchor.captured_at + timedelta(days=1, microseconds=1)
        if overnight
        else timezone.make_aware(datetime.combine(work_date + timedelta(days=1), time.min), local_tz)
    )
    punches = list(
        RawAttendance.objects.select_for_update()
        .filter(employee_id=employee.pk, processed=False, captured_at__gte=start, captured_at__lt=end)
        .order_by("captured_at", "pk")[:3]
    )
    if len(punches) != 2 or raw.pk not in {punch.pk for punch in punches}:
        raise ValidationError("Exactly one check-in and one check-out punch are required for this day.")
    check_in, check_out = punches
    if (
        check_in.pk != anchor.pk
        or check_in.event_type != RawAttendance.EventType.CHECK_IN
        or check_out.event_type != RawAttendance.EventType.CHECK_OUT
        or check_in.captured_at >= check_out.captured_at
        or check_out.captured_at - check_in.captured_at > timedelta(days=1)
    ):
        raise ValidationError("Punches must be an unprocessed check-in followed by an unprocessed check-out.")
    out_date = timezone.localtime(check_out.captured_at, local_tz).date()
    expected_out_date = work_date + timedelta(days=1) if overnight else work_date
    if out_date != expected_out_date:
        raise ValidationError("Check-out date does not match the assigned day or overnight shift.")
    if Attendance.objects.filter(employee_id=employee.pk, attendance_date=work_date).exists():
        raise ValidationError("Daily attendance already exists; use the correction workflow instead.")
    if AttendanceChangeRequest.objects.filter(
        employee_id=employee.pk, attendance_date=work_date, status="submitted"
    ).exists():
        raise ValidationError("A submitted attendance correction is awaiting approval for this day.")

    try:
        with transaction.atomic():
            attendance = Attendance.objects.create(
                employee_id=employee.pk,
                attendance_date=work_date,
                check_in=timezone.localtime(check_in.captured_at, local_tz).time().replace(tzinfo=None),
                check_out=timezone.localtime(check_out.captured_at, local_tz).time().replace(tzinfo=None),
                check_in_at=check_in.captured_at,
                check_out_at=check_out.captured_at,
                from_raw_attendance=True,
                status=Attendance.Status.PRESENT,
                note=f"Raw punches #{check_in.pk}, #{check_out.pk}",
            )
    except IntegrityError as exc:
        raise ValidationError("Daily attendance was created concurrently. Review the existing record.") from exc
    RawAttendance.objects.filter(pk__in=[check_in.pk, check_out.pk]).update(processed=True, updated_at=timezone.now())
    raw.processed = True
    raw._workflow_audit_details = {
        "employee_id": employee.pk,
        "attendance_date": work_date.isoformat(),
        "attendance_id": attendance.pk,
        "raw_ids": [check_in.pk, check_out.pk],
        "check_in": attendance.check_in.isoformat(),
        "check_out": attendance.check_out.isoformat(),
        "check_in_at": attendance.check_in_at.isoformat(),
        "check_out_at": attendance.check_out_at.isoformat(),
    }
    return attendance
