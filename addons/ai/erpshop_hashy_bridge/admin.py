from django.contrib import admin

from .models import BridgeAuditLog


@admin.register(BridgeAuditLog)
class BridgeAuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "company", "user", "endpoint", "ok", "http_status", "duration_ms")
    list_filter = ("ok", "http_status", "endpoint", "company")
    search_fields = ("run_id", "error_code", "error_message", "user__username")
    readonly_fields = tuple(field.name for field in BridgeAuditLog._meta.fields)
    ordering = ("-pk",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
