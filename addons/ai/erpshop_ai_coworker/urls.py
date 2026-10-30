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
    path("ai/coworker/schedules/", views.schedules, name="schedules"),
    path("ai/coworker/schedules/save/", views.save_schedule, name="schedules_save"),
    path("ai/coworker/schedules/<int:schedule_id>/", views.schedules, name="schedules_edit"),
    path("ai/coworker/schedules/<int:schedule_id>/save/", views.save_schedule, name="schedules_update"),
    path("ai/coworker/tasks/new/", views.create_task, name="create_task"),
    path("ai/coworker/tasks/<int:task_id>/", views.task_detail, name="task_detail"),
    path("ai/coworker/tasks/<int:task_id>/state/", views.task_state, name="task_state"),
    path("ai/coworker/tasks/<int:task_id>/cancel/", views.cancel_task, name="cancel_task"),
]
