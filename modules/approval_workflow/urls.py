from django.urls import path

from . import views


urlpatterns = [
    path("approvals/", views.approval_inbox, name="approval_inbox"),
    path("approvals/mass-action/", views.approval_mass_action, name="approval_mass_action"),
    path("approvals/<int:pk>/", views.approval_detail, name="approval_detail"),
    path("approvals/<int:pk>/action/", views.approval_action, name="approval_action"),
]
