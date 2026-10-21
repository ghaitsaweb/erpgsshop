from django.core.exceptions import ValidationError


def apply_approval_result(target, action, actor):
    """Apply a completed approval through the registered domain adapter."""
    if target._meta.label_lower == "gsshop_hr.employeechangerequest":
        from modules.hr.change_requests import transition_employee_change_request

        transition_employee_change_request(target, action, actor)
        return
    raise ValidationError("This approval target has no registered result adapter.")
