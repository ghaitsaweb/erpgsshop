from django.contrib import admin

from .models import KitchenTicket, POSOrder, POSOrderLine, POSRegister, POSSession, RestaurantTable


class POSOrderLineInline(admin.TabularInline):
    model = POSOrderLine
    extra = 1


@admin.register(POSOrder)
class POSOrderAdmin(admin.ModelAdmin):
    list_display = ("number", "session", "order_type", "status", "total", "paid_amount", "created_at")
    readonly_fields = ("total",)
    inlines = [POSOrderLineInline]


for model in [POSRegister, POSSession, RestaurantTable, KitchenTicket]:
    admin.site.register(model)

