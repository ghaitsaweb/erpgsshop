"""Dashboard builders for core Inventory and Operations-owned Manufacturing."""

from decimal import Decimal

from django.db.models import BigIntegerField, Count, F, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse

from core.models import Product, StockMove, Warehouse
from modules.operations.models import BillOfMaterial, ManufacturingOrder, QualityInspection
from modules.organization.context import get_current_context, scope_queryset
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


RECENT_ROWS = 8


def _scoped(model):
    return scope_queryset(model.objects.all())


def _main_branch_subquery():
    return Subquery(
        Branch.objects.filter(company_id=OuterRef("company_id"), is_main=True)
        .order_by("pk").values("pk")[:1],
        output_field=BigIntegerField(),
    )


def _metric(label, value, detail, tone="blue"):
    return {"label": label, "value": value, "detail": detail, "tone": tone}


def _base_payload(*, app_label, route_name, start, end, error, branches, **values):
    return {
        "template_name": "apphub/branch_dashboard.html",
        "feature_slug": "dashboard",
        "app_label": app_label,
        "route_name": route_name,
        "country": "Indonesia",
        "date_from": start,
        "date_to": end,
        "period_label": period_label(start, end),
        "filter_error": error,
        "branches": branches,
        "branch_links": branch_links(branches),
        "branch_count": len(branches),
        **values,
    }


def build_inventory_dashboard(user, params=None):
    start, end, error = parse_dashboard_period(params)
    moves = _scoped(StockMove).filter(movement_date__range=(start, end))
    products = _scoped(Product)
    warehouses = _scoped(Warehouse)
    summary = moves.aggregate(
        total=Count("pk"),
        inbound=Sum("quantity", filter=Q(direction=StockMove.Direction.IN)),
        outbound=Sum("quantity", filter=Q(direction=StockMove.Direction.OUT)),
    )
    inbound = summary["inbound"] or Decimal("0")
    outbound = summary["outbound"] or Decimal("0")
    total_flow = inbound + outbound
    context = get_current_context()
    branch_rows = list(
        moves.annotate(
            dashboard_branch_id=Coalesce(
                "warehouse__branch_id", _main_branch_subquery(), output_field=BigIntegerField()
            )
        )
        .values("dashboard_branch_id")
        .annotate(dashboard_value=Sum("quantity"), dashboard_count=Count("pk"))
        .order_by("-dashboard_value", "dashboard_branch_id")[:8]
    )
    branches = branch_nodes(
        branch_rows, context, total_value=total_flow,
        value_formatter=quantity, count_suffix="moves",
    )
    low_stock = products.filter(
        active=True,
        product_type=Product.ProductType.STOCKABLE,
        current_stock__lte=F("reorder_level"),
    ).count()
    direction_rows = list(
        moves.values("direction").annotate(total=Count("pk")).order_by("direction")
    )
    direction_total = sum(row["total"] for row in direction_rows)
    direction_labels = dict(StockMove.Direction.choices)
    breakdown = [
        {
            "label": direction_labels.get(row["direction"], row["direction"]),
            "total": row["total"],
            "share": row["total"] / direction_total * 100 if direction_total else 0,
        }
        for row in direction_rows
    ]
    top_products = list(
        moves.values("product__name")
        .annotate(total=Sum("quantity"))
        .order_by("-total", "product__name")[:6]
    )
    top_total = sum((row["total"] or Decimal("0")) for row in top_products)
    secondary = [
        {
            "label": row["product__name"],
            "total": quantity(row["total"]),
            "share": float((row["total"] or 0) / top_total * 100) if top_total else 0,
        }
        for row in top_products
    ]
    recent = []
    for move in moves.select_related("product", "warehouse").order_by("-movement_date", "-pk")[:RECENT_ROWS]:
        recent.append({"cells": [
            {"value": move.reference or f"Move #{move.pk}", "url": reverse("entity_edit", args=["stock-moves", move.pk])},
            {"value": move.product.name},
            {"value": move.warehouse.name},
            {"value": move.get_direction_display()},
            {"value": quantity(move.quantity), "align": "right"},
            {"value": move.movement_date.strftime("%d %b %Y")},
        ]})
    return _base_payload(
        app_label="Inventory", route_name="inventory_feature", start=start, end=end,
        error=error, branches=branches,
        title="Inventory Dashboard",
        live_badge="Live inventory control",
        intro=f"Stock movement visibility across {len(branches)} branches",
        network_title="stock movement by branch",
        scope_pill="Warehouse and product flow visibility",
        network_total_label=f"{quantity(total_flow)} units total movement",
        network_empty="No stock movement in this period.",
        primary_entity="stock-moves", primary_label="Stock Movements",
        secondary_entity="products", secondary_label="Products",
        metrics=[
            _metric("Stock In", quantity(inbound), "Units received in this period", "green"),
            _metric("Stock Out", quantity(outbound), "Units issued in this period", "amber"),
            _metric("Net Flow", quantity(inbound - outbound), "Inbound less outbound", "blue"),
            _metric("Low Stock", low_stock, "Products at or below reorder level", "violet"),
        ],
        flow_title="Warehouse → Stock Movement → Product Balance",
        pipeline=[
            {"label": "Warehouses", "value": warehouses.filter(active=True).count(), "detail": "Active storage locations", "tone": "blue"},
            {"label": "Movements", "value": summary["total"], "detail": "Posted in selected period", "tone": "violet"},
            {"label": "Stockable Products", "value": products.filter(active=True, product_type=Product.ProductType.STOCKABLE).count(), "detail": "Tracked inventory items", "tone": "green"},
            {"label": "Reorder Attention", "value": low_stock, "detail": "Products requiring review", "tone": "amber"},
        ],
        breakdown_title="Movement direction", breakdown=breakdown,
        secondary_title="Top products by movement", secondary_rows=secondary,
        recent_title="Latest Stock Movements",
        table_headers=["Reference", "Product", "Warehouse", "Direction", "Quantity", "Date"],
        recent_rows=recent,
        tour_steps=[
            {"target": "overview", "title": "Kontrol stok dari satu layar", "copy": "Dashboard menyatukan pergerakan barang, kondisi reorder, gudang, dan cabang dalam periode yang dipilih."},
            {"target": "network", "title": "Aktivitas stok terlihat per cabang", "copy": "Klik pin atau ranking untuk menyorot cabang dan kontribusi pergerakan stoknya."},
            {"target": "metrics", "title": "KPI inventory yang bisa ditindaklanjuti", "copy": "Stock In, Stock Out, Net Flow, dan Low Stock dihitung langsung dari transaksi yang dapat Anda akses."},
            {"target": "flow", "title": "Alur kontrol persediaan", "copy": "Lihat hubungan warehouse aktif, movement terposting, produk stockable, dan item yang perlu reorder."},
            {"target": "breakdown", "title": "Pola pergerakan barang", "copy": "Distribusi arah movement dan produk teraktif membantu tim mengidentifikasi tekanan stok."},
            {"target": "records", "title": "Tindak lanjut operasional", "copy": "Buka movement terbaru langsung dari tabel tanpa mencari dokumen secara manual."},
        ],
    )


def build_manufacturing_dashboard(user, params=None):
    start, end, error = parse_dashboard_period(params)
    orders = _scoped(ManufacturingOrder).filter(planned_start__date__range=(start, end))
    boms = _scoped(BillOfMaterial)
    inspections = _scoped(QualityInspection).filter(inspection_date__range=(start, end))
    summary = orders.aggregate(
        total=Count("pk"), quantity=Sum("quantity"),
        active=Count("pk", filter=Q(status__in=[ManufacturingOrder.Status.CONFIRMED, ManufacturingOrder.Status.IN_PROGRESS])),
        done=Count("pk", filter=Q(status=ManufacturingOrder.Status.DONE)),
    )
    planned_quantity = summary["quantity"] or Decimal("0")
    context = get_current_context()
    branch_rows = list(
        orders.annotate(
            dashboard_branch_id=Coalesce(
                "warehouse__branch_id", _main_branch_subquery(), output_field=BigIntegerField()
            )
        )
        .values("dashboard_branch_id")
        .annotate(dashboard_value=Sum("quantity"), dashboard_count=Count("pk"))
        .order_by("-dashboard_value", "dashboard_branch_id")[:8]
    )
    branches = branch_nodes(
        branch_rows, context, total_value=planned_quantity,
        value_formatter=quantity, count_suffix="orders",
    )
    result_rows = list(
        inspections.values("result").annotate(total=Count("pk")).order_by("result")
    )
    result_total = sum(row["total"] for row in result_rows)
    result_labels = dict(QualityInspection.Result.choices)
    secondary = [
        {"label": result_labels.get(row["result"], row["result"]), "total": row["total"], "share": row["total"] / result_total * 100 if result_total else 0}
        for row in result_rows
    ]
    recent = []
    for order in orders.select_related("bom__product", "warehouse").order_by("-planned_start", "-pk")[:RECENT_ROWS]:
        recent.append({"cells": [
            {"value": order.number, "url": reverse("entity_edit", args=["manufacturing-orders", order.pk])},
            {"value": order.bom.product.name},
            {"value": order.warehouse.name},
            {"value": quantity(order.quantity), "align": "right"},
            {"value": order.planned_start.strftime("%d %b %Y")},
            {"value": order.get_status_display(), "status": order.status},
        ]})
    return _base_payload(
        app_label="Manufacturing", route_name="manufacturing_feature", start=start, end=end,
        error=error, branches=branches,
        title="Manufacturing Dashboard", live_badge="Live production control",
        intro=f"Production planning across {len(branches)} branches",
        network_title="planned output by branch",
        scope_pill="Manufacturing Order to quality visibility",
        network_total_label=f"{quantity(planned_quantity)} units planned",
        network_empty="No Manufacturing Orders in this period.",
        primary_entity="manufacturing-orders", primary_label="Manufacturing Orders",
        secondary_entity="boms", secondary_label="Bills of Material",
        metrics=[
            _metric("Manufacturing Orders", summary["total"], "Orders in selected period", "blue"),
            _metric("Planned Output", quantity(planned_quantity), "Total production quantity", "violet"),
            _metric("Active Production", summary["active"], "Confirmed or in progress", "amber"),
            _metric("Completed", summary["done"], "Manufacturing Orders done", "green"),
        ],
        flow_title="Bill of Material → Production → Quality",
        pipeline=[
            {"label": "Active BOM", "value": boms.filter(active=True).count(), "detail": "Production recipes available", "tone": "blue"},
            {"label": "Draft", "value": orders.filter(status=ManufacturingOrder.Status.DRAFT).count(), "detail": "Awaiting confirmation", "tone": "violet"},
            {"label": "In Progress", "value": orders.filter(status=ManufacturingOrder.Status.IN_PROGRESS).count(), "detail": "Currently being produced", "tone": "amber"},
            {"label": "Quality Checks", "value": inspections.count(), "detail": "Inspections in selected period", "tone": "green"},
        ],
        breakdown_title="Manufacturing Order status",
        breakdown=status_rows(orders, ManufacturingOrder.Status.choices),
        secondary_title="Quality inspection result", secondary_rows=secondary,
        recent_title="Latest Manufacturing Orders",
        table_headers=["Order", "Product", "Warehouse", "Quantity", "Planned Start", "Status"],
        recent_rows=recent,
        tour_steps=[
            {"target": "overview", "title": "Kontrol produksi dari satu layar", "copy": "Dashboard menyatukan Manufacturing Order, planned output, BOM, dan quality inspection."},
            {"target": "network", "title": "Rencana output per cabang", "copy": "Peta menunjukkan kuantitas produksi yang direncanakan untuk setiap cabang dan warehouse."},
            {"target": "metrics", "title": "KPI produksi utama", "copy": "Jumlah order, planned output, produksi aktif, dan completed dihitung dari Manufacturing Order nyata."},
            {"target": "flow", "title": "Alur BOM sampai quality", "copy": "Pipeline membantu memonitor kesiapan BOM, draft order, work in progress, dan quality checks."},
            {"target": "breakdown", "title": "Status produksi dan kualitas", "copy": "Distribusi status order dan hasil inspection menunjukkan hambatan proses dengan cepat."},
            {"target": "records", "title": "Order terbaru siap ditindaklanjuti", "copy": "Klik nomor Manufacturing Order untuk langsung membuka detail produksi."},
        ],
    )
