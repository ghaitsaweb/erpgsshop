from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "ERP GSSHOP"

    def ready(self):
        from . import signals  # noqa: F401
        from .sales_security import ensure_sales_roles

        post_migrate.connect(
            ensure_sales_roles,
            sender=self,
            dispatch_uid="core.ensure_sales_roles",
        )

