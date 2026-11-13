"""Structured cost-center helpers shared by Purchase documents and accounting."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.core.exceptions import ValidationError


ASSIGNMENT_FIELDS = (
    "cost_center",
    "cost_center_mode",
    "cost_center_distribution_ids",
    "cost_center_distribution_data",
    "cost_center_analytic_tag_ids",
)
MAX_COST_CENTER_ASSIGNMENTS = 100


def relation_id(value):
    if not value:
        return None
    if hasattr(value, "pk"):
        return value.pk
    if isinstance(value, dict):
        value = value.get("pk") or value.get("value")
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return value


def _json_safe(value):
    if hasattr(value, "pk"):
        return value.pk
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(child) for child in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def normalize_relation_ids(value):
    if hasattr(value, "ids"):
        value = value.ids
    if not value:
        return []
    normalized = []
    seen = set()
    for item in value:
        item_id = relation_id(item)
        if isinstance(item_id, str) and item_id.strip().isdigit():
            item_id = int(item_id.strip())
        if not isinstance(item_id, int) or item_id <= 0:
            raise ValidationError("Cost-center and analytic-tag assignments require valid record IDs.")
        if item_id not in seen:
            normalized.append(item_id)
            seen.add(item_id)
    if len(normalized) > MAX_COST_CENTER_ASSIGNMENTS:
        raise ValidationError(
            f"One Purchase assignment supports at most {MAX_COST_CENTER_ASSIGNMENTS} cost centers or analytic tags."
        )
    return normalized


def normalize_distribution_data(value):
    if isinstance(value, dict):
        value = value.get("rows") or []
    if not value:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValidationError("Cost-center distribution data must be a list of rows.")
    rows = []
    for raw_row in value:
        if not isinstance(raw_row, dict):
            raise ValidationError("Every cost-center distribution row must be an object.")
        cost_center_id = relation_id(
            raw_row.get("cost_center_id") or raw_row.get("cost_center")
        )
        if isinstance(cost_center_id, str) and cost_center_id.strip().isdigit():
            cost_center_id = int(cost_center_id.strip())
        if not isinstance(cost_center_id, int) or cost_center_id <= 0:
            raise ValidationError("Every distribution row requires a valid cost_center_id.")
        try:
            percentage = Decimal(str(raw_row.get("percentage", "")))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationError("Every distribution row requires a numeric percentage.") from exc
        if percentage <= 0 or percentage > 100:
            raise ValidationError("Distribution percentages must be greater than 0 and at most 100.")
        row = {
            "cost_center_id": cost_center_id,
            "percentage": str(percentage.normalize()),
        }
        for field_name in ("cost_center", "cost_center_name", "amount"):
            if field_name in raw_row:
                row[field_name] = _json_safe(raw_row[field_name])
        rows.append(row)
    if len(rows) > MAX_COST_CENTER_ASSIGNMENTS:
        raise ValidationError(
            f"One Purchase distribution supports at most {MAX_COST_CENTER_ASSIGNMENTS} rows."
        )
    if len({row["cost_center_id"] for row in rows}) != len(rows):
        raise ValidationError("A cost center can appear only once in one distribution.")
    return rows


def validate_purchase_cost_center_values(
    *,
    company_id,
    cost_center="",
    cost_center_mode="single",
    cost_center_distribution_ids=None,
    cost_center_distribution_data=None,
    cost_center_analytic_tag_ids=None,
):
    return validate_purchase_cost_center_batch(
        company_id,
        [
            {
                "cost_center": cost_center,
                "cost_center_mode": cost_center_mode,
                "cost_center_distribution_ids": cost_center_distribution_ids,
                "cost_center_distribution_data": cost_center_distribution_data,
                "cost_center_analytic_tag_ids": cost_center_analytic_tag_ids,
            }
        ],
    )[0]


def validate_purchase_cost_center_batch(company_id, assignments):
    normalized_assignments = []
    all_cost_center_ids = set()
    all_tag_ids = set()
    for assignment in assignments:
        distribution_ids = normalize_relation_ids(
            assignment.get("cost_center_distribution_ids")
        )
        tag_ids = normalize_relation_ids(
            assignment.get("cost_center_analytic_tag_ids")
        )
        rows = normalize_distribution_data(
            assignment.get("cost_center_distribution_data")
        )
        row_ids = {row["cost_center_id"] for row in rows}
        assignment_cost_center_ids = set(distribution_ids) | row_ids
        mode = assignment.get("cost_center_mode") or "single"
        if mode not in {"single", "distribution"}:
            raise ValidationError("Cost-center mode must be single or distribution.")
        if mode == "single" and len(assignment_cost_center_ids) > 1:
            raise ValidationError("Single cost-center mode accepts at most one assigned cost center.")
        if mode == "distribution":
            if not rows:
                raise ValidationError("Distribution mode requires percentage rows.")
            if sum((Decimal(row["percentage"]) for row in rows), Decimal("0")) != Decimal("100"):
                raise ValidationError("Cost-center distribution percentages must total exactly 100%.")
        normalized_assignments.append(
            {
                "cost_center": str(assignment.get("cost_center") or "").strip(),
                "cost_center_mode": mode,
                "cost_center_distribution_ids": distribution_ids,
                "cost_center_distribution_data": rows,
                "cost_center_analytic_tag_ids": tag_ids,
            }
        )
        all_cost_center_ids.update(assignment_cost_center_ids)
        all_tag_ids.update(tag_ids)

    AccountingCostCenter = apps.get_model("gsshop_finance", "AccountingCostCenter")
    AccountTag = apps.get_model("gsshop_finance", "AccountTag")
    valid_cost_center_ids = set(
        AccountingCostCenter.objects.filter(
            company_id=company_id,
            pk__in=all_cost_center_ids,
        ).values_list("pk", flat=True)
    )
    if valid_cost_center_ids != all_cost_center_ids:
        raise ValidationError("Every assigned cost center must belong to the Purchase document company.")
    valid_tag_ids = set(
        AccountTag.objects.filter(
            company_id=company_id,
            pk__in=all_tag_ids,
        ).values_list("pk", flat=True)
    )
    if valid_tag_ids != all_tag_ids:
        raise ValidationError("Every analytic tag must belong to the Purchase document company.")
    return normalized_assignments


def cost_center_analytic_values(source, parent=None):
    if source is None:
        return cost_center_analytic_values(parent) if parent is not None else {}
    values = {
        "cost_center": str(getattr(source, "cost_center", "") or "").strip(),
        "cost_center_mode": getattr(source, "cost_center_mode", "single") or "single",
        "cost_center_distribution_ids": normalize_relation_ids(
            getattr(source, "cost_center_distribution_ids", [])
        ),
        "cost_center_distribution_data": normalize_distribution_data(
            getattr(source, "cost_center_distribution_data", [])
        ),
        "cost_center_analytic_tag_ids": normalize_relation_ids(
            getattr(source, "cost_center_analytic_tag_ids", [])
        ),
    }
    if any(
        values[field_name]
        for field_name in (
            "cost_center",
            "cost_center_distribution_ids",
            "cost_center_distribution_data",
            "cost_center_analytic_tag_ids",
        )
    ):
        return values
    return cost_center_analytic_values(parent) if parent is not None else values


def purchase_invoice_analytic_values(source, lines=()):
    values = cost_center_analytic_values(source)
    if any(
        values.get(field_name)
        for field_name in (
            "cost_center",
            "cost_center_distribution_ids",
            "cost_center_distribution_data",
        )
    ):
        return values
    for line in lines:
        line_values = cost_center_analytic_values(line, parent=source)
        if any(
            line_values.get(field_name)
            for field_name in (
                "cost_center",
                "cost_center_distribution_ids",
                "cost_center_distribution_data",
            )
        ):
            return line_values
    return values


def assignment_kwargs(source, parent=None):
    return cost_center_analytic_values(source, parent=parent)
