from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Product, StockMove, Warehouse
from modules.organization.models import Branch

from .models import (
    InventoryBalanceEvent,
    InventoryLocation,
    InventoryLotSerial,
    InventoryReservation,
    InventoryReservationAllocation,
    InventoryStockBalance,
    InventoryStockCount,
)
from .services import (
    cancel_stock_count,
    confirm_stock_count,
    consume_inventory_reservation,
    release_inventory_reservation,
    reserve_inventory,
)


class InventoryReservationWorkflowTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Reservation Co", code="RSV")
        self.branch = Branch.objects.get(company=self.company, is_main=True)
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            branch=self.branch,
            code="RSV-WH",
            name="Reservation Warehouse",
        )
        self.user = get_user_model().objects.create_user(
            username="reservation-user",
            password="test-only-password",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="RSV-PRODUCT",
            name="Reserved Product",
            product_type=Product.ProductType.STOCKABLE,
        )

    def _location(self, sequence, *, strategy=InventoryLocation.RemovalStrategy.FIFO):
        return InventoryLocation.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.warehouse,
            name=f"Bin {sequence}",
            usage=InventoryLocation.Usage.INTERNAL,
            sequence=sequence,
            removal_strategy=strategy,
        )

    def _balance(self, location, quantity, *, lot=None, incoming_date=None):
        return InventoryStockBalance.objects.create(
            company=self.company,
            product=self.product,
            warehouse=self.warehouse,
            location=location,
            lot=lot,
            qty_on_hand=quantity,
            qty_reserved=Decimal("0"),
            incoming_date=incoming_date or timezone.localdate(),
        )

    def _set_global_stock(self):
        quantity = sum(
            InventoryStockBalance.objects.filter(product=self.product).values_list(
                "qty_on_hand", flat=True
            ),
            Decimal("0"),
        )
        Product.objects.filter(pk=self.product.pk).update(current_stock=quantity)
        self.product.refresh_from_db()

    def _reservation(self, quantity, **kwargs):
        return InventoryReservation.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.warehouse,
            product=self.product,
            requested_quantity=quantity,
            **kwargs,
        )

    def test_reserve_uses_location_priority_and_prevents_oversell(self):
        first = self._balance(self._location(10), Decimal("10"))
        second = self._balance(self._location(20), Decimal("10"))
        self._set_global_stock()
        reservation = self._reservation(Decimal("15"))

        reserve_inventory(reservation, self.user)
        reservation.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(reservation.status, InventoryReservation.Status.RESERVED)
        self.assertEqual(reservation.reserved_quantity, Decimal("15"))
        allocations = list(reservation.allocations.order_by("balance_id"))
        self.assertEqual(
            {row.balance_id: row.quantity for row in allocations},
            {first.pk: Decimal("10"), second.pk: Decimal("5")},
        )
        self.assertEqual(first.qty_available, Decimal("0"))
        self.assertEqual(second.qty_available, Decimal("5"))

        unavailable = self._reservation(Decimal("6"))
        with self.assertRaisesMessage(ValidationError, "Insufficient available stock"):
            reserve_inventory(unavailable, self.user)
        unavailable.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(unavailable.status, InventoryReservation.Status.DRAFT)
        self.assertEqual(second.qty_reserved, Decimal("5"))

        partial = self._reservation(Decimal("6"), allow_partial=True)
        reserve_inventory(partial, self.user)
        partial.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(partial.status, InventoryReservation.Status.PARTIAL)
        self.assertEqual(partial.reserved_quantity, Decimal("5"))
        self.assertEqual(second.qty_reserved, Decimal("10"))
        self.assertEqual(second.qty_available, Decimal("0"))

    def test_fefo_location_allocates_earliest_expiring_lot_first(self):
        location = self._location(10, strategy=InventoryLocation.RemovalStrategy.FEFO)
        later_lot = InventoryLotSerial.objects.create(
            company=self.company,
            product=self.product,
            name="LOT-LATER",
            expiration_date=timezone.localdate() + timedelta(days=30),
        )
        earlier_lot = InventoryLotSerial.objects.create(
            company=self.company,
            product=self.product,
            name="LOT-EARLIER",
            expiration_date=timezone.localdate() + timedelta(days=5),
        )
        later = self._balance(location, Decimal("5"), lot=later_lot)
        earlier = self._balance(location, Decimal("5"), lot=earlier_lot)
        self._set_global_stock()

        reservation = self._reservation(Decimal("6"))
        reserve_inventory(reservation, self.user)

        allocations = {
            row.balance_id: row.quantity for row in reservation.allocations.all()
        }
        self.assertEqual(allocations[earlier.pk], Decimal("5"))
        self.assertEqual(allocations[later.pk], Decimal("1"))

    def test_release_restores_available_stock_and_cannot_repeat(self):
        lot = InventoryLotSerial.objects.create(
            company=self.company,
            product=self.product,
            name="LOT-RELEASE",
            expiration_date=timezone.localdate() + timedelta(days=1),
        )
        balance = self._balance(self._location(10), Decimal("8"), lot=lot)
        self._set_global_stock()
        reservation = self._reservation(Decimal("6"), lot=lot)
        reserve_inventory(reservation, self.user)
        InventoryLotSerial.objects.filter(pk=lot.pk).update(
            expiration_date=timezone.localdate() - timedelta(days=1)
        )

        release_inventory_reservation(reservation, self.user)
        reservation.refresh_from_db()
        balance.refresh_from_db()
        self.assertEqual(reservation.status, InventoryReservation.Status.RELEASED)
        self.assertEqual(balance.qty_reserved, Decimal("0"))
        self.assertEqual(balance.qty_available, Decimal("8"))
        with self.assertRaisesMessage(ValidationError, "Only active Reservations"):
            release_inventory_reservation(reservation, self.user)

    def test_consume_posts_exact_moves_events_and_updates_lot_and_product(self):
        location = self._location(10)
        lot = InventoryLotSerial.objects.create(
            company=self.company,
            product=self.product,
            name="LOT-CONSUME",
            quantity=Decimal("10"),
        )
        balance = self._balance(location, Decimal("10"), lot=lot)
        self._set_global_stock()
        reservation = self._reservation(Decimal("4"), lot=lot)
        reserve_inventory(reservation, self.user)

        consume_inventory_reservation(reservation, self.user)
        reservation.refresh_from_db()
        balance.refresh_from_db()
        self.product.refresh_from_db()
        lot.refresh_from_db()
        allocation = reservation.allocations.get()
        move = allocation.stock_move

        self.assertEqual(reservation.status, InventoryReservation.Status.CONSUMED)
        self.assertEqual(balance.qty_on_hand, Decimal("6"))
        self.assertEqual(balance.qty_reserved, Decimal("0"))
        self.assertEqual(balance.qty_available, Decimal("6"))
        self.assertEqual(self.product.current_stock, Decimal("6"))
        self.assertEqual(lot.quantity, Decimal("6"))
        self.assertEqual(move.direction, StockMove.Direction.OUT)
        self.assertEqual(move.quantity, Decimal("4"))
        self.assertEqual(move.reference, reservation.reference)
        self.assertTrue(InventoryBalanceEvent.objects.filter(stock_move=move).exists())
        with self.assertRaisesMessage(ValidationError, "Only active Reservations"):
            consume_inventory_reservation(reservation, self.user)
        self.assertEqual(StockMove.objects.filter(reference=reservation.reference).count(), 1)

    def test_stock_count_freeze_blocks_reserve_release_and_consume(self):
        self._balance(self._location(10), Decimal("12"))
        self._set_global_stock()
        active = self._reservation(Decimal("4"))
        reserve_inventory(active, self.user)
        pending = self._reservation(Decimal("2"))
        stock_count = InventoryStockCount.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.warehouse,
            counted_qty_default=InventoryStockCount.CountedDefault.SYSTEM,
        )
        confirm_stock_count(stock_count, self.user)

        for operation, reservation in (
            (reserve_inventory, pending),
            (release_inventory_reservation, active),
            (consume_inventory_reservation, active),
        ):
            with self.assertRaisesMessage(ValidationError, "locked by Stock Count"):
                operation(reservation, self.user)
        active.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(active.status, InventoryReservation.Status.RESERVED)
        self.assertEqual(pending.status, InventoryReservation.Status.DRAFT)
        cancel_stock_count(stock_count, self.user)

    def test_ordinary_outbound_move_cannot_consume_reserved_stock(self):
        opening = StockMove.objects.create(
            company=self.company,
            product=self.product,
            warehouse=self.warehouse,
            direction=StockMove.Direction.IN,
            quantity=Decimal("5"),
            reference="RSV-OPENING",
            created_by=self.user,
        )
        self.assertIsNotNone(opening.pk)
        reservation = self._reservation(Decimal("4"))
        reserve_inventory(reservation, self.user)

        with self.assertRaisesMessage(ValidationError, "Available: 1.000"):
            StockMove.objects.create(
                company=self.company,
                product=self.product,
                warehouse=self.warehouse,
                direction=StockMove.Direction.OUT,
                quantity=Decimal("2"),
                reference="WOULD-STEAL-RESERVED",
                created_by=self.user,
            )
        self.product.refresh_from_db()
        balance = InventoryStockBalance.objects.get(product=self.product)
        self.assertEqual(self.product.current_stock, Decimal("5"))
        self.assertEqual(balance.qty_on_hand, Decimal("5"))
        self.assertEqual(balance.qty_reserved, Decimal("4"))

    def test_generic_workflow_accepts_partial_reservation_as_success(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=["is_superuser", "is_staff"])
        self.client.force_login(self.user)
        self._balance(self._location(10), Decimal("3"))
        self._set_global_stock()
        reservation = self._reservation(Decimal("5"), allow_partial=True)

        response = self.client.post(
            reverse(
                "document_action",
                args=["inventory-reservations", reservation.pk, "reserve"],
            ),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        reservation.refresh_from_db()
        self.assertEqual(reservation.status, InventoryReservation.Status.PARTIAL)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("moved to Partially Reserved" in message for message in messages))
        self.assertFalse(any("review the validation details" in message for message in messages))

    def test_reservation_query_count_does_not_grow_with_allocations(self):
        def run_fixture(count, suffix):
            warehouse = Warehouse.objects.create(
                company=self.company,
                branch=self.branch,
                code=f"RSV-{suffix}",
                name=f"Reservation {suffix}",
            )
            product = Product.objects.create(
                company=self.company,
                sku=f"RSV-{suffix}",
                name=f"Product {suffix}",
                product_type=Product.ProductType.STOCKABLE,
                current_stock=Decimal(count),
            )
            locations = [
                InventoryLocation.objects.create(
                    company=self.company,
                    branch=self.branch,
                    warehouse=warehouse,
                    name=f"{suffix} Bin {index}",
                    sequence=index,
                )
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
                    for location in locations
                ]
            )
            reservation = InventoryReservation.objects.create(
                company=self.company,
                branch=self.branch,
                warehouse=warehouse,
                product=product,
                requested_quantity=Decimal(count),
            )
            with CaptureQueriesContext(connection) as reserve_queries:
                reserve_inventory(reservation, self.user)
            with CaptureQueriesContext(connection) as release_queries:
                release_inventory_reservation(reservation, self.user)
            return len(reserve_queries), len(release_queries)

        small_reserve, small_release = run_fixture(2, "SMALL")
        large_reserve, large_release = run_fixture(12, "LARGE")
        self.assertLessEqual(large_reserve, small_reserve + 1)
        self.assertLessEqual(large_release, small_release + 1)

        self.assertEqual(
            InventoryReservationAllocation.objects.filter(
                reservation__warehouse__code="RSV-LARGE"
            ).count(),
            12,
        )


class InventoryReservationConcurrencyTests(TransactionTestCase):
    available_apps = [
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "core",
        "modules.organization",
        "modules.inventory",
    ]

    def setUp(self):
        company = Company.objects.create(name="Concurrent Reservation", code="RSV-CON")
        branch = Branch.objects.get(company=company, is_main=True)
        warehouse = Warehouse.objects.create(
            company=company,
            branch=branch,
            code="RSV-CON-WH",
            name="Concurrent Warehouse",
        )
        product = Product.objects.create(
            company=company,
            sku="RSV-CON-P",
            name="Concurrent Product",
            product_type=Product.ProductType.STOCKABLE,
            current_stock=Decimal("5"),
        )
        location = InventoryLocation.objects.create(
            company=company,
            branch=branch,
            warehouse=warehouse,
            name="Concurrent Bin",
            sequence=10,
        )
        self.balance = InventoryStockBalance.objects.create(
            company=company,
            product=product,
            warehouse=warehouse,
            location=location,
            qty_on_hand=Decimal("5"),
        )
        self.user = get_user_model().objects.create_user(
            username="concurrent-reservation-user",
            password="test-only-password",
        )
        self.reservations = [
            InventoryReservation.objects.create(
                company=company,
                branch=branch,
                warehouse=warehouse,
                product=product,
                requested_quantity=Decimal("4"),
            )
            for _index in range(2)
        ]

    def test_concurrent_reservations_lock_stock_and_never_oversell(self):
        barrier = Barrier(2)

        def attempt(reservation_id):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                reserve_inventory(reservation_id, self.user)
                return "reserved"
            except ValidationError:
                return "rejected"
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(attempt, [row.pk for row in self.reservations])
            )

        self.balance.refresh_from_db()
        statuses = list(
            InventoryReservation.objects.filter(
                pk__in=[row.pk for row in self.reservations]
            )
            .order_by("pk")
            .values_list("status", flat=True)
        )
        self.assertCountEqual(results, ["reserved", "rejected"])
        self.assertCountEqual(
            statuses,
            [InventoryReservation.Status.RESERVED, InventoryReservation.Status.DRAFT],
        )
        self.assertEqual(self.balance.qty_on_hand, Decimal("5"))
        self.assertEqual(self.balance.qty_reserved, Decimal("4"))
        self.assertEqual(self.balance.qty_available, Decimal("1"))
        self.assertEqual(InventoryReservationAllocation.objects.count(), 1)
