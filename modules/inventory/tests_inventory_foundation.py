from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.models import Company, Product, StockMove, Warehouse
from modules.organization.models import Branch

from .models import (
    InventoryBalanceEvent,
    InventoryLocation,
    InventoryReorderRule,
    InventoryReplenishmentSnapshot,
    InventoryStockBalance,
)
from .services import apply_stock_moves_to_balances, evaluate_replenishment_rules


class InventoryBalanceServiceTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Inventory Co", code="INV")
        self.branch = Branch.objects.get(company=self.company, is_main=True)
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            branch=self.branch,
            code="WH-1",
            name="Main Warehouse",
        )
        self.user = get_user_model().objects.create_user(
            username="inventory-user",
            password="test-only-password",
        )

    def _product(self, sequence):
        return Product.objects.create(
            company=self.company,
            sku=f"SKU-{sequence}",
            name=f"Product {sequence}",
            product_type=Product.ProductType.STOCKABLE,
        )

    def _bulk_moves(self, count, *, warehouse=None, start=0):
        products = [self._product(start + index) for index in range(count)]
        moves = [
            StockMove(
                company=self.company,
                product=product,
                warehouse=warehouse or self.warehouse,
                direction=StockMove.Direction.IN,
                quantity=Decimal("5"),
                reference=f"REPLAY-{product.sku}",
                created_by=self.user,
            )
            for product in products
        ]
        StockMove.objects.bulk_create(moves)
        return moves

    def test_single_stock_move_signal_creates_default_location_and_balance(self):
        product = self._product(1)

        move = StockMove.objects.create(
            company=self.company,
            product=product,
            warehouse=self.warehouse,
            direction=StockMove.Direction.IN,
            quantity=Decimal("7"),
            reference="GR-1",
            created_by=self.user,
        )

        balance = InventoryStockBalance.objects.get(product=product)
        self.assertEqual(balance.qty_on_hand, Decimal("7"))
        self.assertEqual(balance.qty_available, Decimal("7"))
        self.assertEqual(balance.location.parent.name, "Stock")
        self.assertEqual(balance.location.name, "Main Stock")
        self.assertEqual(balance.last_move, move)
        self.assertTrue(InventoryBalanceEvent.objects.filter(stock_move=move).exists())

    def test_balance_replay_is_idempotent(self):
        move = self._bulk_moves(1)[0]

        self.assertEqual(apply_stock_moves_to_balances([move]), 1)
        self.assertEqual(apply_stock_moves_to_balances([move]), 0)

        balance = InventoryStockBalance.objects.get(product_id=move.product_id)
        self.assertEqual(balance.qty_on_hand, Decimal("5"))
        self.assertEqual(InventoryBalanceEvent.objects.filter(stock_move=move).count(), 1)

    def test_balance_sync_query_count_does_not_grow_with_move_count(self):
        small_moves = self._bulk_moves(2, start=10)
        with CaptureQueriesContext(connection) as small_context:
            apply_stock_moves_to_balances(small_moves)

        second_warehouse = Warehouse.objects.create(
            company=self.company,
            branch=self.branch,
            code="WH-2",
            name="Second Warehouse",
        )
        larger_moves = self._bulk_moves(12, warehouse=second_warehouse, start=100)
        with CaptureQueriesContext(connection) as larger_context:
            apply_stock_moves_to_balances(larger_moves)

        self.assertLessEqual(len(larger_context), len(small_context) + 1)

    def test_location_rejects_cross_warehouse_parent(self):
        other = Warehouse.objects.create(
            company=self.company,
            branch=self.branch,
            code="WH-OTHER",
            name="Other Warehouse",
        )
        root = InventoryLocation.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.warehouse,
            name="Root",
            usage=InventoryLocation.Usage.VIEW,
        )
        child = InventoryLocation(
            company=self.company,
            branch=self.branch,
            warehouse=other,
            parent=root,
            name="Invalid Child",
        )

        with self.assertRaisesMessage(ValidationError, "same company, branch, and warehouse"):
            child.full_clean()

    def test_replenishment_snapshot_tracks_due_then_healthy_stock(self):
        product = self._product(500)
        StockMove.objects.create(
            company=self.company,
            product=product,
            warehouse=self.warehouse,
            direction=StockMove.Direction.IN,
            quantity=Decimal("7"),
            reference="REPLENISH-OPENING",
            created_by=self.user,
        )
        rule = InventoryReorderRule.objects.create(
            company=self.company,
            branch=self.branch,
            product=product,
            warehouse=self.warehouse,
            minimum_quantity=Decimal("10"),
            maximum_quantity=Decimal("20"),
            reorder_quantity=Decimal("0"),
            lead_time_days=2,
        )

        self.assertEqual(evaluate_replenishment_rules([rule]), 1)
        snapshot = InventoryReplenishmentSnapshot.objects.get(rule=rule)
        self.assertEqual(snapshot.status, InventoryReplenishmentSnapshot.Status.DUE)
        self.assertEqual(snapshot.quantity_available, Decimal("7"))
        self.assertEqual(snapshot.suggested_quantity, Decimal("13"))

        StockMove.objects.create(
            company=self.company,
            product=product,
            warehouse=self.warehouse,
            direction=StockMove.Direction.IN,
            quantity=Decimal("10"),
            reference="REPLENISH-RECOVERY",
            created_by=self.user,
        )
        self.assertEqual(evaluate_replenishment_rules([rule]), 0)
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.status, InventoryReplenishmentSnapshot.Status.HEALTHY)
        self.assertEqual(snapshot.quantity_available, Decimal("17"))
        self.assertEqual(snapshot.suggested_quantity, Decimal("0"))

    def test_internal_reorder_rule_requires_another_source_warehouse(self):
        product = self._product(501)
        rule = InventoryReorderRule(
            company=self.company,
            branch=self.branch,
            product=product,
            warehouse=self.warehouse,
            route=InventoryReorderRule.Route.INTERNAL,
            source_warehouse=self.warehouse,
            maximum_quantity=Decimal("10"),
        )

        with self.assertRaisesMessage(ValidationError, "must differ"):
            rule.full_clean()

    def test_replenishment_query_count_does_not_grow_with_rule_count(self):
        def build_rules(count, start):
            products = [self._product(start + index) for index in range(count)]
            return InventoryReorderRule.objects.bulk_create(
                [
                    InventoryReorderRule(
                        company=self.company,
                        branch=self.branch,
                        product=product,
                        warehouse=self.warehouse,
                        minimum_quantity=Decimal("2"),
                        maximum_quantity=Decimal("8"),
                    )
                    for product in products
                ]
            )

        small_rules = build_rules(2, 600)
        with CaptureQueriesContext(connection) as small_context:
            evaluate_replenishment_rules(small_rules)

        larger_rules = build_rules(12, 700)
        with CaptureQueriesContext(connection) as larger_context:
            evaluate_replenishment_rules(larger_rules)

        self.assertLessEqual(len(larger_context), len(small_context) + 1)
