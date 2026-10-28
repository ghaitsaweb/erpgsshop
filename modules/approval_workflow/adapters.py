from django.core.exceptions import ValidationError


GENERIC_HR_TARGETS = {
    "gsshop_hr.leaverequest",
    "gsshop_hr.overtimerequest",
    "gsshop_hr.employeeoffboarding",
    "gsshop_hr.employeeonboarding",
    "gsshop_hr.leaveallocation",
}


def uses_approval_engine(target):
    return target._meta.label_lower in {
        "gsshop_hr.employeechangerequest",
        "gsshop_hr.attendancechangerequest",
        "gsshop_hr.careertransitionrequest",
        "gsshop_hr.employmentcontract",
        *GENERIC_HR_TARGETS,
    }


def _generic_transition(target, action, actor):
    transitions = {
        "submit": ("draft", "submitted"),
        "approve": ("submitted", "approved"),
        "reject": ("submitted", "rejected"),
        "cancel": (("draft", "submitted"), "cancelled"),
        "reset": (("rejected", "cancelled"), "draft"),
    }
    transition = transitions.get(action)
    if transition is None:
        raise ValidationError("This approval target does not support that lifecycle action.")
    allowed, next_status = transition
    allowed = {allowed} if isinstance(allowed, str) else set(allowed)
    if target.status not in allowed:
        raise ValidationError("That workflow action is not available for the current status.")
    target.status = next_status
    fields = ["status", "updated_at"]
    if action in {"approve", "reject"} and hasattr(target, "approver_id"):
        target.approver = actor
        fields.append("approver")
    target.save(update_fields=fields)
    return target


def apply_approval_submit(target, actor):
    """Move a target into Submitted only after an approval instance is created."""
    label = target._meta.label_lower
    if label == "gsshop_hr.employeechangerequest":
        from modules.hr.change_requests import transition_employee_change_request

        return transition_employee_change_request(target, "submit", actor)
    if label == "gsshop_hr.attendancechangerequest":
        from modules.hr.attendance_changes import transition_attendance_change_request

        return transition_attendance_change_request(target, "submit", actor)
    if label == "gsshop_hr.careertransitionrequest":
        from modules.hr.career_transitions import transition_career_request

        return transition_career_request(target, "submit", actor)
    if label == "gsshop_hr.employmentcontract":
        from modules.hr.contracts import transition_employment_contract

        return transition_employment_contract(target, "submit", actor)
    if label in GENERIC_HR_TARGETS:
        return _generic_transition(target, "submit", actor)
    raise ValidationError("This approval target has no registered submit adapter.")


def apply_approval_lifecycle(target, action, actor):
    """Apply a non-decision transition such as cancel or reset."""
    label = target._meta.label_lower
    if label == "gsshop_hr.employeechangerequest":
        from modules.hr.change_requests import transition_employee_change_request

        return transition_employee_change_request(target, action, actor)
    if label == "gsshop_hr.attendancechangerequest":
        from modules.hr.attendance_changes import transition_attendance_change_request

        return transition_attendance_change_request(target, action, actor)
    if label == "gsshop_hr.careertransitionrequest":
        from modules.hr.career_transitions import transition_career_request

        return transition_career_request(target, action, actor)
    if label == "gsshop_hr.employmentcontract":
        from modules.hr.contracts import transition_employment_contract

        return transition_employment_contract(target, action, actor)
    if label in GENERIC_HR_TARGETS:
        return _generic_transition(target, action, actor)
    raise ValidationError("This approval target has no registered lifecycle adapter.")


def apply_approval_result(target, action, actor):
    """Apply a completed approval through the registered domain adapter."""
    label = target._meta.label_lower
    if label == "gsshop_hr.employeechangerequest":
        from modules.hr.change_requests import transition_employee_change_request

        transition_employee_change_request(target, action, actor)
        return target
    if label == "gsshop_hr.attendancechangerequest":
        from modules.hr.attendance_changes import transition_attendance_change_request

        return transition_attendance_change_request(target, action, actor)
    if label == "gsshop_hr.careertransitionrequest":
        from modules.hr.career_transitions import transition_career_request

        return transition_career_request(target, action, actor)
    if label == "gsshop_hr.employmentcontract":
        from modules.hr.contracts import transition_employment_contract

        return transition_employment_contract(target, "activate" if action == "approve" else "reject", actor)
    if label in GENERIC_HR_TARGETS:
        return _generic_transition(target, action, actor)
    raise ValidationError("This approval target has no registered result adapter.")
