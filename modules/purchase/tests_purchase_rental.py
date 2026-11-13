from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.forms import inlineformset_factory
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse
from core.views import DOCUMENT_CONFIG

from .line_merge import save_and_merge_purchase_document_lines
from .models import (
    PurchaseOrderControl,
    PurchaseOrderLinePricingProvenance,
    PurchaseOrderType,
    PurchaseProductRentalProfile,
    PurchaseSetting,
    VendorProfile,
)
from .services import confirm_purchase_order, sync_purchase_order_rental_controls


class PurchaseRentalParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="purchase-rental-admin",
            password="test-password",
            email="purchase-rental@example.com",
        )
        self.company = Company.objects.create(code="PRENT", name="Purchase Rental")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="PRENT-VENDOR",
            name="Rental Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="PRENT-WH",
            name="Rental Warehouse",
        )
        VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor,
            status=VendorProfile.Status.ACTIVE,
        )
        PurchaseSetting.objects.create(
            company=self.company,
            require_vendor_prequalification=False,
        )
        self.rental_type = PurchaseOrderType.objects.create(
            company=self.company,
            name="  ReNtAl  ",
            requires_approval=False,
        )
        self.rental_type.companies.add(self.company)

    def _product(self, code, *, rental=True):
        product = Product.objects.create(
            company=self.company,
            sku=code,
            name=code.replace("-", " ").title(),
            purchase_price=Decimal("100"),
        )
        PurchaseProductRentalProfile.objects.create(
            product=product,
            is_rental=rental,
        )
        return product

    def _order(
        self,
        suffix,
        *,
        expected_date=date(2027, 6, 10),
        duration=0,
        unit=PurchaseOrderControl.DurationUnit.DAY,
        multiple_duration=False,
        multiple_expected=False,
    ):
        order = PurchaseOrder.objects.create(
            company=self.company,
            number=f"PO-RENT-{suffix}",
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=timezone.localdate(),
            status=PurchaseOrder.Status.DRAFT,
        )
        PurchaseOrderControl.objects.create(
            order=order,
            order_type=self.rental_type,
            expected_date=expected_date,
            rental_duration=duration,
            duration_unit=unit,
            is_multiple_rental_duration=multiple_duration,
            is_multiple_expected_date=multiple_expected,
        )
        return order

    def _line(self, order, product, *, quantity="1", price="100"):
        return PurchaseOrderLine.objects.create(
            order=order,
            product=product,
            warehouse=self.warehouse,
            description=product.name,
            quantity=Decimal(quantity),
            unit_price=Decimal(price),
        )

    def test_rental_type_name_snapshots_product_flag_and_rejects_regular_product(self):
        rental_product = self._product("RENTAL-EXCAVATOR")
        order = self._order("SNAPSHOT", duration=5)
        rental_line = self._line(order, rental_product)

        refreshed = sync_purchase_order_rental_controls(order)

        provenance = PurchaseOrderLinePricingProvenance.objects.get(order_line=rental_line)
        self.assertTrue(refreshed._is_rental_order_type)
        self.assertTrue(provenance.is_rental)

        regular_product = self._product("REGULAR-ITEM", rental=False)
        invalid_order = self._order("REGULAR", duration=5)
        self._line(invalid_order, regular_product)
        with self.assertRaisesMessage(ValidationError, "only accept rental products"):
            sync_purchase_order_rental_controls(invalid_order)

    def test_global_duration_defaults_line_and_multiplies_subtotal(self):
        product = self._product("RENTAL-GLOBAL")
        order = self._order("GLOBAL", duration=5)
        line = self._line(order, product, quantity="2", price="100")

        sync_purchase_order_rental_controls(order)

        order.refresh_from_db()
        control = order.purchase_control
        provenance = line.purchase_pricing_provenance
        self.assertEqual(provenance.rent_duration, 5)
        self.assertEqual(provenance.rental_period, PurchaseOrderControl.DurationUnit.DAY)
        self.assertEqual(provenance.rental_end_date, date(2027, 6, 15))
        self.assertEqual(provenance.total_rental_duration, 5)
        self.assertEqual(control.total_rental_duration, 5)
        self.assertEqual(control.expected_return_date, date(2027, 6, 15))
        self.assertEqual(order.total, Decimal("1000"))

    def test_calendar_month_and_year_duration_respects_month_end_and_leap_year(self):
        cases = [
            (date(2027, 1, 31), "month", 1, date(2027, 2, 28)),
            (date(2028, 1, 31), "month", 1, date(2028, 2, 29)),
            (date(2028, 2, 29), "year", 1, date(2029, 2, 28)),
        ]
        for index, (expected_date, unit, duration, expected_return) in enumerate(cases):
            with self.subTest(expected_date=expected_date, unit=unit):
                product = self._product(f"RENTAL-CALENDAR-{index}")
                order = self._order(
                    f"CALENDAR-{index}",
                    expected_date=expected_date,
                    duration=duration,
                    unit=unit,
                )
                line = self._line(order, product)

                sync_purchase_order_rental_controls(order)

                order.purchase_control.refresh_from_db()
                self.assertEqual(order.purchase_control.expected_return_date, expected_return)
                self.assertEqual(line.purchase_pricing_provenance.rental_end_date, expected_return)

    def test_first_line_fills_empty_header_and_line_override_survives_header_change(self):
        product = self._product("RENTAL-OVERRIDE")
        order = self._order("OVERRIDE")
        line = self._line(order, product, quantity="2", price="100")

        sync_purchase_order_rental_controls(
            order,
            {
                line.pk: {
                    "rent_duration": 4,
                    "rental_period": PurchaseOrderControl.DurationUnit.MONTH,
                }
            },
        )

        control = order.purchase_control
        control.refresh_from_db()
        provenance = line.purchase_pricing_provenance
        self.assertEqual(control.rental_duration, 4)
        self.assertEqual(control.duration_unit, PurchaseOrderControl.DurationUnit.MONTH)
        self.assertEqual(provenance.rental_end_date, date(2027, 10, 10))
        order.refresh_from_db()
        self.assertEqual(order.total, Decimal("800"))

        sync_purchase_order_rental_controls(
            order,
            {
                line.pk: {
                    "rent_duration": 2,
                    "rental_period": PurchaseOrderControl.DurationUnit.MONTH,
                }
            },
        )
        control.refresh_from_db()
        control.rental_duration = 5
        control.duration_unit = PurchaseOrderControl.DurationUnit.YEAR
        control.save(update_fields=["rental_duration", "duration_unit", "updated_at"])
        sync_purchase_order_rental_controls(order)

        provenance.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(provenance.rent_duration, 2)
        self.assertEqual(provenance.rental_period, PurchaseOrderControl.DurationUnit.MONTH)
        self.assertEqual(provenance.rental_end_date, date(2027, 8, 10))
        self.assertEqual(order.total, Decimal("400"))

    def test_multiple_duration_uses_global_or_line_expected_date_by_mode(self):
        product = self._product("RENTAL-MULTIPLE")
        global_date_order = self._order(
            "MULTI-GLOBAL-DATE",
            multiple_duration=True,
            multiple_expected=False,
        )
        global_date_line = self._line(
            global_date_order,
            product,
            quantity="2",
            price="100",
        )
        sync_purchase_order_rental_controls(
            global_date_order,
            {
                global_date_line.pk: {
                    "rent_duration": 10,
                    "rental_period": PurchaseOrderControl.DurationUnit.DAY,
                    "expected_date": date(2027, 1, 31),
                }
            },
        )
        global_provenance = global_date_line.purchase_pricing_provenance
        global_date_order.purchase_control.refresh_from_db()
        self.assertEqual(global_provenance.rental_end_date, date(2027, 6, 20))
        self.assertEqual(global_date_order.purchase_control.total_rental_duration, 10)
        self.assertIsNone(global_date_order.purchase_control.expected_return_date)
        global_date_order.refresh_from_db()
        self.assertEqual(global_date_order.total, Decimal("2000"))

        line_date_order = self._order(
            "MULTI-LINE-DATE",
            multiple_duration=True,
            multiple_expected=True,
        )
        line_date_line = self._line(line_date_order, product)
        sync_purchase_order_rental_controls(
            line_date_order,
            {
                line_date_line.pk: {
                    "rent_duration": 1,
                    "rental_period": PurchaseOrderControl.DurationUnit.MONTH,
                    "expected_date": date(2027, 1, 31),
                }
            },
        )
        self.assertEqual(
            line_date_line.purchase_pricing_provenance.rental_end_date,
            date(2027, 2, 28),
        )

    def test_confirmation_requires_complete_rental_duration(self):
        product = self._product("RENTAL-CONFIRM")
        order = self._order("CONFIRM", duration=0)
        line = self._line(order, product)
        sync_purchase_order_rental_controls(order)

        with self.assertRaisesMessage(ValidationError, "Rental Duration"):
            confirm_purchase_order(order, self.actor)

        sync_purchase_order_rental_controls(
            order,
            {line.pk: {"rent_duration": 3, "rental_period": "week"}},
        )
        confirm_purchase_order(order, self.actor)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)

    def test_document_line_form_persists_module_owned_rental_controls(self):
        product = self._product("RENTAL-FORM")
        order = self._order("FORM", duration=3, unit="week")
        document = DOCUMENT_CONFIG["purchases"]
        formset_class = inlineformset_factory(
            PurchaseOrder,
            PurchaseOrderLine,
            form=document["line_form"],
            fields=document["line_fields"],
            extra=1,
            can_delete=True,
        )
        formset = formset_class(
            {
                "document_lines-TOTAL_FORMS": "1",
                "document_lines-INITIAL_FORMS": "0",
                "document_lines-MIN_NUM_FORMS": "0",
                "document_lines-MAX_NUM_FORMS": "500",
                "document_lines-0-product": str(product.pk),
                "document_lines-0-description": "Rental form line",
                "document_lines-0-warehouse": str(self.warehouse.pk),
                "document_lines-0-quantity": "2",
                "document_lines-0-unit_price": "125",
                "document_lines-0-cost_center": "OPS-RENT",
                "document_lines-0-expected_date": "2027-07-01",
                "document_lines-0-expiry_date": "2027-06-20",
                "document_lines-0-rent_duration": "2",
                "document_lines-0-rental_period": "month",
            },
            instance=order,
            prefix="document_lines",
        )
        self.assertTrue(formset.is_valid(), formset.errors)

        save_and_merge_purchase_document_lines(order, formset)

        provenance = PurchaseOrderLinePricingProvenance.objects.get(
            order_line__order=order,
            free_product=False,
        )
        self.assertEqual(provenance.rent_duration, 2)
        self.assertEqual(provenance.rental_period, "month")
        self.assertEqual(provenance.expected_date, date(2027, 7, 1))
        self.assertEqual(provenance.expiry_date, date(2027, 6, 20))
        self.assertEqual(provenance.cost_center, "OPS-RENT")
        self.assertEqual(provenance.rental_end_date, date(2027, 8, 10))

    def test_rental_sync_query_count_does_not_grow_with_lines(self):
        small_order = self._order("QUERY-SMALL", duration=2)
        large_order = self._order("QUERY-LARGE", duration=2)
        small_products = [self._product(f"RENTAL-Q-SMALL-{index}") for index in range(2)]
        large_products = [self._product(f"RENTAL-Q-LARGE-{index}") for index in range(12)]
        PurchaseOrderLine.objects.bulk_create(
            [
                PurchaseOrderLine(
                    order=small_order,
                    product=product,
                    warehouse=self.warehouse,
                    quantity=Decimal("1"),
                    unit_price=Decimal("100"),
                )
                for product in small_products
            ]
        )
        PurchaseOrderLine.objects.bulk_create(
            [
                PurchaseOrderLine(
                    order=large_order,
                    product=product,
                    warehouse=self.warehouse,
                    quantity=Decimal("1"),
                    unit_price=Decimal("100"),
                )
                for product in large_products
            ]
        )

        with CaptureQueriesContext(connection) as small_queries:
            sync_purchase_order_rental_controls(small_order)
        with CaptureQueriesContext(connection) as large_queries:
            sync_purchase_order_rental_controls(large_order)

        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Rental-sync queries grew from {len(small_queries)} to {len(large_queries)}.",
        )
