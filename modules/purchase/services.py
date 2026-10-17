"""Transactional business effects for procurement workflows."""

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import ProductBudget, ProductBudgetMovement


@transaction.atomic
def approve_budget_movement(movement_id, _actor):
    """Approve one submitted movement while locking its budget balance."""
    movement = ProductBudgetMovement.objects.select_for_update().get(pk=movement_id)
    if movement.status != "submitted":
        raise ValidationError("Only a submitted budget movement can be approved.")

    budget = ProductBudget.objects.select_for_update().get(pk=movement.budget_id)
    amount = movement.amount
    movement_type = movement.movement_type

    if movement_type == ProductBudgetMovement.MovementType.RESERVE:
        if not budget.allow_overbudget and amount > budget.available_amount:
            raise ValidationError("The movement exceeds the available product budget.")
        budget.reserved_amount += amount
    elif movement_type == ProductBudgetMovement.MovementType.RELEASE:
        if amount > budget.reserved_amount:
            raise ValidationError("The release exceeds the reserved product budget.")
        budget.reserved_amount -= amount
    elif movement_type == ProductBudgetMovement.MovementType.CONSUME:
        if amount > budget.reserved_amount:
            raise ValidationError("The consumption exceeds the reserved product budget.")
        budget.reserved_amount -= amount
        budget.consumed_amount += amount
    elif movement_type == ProductBudgetMovement.MovementType.REVERSE:
        if amount > budget.consumed_amount:
            raise ValidationError("The reversal exceeds the consumed product budget.")
        budget.consumed_amount -= amount
    elif movement_type == ProductBudgetMovement.MovementType.ADJUST:
        budget.planned_amount += amount
    else:
        raise ValidationError("Unsupported product budget movement type.")

    budget.save(update_fields=["planned_amount", "reserved_amount", "consumed_amount", "updated_at"])
    movement.status = "approved"
    movement.approved_by = _actor
    movement.save(update_fields=["status", "approved_by", "updated_at"])
    return movement
