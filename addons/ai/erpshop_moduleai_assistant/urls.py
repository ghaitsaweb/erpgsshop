from django.urls import path

from . import compat_api, views


app_name = "erpshop_moduleai_assistant"

urlpatterns = [
    # HMX-compatible Base Knowledge contract. Keep static action routes before
    # identifier routes so words such as ``generate`` cannot be parsed as IDs.
    path("hmx_api/ai/knowledge/base/company-profile", compat_api.company_profile, name="compat_company_profile"),
    path(
        "hmx_api/ai/knowledge/base/company-profile/update",
        compat_api.company_profile_update,
        name="compat_company_profile_update",
    ),
    path("hmx_api/ai/knowledge/base/divisions", compat_api.divisions, name="compat_divisions"),
    path(
        "hmx_api/ai/knowledge/base/divisions/<int:division_id>/delete",
        compat_api.division_delete_alias,
        name="compat_division_delete",
    ),
    path(
        "hmx_api/ai/knowledge/base/divisions/<int:division_id>",
        compat_api.division_detail,
        name="compat_division_detail",
    ),
    path("hmx_api/ai/knowledge/base/categories", compat_api.categories, name="compat_categories"),
    path(
        "hmx_api/ai/knowledge/base/categories/<str:category>/delete",
        compat_api.category_delete_alias,
        name="compat_category_delete",
    ),
    path(
        "hmx_api/ai/knowledge/base/categories/<str:category>",
        compat_api.category_detail,
        name="compat_category_detail",
    ),
    path("hmx_api/ai/knowledge/documents", compat_api.documents, name="compat_documents"),
    path("hmx_api/ai/knowledge/upload", compat_api.upload_document, name="compat_upload"),
    path("hmx_api/ai/knowledge/documents/upload", compat_api.upload_document, name="compat_documents_upload"),
    path(
        "hmx_api/ai/knowledge/documents/<int:document_id>/delete",
        compat_api.document_delete,
        name="compat_document_delete",
    ),
    path(
        "hmx_api/ai/knowledge/documents/<int:document_id>",
        compat_api.document_delete,
        name="compat_document_delete_exact",
    ),
    path("hmx_api/ai/knowledge/base/documents", compat_api.base_documents, name="compat_base_documents"),
    path(
        "hmx_api/ai/knowledge/base/documents/upload-jobs/<str:job_id>",
        compat_api.document_upload_job,
        name="compat_document_upload_job",
    ),
    path(
        "hmx_api/ai/knowledge/base/documents/<int:document_id>/retry",
        compat_api.document_retry,
        name="compat_document_retry",
    ),
    path(
        "hmx_api/ai/knowledge/base/documents/<int:document_id>/delete",
        compat_api.document_delete,
        name="compat_base_document_delete",
    ),
    path(
        "hmx_api/ai/knowledge/base/documents/<int:document_id>",
        compat_api.document_detail,
        name="compat_document_detail",
    ),
    path(
        "hmx_api/ai/knowledge/base/business-flow/generate/prerequisites",
        compat_api.business_flow_prerequisites,
        name="compat_business_flow_prerequisites",
    ),
    path(
        "hmx_api/ai/knowledge/base/business-flow/generate/<str:job_id>",
        compat_api.business_flow_job,
        name="compat_business_flow_job",
    ),
    path(
        "hmx_api/ai/knowledge/base/business-flow/generate",
        compat_api.business_flow_generate,
        name="compat_business_flow_generate",
    ),
    path(
        "hmx_api/ai/knowledge/base/business-flow/map/<int:flow_id>",
        compat_api.business_flow_map,
        name="compat_business_flow_map",
    ),
    path(
        "hmx_api/ai/knowledge/base/business-flow/reason",
        compat_api.business_flow_reason,
        name="compat_business_flow_reason",
    ),
    path("hmx_api/ai/knowledge/base/business-flow", compat_api.business_flows, name="compat_business_flows"),
    path(
        "hmx_api/ai/knowledge/base/business-flow/<int:flow_id>/delete",
        compat_api.business_flow_delete,
        name="compat_business_flow_delete",
    ),
    path(
        "hmx_api/ai/knowledge/base/business-flow/<int:flow_id>",
        compat_api.business_flow_detail,
        name="compat_business_flow_detail",
    ),
    path(
        "hmx_api/ai/knowledge/base/record-model-mappings",
        compat_api.record_model_mappings,
        name="compat_record_model_mappings",
    ),
    path(
        "hmx_api/ai/knowledge/base/record-model-mappings/<str:record_model>/delete",
        compat_api.record_model_mapping_delete,
        name="compat_record_model_mapping_delete",
    ),
    path(
        "hmx_api/ai/knowledge/base/record-model-mappings/<str:record_model>",
        compat_api.record_model_mapping_delete,
        name="compat_record_model_mapping_delete_exact",
    ),
    path("hmx_api/ai/knowledge/base/graph/status", compat_api.graph_status, name="compat_graph_status"),
    path(
        "hmx_api/ai/knowledge/base/graph/overview",
        compat_api.graph_overview_api,
        name="compat_graph_overview",
    ),
    path("hmx_api/ai/knowledge/base/graph/view", compat_api.graph_view, name="compat_graph_view"),
    path("hmx_api/ai/knowledge/base/graph/search", compat_api.graph_search_api, name="compat_graph_search"),
    path(
        "hmx_api/ai/knowledge/base/graph/nodes/<str:node_id>",
        compat_api.graph_node_api,
        name="compat_graph_node",
    ),
    path(
        "hmx_api/ai/knowledge/base/graph/neighbors",
        compat_api.graph_neighbors_api,
        name="compat_graph_neighbors",
    ),
    path("hmx_api/ai/knowledge/base/graph/path", compat_api.graph_path_api, name="compat_graph_path"),
    path("hmx_api/ai/knowledge/base/graph/trace", compat_api.graph_trace_api, name="compat_graph_trace"),
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
