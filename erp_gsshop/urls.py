from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core.views import health
from modules.apphub.discovery import discover_urlconfs


admin.site.site_header = "ERP GSSHOP Administration"
admin.site.site_title = "ERP GSSHOP"
admin.site.index_title = "Data & Configuration"

urlpatterns = [
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("customer-portal/", include("modules.finance.portal_urls")),
    path("accounting/tax/", include("modules.finance.tax_urls")),
    path("", include("modules.apphub.urls")),
    path("", include("core.urls")),
]

for addon_urlconf in discover_urlconfs():
    urlpatterns.append(path("", include(addon_urlconf)))
