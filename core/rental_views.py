import csv
from calendar import month_name
from datetime import date, timedelta

from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth
from django.http import StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone

from .models import SaleOrderLine


class _Echo:
    def write(self, value):
        return value


def _rental_queryset(request):
    queryset = SaleOrderLine.objects.filter(
        Q(odoo_source_id__isnull=False)
        | Q(rental_start_date__isnull=False)
        | Q(rental_return_date__isnull=False)
        | ~Q(rental_status="")
    ).select_related("order", "order__customer", "product", "order__company")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "all")
    urgency = request.GET.get("urgency", "all")
    today = timezone.localdate()

    if query:
        queryset = queryset.filter(
            Q(order__number__icontains=query)
            | Q(order__customer__name__icontains=query)
            | Q(description__icontains=query)
            | Q(product__name__icontains=query)
        )
    if status in {SaleOrderLine.RentalStatus.RETURNED, SaleOrderLine.RentalStatus.AT_CUSTOMER}:
        queryset = queryset.filter(rental_status=status)
    else:
        status = "all"
    if urgency == "overdue":
        queryset = queryset.filter(rental_status=SaleOrderLine.RentalStatus.AT_CUSTOMER, rental_return_date__lte=today)
    elif urgency == "due_soon":
        queryset = queryset.filter(
            rental_status=SaleOrderLine.RentalStatus.AT_CUSTOMER,
            rental_return_date__gt=today,
            rental_return_date__lte=today + timedelta(days=2),
        )
    elif urgency == "open":
        queryset = queryset.filter(rental_status=SaleOrderLine.RentalStatus.AT_CUSTOMER)
    else:
        urgency = "all"
    return queryset, query, status, urgency, today


def _export_csv(queryset):
    writer = csv.writer(_Echo())

    def content():
        yield writer.writerow(
            ["Sales Order", "Customer", "Description", "Qty", "Tanggal Sewa", "Tanggal Kembali", "Status Barang", "Subtotal"]
        )
        for line in queryset.iterator(chunk_size=500):
            yield writer.writerow(
                [
                    line.order.number,
                    line.order.customer.name,
                    line.description,
                    line.quantity,
                    line.rental_start_date or "",
                    line.rental_return_date or "",
                    line.get_rental_status_display() if line.rental_status else "",
                    line.subtotal,
                ]
            )

    response = StreamingHttpResponse(content(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="detail-sewa.csv"'
    return response


def _filter_month(queryset, field, key):
    if key == "none":
        return queryset.filter(**{f"{field}__isnull": True}), key
    if not key:
        return queryset, ""
    try:
        month = date.fromisoformat(f"{key}-01")
    except ValueError:
        return queryset, ""
    next_month = date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)
    return queryset.filter(**{f"{field}__gte": month, f"{field}__lt": next_month}), key


def rental_detail_list(request, config, can_change=False, can_delete=False):
    queryset, query, status, urgency, today = _rental_queryset(request)
    view_mode = request.GET.get("view", "grouped")
    if view_mode not in {"grouped", "list"}:
        view_mode = "grouped"
    group_start_filter = ""
    group_return_filter = ""
    if view_mode == "list":
        queryset, group_start_filter = _filter_month(queryset, "rental_start_date", request.GET.get("start_month", ""))
        queryset, group_return_filter = _filter_month(queryset, "rental_return_date", request.GET.get("return_month", ""))

    ordering = request.GET.get("sort", "return_date")
    orderings = {
        "return_date": ("rental_return_date", "order__number"),
        "newest": ("-order__order_date", "-id"),
        "customer": ("order__customer__name", "rental_return_date"),
    }
    if ordering not in orderings:
        ordering = "return_date"
    queryset = queryset.order_by(*orderings[ordering])
    if request.GET.get("export") == "csv":
        return _export_csv(queryset)

    value_expression = ExpressionWrapper(
        F("quantity") * F("unit_price") * (100 - F("discount_percent")) / 100,
        output_field=DecimalField(max_digits=18, decimal_places=2),
    )
    summary = queryset.aggregate(
        line_count=Count("id"),
        total_quantity=Coalesce(Sum("quantity"), 0, output_field=DecimalField(max_digits=18, decimal_places=3)),
        value=Coalesce(Sum(value_expression), 0, output_field=DecimalField(max_digits=18, decimal_places=2)),
    )
    groups = []
    page = None
    rows = []
    selected_group_key = ""
    if view_mode == "grouped":
        aggregate_rows = list(
            queryset.order_by().annotate(
                start_month=TruncMonth("rental_start_date"),
                return_month=TruncMonth("rental_return_date"),
            )
            .values("start_month", "return_month")
            .annotate(
                line_count=Count("id"),
                total_quantity=Coalesce(Sum("quantity"), 0, output_field=DecimalField(max_digits=18, decimal_places=3)),
                total_value=Coalesce(Sum(value_expression), 0, output_field=DecimalField(max_digits=18, decimal_places=2)),
            )
        )
        aggregate_rows.sort(
            key=lambda item: (
                item["start_month"] is None,
                item["start_month"] or date.max,
                item["return_month"] is None,
                item["return_month"] or date.max,
            )
        )
        group_map = {}
        for item in aggregate_rows:
            start_key = item["start_month"].strftime("%Y-%m") if item["start_month"] else "none"
            group = group_map.get(start_key)
            if group is None:
                group = {
                    "key": start_key,
                    "month": item["start_month"],
                    "label": (
                        f"{month_name[item['start_month'].month]} {item['start_month'].year}"
                        if item["start_month"]
                        else "None"
                    ),
                    "line_count": 0,
                    "total_quantity": 0,
                    "total_value": 0,
                    "children": [],
                }
                group_map[start_key] = group
                groups.append(group)
            group["line_count"] += item["line_count"]
            group["total_quantity"] += item["total_quantity"]
            group["total_value"] += item["total_value"]
            group["children"].append(
                {
                    "key": item["return_month"].strftime("%Y-%m") if item["return_month"] else "none",
                    "month": item["return_month"],
                    "label": (
                        f"{month_name[item['return_month'].month]} {item['return_month'].year}"
                        if item["return_month"]
                        else "None"
                    ),
                    "line_count": item["line_count"],
                    "total_quantity": item["total_quantity"],
                    "total_value": item["total_value"],
                }
            )
        requested_group_key = request.GET.get("group_start", "")
        available_group_keys = {group["key"] for group in groups}
        current_month_key = today.strftime("%Y-%m")
        if requested_group_key in available_group_keys:
            selected_group_key = requested_group_key
        elif current_month_key in available_group_keys:
            selected_group_key = current_month_key
        elif groups:
            selected_group_key = groups[-1]["key"]
        for group in groups:
            group["expanded"] = group["key"] == selected_group_key
    else:
        page = Paginator(queryset, 60).get_page(request.GET.get("page"))
        rows = list(page.object_list)
        due_soon_limit = today + timedelta(days=2)
        for line in rows:
            line.is_overdue = bool(
                line.rental_status == SaleOrderLine.RentalStatus.AT_CUSTOMER
                and line.rental_return_date
                and line.rental_return_date <= today
            )
            line.is_due_soon = bool(
                line.rental_status == SaleOrderLine.RentalStatus.AT_CUSTOMER
                and line.rental_return_date
                and today < line.rental_return_date <= due_soon_limit
            )

    return render(
        request,
        "sales/rental_detail_list.html",
        {
            "config": config,
            "entity": "rental-details",
            "view_mode": view_mode,
            "groups": groups,
            "selected_group_key": selected_group_key,
            "group_start_filter": group_start_filter,
            "group_return_filter": group_return_filter,
            "can_change": can_change,
            "can_delete": can_delete,
            "rows": rows,
            "page": page,
            "query": query,
            "status_filter": status,
            "urgency_filter": urgency,
            "sort": ordering,
            "today": today,
            "summary": summary,
        },
    )
