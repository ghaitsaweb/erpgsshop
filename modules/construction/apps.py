from django.apps import AppConfig
from django.db.models.signals import post_migrate


class ConstructionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.construction"
    label = "gsshop_construction"
    verbose_name = "GSSHOP Construction"

    def ready(self):
        from .security import ensure_construction_roles

        post_migrate.connect(
            ensure_construction_roles,
            sender=self,
            dispatch_uid="gsshop_construction.ensure_roles",
        )
