from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Company, Product, StockMove, Warehouse
from modules.organization.models import Branch

from .forms import InventoryStockCountLineForm
from .models import (
    InventoryBalanceEvent,
    InventoryStockBalance,
    InventoryStockCount,
    InventoryStockFreeze,
)
from .services import (
    cancel_stock_count,
    complete_stock_count,
    confirm_stock_count,
    validate_stock_count,
)


class InventoryStockCountWorkflowTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Count Co", code="COUNT")
        self.branch = Branch.objects.get(company=self.company, is_main=True)
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            branch=self.branch,
            code="COUNT-WH",
            name="Count Warehouse",
        )
        self.user = get_user_model().objects.create_user(
            username="stock-counter",
            password="test-only-password",
        )

    def _product(self, sequence):
        return Product.objects.create(
            company=self.company,
            sku=f"COUNT-{sequence}",
            name=f"Count Product {sequence}",
            product_type=Product.ProductType.STOCKABLE,
        )

    def _opening_stock(self, product, quantity, warehouse=None):
        return StockMove.objects.create(
            company=self.company,
            product=product,
            warehouse=warehouse or self.warehouse,
            direction=StockMove.Direction.IN,
            quantity=quantity,
            reference=f"OPEN-{product.sku}",
            created_by=self.user,
        )

    def _count(self, warehouse=None):
        return InventoryStockCount.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=warehouse or self.warehouse,
            counted_qty_default=InventoryStockCount.CountedDefault.SYSTEM,
        )

    def test_count_lifecycle_posts_exact_variance_and_releases_freeze(self):
        product = self._product(1)
        self._opening_stock(product, Decimal("10"))
        stock_count = self._count()

        confirm_stock_count(stock_count, self.user)
        stock_count.refresh_from_db()
        self.assertEqual(stock_count.status, InventoryStockCount.Status.IN_PROGRESS)
        self.assertTrue(InventoryStockFreeze.objects.filter(stock_count=stock_count).exists())
        line = stock_count.lines.get()
        self.assertEqual(line.system_qty, Decimal("10"))
        self.assertEqual(line.counted_qty, Decimal("10"))

        line.counted_qty = Decimal("7")
        line.save()
        complete_stock_count(stock_count, self.user)
        validate_stock_count(stock_count, self.user)

        stock_count.refresh_from_db()
        line.refresh_from_db()
        product.refresh_from_db()
        balance = InventoryStockBalance.objects.get(product=product)
        adjustment = StockMove.objects.get(pk=line.adjustment_stock_move_id)
        self.assertEqual(stock_count.status, InventoryStockCount.Status.VALIDATED)
        self.assertEqual(line.difference_qty, Decimal("-3"))
        self.assertEqual(adjustment.direction, StockMove.Direction.OUT)
        self.assertEqual(adjustment.quantity, Decimal("3"))
        self.assertEqual(adjustment.reference, stock_count.name)
        self.assertEqual(product.current_stock, Decimal("7"))
        self.assertEqual(balance.qty_on_hand, Decimal("7"))
        self.assertEqual(balance.qty_available, Decimal("7"))
        self.assertTrue(InventoryBalanceEvent.objects.filter(stock_move=adjustment).exists())
        self.assertFalse(InventoryStockFreeze.objects.filter(stock_count=stock_count).exists())

        with self.assertRaisesMessage(ValidationError, "Only Completed"):
            validate_stock_count(stock_count, self.user)
        self.assertEqual(StockMove.objects.filter(reference=stock_count.name).count(), 1)

    def test_freeze_blocks_other_stock_moves_until_count_is_cancelled(self):
        product = self._product(2)
        self._opening_stock(product, Decimal("5"))
        stock_count = self._count()
        confirm_stock_count(stock_count, self.user)
        move_count = StockMove.objects.count()

        with self.assertRaisesMessage(ValidationError, "locked by Stock Count"):
            StockMove.objects.create(
                company=self.company,
                product=product,
                warehouse=self.warehouse,
                direction=StockMove.Direction.IN,
                quantity=Decimal("1"),
                reference="BLOCKED-DURING-COUNT",
                created_by=self.user,
            )
        self.assertEqual(StockMove.objects.count(), move_count)

        cancel_stock_count(stock_count, self.user)
        accepted = self._opening_stock(product, Decimal("1"))
        self.assertIsNotNone(accepted.pk)
        stock_count.refresh_from_db()
        self.assertEqual(stock_count.status, InventoryStockCount.Status.CANCELLED)
        self.assertFalse(InventoryStockFreeze.objects.filter(stock_count=stock_count).exists())

    def test_validation_rolls_back_when_snapshot_balance_changed(self):
        product = self._product(3)
        self._opening_stock(product, Decimal("8"))
        stock_count = self._count()
        confirm_stock_count(stock_count, self.user)
        line = stock_count.lines.get()
        line.counted_qty = Decimal("6")
        line.save()
        complete_stock_count(stock_count, self.user)
        InventoryStockBalance.objects.filter(product=product).update(
            qty_on_hand=Decimal("9"),
            qty_available=Decimal("9"),
        )

        with self.assertRaisesMessage(ValidationError, "Stock changed after the snapshot"):
            validate_stock_count(stock_count, self.user)

        stock_count.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(stock_count.status, InventoryStockCount.Status.COMPLETED)
        self.assertIsNone(line.adjustment_stock_move_id)
        self.assertTrue(InventoryStockFreeze.objects.filter(stock_count=stock_count).exists())
        self.assertFalse(StockMove.objects.filter(reference=stock_count.name).exists())

    def test_confirm_and_validate_query_counts_do_not_grow_with_lines(self):
        def build_fixture(count, sequence, warehouse_code):
            warehouse = Warehouse.objects.create(
                company=self.company,
                branch=self.branch,
                code=warehouse_code,
                name=warehouse_code,
            )
            products = [self._product(sequence + index) for index in range(count)]
            for product in products:
                self._opening_stock(product, Decimal("4"), warehouse=warehouse)
            return self._count(warehouse=warehouse)

        small_count = build_fixture(2, 100, "COUNT-SMALL")
        with CaptureQueriesContext(connection) as small_confirm:
            confirm_stock_count(small_count, self.user)
        small_count.lines.update(
            counted_qty=Decimal("3"),
            difference_qty=Decimal("-1"),
        )
        complete_stock_count(small_count, self.user)
        with CaptureQueriesContext(connection) as small_validate:
            validate_stock_count(small_count, self.user)

        larger_count = build_fixture(12, 200, "COUNT-LARGE")
        with CaptureQueriesContext(connection) as larger_confirm:
            confirm_stock_count(larger_count, self.user)
        larger_count.lines.update(
            counted_qty=Decimal("3"),
            difference_qty=Decimal("-1"),
        )
        complete_stock_count(larger_count, self.user)
        with CaptureQueriesContext(connection) as larger_validate:
            validate_stock_count(larger_count, self.user)

        self.assertLessEqual(len(larger_confirm), len(small_confirm) + 1)
        self.assertLessEqual(len(larger_validate), len(small_validate) + 1)

    def test_generic_entity_workflow_dispatches_module_owned_handlers(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])
        self.client.force_login(self.user)
        product = self._product(900)
        self._opening_stock(product, Decimal("5"))
        stock_count = self._count()

        for action, expected in (
            ("confirm", InventoryStockCount.Status.IN_PROGRESS),
            ("complete", InventoryStockCount.Status.COMPLETED),
            ("validate", InventoryStockCount.Status.VALIDATED),
        ):
            response = self.client.post(
                reverse(
                    "document_action",
                    args=["inventory-stock-counts", stock_count.pk, action],
                )
            )
            self.assertEqual(response.status_code, 302)
            stock_count.refresh_from_db()
            self.assertEqual(stock_count.status, expected)

    def test_manual_counting_line_uses_current_exact_position(self):
        opening_product = self._product(950)
        manual_product = self._product(951)
        self._opening_stock(opening_product, Decimal("2"))
        stock_count = self._count()
        confirm_stock_count(stock_count, self.user)
        location = stock_count.lines.get().location

        form = InventoryStockCountLineForm(
            data={
                "stock_count": stock_count.pk,
                "product": manual_product.pk,
                "location": location.pk,
                "lot": "",
                "system_qty": "0",
                "counted_qty": "3",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        line = form.save()
        self.assertEqual(line.system_qty, Decimal("0"))
        self.assertEqual(line.counted_qty, Decimal("3"))
        self.assertEqual(line.difference_qty, Decimal("3"))
        self.assertEqual(line.line_source, line.LineSource.MANUAL)

        line.system_qty = Decimal("1")
        with self.assertRaisesMessage(ValidationError, "snapshot identity"):
            line.save()
        line.refresh_from_db()

        complete_stock_count(stock_count, self.user)
        line.refresh_from_db()
        locked_form = InventoryStockCountLineForm(
            data={
                "stock_count": stock_count.pk,
                "product": manual_product.pk,
                "location": location.pk,
                "lot": "",
                "system_qty": "0",
                "counted_qty": "4",
            },
            instance=line,
        )
        self.assertFalse(locked_form.is_valid())
        self.assertIn("In Progress", str(locked_form.non_field_errors()))
