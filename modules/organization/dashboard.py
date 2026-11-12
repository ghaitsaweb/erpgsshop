"""Shared, bounded presentation helpers for organization-aware dashboards."""

from datetime import date
from decimal import Decimal

from django.utils import timezone

from .models import Branch


MAX_DASHBOARD_DAYS = 366
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
    (18, 34), (39, 62), (49, 56), (59, 66),
    (64, 42), (72, 69), (82, 40), (89, 57),
)
SPARK_PATHS = (
    "M1 17 L7 10 L13 15 L19 5 L25 13 L31 9 L37 18 L43 6 L49 10",
    "M1 16 L7 13 L13 6 L19 8 L25 14 L31 11 L37 16 L43 3 L49 12",
    "M1 8 L7 14 L13 10 L19 17 L25 7 L31 13 L37 5 L43 14 L49 11",
    "M1 13 L7 8 L13 11 L19 15 L25 14 L31 17 L37 6 L43 12 L49 4",
)


def parse_dashboard_period(params, *, today=None):
    today = today or timezone.localdate()
    default_start = today.replace(day=1)
    raw_start = str((params or {}).get("date_from") or "").strip()
    raw_end = str((params or {}).get("date_to") or "").strip()
    if not raw_start and not raw_end:
        return default_start, today, ""
    try:
        start = date.fromisoformat(raw_start)
        end = date.fromisoformat(raw_end)
    except (TypeError, ValueError):
        return default_start, today, "Use valid From and To dates."
    if end < start:
        return default_start, today, "To date cannot be earlier than From date."
    if (end - start).days + 1 > MAX_DASHBOARD_DAYS:
        return default_start, today, f"Choose a period of at most {MAX_DASHBOARD_DAYS} days."
    return start, end, ""


def period_label(start, end):
    return f"{start:%d/%m/%Y} – {end:%d/%m/%Y}"


def money(value):
    return f"Rp {Decimal(value or 0):,.0f}".replace(",", ".")


def quantity(value):
    rendered = f"{Decimal(value or 0):,.3f}".rstrip("0").rstrip(".")
    return rendered.replace(",", ".")


def branch_nodes(rows, context, *, total_value, value_formatter=quantity, count_suffix="records"):
    """Turn one bounded aggregate into map nodes without query growth."""
    selected_ids = tuple(getattr(context, "active_branch_ids", ()) or ())
    values = {
        row["dashboard_branch_id"]: {
            "value": row.get("dashboard_value") or Decimal("0"),
            "count": row.get("dashboard_count") or 0,
        }
        for row in rows
        if row.get("dashboard_branch_id") in selected_ids
    }
    ordered_ids = list(values)[:MAX_BRANCH_NODES]
    if len(ordered_ids) < MAX_BRANCH_NODES:
        ordered_ids.extend(
            Branch.objects.filter(pk__in=selected_ids, active=True)
            .exclude(pk__in=ordered_ids)
            .order_by("company__name", "-is_main", "sequence", "name", "pk")
            .values_list("pk", flat=True)[: MAX_BRANCH_NODES - len(ordered_ids)]
        )
    branches = Branch.objects.filter(pk__in=ordered_ids).select_related("company").in_bulk()
    nodes = []
    total = Decimal(total_value or 0)
    for index, branch_id in enumerate(ordered_ids):
        branch = branches.get(branch_id)
        if branch is None:
            continue
        aggregate = values.get(branch_id, {"value": Decimal("0"), "count": 0})
        value = Decimal(aggregate["value"] or 0)
        share = float(value / total * 100) if total else 0
        normalized = branch.name.lower()
        position = next(
            (coordinates for city, coordinates in CITY_POSITIONS.items() if city in normalized),
            FALLBACK_POSITIONS[index % len(FALLBACK_POSITIONS)],
        )
        nodes.append({
            "id": branch.pk,
            "name": branch.name,
            "code": branch.code,
            "company": branch.company.name,
            "is_main": branch.is_main,
            "value_label": value_formatter(value),
            "count": aggregate["count"],
            "count_label": f"{aggregate['count']} {count_suffix}",
            "share": share,
            "share_label": f"{share:.1f}".replace(".", ","),
            "x": position[0],
            "y": position[1],
            "tone": index % 6,
            "spark_path": SPARK_PATHS[index % len(SPARK_PATHS)],
        })
    return nodes


def branch_links(nodes):
    if len(nodes) < 2:
        return []
    hub = next((node for node in nodes if node["is_main"]), nodes[0])
    satellites = [node for node in nodes if node["id"] != hub["id"]]

    def curve(source, target, bend=0):
        midpoint_x = (source["x"] + target["x"]) / 2
        midpoint_y = (source["y"] + target["y"]) / 2 - 7 - bend
        return f'M {source["x"]} {source["y"]} Q {midpoint_x:.1f} {midpoint_y:.1f} {target["x"]} {target["y"]}'

    links = [
        {"path": curve(hub, node, (index % 3) * 2), "tone": node["tone"], "kind": "primary"}
        for index, node in enumerate(satellites)
    ]
    links.extend(
        {"path": curve(source, target, -3), "tone": target["tone"], "kind": "secondary"}
        for source, target in zip(satellites, satellites[1:])
    )
    return links


def status_rows(queryset, choices):
    from django.db.models import Count

    labels = dict(choices)
    rows = list(queryset.values("status").annotate(total=Count("pk")).order_by("status"))
    total = sum(row["total"] for row in rows)
    return [
        {
            "label": labels.get(row["status"], row["status"] or "Unassigned"),
            "total": row["total"],
            "share": row["total"] / total * 100 if total else 0,
        }
        for row in rows
    ]
