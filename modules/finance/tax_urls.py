from django.urls import path

from .tax_callback_views import tax_authority_callback


urlpatterns = [
    path("callbacks/<int:profile_id>/", tax_authority_callback, name="tax_authority_callback"),
]
