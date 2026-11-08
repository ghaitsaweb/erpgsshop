from django.urls import path

from . import views


urlpatterns = [
    path(
        "app/purchase-requests/<int:pk>/approve-lines/",
        views.purchase_request_approve,
        name="purchase_request_approve",
    ),
    path(
        "app/purchase-requests/<int:pk>/re-request/",
        views.purchase_request_re_request,
        name="purchase_request_re_request",
    ),
    path(
        "app/blanket-orders/<int:pk>/convert-to-rfq/",
        views.blanket_order_convert_to_rfq,
        name="blanket_order_convert_to_rfq",
    ),
    path(
        "app/purchase-requests/<int:pk>/convert-to-rfq/",
        views.purchase_request_convert_to_rfq,
        name="purchase_request_convert_to_rfq",
    ),
    path(
        "app/purchase-requests/<int:pk>/convert-to-blanket/",
        views.purchase_request_convert_to_blanket,
        name="purchase_request_convert_to_blanket",
    ),
    path(
        "app/purchase-requests/<int:pk>/convert-to-tender/",
        views.purchase_request_convert_to_tender,
        name="purchase_request_convert_to_tender",
    ),
    path(
        "app/purchase-requests/<int:pk>/convert-to-direct/",
        views.purchase_request_convert_to_direct,
        name="purchase_request_convert_to_direct",
    ),
]
