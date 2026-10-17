"""Bounded, company-scoped analytics for the Construction workspace."""

from django.db.models import Avg, Count, DecimalField, ExpressionWrapper, F, Sum

from modules.operations.models import ConstructionProject

from .access import scope_construction_queryset
from .models import (
    ConstructionBOQ,
    ConstructionContract,
    ConstructionIssueLog,
    ConstructionJobOrder,
    ConstructionProgressiveClaim,
    ConstructionQuantitySurvey,
    ConstructionQuantitySurveyJOSource,
    ConstructionResourceActualizationLine,
    ConstructionRiskRegister,
    ConstructionTimesheetLine,
)


ANALYTIC_FEATURES = {
    "project-gantt",
    "s-curve",
    "cost-resource-analysis",
    "boq-commercial-analysis",
    "job-order-analysis",
    "quantity-survey-analysis",
    "claim-analysis",
    "resource-analysis",
    "timesheet-analysis",
    "risk-analysis",
    "issue-analysis",
    "my-timesheets",
    "qs-job-order-progress-analysis",
    "contract-analysis",
}


def _scoped(user, entity, model):
    return scope_construction_queryset(user, entity, model.objects.all())


def _metric(label, value, detail, tone="blue"):
    return {"label": label, "value": value or 0, "detail": detail, "tone": tone}


def _status_rows(queryset, field="status"):
    rows = queryset.values(field).annotate(total=Count("id")).order_by(field)
    return [{"label": row[field] or "Unassigned", "total": row["total"]} for row in rows]


def build_construction_analytics(feature_slug, user):
    if feature_slug not in ANALYTIC_FEATURES:
        return None

    projects = _scoped(user, "construction-projects", ConstructionProject)
    if feature_slug in {"project-gantt", "s-curve"}:
        summary = projects.aggregate(count=Count("id"), value=Sum("contract_value"), average=Avg("progress"))
        return {
            "metrics": [
                _metric("Projects", summary["count"], "Within your company access"),
                _metric("Contract Value", summary["value"], "Visible construction portfolio", "violet"),
                _metric("Average Progress", summary["average"], "Portfolio completion percentage"),
                _metric("Active", projects.filter(status="active").count(), "Projects in execution", "amber"),
            ],
            "breakdown_title": "Construction projects by status",
            "breakdown": _status_rows(projects),
            "recent_title": "Recently updated projects",
            "recent": list(projects.select_related("customer", "company").order_by("-updated_at")[:8]),
        }

    if feature_slug == "boq-commercial-analysis":
        boqs = _scoped(user, "construction-boqs", ConstructionBOQ)
        return {
            "metrics": [
                _metric("BOQs", boqs.count(), "Visible bills of quantity"),
                _metric("Approved", boqs.filter(status="approved").count(), "Commercial baseline", "violet"),
                _metric("Under Review", boqs.filter(status="review").count(), "Needs review", "amber"),
                _metric("Revised", boqs.filter(status="revised").count(), "Revision in progress"),
            ],
            "breakdown_title": "BOQs by status",
            "breakdown": _status_rows(boqs),
            "recent_title": "Recently updated BOQs",
            "recent": list(boqs.select_related("project", "contract").order_by("-updated_at")[:8]),
        }

    if feature_slug == "my-timesheets":
        records = _scoped(user, "construction-timesheet-lines", ConstructionTimesheetLine)
        if not user.is_superuser:
            records = records.filter(employee__user=user)
        summary = records.aggregate(count=Count("id"), hours=Sum("hours"))
        employees = records.values("employee__name").annotate(total=Sum("hours")).order_by("-total")[:12]
        return {
            "metrics": [
                _metric("Entries", summary["count"], "Your visible timesheet entries"),
                _metric("Recorded Hours", summary["hours"], "Your field labour hours", "violet"),
                _metric("Projects", records.values("timesheet__project_id").distinct().count(), "Projects covered"),
                _metric("Job Orders", records.values("job_order_id").distinct().count(), "Execution orders covered"),
            ],
            "breakdown_title": "Hours by employee",
            "breakdown": [{"label": row["employee__name"], "total": row["total"]} for row in employees],
            "recent_title": "Recent timesheet entries",
            "recent": list(records.select_related("timesheet", "employee", "job_order").order_by("-work_date")[:8]),
        }

    if feature_slug == "qs-job-order-progress-analysis":
        records = _scoped(user, "construction-quantity-survey-sources", ConstructionQuantitySurveyJOSource)
        summary = records.aggregate(
            count=Count("id"),
            approved=Sum("approved_quantity"),
            previous=Sum("previous_approved_quantity"),
        )
        orders = records.values("progress_log__job_order__number").annotate(
            total=Sum("approved_quantity")
        ).order_by("-total")[:12]
        return {
            "metrics": [
                _metric("Progress Sources", summary["count"], "QS lines linked to job-order progress"),
                _metric("Approved Quantity", summary["approved"], "Approved in the selected survey records", "violet"),
                _metric("Previously Approved", summary["previous"], "Cumulative quantity before this survey"),
                _metric("Job Orders", records.values("progress_log__job_order_id").distinct().count(), "Execution orders measured"),
            ],
            "breakdown_title": "Approved quantity by job order",
            "breakdown": [{"label": row["progress_log__job_order__number"], "total": row["total"]} for row in orders],
            "recent_title": "Recent quantity-survey progress sources",
            "recent": list(records.select_related("survey_line", "progress_log", "progress_log__job_order").order_by("-updated_at")[:8]),
        }

    if feature_slug == "contract-analysis":
        records = _scoped(user, "construction-contracts", ConstructionContract)
        summary = records.aggregate(count=Count("id"), value=Sum("contract_value"), dp=Sum("down_payment_amount"))
        return {
            "metrics": [
                _metric("Contracts", summary["count"], "Visible construction contracts"),
                _metric("Contract Value", summary["value"], "Total contracted value", "violet"),
                _metric("Down Payment", summary["dp"], "Configured down-payment amount"),
                _metric("Active", records.filter(status="active").count(), "Contracts in execution", "amber"),
            ],
            "breakdown_title": "Contracts by status",
            "breakdown": _status_rows(records),
            "recent_title": "Recently updated contracts",
            "recent": list(records.select_related("project", "customer").order_by("-updated_at")[:8]),
        }

    if feature_slug == "job-order-analysis":
        records = _scoped(user, "construction-job-orders", ConstructionJobOrder)
        summary = records.aggregate(count=Count("id"), average=Avg("progress"))
        metrics = [
            _metric("Job Orders", summary["count"], "Visible execution orders"),
            _metric("Average Progress", summary["average"], "Execution completion"),
            _metric("In Progress", records.filter(status="in_progress").count(), "Active field work", "amber"),
            _metric("Done", records.filter(status="done").count(), "Completed work", "violet"),
        ]
    elif feature_slug == "quantity-survey-analysis":
        records = _scoped(user, "construction-quantity-surveys", ConstructionQuantitySurvey)
        metrics = [
            _metric("Quantity Surveys", records.count(), "Visible measurements"),
            _metric("Submitted", records.filter(status="submitted").count(), "Waiting approval", "amber"),
            _metric("Approved", records.filter(status="approved").count(), "Accepted field measurements", "violet"),
            _metric("Draft", records.filter(status="draft").count(), "Work in preparation"),
        ]
    elif feature_slug == "claim-analysis":
        records = _scoped(user, "construction-progressive-claims", ConstructionProgressiveClaim)
        summary = records.aggregate(count=Count("id"), gross=Sum("gross_amount"), certified=Sum("certified_amount"))
        metrics = [
            _metric("Claims", summary["count"], "Visible progressive claims"),
            _metric("Gross Claimed", summary["gross"], "Submitted claim value", "violet"),
            _metric("Certified", summary["certified"], "Certified claim value"),
            _metric("Paid", records.filter(status="paid").count(), "Settled claims", "blue"),
        ]
    elif feature_slug in {"resource-analysis", "cost-resource-analysis"}:
        records = _scoped(user, "construction-resource-lines", ConstructionResourceActualizationLine)
        amount = ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=DecimalField(max_digits=18, decimal_places=2))
        summary = records.aggregate(count=Count("id"), total_quantity=Sum("quantity"), cost=Sum(amount))
        metrics = [
            _metric("Resource Lines", summary["count"], "Actualized resources"),
            _metric("Total Quantity", summary["total_quantity"], "Recorded resource units"),
            _metric("Unit Cost Total", summary["cost"], "Sum of recorded unit costs", "violet"),
            _metric("Material Lines", records.filter(resource_type="material").count(), "Material consumption", "amber"),
        ]
        feature_slug = "resource-analysis"
        records = records.select_related("actualization")
    elif feature_slug == "timesheet-analysis":
        records = _scoped(user, "construction-timesheet-lines", ConstructionTimesheetLine)
        summary = records.aggregate(count=Count("id"), hours=Sum("hours"))
        employees = records.values("employee__name").annotate(total=Sum("hours")).order_by("-total")[:12]
        metrics = [
            _metric("Timesheet Lines", summary["count"], "Visible time entries"),
            _metric("Recorded Hours", summary["hours"], "Field labour hours", "violet"),
            _metric("Employees", records.values("employee_id").distinct().count(), "Contributors"),
            _metric("Job Orders", records.values("job_order_id").distinct().count(), "Covered job orders"),
        ]
        records = records.select_related("timesheet", "employee")
        return {
            "metrics": metrics,
            "breakdown_title": "Hours by employee",
            "breakdown": [{"label": row["employee__name"], "total": row["total"]} for row in employees],
            "recent_title": "Recently updated records",
            "recent": list(records.order_by("-updated_at")[:8]),
        }
    elif feature_slug == "risk-analysis":
        records = _scoped(user, "construction-risks", ConstructionRiskRegister)
        metrics = [
            _metric("Risks", records.count(), "Visible risk register"),
            _metric("Open", records.filter(status="open").count(), "Needs mitigation", "amber"),
            _metric("High Impact", records.filter(impact="high").count(), "Material project exposure", "amber"),
            _metric("Closed", records.filter(status="closed").count(), "Mitigated risks", "violet"),
        ]
    else:
        records = _scoped(user, "construction-issues", ConstructionIssueLog)
        metrics = [
            _metric("Issues", records.count(), "Visible issue log"),
            _metric("Open", records.filter(status="open").count(), "Needs action", "amber"),
            _metric("In Progress", records.filter(status="in_progress").count(), "Under resolution"),
            _metric("Closed", records.filter(status="closed").count(), "Resolved and closed", "violet"),
        ]

    return {
        "metrics": metrics,
        "breakdown_title": "Records by status" if hasattr(records.model, "status") else "Resources by type",
        "breakdown": _status_rows(records, "status" if hasattr(records.model, "status") else "resource_type"),
        "recent_title": "Recently updated records",
        "recent": list(records.order_by("-updated_at")[:8]),
    }
