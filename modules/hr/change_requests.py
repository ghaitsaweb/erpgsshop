"""Apply supported employee profile changes through an auditable approval."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Employee


# Employment/organization changes belong to their own HR workflows. A free-form
# field name must never become an arbitrary Employee model attribute assignment.
CHANGE_REQUEST_FIELDS = {
    "email": "Work email",
    "phone": "Phone",
}

CHANGE_REQUEST_TRANSITIONS = {
    "submit": ("draft", "submitted"),
    "approve": ("submitted", "approved"),
    "reject": ("submitted", "rejected"),
    "cancel": ("draft", "cancelled"),
}


@transaction.atomic
def transition_employee_change_request(change_request, action, actor):
    """Transition a locked request and apply its value exactly on approval.

    The caller scopes the request by the actor's access rights. Both the request
    and its employee are locked so a concurrent approval cannot apply stale data.
    """
    transition = CHANGE_REQUEST_TRANSITIONS.get(action)
    if transition is None or change_request.status != transition[0]:
        raise ValidationError("That employee change action is not available for the current status.")

    if action in {"submit", "approve"}:
        field_name = change_request.field_name
        if field_name not in CHANGE_REQUEST_FIELDS:
            raise ValidationError("This employee field is not supported by change requests.")
        employee = Employee.objects.select_for_update().get(pk=change_request.employee_id)
        current_value = getattr(employee, field_name)
        if current_value != change_request.current_value:
            raise ValidationError("The employee value changed since this request was saved. Review a new draft.")
        requested_value = change_request.requested_value
        Employee._meta.get_field(field_name).clean(requested_value, employee)
        if requested_value == current_value:
            raise ValidationError("The requested value is already on the employee profile.")
        if action == "approve":
            if change_request.effective_date > timezone.localdate():
                raise ValidationError("This change cannot be applied before its effective date.")
            setattr(employee, field_name, requested_value)
            employee.save(update_fields=[field_name, "updated_at"])

    change_request.status = transition[1]
    update_fields = ["status", "updated_at"]
    if action in {"approve", "reject"}:
        change_request.approver = actor
        update_fields.append("approver")
    change_request.save(update_fields=update_fields)
    return change_request
