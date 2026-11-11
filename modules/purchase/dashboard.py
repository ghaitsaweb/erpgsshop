"""Bounded, company-scoped analytics for the Purchase workspace."""

from django.db.models import Avg, Count, Sum

from core.models import PurchaseOrder
from modules.organization.context import scope_queryset as scope_organization_queryset

from .access import scope_purchase_queryset
from .models import (
    BlanketOrder,
    DirectPurchase,
    GoodsReceipt,
    ProductBudget,
    PurchaseInsight,
    PurchaseQuotation,
    PurchaseRequest,
    PurchaseTender,
    VendorPerformanceEvaluation,
    VendorPerformanceScoreHistory,
    VendorProfile,
)


ANALYTIC_FEATURES = {
    "overview",
    "insight-feed",
    "spend-analysis",
    "supplier-performance",
    "demand-analysis",
    "savings-opportunities",
    "contract-intelligence",
    "risk-center",
    "purchase-performance",
    "order-analysis",
    "request-analysis",
    "budget-report",
}


def _scoped(user, entity, model):
    return scope_organization_queryset(scope_purchase_queryset(user, entity, model.objects.all()))


def _metric(label, value, detail, tone="blue"):
    return {"label": label, "value": value or 0, "detail": detail, "tone": tone}


def _status_rows(queryset, field="status"):
    rows = queryset.values(field).annotate(total=Count("id")).order_by(field)
    return [{"label": row[field] or "Unassigned", "total": row["total"]} for row in rows]


def _order_analytics(user):
    orders = _scoped(user, "purchases", PurchaseOrder)
    totals = orders.aggregate(count=Count("id"), value=Sum("total"))
    return {
        "metrics": [
            _metric("Purchase Orders", totals["count"], "Within your company access", "blue"),
            _metric("Order Value", totals["value"], "All visible non-filtered orders", "violet"),
            _metric("Waiting Approval", orders.filter(status="waiting_approval").count(), "Needs manager action", "amber"),
            _metric("Confirmed", orders.filter(status="confirmed").count(), "Approved procurement", "blue"),
        ],
        "breakdown_title": "Purchase orders by status",
        "breakdown": _status_rows(orders),
        "recent_title": "Recently updated purchase orders",
        "recent": list(orders.select_related("vendor").order_by("-updated_at")[:8]),
    }


def _request_analytics(user):
    requests = _scoped(user, "purchase-requests", PurchaseRequest)
    return {
        "metrics": [
            _metric("Purchase Requests", requests.count(), "Within your company access", "blue"),
            _metric("Pending", requests.filter(status="pending").count(), "Waiting for approval", "amber"),
            _metric("Approved", requests.filter(status="approved").count(), "Ready for sourcing", "violet"),
            _metric("In Progress", requests.filter(status="in_progress").count(), "Being fulfilled", "blue"),
        ],
        "breakdown_title": "Purchase requests by status",
        "breakdown": _status_rows(requests),
        "recent_title": "Recently updated requests",
        "recent": list(requests.select_related("company", "requestor").order_by("-updated_at")[:8]),
    }


def _insight_analytics(feature_slug, user):
    insights = _scoped(user, "purchase-insights", PurchaseInsight).filter(active=True)
    insight_type = {
        "savings-opportunities": PurchaseInsight.InsightType.SAVINGS,
        "risk-center": PurchaseInsight.InsightType.RISK,
    }.get(feature_slug)
    if insight_type:
        insights = insights.filter(insight_type=insight_type)
    return {
        "metrics": [
            _metric("Active Insights", insights.count(), "Current generated insight records", "blue"),
            _metric("High Severity", insights.filter(severity__iexact="high").count(), "Needs attention", "amber"),
            _metric("Risks", insights.filter(insight_type=PurchaseInsight.InsightType.RISK).count(), "Active risk signals", "amber"),
            _metric("Savings", insights.filter(insight_type=PurchaseInsight.InsightType.SAVINGS).count(), "Savings opportunities", "violet"),
        ],
        "breakdown_title": "Insights by type",
        "breakdown": _status_rows(insights, "insight_type"),
        "recent_title": "Latest purchase insights",
        "recent": list(insights.order_by("-updated_at")[:8]),
    }


def _lifecycle_analytics(user):
    """HMX lifecycle totals adapted to GS Shop's separate sourcing models."""

    requests = _scoped(user, "purchase-requests", PurchaseRequest)
    quotations = _scoped(user, "purchase-quotations", PurchaseQuotation)
    orders = _scoped(user, "purchases", PurchaseOrder).filter(direct_purchase_source__isnull=True)
    direct_purchases = _scoped(user, "direct-purchases", DirectPurchase)
    tenders = _scoped(user, "purchase-tenders", PurchaseTender)
    blanket_orders = _scoped(user, "blanket-orders", BlanketOrder)
    breakdown = [
        {"label": "Purchase Request", "total": requests.count()},
        {"label": "Request for Quotation", "total": quotations.count()},
        {"label": "Purchase Order", "total": orders.count()},
        {"label": "Direct Purchase", "total": direct_purchases.count()},
        {"label": "Purchase Tender", "total": tenders.count()},
        {"label": "Blanket Order", "total": blanket_orders.count()},
    ]
    totals = {row["label"]: row["total"] for row in breakdown}
    return {
        "metrics": [
            _metric("Lifecycle Documents", sum(totals.values()), "All visible purchase stages", "blue"),
            _metric("Purchase Requests", totals["Purchase Request"], "Demand intake", "amber"),
            _metric("Active Sourcing", totals["Request for Quotation"] + totals["Purchase Tender"], "RFQs and tenders", "violet"),
            _metric("Purchase Orders", totals["Purchase Order"], "Non-direct purchase orders", "blue"),
        ],
        "breakdown_title": "Purchase lifecycle stages",
        "breakdown": breakdown,
        "recent_title": "Recently updated purchase orders",
        "recent": list(orders.select_related("vendor").order_by("-updated_at")[:8]),
    }


def build_purchase_analytics(feature_slug, user):
    """Return procurement analytics using a fixed number of aggregate queries."""
    if feature_slug not in ANALYTIC_FEATURES:
        return None
    if feature_slug == "overview":
        return _lifecycle_analytics(user)
    if feature_slug in {"spend-analysis", "purchase-performance", "order-analysis"}:
        return _order_analytics(user)
    if feature_slug in {"demand-analysis", "request-analysis"}:
        return _request_analytics(user)
    if feature_slug in {"insight-feed", "savings-opportunities", "risk-center"}:
        return _insight_analytics(feature_slug, user)

    if feature_slug == "supplier-performance":
        evaluations = _scoped(user, "vendor-performance-evaluations", VendorPerformanceEvaluation)
        vendors = _scoped(user, "vendor-profiles", VendorProfile)
        score_history = _scoped(user, "vendor-performance-score-history", VendorPerformanceScoreHistory)
        summary = evaluations.aggregate(count=Count("id"))
        vendor_summary = vendors.aggregate(average=Avg("latest_performance_score"))
        return {
            "metrics": [
                _metric("Vendors", vendors.count(), "Visible vendor profiles", "blue"),
                _metric("Evaluations", summary["count"], "Recorded performance reviews", "violet"),
                _metric("Average Score", vendor_summary["average"], "Latest calculated vendor scores", "blue"),
                _metric("Confirmed Reviews", evaluations.filter(status="confirmed").count(), "Finalized evaluations", "violet"),
            ],
            "breakdown_title": "Vendor scoring history by status",
            "breakdown": _status_rows(score_history, "status"),
            "recent_title": "Latest vendor score calculations",
            "recent": list(score_history.select_related("vendor__partner", "template").order_by("-calculated_at")[:8]),
        }

    if feature_slug == "contract-intelligence":
        agreements = _scoped(user, "blanket-orders", BlanketOrder)
        summary = agreements.aggregate(count=Count("id"), value=Sum("agreement_value"), consumed=Sum("consumed_value"))
        return {
            "metrics": [
                _metric("Agreements", summary["count"], "Visible blanket orders", "blue"),
                _metric("Agreement Value", summary["value"], "Contracted purchasing value", "violet"),
                _metric("Consumed Value", summary["consumed"], "Released against agreements", "amber"),
                _metric("Active", agreements.filter(status="active").count(), "Currently available agreements", "blue"),
            ],
            "breakdown_title": "Agreements by status",
            "breakdown": _status_rows(agreements),
            "recent_title": "Recently updated agreements",
            "recent": list(agreements.select_related("vendor").order_by("-updated_at")[:8]),
        }

    if feature_slug == "budget-report":
        budgets = _scoped(user, "product-budgets", ProductBudget)
        summary = budgets.aggregate(
            count=Count("id"),
            planned=Sum("planned_amount"),
            reserved=Sum("reserved_amount"),
            consumed=Sum("consumed_amount"),
        )
        return {
            "metrics": [
                _metric("Product Budgets", summary["count"], "Visible annual budgets", "blue"),
                _metric("Planned", summary["planned"], "Approved planning amount", "violet"),
                _metric("Reserved", summary["reserved"], "Committed but not consumed", "amber"),
                _metric("Consumed", summary["consumed"], "Posted consumption", "blue"),
            ],
            "breakdown_title": "Budgets by year",
            "breakdown": _status_rows(budgets, "year"),
            "recent_title": "Recently updated budgets",
            "recent": list(budgets.select_related("product", "group").order_by("-updated_at")[:8]),
        }

    orders = _scoped(user, "purchases", PurchaseOrder)
    requests = _scoped(user, "purchase-requests", PurchaseRequest)
    receipts = _scoped(user, "goods-receipts", GoodsReceipt)
    order_summary = orders.aggregate(count=Count("id"), value=Sum("total"))
    return {
        "metrics": [
            _metric("Order Value", order_summary["value"], "Visible purchase orders", "violet"),
            _metric("Purchase Orders", order_summary["count"], "All visible orders", "blue"),
            _metric("Open Requests", requests.exclude(status="closed").count(), "Demand still in progress", "amber"),
            _metric("Received", receipts.filter(status="received").count(), "Completed goods receipts", "blue"),
        ],
        "breakdown_title": "Purchase orders by status",
        "breakdown": _status_rows(orders),
        "recent_title": "Recently updated purchase orders",
        "recent": list(orders.select_related("vendor").order_by("-updated_at")[:8]),
    }
