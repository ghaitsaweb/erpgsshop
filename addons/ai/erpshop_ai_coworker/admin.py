from django.contrib import admin

from .models import (
    AIAgent,
    AICompanyCredit,
    AICreditLedger,
    AIConversation,
    AICoworkerSecret,
    AICoworkerConnectionCheck,
    AIMessage,
    AIModelPricing,
    AIModelTemplate,
    AIProvider,
    AIUsageEvent,
    AIToolApproval,
    AITask,
    AITaskEvent,
    AIWorkspace,
)
from .tool_runtime import decide_approval


class AIAgentInline(admin.TabularInline):
    model = AIAgent
    extra = 1


@admin.register(AIWorkspace)
class AIWorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "active")
    inlines = [AIAgentInline]


@admin.register(AICoworkerSecret)
class AICoworkerSecretAdmin(admin.ModelAdmin):
    list_display = ("agent", "scope", "participant", "key", "credential_env_var", "active", "is_available")
    list_filter = ("scope", "active")
    search_fields = ("agent__name", "key", "credential_env_var", "participant__display_name")
    readonly_fields = ("is_available", "created_at", "updated_at")


@admin.register(AICoworkerConnectionCheck)
class AICoworkerConnectionCheckAdmin(admin.ModelAdmin):
    list_display = ("agent", "state", "provider_name", "model_name", "latency_ms", "requested_by", "created_at")
    list_filter = ("state",)
    search_fields = ("agent__name", "provider_name", "model_name")
    readonly_fields = tuple(field.name for field in AICoworkerConnectionCheck._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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


@admin.register(AIProvider)
class AIProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "display_name", "backend", "active")
    list_filter = ("backend", "active")
    search_fields = ("name", "display_name")


@admin.register(AIModelTemplate)
class AIModelTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "model_name", "max_output_tokens", "active")
    list_filter = ("provider", "active")
    search_fields = ("name", "model_name")


@admin.register(AIModelPricing)
class AIModelPricingAdmin(admin.ModelAdmin):
    list_display = ("template", "effective_from", "effective_to", "currency", "active")
    list_filter = ("active", "currency")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AICompanyCredit)
class AICompanyCreditAdmin(admin.ModelAdmin):
    list_display = ("company", "credit_balance", "credit_limit", "currency", "enforce_credits")
    list_filter = ("enforce_credits", "currency")


@admin.register(AICreditLedger)
class AICreditLedgerAdmin(admin.ModelAdmin):
    list_display = ("company", "entry_type", "amount", "balance_after", "currency", "created_at")
    list_filter = ("entry_type", "currency")
    readonly_fields = ("company", "entry_type", "amount", "balance_after", "currency", "description", "reference", "created_by", "expires_at", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AIUsageEvent)
class AIUsageEventAdmin(admin.ModelAdmin):
    list_display = ("agent", "company", "provider", "model_name", "total_cost", "currency", "created_at")
    list_filter = ("provider", "currency", "trigger")
    search_fields = ("request_id", "model_name", "agent__name")
    readonly_fields = tuple(field.name for field in AIUsageEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AIToolApproval)
class AIToolApprovalAdmin(admin.ModelAdmin):
    list_display = ("tool_name", "task", "agent", "state", "expires_at", "decided_by", "created_at")
    list_filter = ("state", "tool_name")
    search_fields = ("task__title", "agent__name", "request_digest")
    readonly_fields = (
        "agent", "task", "tool_name", "request_digest", "request_preview", "state",
        "decided_by", "decided_at", "expires_at", "result", "created_at", "updated_at",
    )
    actions = ("approve_selected", "reject_selected")

    @admin.action(description="Approve selected pending tool requests")
    def approve_selected(self, request, queryset):
        for approval in queryset.filter(state=AIToolApproval.State.PENDING).iterator(chunk_size=100):
            decide_approval(approval=approval, actor=request.user, approve=True)

    @admin.action(description="Reject selected pending tool requests")
    def reject_selected(self, request, queryset):
        for approval in queryset.filter(state=AIToolApproval.State.PENDING).iterator(chunk_size=100):
            decide_approval(approval=approval, actor=request.user, approve=False)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
