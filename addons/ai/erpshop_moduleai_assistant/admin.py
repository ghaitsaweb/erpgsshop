from django.contrib import admin

from .models import (
    AssistantAttachment,
    AssistantAuditEvent,
    AssistantConfiguration,
    AssistantMessage,
    AssistantSession,
    HashyOSConnection,
)


@admin.register(AssistantConfiguration)
class AssistantConfigurationAdmin(admin.ModelAdmin):
    list_display = ("company", "display_name", "provider", "model_name", "active")
    list_filter = ("provider", "active")
    search_fields = ("company__name", "display_name", "model_name")


@admin.register(HashyOSConnection)
class HashyOSConnectionAdmin(admin.ModelAdmin):
    fields = ("company", "account_email", "workspace_identifier")
    list_display = ("company", "account_email", "workspace_identifier")


@admin.register(AssistantSession)
class AssistantSessionAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "user", "status", "last_activity_at")
    list_filter = ("status", "company")
    search_fields = ("title", "user__username")


@admin.register(AssistantMessage)
class AssistantMessageAdmin(admin.ModelAdmin):
    list_display = ("session", "role", "state", "created_at", "input_tokens", "output_tokens")
    list_filter = ("role", "state")
    search_fields = ("content", "session__title")


admin.site.register(AssistantAttachment)
admin.site.register(AssistantAuditEvent)
