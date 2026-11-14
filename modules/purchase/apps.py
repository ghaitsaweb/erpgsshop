from django.apps import AppConfig
from django.db.models.signals import post_migrate


class PurchaseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.purchase"
    label = "gsshop_purchase"
    verbose_name = "GSSHOP Purchase"

    def ready(self):
        from .approval import register_purchase_approval_adapter
        from .invoice_extension import register_purchase_invoice_extension
        from .line_merge import register_purchase_line_merge
        from .security import ensure_purchase_roles
        from .services import register_purchase_workflow_hooks
        from .smart_action_extensions import register_purchase_smart_action_extensions
        from .workflow_extensions import register_purchase_document_extensions

        register_purchase_approval_adapter()
        register_purchase_invoice_extension()
        register_purchase_line_merge()
        register_purchase_workflow_hooks()
        register_purchase_smart_action_extensions()
        register_purchase_document_extensions()

        post_migrate.connect(
            ensure_purchase_roles,
            sender=self,
            dispatch_uid="gsshop_purchase.ensure_roles",
        )
