"""Bounded, row-scoped analytics for the Human Resources workspace."""

from datetime import timedelta

from django.db.models import Count, Sum
from django.utils import timezone

from core.models import Attendance, Employee
from modules.finance.models import ExpenseClaim, Loan
from modules.organization.context import scope_queryset as scope_organization_queryset

from .access import scope_hr_queryset
from .models import (
    CareerTransitionRequest,
    EmployeeOffboarding,
    EmployeeOnboarding,
    LeaveRequest,
    OvertimeRequest,
    Payslip,
    TravelRequest,
)


ANALYTIC_FEATURES = {
    "my-dashboard",
    "director-dashboard",
    "hr-operational",
    "organization-chart",
    "onboarding-dashboard",
    "offboarding-dashboard",
    "leave-dashboard",
    "leave-reporting",
    "overtime-analysis",
    "loan-analysis",
    "career-transition-analysis",
    "travel-reporting",
    "expense-analysis",
    "employee-analysis",
    "attendance-analysis",
    "leave-analysis",
    "payroll-analysis",
}


def _scoped(user, entity, model):
    return scope_organization_queryset(scope_hr_queryset(user, entity, model.objects.all()))


def _status_rows(queryset):
    rows = queryset.values("status").annotate(total=Count("id")).order_by("status")
    return [{"label": row["status"] or "Unassigned", "total": row["total"]} for row in rows]


def _metric(label, value, detail, tone="blue"):
    return {"label": label, "value": value or 0, "detail": detail, "tone": tone}


def _people_dashboard(user):
    employees = _scoped(user, "employees", Employee)
    leave = _scoped(user, "leave-requests", LeaveRequest)
    overtime = _scoped(user, "overtime", OvertimeRequest)
    attendance = _scoped(user, "attendance", Attendance)
    today = timezone.localdate()
    return {
        "metrics": [
            _metric("Visible Employees", employees.filter(status=Employee.Status.ACTIVE).count(), "Active employees in your access scope", "violet"),
            _metric("Present Today", attendance.filter(attendance_date=today, status=Attendance.Status.PRESENT).count(), "Attendance recorded today", "blue"),
            _metric("Leave Pending", leave.filter(status="submitted").count(), "Waiting for approval", "amber"),
            _metric("Overtime Pending", overtime.filter(status="submitted").count(), "Waiting for approval", "amber"),
        ],
        "breakdown_title": "Employees by department",
        "breakdown": [
            {"label": row["department"] or "Unassigned", "total": row["total"]}
            for row in employees.values("department")
            .annotate(total=Count("id"))
            .order_by("-total", "department")[:24]
        ],
        "recent_title": "Recently updated employees",
        "recent": list(employees.select_related("company").order_by("-updated_at")[:8]),
    }


def build_hr_analytics(feature_slug, user):
    """Return analytics data using a fixed number of aggregate queries."""
    if feature_slug not in ANALYTIC_FEATURES:
        return None

    if feature_slug in {"my-dashboard", "director-dashboard", "hr-operational", "organization-chart", "employee-analysis"}:
        return _people_dashboard(user)

    definitions = {
        "onboarding-dashboard": ("employee-onboarding", EmployeeOnboarding, "Onboarding by status", None),
        "offboarding-dashboard": ("employee-offboarding", EmployeeOffboarding, "Offboarding by status", None),
        "leave-dashboard": ("leave-requests", LeaveRequest, "Leave requests by status", "days"),
        "leave-reporting": ("leave-requests", LeaveRequest, "Leave requests by status", "days"),
        "leave-analysis": ("leave-requests", LeaveRequest, "Leave requests by status", "days"),
        "overtime-analysis": ("overtime", OvertimeRequest, "Overtime requests by status", "hours"),
        "loan-analysis": ("loans", Loan, "Employee loans by status", "balance"),
        "career-transition-analysis": ("career-transition-requests", CareerTransitionRequest, "Transitions by status", None),
        "travel-reporting": ("travel-requests", TravelRequest, "Travel requests by status", "estimated_amount"),
        "expense-analysis": ("expenses", ExpenseClaim, "Expense claims by status", "amount"),
        "payroll-analysis": ("payslips", Payslip, "Payslips by status", "net_salary"),
    }

    if feature_slug == "attendance-analysis":
        queryset = _scoped(user, "attendance", Attendance).filter(
            attendance_date__gte=timezone.localdate() - timedelta(days=30)
        )
        return {
            "metrics": [
                _metric("Attendance Entries", queryset.count(), "Last 30 days", "blue"),
                _metric("Present", queryset.filter(status=Attendance.Status.PRESENT).count(), "Last 30 days", "violet"),
                _metric("Absent", queryset.filter(status=Attendance.Status.ABSENT).count(), "Last 30 days", "amber"),
                _metric("Leave / Sick", queryset.filter(status__in=[Attendance.Status.LEAVE, Attendance.Status.SICK]).count(), "Last 30 days", "amber"),
            ],
            "breakdown_title": "Attendance by status",
            "breakdown": _status_rows(queryset),
            "recent_title": "Latest attendance",
            "recent": list(queryset.select_related("employee").order_by("-attendance_date", "employee__name")[:8]),
        }

    entity, model, breakdown_title, amount_field = definitions[feature_slug]
    queryset = _scoped(user, entity, model)
    aggregate = queryset.aggregate(total=Count("id"), amount=Sum(amount_field) if amount_field else Count("id"))
    return {
        "metrics": [
            _metric("Total Records", aggregate["total"], "Within your permitted scope", "blue"),
            _metric("Draft", queryset.filter(status="draft").count(), "Still being prepared", "violet"),
            _metric("Pending", queryset.filter(status__in=["submitted", "processing", "running"]).count(), "Needs action or processing", "amber"),
            _metric("Total Value", aggregate["amount"], amount_field.replace("_", " ").title() if amount_field else "Tracked records", "blue"),
        ],
        "breakdown_title": breakdown_title,
        "breakdown": _status_rows(queryset),
        "recent_title": "Recently updated records",
        "recent": list(queryset.order_by("-updated_at")[:8]),
    }
