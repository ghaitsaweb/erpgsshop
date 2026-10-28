from django.contrib import admin

from .models import AIAgent, AIConversation, AIMessage, AITask, AITaskEvent, AIWorkspace


class AIAgentInline(admin.TabularInline):
    model = AIAgent
    extra = 1


@admin.register(AIWorkspace)
class AIWorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "active")
    inlines = [AIAgentInline]


class AIMessageInline(admin.TabularInline):
    model = AIMessage
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(AIConversation)
class AIConversationAdmin(admin.ModelAdmin):
    list_display = ("title", "agent", "user", "status", "last_activity_at")
    inlines = [AIMessageInline]


@admin.register(AITask)
class AITaskAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "agent", "requested_by", "task_type", "status", "created_at")
    list_filter = ("status", "task_type", "company")
    search_fields = ("title", "agent__name", "requested_by__username")
    readonly_fields = (
        "company", "branch", "requested_by", "conversation", "status", "instruction", "entity",
        "selected_fields", "filters", "result_text", "error_message", "cancel_requested", "input_tokens",
        "output_tokens", "latency_ms", "context_record_count", "context_truncated", "idempotency_key",
        "started_at", "completed_at", "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
@admin.register(AITaskEvent)
class AITaskEventAdmin(admin.ModelAdmin):
    list_display = ("task", "event", "actor", "created_at")
    readonly_fields = ("task", "event", "actor", "details", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
