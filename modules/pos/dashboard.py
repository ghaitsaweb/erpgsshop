"""Bounded aggregate analytics for Retail and Food & Beverage workspaces."""

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum

from modules.organization.context import scope_queryset as scope_organization_queryset

from .access import scope_pos_queryset
from .models import POSOrder, POSOrderLine
from .retail_models import (
    EmployeeMealProgramRecord,
    LoyaltyTransaction,
    POSInternalUsage,
    POSPayment,
    POSProductAvailability,
    POSProfile,
    POSPromotion,
    POSRFMCustomerResult,
    POSRealtimeChange,
)


POS_FEATURES = {
    "overview", "payments", "orders", "order-lines", "profit-loss", "rfm",
    "intelligence-overview", "cross-selling", "top-selling", "promotion-analytics",
    "risk", "expiry-waste", "feed", "rve-analysis", "member-behavior", "cashier",
    "outlets", "pareto", "pre-orders", "other-fnb-orders", "general-settings",
}
FNB_FEATURES = {"menu-engineering", "employee-meals"}


def _metric(label, value, detail, tone="blue"):
    return {"label": label, "value": value or 0, "detail": detail, "tone": tone}


def _scoped(user, entity, model):
    return scope_organization_queryset(scope_pos_queryset(user, entity, model.objects.all()))


def _rows(queryset, field):
    return [
        {"label": row[field] or "Unassigned", "total": row["total"]}
        for row in queryset.values(field).annotate(total=Count("id")).order_by(field)
    ]


def _result(metrics, breakdown_title, breakdown, recent_title, recent):
    return {
        "metrics": metrics,
        "breakdown_title": breakdown_title,
        "breakdown": breakdown,
        "recent_title": recent_title,
        "recent": list(recent[:8]),
    }


def build_pos_analytics(feature_slug, user):
    if feature_slug not in POS_FEATURES:
        return None

    orders = _scoped(user, "pos-orders", POSOrder).select_related("customer", "session", "session__register")
    order_totals = orders.exclude(status=POSOrder.Status.CANCELLED).aggregate(
        count=Count("id"), sales=Sum("total"), paid=Sum("paid_amount")
    )
    if feature_slug in {"overview", "intelligence-overview", "orders"}:
        return _result(
            [
                _metric("Orders", order_totals["count"], "Non-cancelled POS orders"),
                _metric("Sales", order_totals["sales"], "Gross retail and F&B sales", "violet"),
                _metric("Collected", order_totals["paid"], "Payment captured"),
                _metric("Open", orders.filter(status="open").count(), "Orders awaiting settlement", "amber"),
            ],
            "Orders by status",
            _rows(orders, "status"),
            "Recent orders",
            orders.order_by("-created_at"),
        )

    if feature_slug == "pre-orders":
        records = orders.filter(is_preorder=True)
        totals = records.aggregate(count=Count("id"), amount=Sum("total"))
        return _result(
            [
                _metric("Pre-Orders", totals["count"], "Orders reserved for later fulfilment"),
                _metric("Pre-Order Value", totals["amount"], "Gross reserved order value", "violet"),
                _metric("Ordered", records.filter(preorder_status="ordered").count(), "Awaiting readiness", "amber"),
                _metric("Ready", records.filter(preorder_status="ready").count(), "Ready for fulfilment"),
            ],
            "Pre-orders by status", _rows(records, "preorder_status"), "Upcoming pre-orders",
            records.order_by("fulfillment_date", "-created_at"),
        )

    if feature_slug == "other-fnb-orders":
        records = orders.filter(order_type__in=["dine_in", "takeaway", "delivery"])
        totals = records.aggregate(count=Count("id"), amount=Sum("total"))
        return _result(
            [
                _metric("F&B Orders", totals["count"], "Dine-in, takeaway, and delivery orders"),
                _metric("F&B Sales", totals["amount"], "Gross F&B order value", "violet"),
                _metric("Preparing", records.filter(fnb_state="preparing").count(), "Orders in kitchen", "amber"),
                _metric("Ready", records.filter(fnb_state="ready").count(), "Ready to serve or dispatch"),
            ],
            "F&B orders by type", _rows(records, "order_type"), "Recent F&B orders",
            records.order_by("-created_at"),
        )

    if feature_slug == "payments":
        records = _scoped(user, "pos-payments", POSPayment).select_related("order", "payment_method", "cashier")
        totals = records.aggregate(count=Count("id"), amount=Sum("amount"), mdr=Sum("mdr_charge_amount"))
        return _result(
            [_metric("Payments", totals["count"], "Recorded payment lines"), _metric("Amount", totals["amount"], "Collected value", "violet"), _metric("MDR Charges", totals["mdr"], "Merchant discount charges", "amber"), _metric("Methods", records.values("payment_method_id").distinct().count(), "Payment methods used")],
            "Payments by method", _rows(records, "payment_method__name"), "Recent payments", records.order_by("-payment_date"),
        )

    if feature_slug in {"order-lines", "top-selling", "cross-selling", "pareto"}:
        records = _scoped(user, "pos-orders", POSOrderLine).select_related("order", "product")
        amount = ExpressionWrapper(F("quantity") * F("unit_price"), output_field=DecimalField(max_digits=18, decimal_places=2))
        totals = records.aggregate(lines=Count("id"), units=Sum("quantity"), sales=Sum(amount))
        top_products = records.values("product__name").annotate(total=Sum("quantity")).order_by("-total")[:12]
        return _result(
            [_metric("Order Lines", totals["lines"], "Line-level demand"), _metric("Units", totals["units"], "Quantity sold"), _metric("Line Sales", totals["sales"], "Before header charges", "violet"), _metric("Products", records.values("product_id").distinct().count(), "Products sold")],
            "Top products by quantity", [{"label": row["product__name"], "total": row["total"]} for row in top_products], "Recent order lines", records.order_by("-created_at"),
        )

    if feature_slug == "profit-loss":
        records = _scoped(user, "pos-orders", POSOrderLine).exclude(order__status="cancelled").select_related("order", "product")
        revenue = ExpressionWrapper(F("quantity") * F("unit_price"), output_field=DecimalField(max_digits=18, decimal_places=2))
        cost = ExpressionWrapper(F("quantity") * F("product__purchase_price"), output_field=DecimalField(max_digits=18, decimal_places=2))
        totals = records.aggregate(revenue=Sum(revenue), cost=Sum(cost))
        gross = (totals["revenue"] or 0) - (totals["cost"] or 0)
        return _result([_metric("Revenue", totals["revenue"], "Line revenue", "violet"), _metric("Cost", totals["cost"], "Product standard cost", "amber"), _metric("Gross Profit", gross, "Revenue less product cost"), _metric("Orders", order_totals["count"], "Included transactions")], "Sales by order type", _rows(orders.exclude(status="cancelled"), "order_type"), "Recent profitable orders", orders.exclude(status="cancelled").order_by("-created_at"))

    if feature_slug == "promotion-analytics":
        records = _scoped(user, "pos-promotions", POSPromotion).select_related("company")
        return _result([_metric("Promotions", records.count(), "Configured promotions"), _metric("Active", records.filter(active=True).count(), "Enabled promotions", "violet"), _metric("Manual", records.filter(usage="manual").count(), "Cashier-selected offers"), _metric("Birthday", records.filter(birthday_promotion=True).count(), "Birthday campaigns")], "Promotions by usage", _rows(records, "usage"), "Recent promotions", records.order_by("-updated_at"))

    if feature_slug == "rfm":
        records = _scoped(user, "pos-rfm-results", POSRFMCustomerResult).select_related("customer", "run")
        return _result([_metric("Categorized Customers", records.count(), "Latest and historic RFM results"), _metric("Segments", records.values("segment").distinct().count(), "Defined customer segments"), _metric("Frequency", records.aggregate(value=Sum("frequency"))["value"], "Orders represented"), _metric("Monetary", records.aggregate(value=Sum("monetary_value"))["value"], "Customer spend", "violet")], "Customers by segment", _rows(records, "segment"), "Recent RFM results", records.order_by("-created_at"))

    if feature_slug == "member-behavior":
        records = _scoped(user, "loyalty-transactions", LoyaltyTransaction).select_related("wallet", "wallet__member")
        totals = records.aggregate(count=Count("id"), points=Sum("points"))
        return _result([_metric("Transactions", totals["count"], "Loyalty activity"), _metric("Point Movement", totals["points"], "Earn and redeem movement", "violet"), _metric("Members", records.values("wallet__member_id").distinct().count(), "Participating members"), _metric("Redemptions", records.filter(transaction_type="redeem").count(), "Reward redemptions")], "Loyalty activity by type", _rows(records, "transaction_type"), "Recent loyalty activity", records.order_by("-transaction_at"))

    if feature_slug == "general-settings":
        records = _scoped(user, "pos-profiles", POSProfile).select_related("company", "warehouse")
        return _result([_metric("Profiles", records.count(), "Configured outlets"), _metric("Active", records.filter(active=True).count(), "Operational profiles", "violet"), _metric("Retail", records.filter(profile_type="pos_retail").count(), "Retail profiles"), _metric("F&B", records.filter(profile_type="pos_fnb").count(), "Food & Beverage profiles")], "Profiles by type", _rows(records, "profile_type"), "Recently updated profiles", records.order_by("-updated_at"))

    if feature_slug == "rve-analysis":
        anomalies = orders.filter(
            Q(return_type__in=["return", "void", "exchange"])
            | Q(status__in=[POSOrder.Status.CANCELLED, POSOrder.Status.REFUNDED])
        )
        anomaly_totals = anomalies.aggregate(count=Count("id"), amount=Sum("total"))
        return _result(
            [
                _metric("RVE Orders", anomaly_totals["count"], "Returns, voids, and exchanges"),
                _metric("Returned", anomalies.filter(return_type="return").count(), "Returned sales", "violet"),
                _metric("Voided", anomalies.filter(Q(return_type="void") | Q(status=POSOrder.Status.CANCELLED)).count(), "Voided sales", "amber"),
                _metric("RVE Value", anomaly_totals["amount"], "Gross value under review"),
            ],
            "RVE by profile",
            _rows(anomalies, "profile__name"),
            "Recent RVE orders",
            anomalies.order_by("-updated_at"),
        )

    if feature_slug in {"risk", "expiry-waste"}:
        usages = _scoped(user, "pos-internal-usages", POSInternalUsage).select_related("reason", "profile")
        availability = _scoped(user, "pos-product-availability", POSProductAvailability).select_related("profile", "product")
        return _result([_metric("Wastage Records", usages.filter(usage_type="wastage").count(), "Recorded waste", "amber"), _metric("Complimentary", usages.filter(usage_type="complimentary").count(), "Complimentary consumption"), _metric("Sold Out", availability.filter(status="sold_out").count(), "Unavailable products", "amber"), _metric("Hidden", availability.filter(status="hidden").count(), "Suppressed from sale")], "Internal usage by type", _rows(usages, "usage_type"), "Recent internal usage", usages.order_by("-recorded_at"))

    if feature_slug == "cashier":
        records = _scoped(user, "pos-payments", POSPayment).select_related("cashier", "order", "payment_method")
        return _result([_metric("Cashiers", records.values("cashier_id").distinct().count(), "Cashiers with payments"), _metric("Payments", records.count(), "Payments processed"), _metric("Amount", records.aggregate(value=Sum("amount"))["value"], "Collected by cashiers", "violet"), _metric("Approvals", records.exclude(approval_code="").count(), "Approved transactions")], "Payments by cashier", _rows(records, "cashier__username"), "Recent cashier payments", records.order_by("-payment_date"))

    if feature_slug == "outlets":
        return _result([_metric("Profiles", _scoped(user, "pos-profiles", POSProfile).filter(active=True).count(), "Active outlets"), _metric("Registers", orders.values("session__register_id").distinct().count(), "Registers with orders"), _metric("Orders", order_totals["count"], "Non-cancelled orders"), _metric("Sales", order_totals["sales"], "Outlet sales", "violet")], "Orders by register", _rows(orders, "session__register__name"), "Recent outlet orders", orders.order_by("-created_at"))

    records = _scoped(user, "pos-realtime-changes", POSRealtimeChange).select_related("profile")
    return _result([_metric("Changes", records.count(), "Realtime change feed"), _metric("Published", records.exclude(published_at=None).count(), "Published events", "violet"), _metric("Acknowledged", records.exclude(acknowledged_at=None).count(), "Client acknowledgements"), _metric("Pending", records.filter(published_at=None).count(), "Waiting publication", "amber")], "Changes by operation", _rows(records, "operation"), "Recent realtime changes", records.order_by("-created_at"))


def build_fnb_analytics(feature_slug, user):
    if feature_slug not in FNB_FEATURES:
        return None
    if feature_slug == "employee-meals":
        records = _scoped(user, "employee-meal-records", EmployeeMealProgramRecord).select_related("employee", "program", "order")
        totals = records.aggregate(count=Count("id"), covered=Sum("company_covered_amount"), payable=Sum("employee_payable_amount"), cost=Sum("actual_meal_cost"))
        return _result([_metric("Redemptions", totals["count"], "Employee meals redeemed"), _metric("Company Covered", totals["covered"], "Employer benefit", "violet"), _metric("Employee Payable", totals["payable"], "Employee contribution"), _metric("Actual Cost", totals["cost"], "Meal cost", "amber")], "Redemptions by state", _rows(records, "state"), "Recent redemptions", records.order_by("-redeemed_at"))
    lines = _scoped(user, "pos-orders", POSOrderLine).select_related("product", "order").exclude(order__status="cancelled")
    amount = ExpressionWrapper(F("quantity") * F("unit_price"), output_field=DecimalField(max_digits=18, decimal_places=2))
    totals = lines.aggregate(count=Count("id"), units=Sum("quantity"), sales=Sum(amount))
    top = lines.values("product__name").annotate(total=Sum("quantity")).order_by("-total")[:12]
    sold_out = _scoped(user, "pos-product-availability", POSProductAvailability).filter(status="sold_out").count()
    return _result([_metric("Menu Lines", totals["count"], "F&B order lines"), _metric("Units", totals["units"], "Menu items sold"), _metric("Sales", totals["sales"], "Menu sales", "violet"), _metric("Sold Out", sold_out, "Unavailable menu items", "amber")], "Top menu items", [{"label": row["product__name"], "total": row["total"]} for row in top], "Recent menu lines", lines.order_by("-created_at"))
