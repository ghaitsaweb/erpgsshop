"""Finalize a migrated leave balance into the allocation and bucket ledger."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Employee

from .access import FULL_HR_GROUPS, accessible_employees
from .models import LeaveAllocation, LeaveBucket, LeaveMovement, LeaveOpeningBalance


@transaction.atomic
def finalize_leave_opening_balance(record, actor):
    """Post one opening document once, under an employee-scoped lock."""
    if not actor.is_superuser and not actor.groups.filter(name__in=FULL_HR_GROUPS).exists():
        raise ValidationError("Only an HR manager or administrator can finalize opening balances.")
    Employee.objects.select_for_update().get(pk=record.employee_id)
    opening = (
        LeaveOpeningBalance.objects.select_for_update()
        .select_related("employee", "leave_type")
        .get(pk=record.pk)
    )
    if not actor.is_superuser and not accessible_employees(
        actor, entity="leave-opening-balances"
    ).filter(pk=opening.employee_id).exists():
        raise ValidationError("The employee is outside your HR access scope.")
    if opening.status != LeaveOpeningBalance.Status.DRAFT:
        raise ValidationError("Only draft opening balances can be finalized.")
    opening.full_clean()
    total = opening.balance_days + opening.opening_used
    if total <= 0 or total > Decimal("9999.99"):
        raise ValidationError("Enter a positive opening total within the allocation limit.")
    cutoff = opening.cutoff_date or date(opening.year, 1, 1)
    expiry = opening.expiry_date or date(opening.year, 12, 31)
    if expiry < cutoff:
        raise ValidationError("Opening expiry must follow its cut-off date.")
    if LeaveAllocation.objects.filter(
        employee_id=opening.employee_id,
        leave_type_id=opening.leave_type_id,
        year=opening.year,
    ).exists():
        raise ValidationError("An allocation already exists for this employee, leave type, and year.")

    allocation = LeaveAllocation(
        employee=opening.employee,
        leave_type=opening.leave_type,
        year=opening.year,
        allocated_days=total,
        used_days=opening.opening_used,
        valid_from=cutoff,
        expires_on=expiry,
        opening_balance=opening,
        status="approved",
    )
    allocation.full_clean()
    allocation.save()
    bucket = LeaveBucket(
        employee=opening.employee,
        leave_type=opening.leave_type,
        allocation=allocation,
        year=opening.year,
        source=LeaveBucket.Source.OPENING,
        original_days=total,
        consumed_days=opening.opening_used,
        start_valid_date=cutoff,
        expiry_date=expiry,
        status=LeaveBucket.Status.EXHAUSTED if opening.balance_days == 0 else LeaveBucket.Status.ACTIVE,
    )
    bucket.full_clean()
    bucket.save()
    movements = [LeaveMovement(
        employee=opening.employee,
        leave_type=opening.leave_type,
        opening_balance=opening,
        allocation=allocation,
        movement_date=cutoff,
        movement_type=LeaveMovement.MovementType.OPENING,
        days=total,
        reference=f"OPENING-{opening.pk}",
        notes="Opening balance migrated",
    )]
    if opening.opening_used:
        movements.append(LeaveMovement(
            employee=opening.employee,
            leave_type=opening.leave_type,
            opening_balance=opening,
            allocation=allocation,
            movement_date=cutoff,
            movement_type=LeaveMovement.MovementType.OPENING_DEDUCTION,
            days=-opening.opening_used,
            reference=f"OPENING-{opening.pk}",
            notes="Leave used before migration",
        ))
    LeaveMovement.objects.bulk_create(movements)
    opening.status = LeaveOpeningBalance.Status.FINALIZED
    opening.finalized_at = timezone.now()
    opening.finalized_by = actor
    opening.save(update_fields=["status", "finalized_at", "finalized_by", "updated_at"])
    opening._workflow_audit_details = {
        "allocation_id": allocation.pk,
        "bucket_id": bucket.pk,
        "opening_total": str(total),
        "opening_used": str(opening.opening_used),
        "available": str(opening.balance_days),
    }
    return opening
