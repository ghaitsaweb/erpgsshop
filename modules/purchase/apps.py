from django.apps import AppConfig
from django.db.models.signals import post_migrate


class PurchaseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.purchase"
    label = "gsshop_purchase"
    verbose_name = "GSSHOP Purchase"

    def ready(self):
        from .invoice_extension import register_purchase_invoice_extension
        from .security import ensure_purchase_roles
        from .services import register_purchase_workflow_hooks

        register_purchase_invoice_extension()
        register_purchase_workflow_hooks()

        post_migrate.connect(
            ensure_purchase_roles,
            sender=self,
            dispatch_uid="gsshop_purchase.ensure_roles",
        )
