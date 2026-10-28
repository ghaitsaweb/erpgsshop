"""Bounded expiry processing for the dated leave grant ledger."""

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from .models import LeaveAllocation, LeaveBucket, LeaveEncashmentBucket


def locked_leave_buckets(employee_id, leave_type_id, start_date, end_date, years):
    """Load and provision legacy approved allocations under the employee lock."""
    allocations = list(
        LeaveAllocation.objects.select_for_update()
        .filter(employee_id=employee_id, leave_type_id=leave_type_id, status="approved")
        .filter(Q(year__in=years) | Q(valid_from__lte=end_date, expires_on__gte=start_date))
        .order_by("year", "pk")
    )
    allocation_ids = [allocation.pk for allocation in allocations]
    buckets = list(
        LeaveBucket.objects.select_for_update()
        .filter(allocation_id__in=allocation_ids)
        .order_by("expiry_date", "pk")
    )
    existing_ids = {bucket.allocation_id for bucket in buckets}
    missing = [allocation for allocation in allocations if allocation.pk not in existing_ids]
    if missing:
        LeaveBucket.objects.bulk_create([
            LeaveBucket(
                employee_id=employee_id,
                leave_type_id=leave_type_id,
                allocation=allocation,
                year=allocation.year,
                original_days=allocation.allocated_days,
                consumed_days=allocation.used_days,
                start_valid_date=allocation.valid_from or date(allocation.year, 1, 1),
                expiry_date=allocation.expires_on or date(allocation.year, 12, 31),
                status=(
                    LeaveBucket.Status.EXHAUSTED
                    if allocation.used_days >= allocation.allocated_days
                    else LeaveBucket.Status.ACTIVE
                ),
            )
            for allocation in missing
        ])
        buckets = list(
            LeaveBucket.objects.select_for_update()
            .filter(allocation_id__in=allocation_ids)
            .order_by("expiry_date", "pk")
        )
    return allocations, buckets


def current_allocation_availability(allocations, *, today=None):
    """Return today's usable days for one bounded page of allocations."""
    today = today or timezone.localdate()
    allocation_ids = [allocation.pk for allocation in allocations]
    if not allocation_ids:
        return {}
    bucket_scope = LeaveBucket.objects.filter(allocation_id__in=allocation_ids)
    with_buckets = set(bucket_scope.values_list("allocation_id", flat=True).distinct())
    valid_scope = bucket_scope.filter(
        status__in=[LeaveBucket.Status.ACTIVE, LeaveBucket.Status.EXHAUSTED],
        start_valid_date__lte=today,
        expiry_date__gte=today,
    )
    available = {
        row["allocation_id"]: row["days"]
        for row in valid_scope.values("allocation_id").annotate(
            days=Sum(F("original_days") - F("consumed_days") - F("forfeited_days"))
        )
    }
    reserved = {
        row["bucket__allocation_id"]: row["days"]
        for row in LeaveEncashmentBucket.objects.filter(
            bucket__allocation_id__in=allocation_ids,
            bucket__status__in=[LeaveBucket.Status.ACTIVE, LeaveBucket.Status.EXHAUSTED],
            bucket__start_valid_date__lte=today,
            bucket__expiry_date__gte=today,
            status=LeaveEncashmentBucket.Status.RESERVED,
        ).values("bucket__allocation_id").annotate(days=Sum("days"))
    }
    for allocation in allocations:
        if allocation.status != "approved":
            available[allocation.pk] = Decimal("0")
            continue
        if allocation.pk in with_buckets:
            available[allocation.pk] = max(
                available.get(allocation.pk, Decimal("0")) - reserved.get(allocation.pk, Decimal("0")),
                Decimal("0"),
            )
        else:
            valid_from = allocation.valid_from or date(allocation.year, 1, 1)
            expires_on = allocation.expires_on or date(allocation.year, 12, 31)
            available[allocation.pk] = (
                max(allocation.balance_days, Decimal("0"))
                if allocation.status == "approved" and valid_from <= today <= expires_on
                else Decimal("0")
            )
    return available


def expire_leave_buckets(*, today=None, batch_size=500, max_batches=20):
    today = today or timezone.localdate()
    if batch_size < 1 or batch_size > 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    if max_batches < 1 or max_batches > 100:
        raise ValueError("max_batches must be between 1 and 100")
    touched = 0
    # scale-guard: allow SG001 — at most 100 batches of at most 1,000 locked buckets are processed.
    for _batch in range(max_batches):
        with transaction.atomic():
            buckets = list(
                LeaveBucket.objects.select_for_update(skip_locked=True)
                .filter(status=LeaveBucket.Status.ACTIVE, expiry_date__lt=today)
                .order_by("expiry_date", "pk")[:batch_size]
            )
            if not buckets:
                return touched
            reserved_by_bucket = {
                row["bucket_id"]: row["total"]
                for row in LeaveEncashmentBucket.objects.filter(
                    bucket_id__in=[bucket.pk for bucket in buckets],
                    status=LeaveEncashmentBucket.Status.RESERVED,
                ).values("bucket_id").annotate(total=Sum("days"))
            }
            now = timezone.now()
            for bucket in buckets:
                remaining = bucket.remaining_days
                reserved = reserved_by_bucket.get(bucket.pk, Decimal("0"))
                bucket.forfeited_days += max(remaining - reserved, Decimal("0"))
                bucket.status = LeaveBucket.Status.EXPIRED if remaining > 0 else LeaveBucket.Status.EXHAUSTED
                bucket.updated_at = now
            LeaveBucket.objects.bulk_update(buckets, ["forfeited_days", "status", "updated_at"])
            touched += len(buckets)
    return touched
