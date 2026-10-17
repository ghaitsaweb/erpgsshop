from django.apps import AppConfig
from django.db.models.signals import post_migrate


class POSConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.pos"
    label = "gsshop_pos"
    verbose_name = "GSSHOP Point of Sale"

    def ready(self):
        from . import signals  # noqa: F401
        from .security import ensure_pos_roles

        post_migrate.connect(
            ensure_pos_roles,
            sender=self,
            dispatch_uid="gsshop_pos.ensure_roles",
        )
