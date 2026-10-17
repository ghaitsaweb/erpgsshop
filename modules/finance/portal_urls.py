from django.urls import path

from . import portal_views


urlpatterns = [
    path("", portal_views.customer_portal_dashboard, name="customer_portal_dashboard"),
    path(
        "documents/<slug:document_type>/",
        portal_views.customer_portal_document_list,
        name="customer_portal_document_list",
    ),
    path(
        "documents/<slug:document_type>/<uuid:document_uuid>/",
        portal_views.customer_portal_document_detail,
        name="customer_portal_document_detail",
    ),
]
