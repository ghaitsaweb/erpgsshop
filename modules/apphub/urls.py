from django.urls import path

from . import views


urlpatterns = [
    path("apps/", views.app_manager, name="app_manager"),
    path("apps/refresh/", views.refresh_app_catalog, name="app_catalog_refresh"),
    path("apps/developer-mode/", views.set_developer_mode, name="developer_mode"),
    path("apps/hr/feature/<slug:feature>/", views.hr_feature, name="hr_feature"),
    path("apps/accounting/feature/<slug:feature>/", views.accounting_feature, name="accounting_feature"),
    path("apps/accounting/feature/<slug:feature>/export.csv", views.accounting_feature_export, name="accounting_feature_export"),
    path("apps/accounting/feature/<slug:feature>/export.pdf", views.accounting_feature_export_pdf, name="accounting_feature_export_pdf"),
    path("apps/accounting/feature/<slug:feature>/export.xls", views.accounting_feature_export_excel, name="accounting_feature_export_excel"),
    path("apps/purchase/feature/<slug:feature>/", views.purchase_feature, name="purchase_feature"),
    path("apps/construction/feature/<slug:feature>/", views.construction_feature, name="construction_feature"),
    path("apps/pos/feature/<slug:feature>/", views.pos_feature, name="pos_feature"),
    path("apps/pos-fnb/feature/<slug:feature>/", views.pos_fnb_feature, name="pos_fnb_feature"),
    path("apps/<slug:slug>/", views.app_workspace, name="app_workspace"),
    path("apps/<slug:slug>/installation/", views.change_installation, name="app_installation"),
    path("apps/<slug:slug>/upgrade/", views.upgrade_application, name="app_upgrade"),
]
