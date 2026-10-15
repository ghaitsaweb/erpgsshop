from django.apps import AppConfig
from django.db.models.signals import post_migrate


class PurchaseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.purchase"
    label = "gsshop_purchase"
    verbose_name = "GSSHOP Purchase"

    def ready(self):
        from .security import ensure_purchase_roles

        post_migrate.connect(
            ensure_purchase_roles,
            sender=self,
            dispatch_uid="gsshop_purchase.ensure_roles",
        )
