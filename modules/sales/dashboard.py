"""Bounded, branch-aware aggregates owned by the Sales application."""

from datetime import date
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db.models import (
    BigIntegerField,
    CharField,
    Count,
    OuterRef,
    Subquery,
    Sum,
)
from django.db.models.functions import Cast, Coalesce, TruncMonth
from django.utils import timezone

from core.models import SaleOrder
from modules.organization.context import get_current_context, scope_queryset
from modules.organization.models import Branch, RecordOrganizationScope


MAX_DASHBOARD_DAYS = 366
MAX_BRANCH_NODES = 8
TREND_MONTHS = 12
REVENUE_STATUSES = (SaleOrder.Status.SALE, SaleOrder.Status.CLOSED)

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
    (18, 34), (39, 62), (49, 56), (59, 66),
    (64, 42), (72, 69), (82, 40), (89, 57),
)
SPARK_PATHS = (
    "M1 17 L7 10 L13 15 L19 5 L25 13 L31 9 L37 18 L43 6 L49 10",
    "M1 16 L7 13 L13 6 L19 8 L25 14 L31 11 L37 16 L43 3 L49 12",
    "M1 8 L7 14 L13 10 L19 17 L25 7 L31 13 L37 5 L43 14 L49 11",
    "M1 13 L7 8 L13 11 L19 15 L25 14 L31 17 L37 6 L43 12 L49 4",
)


def _money(value):
    amount = Decimal(value or 0)
    return f"Rp {amount:,.0f}".replace(",", ".")


def _previous_year(value):
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _shift_month(value, offset):
    absolute_month = value.year * 12 + value.month - 1 + offset
    year, month_index = divmod(absolute_month, 12)
    return date(year, month_index + 1, 1)


def _comparison(current, previous):
    current = Decimal(current or 0)
    previous = Decimal(previous or 0)
    if not previous:
        return {"label": "No prior-year data", "tone": "neutral"}
    change = (current - previous) / abs(previous) * Decimal("100")
    direction = "+" if change >= 0 else ""
    return {
        "label": f"{direction}{change:.1f}% vs same period last year",
        "tone": "up" if change >= 0 else "down",
    }


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


def _branch_revenue_rows(orders, context):
    selected_branch_ids = tuple(getattr(context, "active_branch_ids", ()) or ())
    if not selected_branch_ids:
        return []

    content_type = ContentType.objects.get_for_model(SaleOrder, for_concrete_model=False)
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
        .annotate(revenue=Sum("total"), order_count=Count("pk"))
        .order_by("-revenue", "dashboard_branch_id")[:MAX_BRANCH_NODES]
    )


def _branch_nodes(orders, context, total_revenue):
    revenue_rows = _branch_revenue_rows(orders, context)
    revenue_by_branch = {
        row["dashboard_branch_id"]: {
            "revenue": row["revenue"] or Decimal("0"),
            "order_count": row["order_count"],
        }
        for row in revenue_rows
    }
    ordered_ids = list(revenue_by_branch)
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
        values = revenue_by_branch.get(branch_id, {"revenue": Decimal("0"), "order_count": 0})
        revenue = values["revenue"]
        share = float((revenue / total_revenue * 100) if total_revenue else 0)
        x, y = _branch_position(branch.name, index)
        nodes.append(
            {
                "id": branch.pk,
                "name": branch.name,
                "code": branch.code,
                "company": branch.company.name,
                "is_main": branch.is_main,
                "revenue": revenue,
                "revenue_label": _money(revenue),
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
    """Build a bounded visual network without any additional ORM reads."""
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


def _revenue_trend(visible_orders, end):
    current_start = _shift_month(end.replace(day=1), -(TREND_MONTHS - 1))
    previous_start = _shift_month(current_start, -TREND_MONTHS)
    rows = list(
        visible_orders.filter(
            status__in=REVENUE_STATUSES,
            order_date__gte=previous_start,
            order_date__lte=end,
        )
        .annotate(month=TruncMonth("order_date"))
        .values("month")
        .annotate(revenue=Sum("total"))
        .order_by("month")
    )
    revenue_by_month = {
        (row["month"].year, row["month"].month): row["revenue"] or Decimal("0")
        for row in rows
    }
    points = []
    for index in range(TREND_MONTHS):
        month = _shift_month(current_start, index)
        previous_month = _shift_month(month, -TREND_MONTHS)
        points.append(
            {
                "label": month.strftime("%b"),
                "value": revenue_by_month.get((month.year, month.month), Decimal("0")),
                "previous": revenue_by_month.get(
                    (previous_month.year, previous_month.month), Decimal("0")
                ),
            }
        )

    maximum = max(
        (point[key] for point in points for key in ("value", "previous")),
        default=Decimal("0"),
    )
    scale = maximum or Decimal("1")
    current_polyline = []
    previous_polyline = []
    for index, point in enumerate(points):
        point["value_label"] = _money(point["value"])
        point["previous_label"] = _money(point["previous"])
        point["height"] = float(point["value"] / scale * 100)
        point["previous_height"] = float(point["previous"] / scale * 100)
        x = 50 + index * 100
        current_polyline.append(f'{x},{220 - point["height"] * 1.8:.1f}')
        previous_polyline.append(f'{x},{220 - point["previous_height"] * 1.8:.1f}')
    return {
        "points": points,
        "current_polyline": " ".join(current_polyline),
        "previous_polyline": " ".join(previous_polyline),
        "start_year": current_start.year,
        "end_year": end.year,
    }


def build_sales_dashboard(user, params, *, context=None, today=None):
    """Return fixed-query, date-bounded Sales analytics for the active branches."""
    context = context or get_current_context()
    start, end, filter_error = _parse_period(params, today=today)
    if context is None:
        return {
            "date_from": start,
            "date_to": end,
            "filter_error": filter_error,
            "period_label": f'{start:%d/%m/%Y} – {end:%d/%m/%Y}',
            "metrics": [],
            "branches": [],
            "branch_links": [],
            "statuses": [],
            "trend": {"points": []},
        }

    visible_orders = scope_queryset(SaleOrder.objects.all(), context=context)
    period_orders = visible_orders.filter(order_date__range=(start, end))
    revenue_orders = period_orders.filter(status__in=REVENUE_STATUSES)
    summary = revenue_orders.aggregate(
        order_count=Count("pk"),
        revenue=Sum("total"),
        promo_savings=Sum("discount_amount"),
    )
    revenue = summary["revenue"] or Decimal("0")
    order_count = summary["order_count"] or 0
    promo_savings = summary["promo_savings"] or Decimal("0")
    average = revenue / order_count if order_count else Decimal("0")
    previous_summary = visible_orders.filter(
        order_date__range=(_previous_year(start), _previous_year(end)),
        status__in=REVENUE_STATUSES,
    ).aggregate(
        order_count=Count("pk"),
        revenue=Sum("total"),
        promo_savings=Sum("discount_amount"),
    )
    previous_revenue = previous_summary["revenue"] or Decimal("0")
    previous_order_count = previous_summary["order_count"] or 0
    previous_average = (
        previous_revenue / previous_order_count if previous_order_count else Decimal("0")
    )
    previous_promo = previous_summary["promo_savings"] or Decimal("0")

    status_counts = {
        row["status"]: row["total"]
        for row in period_orders.values("status").annotate(total=Count("pk")).order_by("status")
    }
    status_total = sum(status_counts.values())
    status_labels = dict(SaleOrder.Status.choices)
    status_order = (
        SaleOrder.Status.DRAFT,
        SaleOrder.Status.SENT,
        SaleOrder.Status.TO_APPROVE,
        SaleOrder.Status.SALE,
        SaleOrder.Status.CLOSED,
        SaleOrder.Status.CANCELLED,
    )
    statuses = [
        {
            "value": status,
            "label": status_labels[status],
            "count": status_counts.get(status, 0),
            "share": (status_counts.get(status, 0) / status_total * 100) if status_total else 0,
        }
        for status in status_order
        if status_counts.get(status, 0)
    ]

    branches = _branch_nodes(revenue_orders, context, revenue)
    selected_branch_count = len(tuple(getattr(context, "active_branch_ids", ()) or ()))
    gross_sales = revenue + promo_savings
    promo_share = promo_savings / gross_sales * 100 if gross_sales else Decimal("0")
    metrics = [
        {
            "key": "revenue",
            "label": "Revenue",
            "value": _money(revenue),
            "detail": _comparison(revenue, previous_revenue),
            "tone": "indigo",
        },
        {
            "key": "orders",
            "label": "Sales Orders",
            "value": order_count,
            "detail": _comparison(order_count, previous_order_count),
            "tone": "emerald",
        },
        {
            "key": "average",
            "label": "Avg. Order Value",
            "value": _money(average),
            "detail": _comparison(average, previous_average),
            "tone": "magenta",
        },
        {
            "key": "promo",
            "label": "Promo Savings Given",
            "value": _money(promo_savings),
            "detail": {
                "label": f"{promo_share:.1f}% of gross sales",
                "tone": _comparison(promo_savings, previous_promo)["tone"],
            },
            "tone": "amber",
        },
    ]
    return {
        "country": "Indonesia",
        "date_from": start,
        "date_to": end,
        "filter_error": filter_error,
        "period_label": f'{start:%d/%m/%Y} – {end:%d/%m/%Y}',
        "metrics": metrics,
        "revenue": revenue,
        "revenue_label": _money(revenue),
        "order_count": order_count,
        "branches": branches,
        "branch_links": _branch_links(branches),
        "branch_count": selected_branch_count,
        "statuses": statuses,
        "trend": _revenue_trend(visible_orders, end),
    }
