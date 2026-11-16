from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Company, Product, StockMove, Warehouse
from modules.organization.models import Branch

from .forms import InventoryPickingLineForm
from .models import (
    InventoryBalanceEvent,
    InventoryLocation,
    InventoryPicking,
    InventoryPickingLine,
    InventoryReservation,
    InventoryStockBalance,
    InventoryStockCount,
)
from .services import (
    cancel_inventory_picking,
    cancel_stock_count,
    check_inventory_picking_availability,
    confirm_inventory_picking,
    confirm_stock_count,
    reset_inventory_picking_to_draft,
    set_inventory_picking_done_quantities,
    unreserve_inventory_picking,
    validate_inventory_picking,
)


class InventoryPickingWorkflowTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Picking Co", code="PICK")
        self.branch = Branch.objects.get(company=self.company, is_main=True)
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            branch=self.branch,
            code="PICK-WH",
            name="Picking Warehouse",
        )
        self.user = get_user_model().objects.create_user(
            username="picker",
            password="test-only-password",
        )

    def _product(self, sequence, stock=Decimal("0")):
        return Product.objects.create(
            company=self.company,
            sku=f"PICK-{sequence}",
            name=f"Picking Product {sequence}",
            product_type=Product.ProductType.STOCKABLE,
            current_stock=stock,
        )

    def _location(self, sequence, warehouse=None):
        warehouse = warehouse or self.warehouse
        return InventoryLocation.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=warehouse,
            name=f"Pick Bin {warehouse.pk}-{sequence}",
            sequence=sequence,
        )

    def _balance(self, product, quantity, *, location=None, warehouse=None):
        warehouse = warehouse or self.warehouse
        location = location or self._location(product.pk, warehouse=warehouse)
        return InventoryStockBalance.objects.create(
            company=self.company,
            product=product,
            warehouse=warehouse,
            location=location,
            qty_on_hand=quantity,
        )

    def _picking(self, *demands, warehouse=None):
        picking = InventoryPicking.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=warehouse or self.warehouse,
            destination_name="Mobile Customer",
            destination_address="Warehouse gate 2",
        )
        lines = [
            InventoryPickingLine.objects.create(
                picking=picking,
                product=product,
                requested_quantity=quantity,
            )
            for product, quantity in demands
        ]
        return picking, lines

    def test_full_picking_lifecycle_consumes_exact_reservations(self):
        first_product = self._product(1, Decimal("5"))
        second_product = self._product(2, Decimal("4"))
        first_balance = self._balance(first_product, Decimal("5"))
        second_balance = self._balance(second_product, Decimal("4"))
        picking, lines = self._picking(
            (first_product, Decimal("3")),
            (second_product, Decimal("2")),
        )

        confirm_inventory_picking(picking, self.user)
        check_inventory_picking_availability(picking, self.user)
        picking.refresh_from_db()
        for line in lines:
            line.refresh_from_db()
        self.assertEqual(picking.status, InventoryPicking.Status.READY)
        self.assertEqual(
            [line.reserved_quantity for line in lines],
            [Decimal("3"), Decimal("2")],
        )

        set_inventory_picking_done_quantities(picking, self.user)
        validate_inventory_picking(picking, self.user)
        picking.refresh_from_db()
        first_balance.refresh_from_db()
        second_balance.refresh_from_db()
        first_product.refresh_from_db()
        second_product.refresh_from_db()
        self.assertEqual(picking.status, InventoryPicking.Status.DONE)
        self.assertEqual(first_balance.qty_on_hand, Decimal("2"))
        self.assertEqual(second_balance.qty_on_hand, Decimal("2"))
        self.assertEqual(first_balance.qty_reserved, Decimal("0"))
        self.assertEqual(second_balance.qty_reserved, Decimal("0"))
        self.assertEqual(first_product.current_stock, Decimal("2"))
        self.assertEqual(second_product.current_stock, Decimal("2"))
        moves = StockMove.objects.filter(reference__startswith=picking.reference)
        self.assertEqual(moves.count(), 2)
        self.assertEqual(
            InventoryBalanceEvent.objects.filter(stock_move__in=moves).count(),
            2,
        )
        self.assertTrue(
            all(
                status == InventoryReservation.Status.CONSUMED
                for status in InventoryReservation.objects.filter(
                    reference__startswith=picking.reference
                ).values_list("status", flat=True)
            )
        )

    def test_partial_picking_validation_creates_draft_backorder(self):
        product = self._product(10, Decimal("3"))
        balance = self._balance(product, Decimal("3"))
        picking, lines = self._picking((product, Decimal("5")))
        confirm_inventory_picking(picking, self.user)
        check_inventory_picking_availability(picking, self.user)
        picking.refresh_from_db()
        lines[0].refresh_from_db()
        self.assertEqual(picking.status, InventoryPicking.Status.PARTIAL)
        self.assertEqual(lines[0].reserved_quantity, Decimal("3"))

        set_inventory_picking_done_quantities(picking, self.user)
        validate_inventory_picking(picking, self.user)
        picking.refresh_from_db()
        balance.refresh_from_db()
        backorder = picking.backorders.get()
        backorder_line = backorder.lines.get()
        self.assertEqual(picking.status, InventoryPicking.Status.DONE)
        self.assertEqual(balance.qty_on_hand, Decimal("0"))
        self.assertEqual(backorder.status, InventoryPicking.Status.DRAFT)
        self.assertEqual(backorder_line.requested_quantity, Decimal("2"))
        self.assertEqual(backorder.destination_name, picking.destination_name)

    def test_unreserve_then_recheck_replaces_reservation_without_double_counting(self):
        product = self._product(20, Decimal("5"))
        balance = self._balance(product, Decimal("3"))
        picking, lines = self._picking((product, Decimal("5")))
        confirm_inventory_picking(picking, self.user)
        check_inventory_picking_availability(picking, self.user)
        lines[0].refresh_from_db()
        first_reservation_id = lines[0].current_reservation_id

        unreserve_inventory_picking(picking, self.user)
        balance.refresh_from_db()
        self.assertEqual(balance.qty_reserved, Decimal("0"))
        InventoryStockBalance.objects.filter(pk=balance.pk).update(
            qty_on_hand=Decimal("5"),
            qty_available=Decimal("5"),
        )
        check_inventory_picking_availability(picking, self.user)
        picking.refresh_from_db()
        lines[0].refresh_from_db()
        balance.refresh_from_db()
        self.assertEqual(picking.status, InventoryPicking.Status.READY)
        self.assertNotEqual(lines[0].current_reservation_id, first_reservation_id)
        self.assertEqual(
            InventoryReservation.objects.get(pk=first_reservation_id).status,
            InventoryReservation.Status.RELEASED,
        )
        self.assertEqual(balance.qty_reserved, Decimal("5"))
        self.assertEqual(balance.qty_available, Decimal("0"))

    def test_cancel_releases_stock_and_reset_restores_editable_draft(self):
        product = self._product(30, Decimal("4"))
        balance = self._balance(product, Decimal("4"))
        picking, lines = self._picking((product, Decimal("4")))
        confirm_inventory_picking(picking, self.user)
        check_inventory_picking_availability(picking, self.user)

        cancel_inventory_picking(picking, self.user)
        picking.refresh_from_db()
        balance.refresh_from_db()
        self.assertEqual(picking.status, InventoryPicking.Status.CANCELLED)
        self.assertEqual(balance.qty_reserved, Decimal("0"))
        reset_inventory_picking_to_draft(picking, self.user)
        picking.refresh_from_db()
        lines[0].refresh_from_db()
        self.assertEqual(picking.status, InventoryPicking.Status.DRAFT)
        self.assertIsNone(lines[0].current_reservation_id)
        self.assertEqual(lines[0].reserved_quantity, Decimal("0"))

    def test_stock_count_freeze_blocks_check_availability_and_validate(self):
        product = self._product(40, Decimal("4"))
        self._balance(product, Decimal("4"))
        picking, _lines = self._picking((product, Decimal("4")))
        confirm_inventory_picking(picking, self.user)
        stock_count = InventoryStockCount.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.warehouse,
            counted_qty_default=InventoryStockCount.CountedDefault.SYSTEM,
        )
        confirm_stock_count(stock_count, self.user)
        with self.assertRaisesMessage(ValidationError, "locked by Stock Count"):
            check_inventory_picking_availability(picking, self.user)
        cancel_stock_count(stock_count, self.user)

        check_inventory_picking_availability(picking, self.user)
        set_inventory_picking_done_quantities(picking, self.user)
        second_count = InventoryStockCount.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.warehouse,
            counted_qty_default=InventoryStockCount.CountedDefault.SYSTEM,
        )
        confirm_stock_count(second_count, self.user)
        with self.assertRaisesMessage(ValidationError, "locked by Stock Count"):
            validate_inventory_picking(picking, self.user)
        picking.refresh_from_db()
        self.assertEqual(picking.status, InventoryPicking.Status.READY)
        cancel_stock_count(second_count, self.user)

    def test_mobile_line_form_accepts_done_quantity_only_when_allocated(self):
        product = self._product(50, Decimal("2"))
        self._balance(product, Decimal("2"))
        picking, lines = self._picking((product, Decimal("2")))
        confirm_inventory_picking(picking, self.user)
        check_inventory_picking_availability(picking, self.user)
        lines[0].refresh_from_db()

        form = InventoryPickingLineForm(
            data={
                "picking": picking.pk,
                "product": product.pk,
                "location": "",
                "lot": "",
                "requested_quantity": "2",
                "reserved_quantity": "2",
                "done_quantity": "2",
            },
            instance=lines[0],
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.done_quantity, Decimal("2"))
        self.assertTrue(form.fields["product"].disabled)
        self.assertTrue(form.fields["reserved_quantity"].disabled)

        unreserve_inventory_picking(picking, self.user)
        saved.done_quantity = Decimal("1")
        with self.assertRaisesMessage(ValidationError, "system managed"):
            saved.save()

    def test_generic_workflow_dispatches_hmx_style_picking_actions(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])
        self.client.force_login(self.user)
        product = self._product(60, Decimal("2"))
        self._balance(product, Decimal("2"))
        picking, _lines = self._picking((product, Decimal("2")))

        for action, expected in (
            ("confirm", InventoryPicking.Status.CONFIRMED),
            ("check_availability", InventoryPicking.Status.READY),
            ("set_done_quantities", InventoryPicking.Status.READY),
            ("validate", InventoryPicking.Status.DONE),
        ):
            response = self.client.post(
                reverse(
                    "document_action",
                    args=["inventory-pickings", picking.pk, action],
                )
            )
            self.assertEqual(response.status_code, 302)
            picking.refresh_from_db()
            self.assertEqual(picking.status, expected)

    def test_check_and_validate_query_counts_do_not_grow_with_lines(self):
        def build_fixture(count, sequence, warehouse_code):
            warehouse = Warehouse.objects.create(
                company=self.company,
                branch=self.branch,
                code=warehouse_code,
                name=warehouse_code,
            )
            products = [
                self._product(sequence + index, Decimal("1"))
                for index in range(count)
            ]
            locations = [
                self._location(sequence + index, warehouse=warehouse)
                for index in range(count)
            ]
            InventoryStockBalance.objects.bulk_create(
                [
                    InventoryStockBalance(
                        company=self.company,
                        product=product,
                        warehouse=warehouse,
                        location=location,
                        qty_on_hand=Decimal("1"),
                        qty_available=Decimal("1"),
                    )
                    for product, location in zip(products, locations)
                ]
            )
            picking, _lines = self._picking(
                *((product, Decimal("1")) for product in products),
                warehouse=warehouse,
            )
            confirm_inventory_picking(picking, self.user)
            return picking

        small = build_fixture(2, 100, "PICK-SMALL")
        with CaptureQueriesContext(connection) as small_check:
            check_inventory_picking_availability(small, self.user)
        set_inventory_picking_done_quantities(small, self.user)
        with CaptureQueriesContext(connection) as small_validate:
            validate_inventory_picking(small, self.user)

        large = build_fixture(12, 200, "PICK-LARGE")
        with CaptureQueriesContext(connection) as large_check:
            check_inventory_picking_availability(large, self.user)
        set_inventory_picking_done_quantities(large, self.user)
        with CaptureQueriesContext(connection) as large_validate:
            validate_inventory_picking(large, self.user)

        self.assertLessEqual(len(large_check), len(small_check) + 1)
        self.assertLessEqual(len(large_validate), len(small_validate) + 1)
