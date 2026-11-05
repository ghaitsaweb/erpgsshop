from django.contrib import admin

from .models import (
    AIDigestCategory,
    AIDigestRecord,
    AIDigestRule,
    AIDigestRuleSource,
    AIDigestRuleSourceAction,
    AIHashyInteractionSignalOutbox,
    AIKnowledgeChunk,
    AIKnowledgeBusinessFlow,
    AIKnowledgeBusinessFlowGenerationJob,
    AIKnowledgeCategory,
    AIKnowledgeCompanyProfile,
    AIKnowledgeDocument,
    AIWorkEpisode,
    AIWorkEpisodeEvent,
    AIWorkProfileRefreshRun,
    AIWorkProfileRefreshSetting,
    AIWorkProfileSignal,
    AIWorkSignalOutbox,
    AIWorkflowInteractionAggregate,
    AIWorkflowScopeAggregate,
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


@admin.register(AIKnowledgeBusinessFlow)
class AIKnowledgeBusinessFlowAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "status", "updated_at")
    list_filter = ("status", "company")
    search_fields = ("name", "trigger", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AIKnowledgeBusinessFlowGenerationJob)
class AIKnowledgeBusinessFlowGenerationJobAdmin(admin.ModelAdmin):
    list_display = ("job_id", "company", "state", "result_flow", "requested_by", "created_at")
    list_filter = ("state", "company")
    search_fields = ("job_id", "result_flow__name", "requested_by__username")
    readonly_fields = tuple(field.name for field in AIKnowledgeBusinessFlowGenerationJob._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AIWorkEpisode)
class AIWorkEpisodeAdmin(admin.ModelAdmin):
    list_display = ("workflow_scope", "company", "actor", "state", "started_at", "completed_at")
    list_filter = ("state", "company", "workflow_scope")
    search_fields = ("episode_id", "source_ref", "summary")
    readonly_fields = tuple(field.name for field in AIWorkEpisode._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AIWorkEpisodeEvent, AIWorkSignalOutbox, AIHashyInteractionSignalOutbox)
class AIWorkAuditAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AIWorkflowInteractionAggregate, AIWorkflowScopeAggregate, AIWorkProfileSignal, AIWorkProfileRefreshRun)
class AIWorkDerivedAdmin(admin.ModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AIWorkProfileRefreshSetting)
class AIWorkProfileRefreshSettingAdmin(admin.ModelAdmin):
    list_display = ("company", "enabled", "interval_minutes", "batch_size", "last_run_at")
    list_filter = ("enabled",)


class AIDigestSourceInline(admin.TabularInline):
    model = AIDigestRuleSource
    extra = 0


@admin.register(AIDigestRule)
class AIDigestRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "trigger_type", "schedule_frequency", "active", "last_run_at")
    list_filter = ("trigger_type", "schedule_frequency", "active")
    search_fields = ("name", "company__name")
    filter_horizontal = ("categories",)
    inlines = (AIDigestSourceInline,)


@admin.register(AIDigestCategory)
class AIDigestCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "active")
    list_filter = ("active", "company")


@admin.register(AIDigestRuleSourceAction)
class AIDigestRuleSourceActionAdmin(admin.ModelAdmin):
    list_display = ("friendly_label", "source", "canonical_action_key", "validity_state")
    list_filter = ("validity_state", "source_type")


@admin.register(AIDigestRecord)
class AIDigestRecordAdmin(admin.ModelAdmin):
    list_display = ("summary", "company", "rule", "urgency", "confidence_score", "created_at")
    list_filter = ("urgency", "company")
    search_fields = ("summary", "trace_id", "source_ref")
    readonly_fields = tuple(field.name for field in AIDigestRecord._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AIKnowledgeDocument)
class AIKnowledgeDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "category", "status", "active", "updated_at")
    list_filter = ("status", "active", "company", "category")
    search_fields = ("title", "category", "original_name")
    readonly_fields = ("content_sha256", "byte_size", "created_at", "updated_at")


@admin.register(AIKnowledgeChunk)
class AIKnowledgeChunkAdmin(admin.ModelAdmin):
    list_display = ("document", "company", "ordinal", "updated_at")
    list_filter = ("company",)
    search_fields = ("document__title", "content")
    readonly_fields = tuple(field.name for field in AIKnowledgeChunk._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AIKnowledgeCompanyProfile)
class AIKnowledgeCompanyProfileAdmin(admin.ModelAdmin):
    list_display = ("company", "active", "updated_at")
    list_filter = ("active",)


@admin.register(AIKnowledgeCategory)
class AIKnowledgeCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "active")
    list_filter = ("active", "company")
    search_fields = ("name", "description")
