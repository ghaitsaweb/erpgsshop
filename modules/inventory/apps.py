from django.apps import AppConfig
from django.db.models.signals import post_migrate, post_save


class InventoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.inventory"
    label = "gsshop_inventory"
    verbose_name = "GSSHOP Inventory"

    def ready(self):
        from core.models import StockMove

        from .security import ensure_inventory_roles
        from .signals import sync_single_stock_move
        from .workspace import register_inventory_workspace

        register_inventory_workspace()
        post_save.connect(
            sync_single_stock_move,
            sender=StockMove,
            dispatch_uid="gsshop_inventory.sync_single_stock_move",
        )
        post_migrate.connect(
            ensure_inventory_roles,
            sender=self,
            dispatch_uid="gsshop_inventory.ensure_roles",
        )
