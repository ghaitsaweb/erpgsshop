from django.urls import path

from . import views


app_name = "organization"

urlpatterns = [
    path("options/", views.options, name="options"),
    path("select/", views.select_context, name="select"),
]
