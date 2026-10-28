"""Company-scoped leave duration and allocation lifecycle.

The employee row serializes requests for one employee. All calendar and
allocation reads are bounded by that employee and the requested date range.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import Employee

from .leave_buckets import locked_leave_buckets
from .models import (
    CompanyHoliday,
    EmploymentContract,
    LeaveAllocation,
    LeaveBlackoutPeriod,
    LeaveBucket,
    LeaveBucketConsumption,
    LeaveEncashmentBucket,
    LeaveMovement,
    LeaveRequest,
    ScheduleAssignment,
)


WEEKDAYS = {name: index for index, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))}
MAX_REQUEST_DAYS = 366


def _validate_leave_policy(request, by_year):
    """Apply the HMX leave-type rules before any balance is reserved."""
    leave_type = request.leave_type_record
    employee = request.employee
    requested_days = sum(by_year.values(), Decimal("0"))
    calendar_span = (request.end_date - request.start_date).days + 1
    today = timezone.localdate()

    if leave_type.eligible_gender != leave_type.EligibleGender.ALL and employee.gender != leave_type.eligible_gender:
        raise ValidationError("The employee is not eligible for this leave type's gender policy.")
    joining = employee.join_date
    if joining and request.start_date < joining:
        raise ValidationError("Leave cannot begin before the employee joining date.")
    minimum = leave_type.min_tenure_value
    if minimum:
        if not joining:
            raise ValidationError("A joining date is required for this leave type.")
        if leave_type.min_tenure_unit == leave_type.TenureUnit.DAYS:
            tenure = (request.start_date - joining).days
        elif leave_type.min_tenure_unit == leave_type.TenureUnit.MONTHS:
            tenure = (request.start_date.year - joining.year) * 12 + request.start_date.month - joining.month
            tenure -= request.start_date.day < joining.day
        else:
            tenure = request.start_date.year - joining.year
            tenure -= (request.start_date.month, request.start_date.day) < (joining.month, joining.day)
        if tenure < minimum:
            raise ValidationError("The employee has not met this leave type's minimum service period.")
    if leave_type.max_days_per_request and requested_days > leave_type.max_days_per_request:
        raise ValidationError("This request exceeds the leave type's maximum days per request.")
    if leave_type.max_consecutive_days and calendar_span > leave_type.max_consecutive_days:
        raise ValidationError("This request exceeds the leave type's consecutive calendar-day limit.")
    if request.start_date < today:
        past_days = (today - request.start_date).days
        if not leave_type.allow_past_date or (
            leave_type.max_past_days and past_days > leave_type.max_past_days
        ):
            raise ValidationError("Past-dated leave is not allowed by this leave type.")
    elif leave_type.notice_flat_days and (request.start_date - today).days < leave_type.notice_flat_days:
        raise ValidationError("This leave type requires more advance notice.")
    if leave_type.cooling_period_days:
        previous_start = request.start_date - timedelta(days=leave_type.cooling_period_days)
        if LeaveRequest.objects.filter(
            employee_id=request.employee_id,
            leave_type_record_id=leave_type.pk,
            status__in=[LeaveRequest.Status.SUBMITTED, LeaveRequest.Status.APPROVED],
            end_date__gte=previous_start,
            end_date__lt=request.start_date,
        ).exclude(pk=request.pk).exists():
            raise ValidationError("The cooling period after a previous leave request has not ended.")
    if LeaveBlackoutPeriod.objects.filter(
        leave_type_id=leave_type.pk,
        active=True,
        date_start__lte=request.end_date,
        date_end__gte=request.start_date,
    ).exists():
        raise ValidationError("The requested dates overlap a leave blackout period.")
    maximum_bytes = leave_type.max_file_size_mb * 1024 * 1024
    if request.attachment_content and len(request.attachment_content) > maximum_bytes:
        raise ValidationError(f"Evidence cannot exceed {leave_type.max_file_size_mb} MB.")
    attachment_required = leave_type.requires_attachment and (
        leave_type.attachment_trigger != "exceed_n_days"
        or requested_days > leave_type.attachment_exceed_n_days
    )
    if attachment_required and not request.attachment_content:
        raise ValidationError("Evidence is required for this leave type and duration.")


def _duration_by_year(request):
    if not request.start_date or not request.end_date or request.end_date < request.start_date:
        raise ValidationError("Enter a valid leave date range.")
    span = (request.end_date - request.start_date).days + 1
    if span > MAX_REQUEST_DAYS:
        raise ValidationError("A leave request may cover at most 366 calendar days.")
    if request.portion == LeaveRequest.Portion.HALF and span != 1:
        raise ValidationError("A half-day request must cover one date.")

    by_year = defaultdict(lambda: Decimal("0"))
    if request.leave_type_record.day_count_by == request.leave_type_record.DayCountBy.CALENDAR_DAY:
        for offset in range(span):
            day = request.start_date + timedelta(days=offset)
            by_year[day.year] += Decimal("0.5") if request.portion == LeaveRequest.Portion.HALF else Decimal("1")
        return dict(by_year)

    assignments = list(
        ScheduleAssignment.objects.filter(
            employee_id=request.employee_id,
            active=True,
            date_start__lte=request.end_date,
        )
        .filter(Q(date_end__isnull=True) | Q(date_end__gte=request.start_date))
        .select_related("schedule")
        .order_by("date_start", "pk")[: MAX_REQUEST_DAYS + 1]
    )
    if len(assignments) > MAX_REQUEST_DAYS:
        raise ValidationError("The employee has too many schedule assignments in this leave period.")
    for assignment in assignments:
        if assignment.schedule.company_id != request.employee.company_id or not assignment.schedule.active:
            raise ValidationError("The employee schedule must be active and belong to the employee company.")

    fixed_holidays = set(
        CompanyHoliday.objects.filter(
            company_id=request.employee.company_id,
            recurring=False,
            holiday_date__range=(request.start_date, request.end_date),
        ).values_list("holiday_date", flat=True)
    )
    months = {(request.start_date + timedelta(days=offset)).month for offset in range(span)}
    recurring_holidays = {
        (holiday_date.month, holiday_date.day)
        for holiday_date in CompanyHoliday.objects.filter(
            company_id=request.employee.company_id,
            recurring=True,
            holiday_date__month__in=months,
        ).values_list("holiday_date", flat=True).iterator(chunk_size=256)
    }

    for offset in range(span):
        day = request.start_date + timedelta(days=offset)
        active = [
            assignment for assignment in assignments
            if assignment.date_start <= day and (assignment.date_end is None or assignment.date_end >= day)
        ]
        if len(active) != 1:
            raise ValidationError(f"Exactly one active work schedule is required for {day}.")
        schedule_days = {
            WEEKDAYS[name.strip()]
            for name in active[0].schedule.working_days.split(",")
            if name.strip() in WEEKDAYS
        }
        if day.weekday() in schedule_days and day not in fixed_holidays and (day.month, day.day) not in recurring_holidays:
            by_year[day.year] += Decimal("0.5") if request.portion == LeaveRequest.Portion.HALF else Decimal("1")

    if not by_year:
        raise ValidationError("The selected range contains no eligible work days.")
    return dict(by_year)


def _reserve(request, by_year):
    allocations, buckets = locked_leave_buckets(
        request.employee_id,
        request.leave_type_record_id,
        request.start_date,
        request.end_date,
        by_year,
    )
    allocation_by_id = {allocation.pk: allocation for allocation in allocations}
    reserved_by_bucket = {
        row["bucket_id"]: row["total"]
        for row in LeaveEncashmentBucket.objects.filter(
            bucket_id__in=[bucket.pk for bucket in buckets],
            status=LeaveEncashmentBucket.Status.RESERVED,
        ).values("bucket_id").annotate(total=Sum("days"))
    }

    taken_by_bucket = defaultdict(lambda: Decimal("0"))
    taken_by_allocation = defaultdict(lambda: Decimal("0"))
    bucket_by_id = {bucket.pk: bucket for bucket in buckets}
    for year, days in sorted(by_year.items()):
        segment_start = max(request.start_date, date(year, 1, 1))
        segment_end = min(request.end_date, date(year, 12, 31))
        remaining = days
        for bucket in buckets:
            if remaining <= 0:
                break
            if (
                bucket.status == LeaveBucket.Status.EXPIRED
                or bucket.start_valid_date > segment_start
                or bucket.expiry_date < segment_end
            ):
                continue
            available = bucket.remaining_days - reserved_by_bucket.get(bucket.pk, Decimal("0"))
            if available <= 0:
                continue
            take = min(available, remaining)
            bucket.consumed_days += take
            bucket.status = (
                LeaveBucket.Status.EXHAUSTED
                if bucket.remaining_days == 0
                else LeaveBucket.Status.ACTIVE
            )
            taken_by_bucket[bucket.pk] += take
            taken_by_allocation[bucket.allocation_id] += take
            remaining -= take
        if remaining:
            raise ValidationError(f"Insufficient date-valid leave balance for {year}: {remaining} days short.")

    now = timezone.now()
    for allocation_id, days in taken_by_allocation.items():
        allocation = allocation_by_id[allocation_id]
        if allocation.used_days + days > allocation.allocated_days:
            raise ValidationError("The allocation balance and bucket ledger do not agree.")
        allocation.used_days += days
        allocation.updated_at = now
    for bucket_id in taken_by_bucket:
        bucket_by_id[bucket_id].updated_at = now
    LeaveAllocation.objects.bulk_update(
        [allocation_by_id[pk] for pk in taken_by_allocation], ["used_days", "updated_at"]
    )
    LeaveBucket.objects.bulk_update(
        [bucket_by_id[pk] for pk in taken_by_bucket], ["consumed_days", "status", "updated_at"]
    )
    LeaveBucketConsumption.objects.bulk_create([
        LeaveBucketConsumption(leave_request=request, bucket_id=bucket_id, days=days)
        for bucket_id, days in taken_by_bucket.items()
    ])
    LeaveMovement.objects.bulk_create([
        LeaveMovement(
            employee_id=request.employee_id,
            leave_type_id=request.leave_type_record_id,
            leave_request=request,
            allocation_id=allocation_id,
            movement_date=request.start_date,
            movement_type=LeaveMovement.MovementType.USAGE,
            days=-days,
            reference=f"LEAVE-{request.pk}",
            notes=f"Reserved {days} days from allocation {allocation_id}",
        )
        for allocation_id, days in sorted(taken_by_allocation.items())
    ])


def _release(request):
    debits = list(
        LeaveMovement.objects.filter(
            leave_request=request,
            movement_type=LeaveMovement.MovementType.USAGE,
        ).order_by("allocation_id")
    )
    if not debits or sum((-movement.days for movement in debits), Decimal("0")) != request.days:
        raise ValidationError("The leave reservation is missing or incomplete; contact HR before changing this request.")
    if LeaveMovement.objects.filter(
        leave_request=request,
        movement_type=LeaveMovement.MovementType.REVERSAL,
    ).exists():
        raise ValidationError("The leave reservation has already been reversed.")
    consumptions = list(
        LeaveBucketConsumption.objects.select_for_update().filter(
            leave_request=request,
        ).order_by("bucket_id")
    )
    if consumptions and (
        any(consumption.reversed_at for consumption in consumptions)
        or sum((consumption.days for consumption in consumptions), Decimal("0")) != request.days
    ):
        raise ValidationError("The bucket reservation is incomplete or already reversed.")
    allocations = {
        allocation.pk: allocation
        for allocation in LeaveAllocation.objects.select_for_update().filter(
            pk__in=[movement.allocation_id for movement in debits]
        )
    }
    buckets = {
        bucket.pk: bucket
        for bucket in LeaveBucket.objects.select_for_update().filter(
            pk__in=[consumption.bucket_id for consumption in consumptions]
        )
    }
    protected_by_bucket = {
        row["bucket_id"]: row["total"]
        for row in LeaveEncashmentBucket.objects.filter(
            bucket_id__in=buckets,
            status=LeaveEncashmentBucket.Status.RESERVED,
        ).values("bucket_id").annotate(total=Sum("days"))
    }
    released_by_allocation = defaultdict(lambda: Decimal("0"))
    for movement in debits:
        released_by_allocation[movement.allocation_id] += -movement.days
    for allocation_id, days in released_by_allocation.items():
        allocation = allocations.get(allocation_id)
        if allocation is None or allocation.used_days < days:
            raise ValidationError("The allocated leave balance changed; the reservation cannot be reversed safely.")
        allocation.used_days -= days
        allocation.updated_at = timezone.now()
    for consumption in consumptions:
        bucket = buckets.get(consumption.bucket_id)
        if bucket is None or bucket.consumed_days < consumption.days:
            raise ValidationError("The original leave bucket cannot be reversed safely.")
        bucket.consumed_days -= consumption.days
        if bucket.status == LeaveBucket.Status.EXPIRED or bucket.expiry_date < timezone.localdate():
            bucket.forfeited_days = max(
                bucket.original_days - bucket.consumed_days - protected_by_bucket.get(bucket.pk, Decimal("0")),
                Decimal("0"),
            )
            bucket.status = LeaveBucket.Status.EXPIRED
        else:
            bucket.status = LeaveBucket.Status.ACTIVE if bucket.remaining_days > 0 else LeaveBucket.Status.EXHAUSTED
        bucket.updated_at = timezone.now()
        consumption.reversed_at = bucket.updated_at
    # Requests submitted before bucket tracking retain their allocation debit.
    # Reconcile the auto-created base bucket when another request has since used it.
    if not consumptions:
        legacy_buckets = list(
            LeaveBucket.objects.select_for_update().filter(
                allocation_id__in=released_by_allocation,
                cycle_key="base",
            )
        )
        protected_by_bucket.update({
            row["bucket_id"]: row["total"]
            for row in LeaveEncashmentBucket.objects.filter(
                bucket_id__in=[bucket.pk for bucket in legacy_buckets],
                status=LeaveEncashmentBucket.Status.RESERVED,
            ).values("bucket_id").annotate(total=Sum("days"))
        })
        for bucket in legacy_buckets:
            days = released_by_allocation[bucket.allocation_id]
            if bucket.consumed_days < days:
                raise ValidationError("The legacy leave bucket cannot be reversed safely.")
            bucket.consumed_days -= days
            if bucket.status == LeaveBucket.Status.EXPIRED or bucket.expiry_date < timezone.localdate():
                bucket.forfeited_days = max(
                    bucket.original_days - bucket.consumed_days - protected_by_bucket.get(bucket.pk, Decimal("0")),
                    Decimal("0"),
                )
                bucket.status = LeaveBucket.Status.EXPIRED
            else:
                bucket.status = LeaveBucket.Status.ACTIVE if bucket.remaining_days > 0 else LeaveBucket.Status.EXHAUSTED
            bucket.updated_at = timezone.now()
        buckets.update({bucket.pk: bucket for bucket in legacy_buckets})
    LeaveAllocation.objects.bulk_update(list(allocations.values()), ["used_days", "updated_at"])
    if buckets:
        LeaveBucket.objects.bulk_update(list(buckets.values()), ["consumed_days", "forfeited_days", "status", "updated_at"])
    if consumptions:
        LeaveBucketConsumption.objects.bulk_update(consumptions, ["reversed_at", "updated_at"])
    LeaveMovement.objects.bulk_create([
        LeaveMovement(
            employee_id=request.employee_id,
            leave_type_id=request.leave_type_record_id,
            leave_request=request,
            allocation_id=movement.allocation_id,
            movement_date=timezone.localdate(),
            movement_type=LeaveMovement.MovementType.REVERSAL,
            days=-movement.days,
            reference=f"LEAVE-{request.pk}",
            notes="Leave reservation reversed",
        )
        for movement in debits
    ])


@transaction.atomic
def transition_leave_request(request, action, actor, *, reason=""):
    """Apply one request transition with an auditable, exactly-once balance effect."""
    Employee.objects.select_for_update().get(pk=request.employee_id)
    request = (
        LeaveRequest.objects.select_for_update(of=("self",))
        .select_related("employee", "leave_type_record")
        .get(pk=request.pk)
    )
    allowed = {
        "submit": {LeaveRequest.Status.DRAFT},
        "approve": {LeaveRequest.Status.SUBMITTED},
        "reject": {LeaveRequest.Status.SUBMITTED},
        "cancel": {LeaveRequest.Status.DRAFT, LeaveRequest.Status.SUBMITTED, LeaveRequest.Status.APPROVED},
    }
    if action not in allowed or request.status not in allowed[action]:
        raise ValidationError("That leave action is not available for the current status.")
    reason = reason.strip()
    if action == "reject" and not reason:
        raise ValidationError("Enter a rejection reason.")

    previous = request.status
    if action == "submit":
        if not request.start_date or not request.end_date or request.end_date < request.start_date:
            raise ValidationError("Enter a valid leave date range.")
        if request.portion not in LeaveRequest.Portion.values:
            raise ValidationError("Select a valid leave portion.")
        if not request.leave_type_record_id:
            raise ValidationError("Select a company leave type before submitting this request.")
        if request.employee.status != Employee.Status.ACTIVE:
            raise ValidationError("Leave can only be requested for an active employee.")
        leave_type = request.leave_type_record
        if leave_type.company_id != request.employee.company_id or not leave_type.active:
            raise ValidationError("Select an active leave type from the employee company.")
        if leave_type.day_count_by not in leave_type.DayCountBy.values:
            raise ValidationError("The leave type has an invalid day-count policy.")
        if not EmploymentContract.objects.filter(
            employee_id=request.employee_id,
            status=EmploymentContract.Status.ACTIVE,
            start_date__lte=request.start_date,
        ).filter(Q(end_date__isnull=True) | Q(end_date__gte=request.end_date)).exists():
            raise ValidationError("An active contract must cover the whole leave period.")
        overlap = LeaveRequest.objects.filter(
            employee_id=request.employee_id,
            status__in=[LeaveRequest.Status.SUBMITTED, LeaveRequest.Status.APPROVED],
            start_date__lte=request.end_date,
            end_date__gte=request.start_date,
        ).exclude(pk=request.pk)
        if overlap.exists():
            raise ValidationError("Another submitted or approved leave request overlaps these dates.")
        if request.movements.exists():
            raise ValidationError("This request already has balance movements and cannot be resubmitted.")
        by_year = _duration_by_year(request)
        _validate_leave_policy(request, by_year)
        _reserve(request, by_year)
        request.days = sum(by_year.values(), Decimal("0"))
        request.leave_type = leave_type.name
        request.status = LeaveRequest.Status.SUBMITTED
        request._workflow_audit_details = {"days": str(request.days), "by_year": {str(k): str(v) for k, v in by_year.items()}}
        request.save(update_fields=["days", "leave_type", "status", "updated_at"])
    elif action == "approve":
        if not request.movements.filter(movement_type=LeaveMovement.MovementType.USAGE).exists():
            raise ValidationError("The leave reservation is missing; recreate this request before approval.")
        request.status = LeaveRequest.Status.APPROVED
        request.approver = actor
        request.save(update_fields=["status", "approver", "updated_at"])
        request._workflow_audit_details = {"days_reserved": str(request.days), "reason": reason}
    elif action in {"reject", "cancel"}:
        if previous in {LeaveRequest.Status.SUBMITTED, LeaveRequest.Status.APPROVED}:
            if action == "cancel" and request.start_date <= timezone.localdate():
                raise ValidationError("Submitted or approved leave can only be cancelled before its start date.")
            _release(request)
        request.status = LeaveRequest.Status.REJECTED if action == "reject" else LeaveRequest.Status.CANCELLED
        if action == "reject":
            request.approver = actor
        request.save(update_fields=["status", "approver", "updated_at"])
        request._workflow_audit_details = {
            "days_reversed": str(request.days) if previous != LeaveRequest.Status.DRAFT else "0",
            "reason": reason,
        }
    return request
