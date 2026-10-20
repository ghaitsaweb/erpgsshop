"""Guard the local employment-contract lifecycle."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import Employee

from .models import EmploymentContract


CONTRACT_TRANSITIONS = {
    "activate": (EmploymentContract.Status.DRAFT, EmploymentContract.Status.ACTIVE),
    "expire": (EmploymentContract.Status.ACTIVE, EmploymentContract.Status.EXPIRED),
    "terminate": (EmploymentContract.Status.ACTIVE, EmploymentContract.Status.TERMINATED),
}


@transaction.atomic
def transition_employment_contract(contract, action):
    """Change one locked contract after checking dates and employee overlap."""
    transition = CONTRACT_TRANSITIONS.get(action)
    if transition is None or contract.status != transition[0]:
        raise ValidationError("That contract action is not available for the current status.")

    if action == "activate":
        employee = Employee.objects.select_for_update().get(pk=contract.employee_id)
        contract.full_clean()
        if employee.status != Employee.Status.ACTIVE:
            raise ValidationError("An inactive employee cannot have a new active contract.")
        if contract.end_date and contract.end_date < timezone.localdate():
            raise ValidationError("A contract that has already ended cannot be activated.")
        overlaps = EmploymentContract.objects.filter(
            employee_id=contract.employee_id,
            status=EmploymentContract.Status.ACTIVE,
        ).exclude(pk=contract.pk).filter(Q(end_date__isnull=True) | Q(end_date__gte=contract.start_date))
        if contract.end_date:
            overlaps = overlaps.filter(start_date__lte=contract.end_date)
        if overlaps.exists():
            raise ValidationError("This employee already has an active contract for the same date range.")
    elif action == "expire":
        if not contract.end_date or contract.end_date > timezone.localdate():
            raise ValidationError("A contract can expire only on or after its end date.")

    contract.status = transition[1]
    contract.save(update_fields=["status", "updated_at"])
    return contract
