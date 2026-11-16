from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from modules.apphub.workspaces import app_menu_groups

from .entity_config import ENTITY_CONFIG
from .models import InventoryLocation, InventoryLotSerial
from .services import MAX_STOCK_MOVE_SYNC_BATCH, _require_bounded_moves
from .workspace import register_inventory_workspace


class InventoryFoundationUnitTests(SimpleTestCase):
    def test_inventory_workspace_registration_is_idempotent(self):
        register_inventory_workspace()
        register_inventory_workspace()
        entities = [
            item.get("entity")
            for group in app_menu_groups("inventory")
            for item in group["items"]
        ]

        self.assertEqual(entities.count("inventory-stock-balances"), 1)
        self.assertEqual(entities.count("inventory-locations"), 1)
        self.assertEqual(entities.count("inventory-lots"), 1)
        self.assertEqual(entities.count("inventory-reorder-rules"), 1)
        self.assertEqual(entities.count("inventory-replenishment"), 1)
        self.assertEqual(entities.count("purchase-inventory-transfers"), 1)

    def test_inventory_entity_registry_exposes_foundation_records(self):
        self.assertEqual(
            set(ENTITY_CONFIG),
            {
                "inventory-locations",
                "inventory-lots",
                "inventory-stock-balances",
                "inventory-reorder-rules",
                "inventory-replenishment",
            },
        )
        self.assertFalse(ENTITY_CONFIG["inventory-stock-balances"]["editable"])
        self.assertFalse(ENTITY_CONFIG["inventory-replenishment"]["editable"])

    def test_location_capacity_snapshot_matches_hmx_statuses(self):
        location = InventoryLocation(
            capacity_length=Decimal("2"),
            capacity_width=Decimal("3"),
            capacity_height=Decimal("4"),
            occupied_volume=Decimal("12"),
        )

        location._refresh_capacity_fields()

        self.assertEqual(location.capacity_volume, Decimal("24"))
        self.assertEqual(location.available_volume, Decimal("12"))
        self.assertEqual(location.fulfilment_percent, Decimal("50.00"))
        self.assertEqual(location.capacity_status, InventoryLocation.CapacityStatus.AVAILABLE)

    def test_lot_expiry_flags_are_date_driven(self):
        today = timezone.localdate()
        lot = InventoryLotSerial(
            status=InventoryLotSerial.Status.ACTIVE,
            expiration_date=today + timedelta(days=10),
        )
        self.assertTrue(lot.is_near_expiry)
        self.assertFalse(lot.is_expired)

        lot.expiration_date = today - timedelta(days=1)
        self.assertFalse(lot.is_near_expiry)
        self.assertTrue(lot.is_expired)

    def test_stock_move_sync_rejects_an_unbounded_request(self):
        moves = [SimpleNamespace(pk=index + 1) for index in range(MAX_STOCK_MOVE_SYNC_BATCH + 1)]
        with self.assertRaisesMessage(Exception, "at most"):
            _require_bounded_moves(moves)
