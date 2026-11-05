from django.urls import path

from . import views


app_name = "erpshop_moduleai_assistant"

urlpatterns = [
    path("ai/assistant/", views.assistant_home, name="home"),
    path("ai/assistant/new/", views.new_session, name="new_session"),
    path("ai/assistant/<int:session_id>/send/", views.send_message, name="send_message"),
    path("ai/assistant/<int:session_id>/archive/", views.archive_session, name="archive_session"),
    path("ai/assistant/<int:session_id>/restore/", views.restore_session, name="restore_session"),
    path("ai/assistant/<int:session_id>/rename/", views.rename_session, name="rename_session"),
    path("ai/assistant/messages/<int:message_id>/feedback/", views.message_feedback, name="message_feedback"),
    path("ai/assistant/<int:session_id>/state/", views.session_state, name="session_state"),
    path("ai/assistant/settings/", views.assistant_settings, name="settings"),
    path("ai/assistant/greeting/", views.proactive_greeting, name="proactive_greeting"),
    path("ai/assistant/knowledge/", views.knowledge_documents, name="knowledge_documents"),
    path("ai/assistant/knowledge/graph/", views.knowledge_graph_page, name="knowledge_graph_page"),
    path("ai/assistant/knowledge/graph/status/", views.knowledge_graph_status, name="knowledge_graph_status"),
    path("ai/assistant/knowledge/graph/overview/", views.knowledge_graph_overview, name="knowledge_graph_overview"),
    path("ai/assistant/knowledge/graph/view/", views.knowledge_graph_view, name="knowledge_graph_view"),
    path("ai/assistant/knowledge/graph/search/", views.knowledge_graph_search, name="knowledge_graph_search"),
    path("ai/assistant/knowledge/graph/neighbors/", views.knowledge_graph_neighbors, name="knowledge_graph_neighbors"),
    path("ai/assistant/knowledge/graph/path/", views.knowledge_graph_path, name="knowledge_graph_path"),
    path("ai/assistant/knowledge/graph/trace/", views.knowledge_graph_trace, name="knowledge_graph_trace"),
    path("ai/assistant/knowledge/graph/nodes/<str:node_id>/", views.knowledge_graph_node, name="knowledge_graph_node"),
    path(
        "ai/assistant/knowledge/<int:document_id>/state/",
        views.set_knowledge_document_state,
        name="knowledge_document_state",
    ),
    path(
        "ai/assistant/knowledge/categories/<int:category_id>/state/",
        views.set_knowledge_category_state,
        name="knowledge_category_state",
    ),
    path(
        "ai/assistant/knowledge/flows/<int:flow_id>/state/",
        views.set_knowledge_flow_state,
        name="knowledge_flow_state",
    ),
    path(
        "ai/assistant/knowledge/flows/generate/prerequisites/",
        views.knowledge_flow_generation_prerequisites,
        name="knowledge_flow_generation_prerequisites",
    ),
    path(
        "ai/assistant/knowledge/flows/generate/",
        views.generate_knowledge_flow,
        name="generate_knowledge_flow",
    ),
    path(
        "ai/assistant/knowledge/flows/generate/<uuid:job_id>/",
        views.knowledge_flow_generation_job,
        name="knowledge_flow_generation_job",
    ),
    path(
        "ai/assistant/knowledge/flows/reason/",
        views.reason_knowledge_flow,
        name="reason_knowledge_flow",
    ),
    path(
        "ai/assistant/knowledge/flows/<int:flow_id>/map/",
        views.knowledge_flow_map,
        name="knowledge_flow_map",
    ),
    path(
        "ai/assistant/knowledge/flows/<int:flow_id>/",
        views.knowledge_flow_detail,
        name="knowledge_flow_detail",
    ),
    path(
        "ai/assistant/knowledge/mappings/<int:mapping_id>/state/",
        views.set_knowledge_mapping_state,
        name="knowledge_mapping_state",
    ),
]
