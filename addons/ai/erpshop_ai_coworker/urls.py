from django.urls import path

from . import views
from . import webhooks
from . import channel_runtime


app_name = "erpshop_ai_coworker"

urlpatterns = [
    path("ai/coworker/webhooks/<str:token>/", webhooks.receive_webhook, name="webhook_receive"),
    path("ai/coworker/channels/<int:channel_id>/webhook/", channel_runtime.receive_channel_webhook, name="channel_webhook"),
    path("ai/coworker/", views.home, name="home"),
    path("ai/coworker/settings/", views.settings, name="settings"),
    path("ai/coworker/settings/save/", views.save_agent, name="settings_save"),
    path("ai/coworker/settings/<int:agent_id>/", views.settings, name="settings_edit"),
    path("ai/coworker/settings/<int:agent_id>/save/", views.save_agent, name="settings_update"),
    path("ai/coworker/runtime/", views.runtime, name="runtime"),
    path("ai/coworker/runtime/memory/save/", views.save_memory, name="memory_save"),
    path("ai/coworker/operations/", views.operations, name="operations"),
    path("ai/coworker/operations/approvals/<int:approval_id>/", views.decide_tool_approval, name="approval_decide"),
    path("ai/coworker/models/", views.model_catalog, name="model_catalog"),
    path("ai/coworker/models/providers/save/", views.save_provider, name="provider_save"),
    path("ai/coworker/models/providers/<int:provider_id>/save/", views.save_provider, name="provider_update"),
    path("ai/coworker/models/templates/save/", views.save_model_template, name="model_template_save"),
    path("ai/coworker/models/templates/<int:template_id>/save/", views.save_model_template, name="model_template_update"),
    path("ai/coworker/models/pricing/save/", views.save_model_pricing, name="model_pricing_save"),
    path("ai/coworker/models/pricing/<int:pricing_id>/save/", views.save_model_pricing, name="model_pricing_update"),
    path("ai/coworker/credit/", views.credit_policy, name="credit_policy"),
    path("ai/coworker/credit/policy/save/", views.save_credit_policy, name="credit_policy_save"),
    path("ai/coworker/credit/adjustments/save/", views.adjust_credit, name="credit_adjust"),
    path("ai/coworker/subagents/", views.subagents, name="subagents"),
    path("ai/coworker/subagents/start/", views.start_subagent, name="subagents_start"),
    path("ai/coworker/automation/", views.automation, name="automation"),
    path("ai/coworker/automation/crons/save/", views.save_cron, name="cron_save"),
    path("ai/coworker/automation/crons/<int:cron_id>/save/", views.save_cron, name="cron_update"),
    path("ai/coworker/automation/heartbeats/save/", views.save_heartbeat, name="heartbeat_save"),
    path("ai/coworker/automation/heartbeats/<int:heartbeat_id>/save/", views.save_heartbeat, name="heartbeat_update"),
    path("ai/coworker/channels/", views.channels, name="channels"),
    path("ai/coworker/channels/save/", views.save_channel, name="channel_save"),
    path("ai/coworker/channels/<int:channel_id>/save/", views.save_channel, name="channel_update"),
    path("ai/coworker/webhooks/", views.webhooks, name="webhooks"),
    path("ai/coworker/webhooks/<int:agent_id>/save/", views.save_webhook, name="webhook_save"),
    path("ai/coworker/webhooks/<int:agent_id>/<int:endpoint_id>/save/", views.save_webhook, name="webhook_update"),
    path("ai/coworker/schedules/", views.schedules, name="schedules"),
    path("ai/coworker/schedules/save/", views.save_schedule, name="schedules_save"),
    path("ai/coworker/schedules/<int:schedule_id>/", views.schedules, name="schedules_edit"),
    path("ai/coworker/schedules/<int:schedule_id>/save/", views.save_schedule, name="schedules_update"),
    path("ai/coworker/tasks/new/", views.create_task, name="create_task"),
    path("ai/coworker/tasks/<int:task_id>/", views.task_detail, name="task_detail"),
    path("ai/coworker/tasks/<int:task_id>/state/", views.task_state, name="task_state"),
    path("ai/coworker/tasks/<int:task_id>/feedback/", views.task_feedback, name="task_feedback"),
    path("ai/coworker/tasks/<int:task_id>/cancel/", views.cancel_task, name="cancel_task"),
]
