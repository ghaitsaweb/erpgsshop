from django.contrib import admin

from .models import AIAgent, AIConversation, AIMessage, AITask, AIWorkspace


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


for model in [AITask]:
    admin.site.register(model)

