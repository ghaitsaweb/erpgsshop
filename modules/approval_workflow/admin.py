from django.contrib import admin

from .models import (
    ApprovalAction,
    ApprovalDelegation,
    ApprovalInstance,
    ApprovalNotification,
    ApprovalOutbox,
    ApprovalRule,
    ApprovalTimeoutWhitelist,
    ApprovalWorkflow,
    ApprovalWorkflowPerimeter,
)


@admin.register(ApprovalWorkflow)
class ApprovalWorkflowAdmin(admin.ModelAdmin):
    list_display = ("content_type", "status_field", "enable_mass_approval", "active")
    list_filter = ("active", "enable_mass_approval")


@admin.register(ApprovalRule)
class ApprovalRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "workflow", "state", "priority", "master_approver")
    list_filter = ("state", "workflow")
    filter_horizontal = ("companies",)


@admin.register(ApprovalInstance)
class ApprovalInstanceAdmin(admin.ModelAdmin):
    list_display = ("reference", "entity", "company", "rule", "status", "current_level", "submitted_at")
    list_filter = ("status", "company", "entity")
    search_fields = ("reference", "entity")
    readonly_fields = tuple(field.name for field in ApprovalInstance._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in (
    ApprovalWorkflowPerimeter,
    ApprovalDelegation,
    ApprovalAction,
    ApprovalOutbox,
    ApprovalNotification,
    ApprovalTimeoutWhitelist,
):
    admin.site.register(model)
