"""Bounded, row-scoped analytics for the Human Resources workspace."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import BigIntegerField, Count, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from core.models import Attendance, Employee
from modules.finance.models import ExpenseClaim, Loan
from modules.organization.context import get_current_context, scope_queryset as scope_organization_queryset
from modules.organization.dashboard import (
    branch_links,
    branch_nodes,
    money,
    parse_dashboard_period,
    period_label,
    quantity,
    status_rows,
)
from modules.organization.models import Branch

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
    "dashboard",
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


def _hr_dashboard(user, params=None):
    start, end, error = parse_dashboard_period(params)
    employees = _scoped(user, "employees", Employee)
    active_employees = employees.filter(status=Employee.Status.ACTIVE)
    attendance = _scoped(user, "attendance", Attendance).filter(attendance_date__range=(start, end))
    leave = _scoped(user, "leave-requests", LeaveRequest).filter(start_date__lte=end, end_date__gte=start)
    payslips = _scoped(user, "payslips", Payslip).filter(period_end__range=(start, end))
    active_count = active_employees.count()
    payroll = payslips.exclude(status=Payslip.Status.CANCELLED).aggregate(total=Sum("net_salary"))["total"] or Decimal("0")
    main_branch = Subquery(
        Branch.objects.filter(company_id=OuterRef("company_id"), is_main=True)
        .order_by("pk").values("pk")[:1],
        output_field=BigIntegerField(),
    )
    branch_rows = list(
        active_employees.annotate(
            dashboard_branch_id=Coalesce("branch_id", main_branch, output_field=BigIntegerField())
        )
        .values("dashboard_branch_id")
        .annotate(dashboard_value=Count("pk"), dashboard_count=Count("pk"))
        .order_by("-dashboard_value", "dashboard_branch_id")[:8]
    )
    branches = branch_nodes(
        branch_rows, get_current_context(), total_value=active_count,
        value_formatter=quantity, count_suffix="employees",
    )
    department_rows = list(
        active_employees.values("department").annotate(total=Count("pk")).order_by("-total", "department")[:6]
    )
    department_total = sum(row["total"] for row in department_rows)
    departments = [
        {
            "label": row["department"] or "Unassigned",
            "total": row["total"],
            "share": row["total"] / department_total * 100 if department_total else 0,
        }
        for row in department_rows
    ]
    recent = []
    for employee in employees.select_related("company", "branch").order_by("-updated_at")[:8]:
        recent.append({"cells": [
            {"value": employee.employee_id, "url": reverse("entity_edit", args=["employees", employee.pk])},
            {"value": employee.name},
            {"value": employee.department or "Unassigned"},
            {"value": employee.branch.name if employee.branch_id else "Main branch"},
            {"value": employee.join_date.strftime("%d %b %Y")},
            {"value": employee.get_status_display(), "status": employee.status},
        ]})
    return {
        "template_name": "apphub/branch_dashboard.html",
        "feature_slug": "dashboard",
        "app_label": "Human Resources",
        "route_name": "hr_feature",
        "title": "Human Resources Dashboard",
        "live_badge": "Live people operations",
        "intro": f"People visibility across {len(branches)} branches",
        "country": "Indonesia",
        "date_from": start,
        "date_to": end,
        "period_label": period_label(start, end),
        "filter_error": error,
        "branches": branches,
        "branch_links": branch_links(branches),
        "branch_count": len(branches),
        "network_title": "active headcount by branch",
        "scope_pill": "Employee to attendance and payroll visibility",
        "network_total_label": f"{active_count} active employees",
        "network_empty": "No active employee branch data.",
        "primary_entity": "employees",
        "primary_label": "Employees",
        "secondary_entity": "attendance",
        "secondary_label": "Attendance",
        "metrics": [
            _metric("Active Employees", active_count, "Employees in your access scope", "violet"),
            _metric("Present Entries", attendance.filter(status=Attendance.Status.PRESENT).count(), "Attendance records in selected period", "blue"),
            _metric("Leave Pending", leave.filter(status=LeaveRequest.Status.SUBMITTED).count(), "Waiting for approval", "amber"),
            _metric("Payroll Value", money(payroll), "Non-cancelled payslips", "green"),
        ],
        "flow_title": "Employee → Attendance & Leave → Payroll",
        "pipeline": [
            {"label": "Active People", "value": active_count, "detail": "Current workforce", "tone": "blue"},
            {"label": "Attendance", "value": attendance.count(), "detail": "Entries in selected period", "tone": "violet"},
            {"label": "Approved Leave", "value": leave.filter(status=LeaveRequest.Status.APPROVED).count(), "detail": "Approved overlapping leave", "tone": "amber"},
            {"label": "Paid Payslips", "value": payslips.filter(status=Payslip.Status.PAID).count(), "detail": "Payroll completed", "tone": "green"},
        ],
        "breakdown_title": "Employees by department",
        "breakdown": departments,
        "secondary_title": "Payslip status",
        "secondary_rows": status_rows(payslips, Payslip.Status.choices),
        "recent_title": "Recently Updated Employees",
        "table_headers": ["Employee ID", "Name", "Department", "Branch", "Join Date", "Status"],
        "recent_rows": recent,
        "tour_steps": [
            {"target": "overview", "title": "People operations dari satu layar", "copy": "Dashboard menyatukan headcount, attendance, leave, dan payroll sesuai scope akses Anda."},
            {"target": "network", "title": "Headcount aktif per cabang", "copy": "Peta dan ranking memperlihatkan distribusi karyawan aktif di jaringan cabang Indonesia."},
            {"target": "metrics", "title": "KPI HR yang langsung berguna", "copy": "Active Employees, Present Entries, Leave Pending, dan Payroll Value dihitung dari data HR nyata."},
            {"target": "flow", "title": "Alur employee sampai payroll", "copy": "Pipeline memperlihatkan tenaga kerja aktif, attendance, approved leave, dan payslip paid."},
            {"target": "breakdown", "title": "Komposisi organisasi", "copy": "Distribusi department dan status payslip membantu HR menemukan area yang memerlukan perhatian."},
            {"target": "records", "title": "Profil terbaru siap dibuka", "copy": "Klik Employee ID untuk langsung membuka data karyawan yang baru diperbarui."},
        ],
    }


def build_hr_analytics(feature_slug, user, params=None):
    """Return analytics data using a fixed number of aggregate queries."""
    if feature_slug not in ANALYTIC_FEATURES:
        return None

    if feature_slug == "dashboard":
        return _hr_dashboard(user, params)

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
    if model is LeaveRequest:
        queryset = queryset.defer("attachment_content")
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
