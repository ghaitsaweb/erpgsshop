from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse

from .models import PurchaseOrderControl, PurchaseOrderType
from .services import sync_purchase_order_rental_controls


class PurchaseOrderTimelineParityTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(code="POTIME", name="Purchase Timeline")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="POTIME-VENDOR",
            name="Timeline Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="POTIME-WH",
            name="Timeline Warehouse",
        )
        self.order_type = PurchaseOrderType.objects.create(
            company=self.company,
            name="Standard Purchase",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="POTIME-PRODUCT",
            name="Timeline Product",
            purchase_price=Decimal("20"),
        )

    def _order(self, suffix, *, expected=None, expiry=None):
        order = PurchaseOrder.objects.create(
            company=self.company,
            number=f"PO-TIME-{suffix}",
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=date(2027, 1, 1),
        )
        PurchaseOrderControl.objects.create(
            order=order,
            order_type=self.order_type,
            expected_date=expected,
            expiry_date=expiry,
        )
        return order

    def _line(self, order, suffix=""):
        product = self.product
        if suffix:
            product = Product.objects.create(
                company=self.company,
                sku=f"POTIME-{suffix}",
                name=f"Timeline {suffix}",
                purchase_price=Decimal("20"),
            )
        return PurchaseOrderLine.objects.create(
            order=order,
            product=product,
            warehouse=self.warehouse,
            quantity=Decimal("1"),
            unit_price=Decimal("20"),
        )

    def test_header_timeline_defaults_to_new_order_line(self):
        expected = date(2027, 2, 15)
        expiry = date(2027, 1, 31)
        order = self._order("DEFAULT", expected=expected, expiry=expiry)
        line = self._line(order)

        sync_purchase_order_rental_controls(order)

        provenance = line.purchase_pricing_provenance
        self.assertEqual(provenance.expected_date, expected)
        self.assertEqual(provenance.expiry_date, expiry)

    def test_header_change_updates_inherited_values_but_preserves_line_override(self):
        old_expected = date(2027, 2, 15)
        old_expiry = date(2027, 1, 31)
        order = self._order("PROPAGATE", expected=old_expected, expiry=old_expiry)
        inherited = self._line(order, "INHERITED")
        overridden = self._line(order, "OVERRIDDEN")
        sync_purchase_order_rental_controls(order)
        sync_purchase_order_rental_controls(
            order,
            {
                overridden.pk: {
                    "expected_date": date(2027, 3, 20),
                    "expiry_date": date(2027, 2, 5),
                }
            },
        )

        control = PurchaseOrderControl.objects.get(order=order)
        control.expected_date = date(2027, 4, 1)
        control.expiry_date = date(2027, 3, 1)
        control.save(update_fields=["expected_date", "expiry_date", "updated_at"])
        order._purchase_previous_expected_date = old_expected
        order._purchase_previous_expiry_date = old_expiry
        sync_purchase_order_rental_controls(order)

        inherited.purchase_pricing_provenance.refresh_from_db()
        overridden.purchase_pricing_provenance.refresh_from_db()
        self.assertEqual(inherited.purchase_pricing_provenance.expected_date, date(2027, 4, 1))
        self.assertEqual(inherited.purchase_pricing_provenance.expiry_date, date(2027, 3, 1))
        self.assertEqual(overridden.purchase_pricing_provenance.expected_date, date(2027, 3, 20))
        self.assertEqual(overridden.purchase_pricing_provenance.expiry_date, date(2027, 2, 5))

    def test_first_line_timeline_fills_empty_header(self):
        order = self._order("FILL")
        line = self._line(order)

        sync_purchase_order_rental_controls(
            order,
            {
                line.pk: {
                    "expected_date": date(2027, 5, 10),
                    "expiry_date": date(2027, 4, 10),
                }
            },
        )

        control = PurchaseOrderControl.objects.get(order=order)
        self.assertEqual(control.expected_date, date(2027, 5, 10))
        self.assertEqual(control.expiry_date, date(2027, 4, 10))
        self.assertEqual(line.purchase_pricing_provenance.expected_date, date(2027, 5, 10))
        self.assertEqual(line.purchase_pricing_provenance.expiry_date, date(2027, 4, 10))
