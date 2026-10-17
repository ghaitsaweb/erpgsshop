from django.apps import AppConfig
from django.db.models.signals import post_migrate


class FinanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.finance"
    label = "gsshop_finance"
    verbose_name = "GSSHOP Finance"

    def ready(self):
        from . import signals  # noqa: F401
        from .security import ensure_accounting_roles
        from .workspace import register_accounting_workspace
        from .rental_extension import register_rental_accounting_extension
        from .asset_disposal_extension import register_asset_disposal_extension
        from .accounting_extensions import register_builtin_accounting_extensions
        from .service_operation_extension import register_service_operation_accounting_extension
        from .external_invoice_extension import register_external_invoice_extension
        from .sale_invoice_extension import register_sale_invoice_accounting_extension
        from .school_accounting_extension import register_school_accounting_extension
        from .portal_registry import register_builtin_portal_documents

        register_accounting_workspace()
        register_builtin_accounting_extensions()
        register_rental_accounting_extension()
        register_asset_disposal_extension()
        register_service_operation_accounting_extension()
        register_external_invoice_extension()
        register_sale_invoice_accounting_extension()
        register_school_accounting_extension()
        register_builtin_portal_documents()

        post_migrate.connect(
            ensure_accounting_roles,
            sender=self,
            dispatch_uid="gsshop_finance.ensure_roles",
        )
