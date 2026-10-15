from django.urls import path

from . import views


urlpatterns = [
    path("apps/", views.app_manager, name="app_manager"),
    path("apps/refresh/", views.refresh_app_catalog, name="app_catalog_refresh"),
    path("apps/hr/feature/<slug:feature>/", views.hr_feature, name="hr_feature"),
    path("apps/<slug:slug>/", views.app_workspace, name="app_workspace"),
    path("apps/<slug:slug>/installation/", views.change_installation, name="app_installation"),
]
