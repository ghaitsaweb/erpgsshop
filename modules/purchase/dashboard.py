"""Bounded, company-scoped analytics for the Purchase workspace."""

from datetime import date
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db.models import (
    Avg,
    BigIntegerField,
    CharField,
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Q,
    Subquery,
    Sum,
)
from django.db.models.functions import Cast, Coalesce
from django.utils import timezone

from core.models import PurchaseOrder, PurchaseOrderLine
from modules.organization.context import (
    get_current_context,
    scope_queryset as scope_organization_queryset,
)
from modules.organization.models import Branch, RecordOrganizationScope

from .access import can_purchase_entity_action, scope_purchase_queryset
from .models import (
    BlanketOrder,
    DirectPurchase,
    GoodsReceipt,
    GoodsReceiptLine,
    ProductBudget,
    PurchaseInsight,
    PurchaseQuotation,
    PurchaseRequest,
    PurchaseTender,
    VendorPerformanceEvaluation,
    VendorPerformanceScoreHistory,
    VendorProfile,
)


MAX_DASHBOARD_DAYS = 366
RECENT_DASHBOARD_ROWS = 8
TOP_VENDOR_ROWS = 6
MAX_BRANCH_NODES = 8

CITY_POSITIONS = {
    "medan": (16, 32),
    "jakarta": (39, 62),
    "bandung": (34, 69),
    "semarang": (48, 60),
    "surabaya": (58, 66),
    "balikpapan": (63, 43),
    "makassar": (67, 66),
    "denpasar": (62, 76),
    "manado": (77, 35),
    "jayapura": (90, 54),
}
FALLBACK_POSITIONS = (
    (18, 34),
    (39, 62),
    (49, 56),
    (59, 66),
    (64, 42),
    (72, 69),
    (82, 40),
    (89, 57),
)
SPARK_PATHS = (
    "M1 17 L7 10 L13 15 L19 5 L25 13 L31 9 L37 18 L43 6 L49 10",
    "M1 16 L7 13 L13 6 L19 8 L25 14 L31 11 L37 16 L43 3 L49 12",
    "M1 8 L7 14 L13 10 L19 17 L25 7 L31 13 L37 5 L43 14 L49 11",
    "M1 13 L7 8 L13 11 L19 15 L25 14 L31 17 L37 6 L43 12 L49 4",
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


def _money(value):
    amount = Decimal(value or 0)
    return f"Rp {amount:,.0f}".replace(",", ".")


def _quantity(value):
    amount = Decimal(value or 0)
    rendered = f"{amount:,.3f}".rstrip("0").rstrip(".")
    return rendered.replace(",", ".")


def _parse_period(params, today=None):
    today = today or timezone.localdate()
    default_start = today.replace(day=1)
    default_end = today
    raw_start = (params.get("date_from") or "").strip()
    raw_end = (params.get("date_to") or "").strip()
    if not raw_start and not raw_end:
        return default_start, default_end, ""
    try:
        start = date.fromisoformat(raw_start)
        end = date.fromisoformat(raw_end)
    except (TypeError, ValueError):
        return default_start, default_end, "Use valid From and To dates."
    if end < start:
        return default_start, default_end, "To date cannot be earlier than From date."
    if (end - start).days + 1 > MAX_DASHBOARD_DAYS:
        return default_start, default_end, f"Choose a period of at most {MAX_DASHBOARD_DAYS} days."
    return start, end, ""


def _branch_position(branch_name, index):
    normalized = (branch_name or "").lower()
    for city, position in CITY_POSITIONS.items():
        if city in normalized:
            return position
    return FALLBACK_POSITIONS[index % len(FALLBACK_POSITIONS)]


def _branch_value_rows(orders, context):
    selected_branch_ids = tuple(getattr(context, "active_branch_ids", ()) or ())
    if not selected_branch_ids:
        return []

    content_type = ContentType.objects.get_for_model(PurchaseOrder, for_concrete_model=False)
    explicit_branch = Subquery(
        RecordOrganizationScope.objects.filter(
            content_type=content_type,
            object_id=Cast(OuterRef("pk"), output_field=CharField()),
        )
        .order_by()
        .values("branch_id")[:1],
        output_field=BigIntegerField(),
    )
    main_branch = Subquery(
        Branch.objects.filter(company_id=OuterRef("company_id"), is_main=True)
        .order_by("pk")
        .values("pk")[:1],
        output_field=BigIntegerField(),
    )
    return list(
        orders.annotate(
            dashboard_branch_id=Coalesce(
                explicit_branch,
                main_branch,
                output_field=BigIntegerField(),
            )
        )
        .filter(dashboard_branch_id__in=selected_branch_ids)
        .values("dashboard_branch_id")
        .annotate(purchase_value=Sum("total"), order_count=Count("pk"))
        .order_by("-purchase_value", "dashboard_branch_id")[:MAX_BRANCH_NODES]
    )


def _branch_nodes(orders, context, total_value):
    value_rows = _branch_value_rows(orders, context)
    value_by_branch = {
        row["dashboard_branch_id"]: {
            "value": row["purchase_value"] or Decimal("0"),
            "order_count": row["order_count"],
        }
        for row in value_rows
    }
    ordered_ids = list(value_by_branch)
    selected_ids = tuple(getattr(context, "active_branch_ids", ()) or ())
    if len(ordered_ids) < MAX_BRANCH_NODES:
        zero_ids = list(
            Branch.objects.filter(pk__in=selected_ids, active=True)
            .exclude(pk__in=ordered_ids)
            .order_by("company__name", "-is_main", "sequence", "name", "pk")
            .values_list("pk", flat=True)[: MAX_BRANCH_NODES - len(ordered_ids)]
        )
        ordered_ids.extend(zero_ids)

    branches = Branch.objects.filter(pk__in=ordered_ids).select_related("company").in_bulk()
    nodes = []
    for index, branch_id in enumerate(ordered_ids):
        branch = branches.get(branch_id)
        if branch is None:
            continue
        values = value_by_branch.get(branch_id, {"value": Decimal("0"), "order_count": 0})
        purchase_value = values["value"]
        share = float((purchase_value / total_value * 100) if total_value else 0)
        x, y = _branch_position(branch.name, index)
        nodes.append(
            {
                "id": branch.pk,
                "name": branch.name,
                "code": branch.code,
                "company": branch.company.name,
                "is_main": branch.is_main,
                "value": purchase_value,
                "value_label": _money(purchase_value),
                "order_count": values["order_count"],
                "share": share,
                "share_label": f"{share:.1f}".replace(".", ","),
                "x": x,
                "y": y,
                "tone": index % 6,
                "spark_path": SPARK_PATHS[index % len(SPARK_PATHS)],
            }
        )
    return nodes


def _curve_path(source, target, bend=0):
    midpoint_x = (source["x"] + target["x"]) / 2
    midpoint_y = (source["y"] + target["y"]) / 2 - 7 - bend
    return (
        f'M {source["x"]} {source["y"]} '
        f'Q {midpoint_x:.1f} {midpoint_y:.1f} {target["x"]} {target["y"]}'
    )


def _branch_links(nodes):
    if len(nodes) < 2:
        return []
    hub = next((node for node in nodes if node["is_main"]), nodes[0])
    satellites = [node for node in nodes if node["id"] != hub["id"]]
    links = [
        {
            "path": _curve_path(hub, node, (index % 3) * 2),
            "tone": node["tone"],
            "kind": "primary",
        }
        for index, node in enumerate(satellites)
    ]
    links.extend(
        {
            "path": _curve_path(source, target, -3),
            "tone": target["tone"],
            "kind": "secondary",
        }
        for source, target in zip(satellites, satellites[1:])
    )
    return links


def _dashboard_status_rows(queryset, choices):
    labels = dict(choices)
    rows = list(queryset.values("status").annotate(total=Count("pk")).order_by("status"))
    total = sum(row["total"] for row in rows)
    return [
        {
            "value": row["status"],
            "label": labels.get(row["status"], row["status"] or "Unassigned"),
            "total": row["total"],
            "share": (row["total"] / total * 100) if total else 0,
        }
        for row in rows
    ]


def _purchase_receiving_analytics(user, params, *, today=None):
    """Build a date-bounded PO-to-receipt dashboard with fixed query growth."""

    start, end, filter_error = _parse_period(params, today=today)
    visible_orders = _scoped(user, "purchases", PurchaseOrder)
    period_orders = visible_orders.filter(order_date__range=(start, end))
    active_orders = period_orders.exclude(
        status__in=(
            PurchaseOrder.Status.REJECTED,
            PurchaseOrder.Status.CANCELLED,
            PurchaseOrder.Status.REVISED,
            PurchaseOrder.Status.EXPIRED,
        )
    )
    committed_statuses = (
        PurchaseOrder.Status.CONFIRMED,
        PurchaseOrder.Status.COMPLETED,
        PurchaseOrder.Status.CLOSED,
    )
    order_summary = active_orders.aggregate(
        order_count=Count("pk"),
        document_value=Sum("total"),
        draft_sent=Count(
            "pk",
            filter=Q(status__in=(PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.SENT)),
        ),
        waiting_approval=Count("pk", filter=Q(status=PurchaseOrder.Status.WAITING_APPROVAL)),
        awaiting_receipt=Count(
            "pk",
            filter=Q(status__in=committed_statuses, received_percentage=0),
        ),
        partially_received=Count(
            "pk",
            filter=Q(
                status__in=committed_statuses,
                received_percentage__gt=0,
                received_percentage__lt=100,
            ),
        ),
        fully_received=Count(
            "pk",
            filter=Q(status__in=committed_statuses, received_percentage__gte=100),
        ),
    )

    ordered_line_value = ExpressionWrapper(
        F("quantity") * F("unit_price"),
        output_field=DecimalField(max_digits=30, decimal_places=2),
    )
    received_line_value = ExpressionWrapper(
        F("received_quantity") * F("unit_price"),
        output_field=DecimalField(max_digits=30, decimal_places=2),
    )
    line_summary = PurchaseOrderLine.objects.filter(order__in=active_orders).aggregate(
        total_ordered_quantity=Sum("quantity"),
        total_received_quantity=Sum("received_quantity"),
        ordered_value=Sum(ordered_line_value),
        received_value=Sum(received_line_value),
    )
    ordered_value = line_summary["ordered_value"] or Decimal("0")
    received_value = line_summary["received_value"] or Decimal("0")
    outstanding_value = max(ordered_value - received_value, Decimal("0"))
    completion = min(
        (received_value / ordered_value * Decimal("100")) if ordered_value else Decimal("0"),
        Decimal("100"),
    )

    visible_receipts = _scoped(user, "goods-receipts", GoodsReceipt)
    period_receipts = visible_receipts.filter(receipt_date__range=(start, end))
    posted_receipts = period_receipts.filter(status=GoodsReceipt.Status.RECEIVED)
    receipt_summary = posted_receipts.aggregate(
        receipt_count=Count("pk"),
        order_count=Count("order_id", distinct=True),
        on_time_count=Count(
            "pk",
            filter=Q(
                delivery_status__in=(
                    GoodsReceipt.DeliveryStatus.EARLY,
                    GoodsReceipt.DeliveryStatus.ON_TIME,
                )
            ),
        ),
    )
    receipt_count = receipt_summary["receipt_count"] or 0
    on_time_count = receipt_summary["on_time_count"] or 0
    on_time_rate = (on_time_count / receipt_count * 100) if receipt_count else 0

    quality_summary = GoodsReceiptLine.objects.filter(receipt__in=posted_receipts).aggregate(
        total_accepted_quantity=Sum("accepted_quantity"),
        total_rejected_quantity=Sum("rejected_quantity"),
    )

    document_value = order_summary["document_value"] or Decimal("0")
    organization_context = get_current_context()
    branches = (
        _branch_nodes(active_orders, organization_context, document_value)
        if organization_context is not None
        else []
    )
    selected_branch_count = len(
        tuple(getattr(organization_context, "active_branch_ids", ()) or ())
    )
    vendor_rows = list(
        active_orders.values("vendor_id", "vendor__name")
        .annotate(order_count=Count("pk"), value=Sum("total"))
        .order_by("-value", "vendor_id")[:TOP_VENDOR_ROWS]
    )
    for row in vendor_rows:
        row["value"] = row["value"] or Decimal("0")
        row["value_label"] = _money(row["value"])
        row["share"] = float(row["value"] / document_value * 100) if document_value else 0

    order_status_labels = dict(PurchaseOrder.Status.choices)
    recent_orders = list(
        active_orders.annotate(
            receipt_count=Count(
                "goods_receipts",
                filter=Q(goods_receipts__status=GoodsReceipt.Status.RECEIVED),
                distinct=True,
            )
        )
        .values(
            "pk",
            "number",
            "vendor__name",
            "order_date",
            "status",
            "total",
            "received_percentage",
            "receipt_count",
        )
        .order_by("-order_date", "-pk")[:RECENT_DASHBOARD_ROWS]
    )
    for row in recent_orders:
        row["status_label"] = order_status_labels.get(row["status"], row["status"])
        row["total_label"] = _money(row["total"])

    recent_receipts = list(
        period_receipts.select_related("order__vendor", "warehouse")
        .order_by("-receipt_date", "-pk")[:RECENT_DASHBOARD_ROWS]
    )
    pipeline = [
        {
            "label": "Draft & Sent",
            "value": order_summary["draft_sent"] or 0,
            "detail": "PO preparation",
            "tone": "slate",
        },
        {
            "label": "Waiting Approval",
            "value": order_summary["waiting_approval"] or 0,
            "detail": "Manager action",
            "tone": "amber",
        },
        {
            "label": "Awaiting Receipt",
            "value": order_summary["awaiting_receipt"] or 0,
            "detail": "Approved, no receipt",
            "tone": "blue",
        },
        {
            "label": "Partially Received",
            "value": order_summary["partially_received"] or 0,
            "detail": "Outstanding quantity",
            "tone": "violet",
        },
        {
            "label": "Fully Received",
            "value": order_summary["fully_received"] or 0,
            "detail": "Receipt complete",
            "tone": "green",
        },
    ]
    return {
        "view_type": "purchase_receiving",
        "template_name": "purchase/dashboard.html",
        "date_from": start,
        "date_to": end,
        "filter_error": filter_error,
        "period_label": f"{start:%d/%m/%Y} – {end:%d/%m/%Y}",
        "country": "Indonesia",
        "branches": branches,
        "branch_links": _branch_links(branches),
        "branch_count": selected_branch_count,
        "document_value_label": _money(document_value),
        "metrics": [
            _metric(
                "Purchase Order Value",
                _money(document_value),
                f"{order_summary['order_count'] or 0} active PO in period",
                "blue",
            ),
            _metric(
                "Received Line Value",
                _money(received_value),
                f"{_quantity(line_summary['total_received_quantity'])} units received",
                "green",
            ),
            _metric(
                "Outstanding Line Value",
                _money(outstanding_value),
                f"{_quantity(line_summary['total_ordered_quantity'])} units ordered",
                "amber",
            ),
            _metric(
                "Posted Goods Receipts",
                receipt_count,
                f"{on_time_rate:.1f}% early or on time",
                "violet",
            ),
        ],
        "pipeline": pipeline,
        "completion_percent": float(completion),
        "completion_label": f"{completion:.1f}%",
        "completion_degrees": float(completion * Decimal("3.6")),
        "ordered_quantity_label": _quantity(line_summary["total_ordered_quantity"]),
        "received_quantity_label": _quantity(line_summary["total_received_quantity"]),
        "accepted_quantity_label": _quantity(quality_summary["total_accepted_quantity"]),
        "rejected_quantity_label": _quantity(quality_summary["total_rejected_quantity"]),
        "receipt_order_count": receipt_summary["order_count"] or 0,
        "order_statuses": _dashboard_status_rows(period_orders, PurchaseOrder.Status.choices),
        "receipt_statuses": _dashboard_status_rows(period_receipts, GoodsReceipt.Status.choices),
        "vendors": vendor_rows,
        "recent_orders": recent_orders,
        "recent_receipts": recent_receipts,
        "can_add_order": can_purchase_entity_action(user, "purchases", "add"),
    }


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


def build_purchase_analytics(feature_slug, user, params=None, *, today=None):
    """Return procurement analytics using a fixed number of aggregate queries."""
    if feature_slug not in ANALYTIC_FEATURES:
        return None
    if feature_slug == "overview":
        return _purchase_receiving_analytics(user, params or {}, today=today)
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
