from django.urls import path

from . import views


urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("api/products/search/", views.product_search, name="product_search"),
    path("api/customers/search/", views.customer_search, name="customer_search"),
    path(
        "app/employee-change-requests/<int:pk>/attachment/",
        views.employee_change_request_attachment,
        name="employee_change_request_attachment",
    ),
    path(
        "app/contracts/<int:pk>/attachments/<int:attachment_pk>/download/",
        views.contract_attachment_download,
        name="contract_attachment_download",
    ),
    path("app/<slug:entity>/", views.entity_list, name="entity_list"),
    path("app/<slug:entity>/new/", views.entity_create, name="entity_create"),
    path("app/<slug:entity>/bulk-delete/", views.entity_bulk_delete, name="entity_bulk_delete"),
    path("app/<slug:entity>/<int:pk>/edit/", views.entity_edit, name="entity_edit"),
    path("app/<slug:entity>/<int:pk>/print.pdf", views.document_pdf, name="document_pdf"),
    path("app/<slug:entity>/<int:pk>/action/<slug:action>/", views.document_action, name="document_action"),
    path("app/<slug:entity>/<int:pk>/delete/", views.entity_delete, name="entity_delete"),
    path("app/rental-details/<int:pk>/status/", views.rental_line_status, name="rental_line_status"),
    path("app/rental-details/<int:pk>/line-delete/", views.rental_line_delete, name="rental_line_delete"),
]
