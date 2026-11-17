from django.db import DatabaseError


def sync_single_stock_move(sender, instance, created, raw=False, **_kwargs):
    del sender
    if not created or raw:
        return
    from .services import apply_stock_moves_to_balances

    apply_stock_moves_to_balances([instance])


def ensure_default_package_type(sender, instance, created, raw=False, **_kwargs):
    del sender
    if not created or raw:
        return
    from .models import InventoryPackageType

    try:
        InventoryPackageType.objects.get_or_create(
            company=instance,
            code="BOX",
            defaults={
                "name": "Box",
                "reusable": False,
                "auto_generated": True,
                "active": True,
            },
        )
    except DatabaseError:
        # Company rows may be created while the Inventory migration graph is
        # still before the Package Type table. Migration 0006 performs the
        # bounded backfill once that table exists.
        return
