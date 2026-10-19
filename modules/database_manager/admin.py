from django.contrib import admin

from .models import DatabaseOperation, ManagedDatabase


@admin.register(ManagedDatabase)
class ManagedDatabaseAdmin(admin.ModelAdmin):
    list_display = ("name", "label", "is_primary", "is_protected", "updated_at")
    list_filter = ("is_primary", "is_protected")
    search_fields = ("name", "label")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DatabaseOperation)
class DatabaseOperationAdmin(admin.ModelAdmin):
    list_display = ("kind", "database_name", "target_name", "status", "progress", "requested_by", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("database_name", "target_name", "requested_by", "message")
    readonly_fields = tuple(field.name for field in DatabaseOperation._meta.fields)
