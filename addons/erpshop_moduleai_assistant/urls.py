from django.urls import path

from . import views


app_name = "erpshop_moduleai_assistant"

urlpatterns = [
    path("ai/assistant/", views.assistant_home, name="home"),
    path("ai/assistant/new/", views.new_session, name="new_session"),
    path("ai/assistant/<int:session_id>/send/", views.send_message, name="send_message"),
    path("ai/assistant/<int:session_id>/archive/", views.archive_session, name="archive_session"),
    path("ai/assistant/messages/<int:message_id>/feedback/", views.message_feedback, name="message_feedback"),
    path("ai/assistant/<int:session_id>/state/", views.session_state, name="session_state"),
    path("ai/assistant/settings/", views.assistant_settings, name="settings"),
]
