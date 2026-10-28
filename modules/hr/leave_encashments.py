"""Leave encashment reservation, posting, and exact reversal."""

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import Employee

from .access import FULL_HR_GROUPS, accessible_employees
from .leave_buckets import locked_leave_buckets
from .models import (
    EmploymentContract,
    LeaveAllocation,
    LeaveBucket,
    LeaveEncashment,
    LeaveEncashmentBucket,
    LeaveMovement,
)


MONEY = Decimal("0.01")


def _manager_required(actor):
    if not actor.is_superuser and not actor.groups.filter(name__in=FULL_HR_GROUPS).exists():
        raise ValidationError("Only an HR manager or administrator can perform this action.")


def _rate_per_day(request):
    leave_type = request.leave_type
    method = leave_type.encashment_calculation_method
    if method == "flat_amount":
        rate = leave_type.flat_cash_amount
    else:
        contract = (
            EmploymentContract.objects.filter(
                employee_id=request.employee_id,
                status=EmploymentContract.Status.ACTIVE,
                start_date__lte=request.request_date,
            )
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=request.request_date))
            .order_by("-start_date", "-pk")
            .first()
        )
        if contract is None:
            raise ValidationError("A running contract is required to value this encashment.")
        if method == "employee_daily_wage":
            rate = contract.daily_wage or Decimal("0")
        elif method == "employee_wage":
            rate = contract.base_salary / Decimal("22")
        else:
            raise ValidationError("Configure a valid encashment calculation method on the leave type.")
    if rate <= 0:
        raise ValidationError("The encashment rate must be greater than zero.")
    return rate.quantize(MONEY, rounding=ROUND_HALF_UP)


def _reservation_scope(request):
    year = request.request_date.year
    prior = LeaveEncashment.objects.filter(
        employee_id=request.employee_id,
        leave_type_id=request.leave_type_id,
        request_date__gte=date(year, 1, 1),
        request_date__lte=date(year, 12, 31),
        status__in=[
            LeaveEncashment.Status.SUBMITTED,
            LeaveEncashment.Status.APPROVED,
            LeaveEncashment.Status.WAITING_FOR_PAYROLL,
            LeaveEncashment.Status.PAYROLL_GENERATED,
        ],
    ).exclude(pk=request.pk).aggregate(total=Sum("days"))["total"] or Decimal("0")
    leave_type = request.leave_type
    if leave_type.max_encashment_days <= 0 or request.days > leave_type.max_encashment_days:
        raise ValidationError("This request exceeds the leave type's encashment limit.")
    if prior + request.days > leave_type.max_encashment_days:
        raise ValidationError("The yearly encashment quota has been used.")


def _reserve(request):
    allocations, buckets = locked_leave_buckets(
        request.employee_id,
        request.leave_type_id,
        request.request_date,
        request.request_date,
        [request.request_date.year],
    )
    approved_ids = {allocation.pk for allocation in allocations}
    reservations = {
        row["bucket_id"]: row["total"]
        for row in LeaveEncashmentBucket.objects.filter(
            bucket_id__in=[bucket.pk for bucket in buckets],
            status=LeaveEncashmentBucket.Status.RESERVED,
        ).values("bucket_id").annotate(total=Sum("days"))
    }
    available = []
    for bucket in buckets:
        if (
            bucket.allocation_id in approved_ids
            and bucket.status != LeaveBucket.Status.EXPIRED
            and bucket.start_valid_date <= request.request_date <= bucket.expiry_date
        ):
            remaining = bucket.remaining_days - reservations.get(bucket.pk, Decimal("0"))
            if remaining > 0:
                available.append((bucket, remaining))
    eligible = sum((days for _bucket, days in available), Decimal("0")) - request.leave_type.min_retained_balance
    if eligible <= 0 or request.days > eligible:
        raise ValidationError("Insufficient eligible leave balance after the minimum retained amount.")
    if not request.leave_type.allow_partial_encashment and request.days != eligible:
        raise ValidationError("Partial encashment is not allowed for this leave type.")
    remaining = request.days
    details = []
    for bucket, days in available:
        if remaining <= 0:
            break
        take = min(days, remaining)
        details.append(LeaveEncashmentBucket(encashment=request, bucket=bucket, days=take))
        remaining -= take
    if remaining:
        raise ValidationError("Insufficient date-valid leave buckets for encashment.")
    LeaveEncashmentBucket.objects.bulk_create(details)


def _locked_details(request, required_status):
    details = list(
        LeaveEncashmentBucket.objects.select_for_update()
        .filter(encashment=request, status=required_status)
        .order_by("bucket_id")
    )
    if not details or sum((detail.days for detail in details), Decimal("0")) != request.days:
        raise ValidationError("The original encashment bucket reservation is incomplete.")
    buckets = {
        bucket.pk: bucket
        for bucket in LeaveBucket.objects.select_for_update().filter(pk__in=[detail.bucket_id for detail in details])
    }
    allocations = {
        allocation.pk: allocation
        for allocation in LeaveAllocation.objects.select_for_update().filter(
            pk__in=[bucket.allocation_id for bucket in buckets.values()]
        )
    }
    if len(buckets) != len(details) or any(bucket.allocation_id not in allocations for bucket in buckets.values()):
        raise ValidationError("An original leave bucket or allocation is unavailable.")
    return details, buckets, allocations


def _post(request, now):
    details, buckets, allocations = _locked_details(request, LeaveEncashmentBucket.Status.RESERVED)
    other_reserved = {
        row["bucket_id"]: row["total"]
        for row in LeaveEncashmentBucket.objects.filter(
            bucket_id__in=buckets,
            status=LeaveEncashmentBucket.Status.RESERVED,
        ).exclude(encashment=request).values("bucket_id").annotate(total=Sum("days"))
    }
    by_allocation = defaultdict(lambda: Decimal("0"))
    for detail in details:
        bucket = buckets[detail.bucket_id]
        if bucket.remaining_days - other_reserved.get(bucket.pk, Decimal("0")) < detail.days:
            raise ValidationError("A reserved leave bucket no longer has enough balance.")
        bucket.consumed_days += detail.days
        if bucket.status != LeaveBucket.Status.EXPIRED:
            bucket.status = LeaveBucket.Status.EXHAUSTED if bucket.remaining_days == 0 else LeaveBucket.Status.ACTIVE
        bucket.updated_at = now
        by_allocation[bucket.allocation_id] += detail.days
        detail.status = LeaveEncashmentBucket.Status.POSTED
        detail.posted_at = now
        detail.updated_at = now
    for allocation_id, days in by_allocation.items():
        allocation = allocations[allocation_id]
        if allocation.used_days + days > allocation.allocated_days:
            raise ValidationError("The allocation and encashment bucket balances do not agree.")
        allocation.used_days += days
        allocation.updated_at = now
    LeaveBucket.objects.bulk_update(list(buckets.values()), ["consumed_days", "status", "updated_at"])
    LeaveAllocation.objects.bulk_update(list(allocations.values()), ["used_days", "updated_at"])
    LeaveEncashmentBucket.objects.bulk_update(details, ["status", "posted_at", "updated_at"])
    LeaveMovement.objects.bulk_create([
        LeaveMovement(
            employee_id=request.employee_id,
            leave_type_id=request.leave_type_id,
            leave_encashment=request,
            allocation_id=allocation_id,
            movement_date=request.request_date,
            movement_type=LeaveMovement.MovementType.ENCASHMENT,
            days=-days,
            reference=f"ENCASH-{request.pk}",
            notes="Encashment approved",
        )
        for allocation_id, days in sorted(by_allocation.items())
    ])


def _reverse(request, now):
    details, buckets, allocations = _locked_details(request, LeaveEncashmentBucket.Status.POSTED)
    protected_by_bucket = {
        row["bucket_id"]: row["total"]
        for row in LeaveEncashmentBucket.objects.filter(
            bucket_id__in=buckets,
            status=LeaveEncashmentBucket.Status.RESERVED,
        ).values("bucket_id").annotate(total=Sum("days"))
    }
    debits = list(LeaveMovement.objects.filter(
        leave_encashment=request,
        movement_type=LeaveMovement.MovementType.ENCASHMENT,
    ))
    if sum((-movement.days for movement in debits), Decimal("0")) != request.days:
        raise ValidationError("The encashment debit movement is incomplete.")
    if LeaveMovement.objects.filter(
        leave_encashment=request,
        movement_type=LeaveMovement.MovementType.REVERSAL,
    ).exists():
        raise ValidationError("The encashment has already been reversed.")
    by_allocation = defaultdict(lambda: Decimal("0"))
    for detail in details:
        bucket = buckets[detail.bucket_id]
        if bucket.consumed_days < detail.days:
            raise ValidationError("The original bucket cannot be restored safely.")
        bucket.consumed_days -= detail.days
        if bucket.status == LeaveBucket.Status.EXPIRED or bucket.expiry_date < timezone.localdate():
            bucket.forfeited_days = max(
                bucket.original_days - bucket.consumed_days - protected_by_bucket.get(bucket.pk, Decimal("0")),
                Decimal("0"),
            )
            bucket.status = LeaveBucket.Status.EXPIRED
        else:
            bucket.status = LeaveBucket.Status.ACTIVE if bucket.remaining_days > 0 else LeaveBucket.Status.EXHAUSTED
        bucket.updated_at = now
        by_allocation[bucket.allocation_id] += detail.days
        detail.status = LeaveEncashmentBucket.Status.REVERSED
        detail.reversed_at = now
        detail.updated_at = now
    for allocation_id, days in by_allocation.items():
        allocation = allocations[allocation_id]
        if allocation.used_days < days:
            raise ValidationError("The original allocation cannot be restored safely.")
        allocation.used_days -= days
        allocation.updated_at = now
    LeaveBucket.objects.bulk_update(list(buckets.values()), ["consumed_days", "forfeited_days", "status", "updated_at"])
    LeaveAllocation.objects.bulk_update(list(allocations.values()), ["used_days", "updated_at"])
    LeaveEncashmentBucket.objects.bulk_update(details, ["status", "reversed_at", "updated_at"])
    LeaveMovement.objects.bulk_create([
        LeaveMovement(
            employee_id=request.employee_id,
            leave_type_id=request.leave_type_id,
            leave_encashment=request,
            allocation_id=movement.allocation_id,
            movement_date=timezone.localdate(),
            movement_type=LeaveMovement.MovementType.REVERSAL,
            days=-movement.days,
            reference=f"ENCASH-{request.pk}",
            notes="Encashment cancellation reversal",
        )
        for movement in debits
    ])


@transaction.atomic
def transition_leave_encashment(record, action, actor):
    """Execute one encashment transition under an employee-scoped lock."""
    Employee.objects.select_for_update().get(pk=record.employee_id)
    request = LeaveEncashment.objects.select_for_update().select_related("employee", "leave_type").get(pk=record.pk)
    if not actor.is_superuser and not accessible_employees(actor, entity="leave-encashments").filter(pk=request.employee_id).exists():
        raise ValidationError("The employee is outside your HR access scope.")
    allowed = {
        "submit": {LeaveEncashment.Status.DRAFT},
        "approve": {LeaveEncashment.Status.SUBMITTED},
        "reject": {LeaveEncashment.Status.SUBMITTED},
        "cancel": {
            LeaveEncashment.Status.DRAFT,
            LeaveEncashment.Status.SUBMITTED,
            LeaveEncashment.Status.APPROVED,
            LeaveEncashment.Status.WAITING_FOR_PAYROLL,
        },
        "assign-payroll": {LeaveEncashment.Status.APPROVED},
    }
    if action not in allowed or request.status not in allowed[action]:
        raise ValidationError("That encashment action is unavailable for the current status.")
    if action in {"approve", "reject", "cancel", "assign-payroll"}:
        _manager_required(actor)
    now = timezone.now()
    if action == "submit":
        leave_type = request.leave_type
        if not (request.reason or "").strip():
            raise ValidationError("Enter a reason for the encashment request.")
        if request.employee.status != Employee.Status.ACTIVE or not leave_type.active:
            raise ValidationError("The employee and leave type must be active.")
        if leave_type.company_id != request.employee.company_id or not leave_type.allow_leave_encashment:
            raise ValidationError("Leave encashment is not enabled for this employee's leave type.")
        if request.days <= 0:
            raise ValidationError("Requested days must be greater than zero.")
        _reservation_scope(request)
        rate = _rate_per_day(request)
        _reserve(request)
        request.rate_per_day = rate
        request.reference = request.reference or f"ENC-{request.request_date.year}-{request.pk:08d}"
        request.valuation_method = leave_type.encashment_calculation_method
        request.amount = (rate * request.days).quantize(MONEY, rounding=ROUND_HALF_UP)
        request.submitted_at = now
        request.status = LeaveEncashment.Status.SUBMITTED
        request.save(update_fields=["reference", "rate_per_day", "valuation_method", "amount", "submitted_at", "status", "updated_at"])
    elif action == "approve":
        _post(request, now)
        request.status = LeaveEncashment.Status.APPROVED
        request.approver = actor
        request.approved_at = now
        request.save(update_fields=["status", "approver", "approved_at", "updated_at"])
    elif action == "reject":
        details, _buckets, _allocations = _locked_details(request, LeaveEncashmentBucket.Status.RESERVED)
        for detail in details:
            detail.status = LeaveEncashmentBucket.Status.RELEASED
            detail.updated_at = now
        LeaveEncashmentBucket.objects.bulk_update(details, ["status", "updated_at"])
        request.status = LeaveEncashment.Status.REJECTED
        request.approver = actor
        request.save(update_fields=["status", "approver", "updated_at"])
    elif action == "cancel":
        if request.status in {LeaveEncashment.Status.APPROVED, LeaveEncashment.Status.WAITING_FOR_PAYROLL}:
            _reverse(request, now)
        elif request.status == LeaveEncashment.Status.SUBMITTED:
            details, _buckets, _allocations = _locked_details(request, LeaveEncashmentBucket.Status.RESERVED)
            for detail in details:
                detail.status = LeaveEncashmentBucket.Status.RELEASED
                detail.updated_at = now
            LeaveEncashmentBucket.objects.bulk_update(details, ["status", "updated_at"])
        request.status = LeaveEncashment.Status.CANCELLED
        request.save(update_fields=["status", "updated_at"])
    else:
        request.status = LeaveEncashment.Status.WAITING_FOR_PAYROLL
        request.save(update_fields=["status", "updated_at"])
    request._workflow_audit_details = {"days": str(request.days), "amount": str(request.amount)}
    return request
