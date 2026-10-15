from django.contrib import admin

from .models import ERPApp


@admin.register(ERPApp)
class ERPAppAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "status", "version", "enabled", "is_core")
    list_filter = ("category", "status", "enabled", "is_core")
    search_fields = ("name", "slug", "summary")
    readonly_fields = ("enabled_at", "enabled_by")

