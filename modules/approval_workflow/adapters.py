from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured, ValidationError


@dataclass(frozen=True)
class ApprovalAdapter:
    submit: object
    lifecycle: object
    result: object
    company_id: object = None
    decision_url: object = None
    direct_decision: bool = True


_REGISTERED_ADAPTERS = {}


def register_approval_adapter(
    model_label,
    *,
    submit,
    lifecycle,
    result,
    company_id=None,
    decision_url=None,
    direct_decision=True,
):
    """Register one installed module's domain transitions with the shared engine."""
    key = str(model_label).strip().lower()
    if not key or "." not in key:
        raise ImproperlyConfigured("Approval adapters require an app_label.model model label.")
    adapter = ApprovalAdapter(
        submit=submit,
        lifecycle=lifecycle,
        result=result,
        company_id=company_id,
        decision_url=decision_url,
        direct_decision=direct_decision,
    )
    existing = _REGISTERED_ADAPTERS.get(key)
    if existing is not None and existing != adapter:
        raise ImproperlyConfigured(f"Approval adapter '{key}' is already registered.")
    _REGISTERED_ADAPTERS[key] = adapter


def _registered_adapter(target):
    return _REGISTERED_ADAPTERS.get(target._meta.label_lower)


def registered_approval_company_id(target):
    """Resolve an indirect company through a module-owned adapter."""
    adapter = _registered_adapter(target)
    if adapter is None or adapter.company_id is None:
        return None
    return adapter.company_id(target)


def registered_approval_decision_url(target):
    """Return a domain decision workspace without coupling shared UI to a module."""
    adapter = _registered_adapter(target)
    if adapter is None or adapter.decision_url is None:
        return ""
    return adapter.decision_url(target)


def registered_approval_allows_direct_decision(target):
    adapter = _registered_adapter(target)
    return adapter is None or adapter.direct_decision


GENERIC_HR_TARGETS = {
    "gsshop_hr.leaverequest",
    "gsshop_hr.overtimerequest",
    "gsshop_hr.employeeoffboarding",
    "gsshop_hr.employeeonboarding",
    "gsshop_hr.leaveallocation",
}


def uses_approval_engine(target):
    return _registered_adapter(target) is not None or target._meta.label_lower in {
        "core.saleorder",
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
    adapter = _registered_adapter(target)
    if adapter is not None:
        return adapter.submit(target, actor)
    label = target._meta.label_lower
    if label == "core.saleorder":
        from core.sales import apply_sale_approval_submit

        return apply_sale_approval_submit(target, actor)
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


def apply_approval_lifecycle(target, action, actor, reason=""):
    """Apply a non-decision transition such as cancel or reset."""
    adapter = _registered_adapter(target)
    if adapter is not None:
        return adapter.lifecycle(target, action, actor, reason)
    label = target._meta.label_lower
    if label == "core.saleorder":
        from core.sales import transition_sale_order

        return transition_sale_order(target, action, actor)
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


def apply_approval_result(target, action, actor, reason=""):
    """Apply a completed approval through the registered domain adapter."""
    adapter = _registered_adapter(target)
    if adapter is not None:
        return adapter.result(target, action, actor, reason)
    label = target._meta.label_lower
    if label == "core.saleorder":
        from core.sales import apply_sale_approval_result

        return apply_sale_approval_result(target, action, actor)
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
