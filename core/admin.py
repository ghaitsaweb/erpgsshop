from django.contrib import admin

from .models import (
    Attendance,
    Company,
    Contact,
    Employee,
    Invoice,
    InvoiceLine,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    SaleOrder,
    SaleOrderLine,
    StockMove,
    Warehouse,
)


class SaleOrderLineInline(admin.TabularInline):
    model = SaleOrderLine
    extra = 1


class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 1


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 1


@admin.register(SaleOrder)
class SaleOrderAdmin(admin.ModelAdmin):
    list_display = ("number", "company", "customer", "order_date", "status", "total")
    list_filter = ("company", "status", "order_date")
    search_fields = ("number", "customer__name")
    readonly_fields = ("total",)
    inlines = [SaleOrderLineInline]


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("number", "company", "vendor", "order_date", "status", "total")
    list_filter = ("company", "status", "order_date")
    search_fields = ("number", "vendor__name")
    readonly_fields = ("total",)
    inlines = [PurchaseOrderLineInline]


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "company", "partner", "invoice_type", "invoice_date", "status", "total", "paid_amount")
    list_filter = ("company", "invoice_type", "status", "invoice_date")
    search_fields = ("number", "partner__name")
    readonly_fields = ("total",)
    inlines = [InvoiceLineInline]


@admin.register(StockMove)
class StockMoveAdmin(admin.ModelAdmin):
    list_display = ("movement_date", "company", "product", "warehouse", "direction", "quantity", "reference", "created_by")
    list_filter = ("company", "warehouse", "direction", "movement_date")
    search_fields = ("product__sku", "product__name", "reference")

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def has_change_permission(self, request, obj=None):
        return obj is None

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "currency", "active")
    search_fields = ("code", "name")


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "contact_type", "company", "active")
    list_filter = ("company", "contact_type", "active")
    search_fields = ("code", "name", "email", "phone")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "company", "product_type", "sale_price", "current_stock", "active")
    list_filter = ("company", "product_type", "active")
    search_fields = ("sku", "name")
    readonly_fields = ("current_stock",)


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "company", "active")
    list_filter = ("company", "active")
    search_fields = ("code", "name")


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("employee_id", "name", "company", "department", "position", "status")
    list_filter = ("company", "department", "status")
    search_fields = ("employee_id", "name", "email")


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("attendance_date", "employee", "check_in", "check_out", "status")
    list_filter = ("status", "attendance_date", "employee__company")
    search_fields = ("employee__employee_id", "employee__name")

