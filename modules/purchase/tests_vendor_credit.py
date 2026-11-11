from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Contact, Invoice, Product, PurchaseOrder, PurchaseOrderLine, Warehouse

from .models import (
    PurchaseAccountingDocument,
    PurchaseOrderControl,
    PurchaseSetting,
    VendorProfile,
)
from .services import confirm_purchase_order
from .vendor_credit import vendor_purchase_credit_used


class VendorPurchaseCreditTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(code="VCL", name="Vendor Credit Limit")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="VCL-VENDOR",
            name="Credit Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.profile = VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor,
            registration_number="VCL-VENDOR",
            purchase_credit_limit=Decimal("1000"),
            status=VendorProfile.Status.ACTIVE,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="VCL-WH",
            name="Credit Warehouse",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="VCL-PRODUCT",
            name="Credit Product",
            product_type=Product.ProductType.STOCKABLE,
        )
        self.setting = PurchaseSetting.objects.create(company=self.company)

    def _order(self, number, amount, *, status=PurchaseOrder.Status.DRAFT, vendor=None):
        order = PurchaseOrder.objects.create(
            company=self.company,
            number=number,
            vendor=vendor or self.vendor,
            warehouse=self.warehouse,
            status=status,
            total=amount if status != PurchaseOrder.Status.DRAFT else Decimal("0"),
        )
        PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            quantity=Decimal("1"),
            unit_price=amount,
        )
        return order

    def test_new_vendor_inherits_default_purchase_limit_when_omitted(self):
        self.setting.credit_limit = Decimal("3000")
        self.setting.save(update_fields=["credit_limit", "updated_at"])
        contact = Contact.objects.create(
            company=self.company,
            code="VCL-DEFAULT",
            name="Default Limit Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )

        profile = VendorProfile.objects.create(
            company=self.company,
            partner=contact,
            registration_number="VCL-DEFAULT",
            status=VendorProfile.Status.ACTIVE,
        )

        self.assertEqual(profile.purchase_credit_limit, Decimal("3000"))

    def test_hard_block_uses_accumulated_debt_and_rolls_back(self):
        self.setting.is_hard_block_limit = True
        self.setting.hard_block_threshold_percent = Decimal("100")
        self.setting.save(
            update_fields=["is_hard_block_limit", "hard_block_threshold_percent", "updated_at"]
        )
        self._order("PO-VCL-USED", Decimal("800"), status=PurchaseOrder.Status.CONFIRMED)
        order = self._order("PO-VCL-HARD", Decimal("300"))
        order.refresh_from_db()
        original_total = order.total

        with self.assertRaisesMessage(ValidationError, "cannot exceed the vendor purchase limit"):
            confirm_purchase_order(order)

        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.DRAFT)
        self.assertEqual(order.total, original_total)
        self.assertFalse(PurchaseOrderControl.objects.filter(order=order).exists())

    def test_soft_block_routes_draft_to_approval_then_allows_approval(self):
        self.setting.is_soft_block_limit = True
        self.setting.soft_block_threshold_percent = Decimal("100")
        self.setting.save(
            update_fields=["is_soft_block_limit", "soft_block_threshold_percent", "updated_at"]
        )
        order = self._order("PO-VCL-SOFT", Decimal("1001"))

        routed = confirm_purchase_order(order)

        control = PurchaseOrderControl.objects.get(order=order)
        self.assertEqual(routed.status, PurchaseOrder.Status.WAITING_APPROVAL)
        self.assertTrue(control.purchase_limit_requires_approval)
        self.assertEqual(control.purchase_limit_total, Decimal("1001"))

        approved = confirm_purchase_order(order)
        self.assertEqual(approved.status, PurchaseOrder.Status.CONFIRMED)

    def test_warning_threshold_records_message_and_continues(self):
        self.setting.is_warning_block_limit = True
        self.setting.warning_block_threshold_percent = Decimal("100")
        self.setting.save(
            update_fields=["is_warning_block_limit", "warning_block_threshold_percent", "updated_at"]
        )
        order = self._order("PO-VCL-WARNING", Decimal("1001"))

        confirmed = confirm_purchase_order(order)

        control = PurchaseOrderControl.objects.get(order=order)
        self.assertEqual(confirmed.status, PurchaseOrder.Status.CONFIRMED)
        self.assertIn("Vendor purchase debt has reached", control.purchase_limit_warning_message)

    def test_zero_vendor_limit_disables_blocking(self):
        self.profile.purchase_credit_limit = Decimal("0")
        self.profile.save(update_fields=["purchase_credit_limit", "updated_at"])
        self.setting.is_hard_block_limit = True
        self.setting.hard_block_threshold_percent = Decimal("100")
        self.setting.save(
            update_fields=["is_hard_block_limit", "hard_block_threshold_percent", "updated_at"]
        )
        order = self._order("PO-VCL-ZERO", Decimal("5000"))

        confirmed = confirm_purchase_order(order)

        self.assertEqual(confirmed.status, PurchaseOrder.Status.CONFIRMED)

    def test_credit_used_counts_debt_states_and_subtracts_paid_bills(self):
        waiting = self._order("PO-VCL-WAIT", Decimal("400"), status=PurchaseOrder.Status.WAITING_APPROVAL)
        confirmed = self._order("PO-VCL-CONF", Decimal("600"), status=PurchaseOrder.Status.CONFIRMED)
        closed = self._order("PO-VCL-CLOSE", Decimal("1000"), status=PurchaseOrder.Status.CLOSED)
        self._order("PO-VCL-DRAFT", Decimal("300"), status=PurchaseOrder.Status.DRAFT)
        self._order("PO-VCL-CANCEL", Decimal("500"), status=PurchaseOrder.Status.CANCELLED)
        paid_invoice = Invoice.objects.create(
            company=self.company,
            number="VB-VCL-PAID",
            invoice_type=Invoice.InvoiceType.VENDOR,
            partner=self.vendor,
            invoice_date=timezone.localdate(),
            due_date=timezone.localdate(),
            status=Invoice.Status.PAID,
            total=Decimal("250"),
            paid_amount=Decimal("250"),
        )
        PurchaseAccountingDocument.objects.create(
            order=closed,
            invoice=paid_invoice,
            number="PAD-VCL-PAID",
            document_type=PurchaseAccountingDocument.DocumentType.VENDOR_BILL,
            total_amount=Decimal("250"),
            status="approved",
        )

        used = vendor_purchase_credit_used(company_id=self.company.pk, vendor_id=self.vendor.pk)

        self.assertEqual(used, Decimal("1750"))
        self.assertEqual(waiting.total + confirmed.total + closed.total - paid_invoice.total, used)

    def test_threshold_configuration_validation_matches_hmx_ordering(self):
        self.setting.is_warning_block_limit = True
        self.setting.warning_block_threshold_percent = Decimal("80")
        self.setting.is_soft_block_limit = True
        self.setting.soft_block_threshold_percent = Decimal("80")
        with self.assertRaises(ValidationError):
            self.setting.save()

        self.setting.warning_block_threshold_percent = Decimal("70")
        self.setting.is_hard_block_limit = True
        self.setting.hard_block_threshold_percent = Decimal("80")
        with self.assertRaises(ValidationError):
            self.setting.save()

    def _vendor_with_orders(self, suffix, count):
        contact = Contact.objects.create(
            company=self.company,
            code=f"VCL-Q-{suffix}",
            name=f"Query Vendor {suffix}",
            contact_type=Contact.ContactType.VENDOR,
        )
        PurchaseOrder.objects.bulk_create(
            [
                PurchaseOrder(
                    company=self.company,
                    number=f"PO-VCL-Q-{suffix}-{index}",
                    vendor=contact,
                    warehouse=self.warehouse,
                    status=PurchaseOrder.Status.CONFIRMED,
                    total=Decimal("10"),
                )
                for index in range(count)
            ]
        )
        return contact

    def test_credit_used_query_count_does_not_grow_with_order_count(self):
        small = self._vendor_with_orders("SMALL", 2)
        large = self._vendor_with_orders("LARGE", 12)

        with CaptureQueriesContext(connection) as small_queries:
            small_used = vendor_purchase_credit_used(company_id=self.company.pk, vendor_id=small.pk)
        with CaptureQueriesContext(connection) as large_queries:
            large_used = vendor_purchase_credit_used(company_id=self.company.pk, vendor_id=large.pk)

        self.assertEqual(small_used, Decimal("20"))
        self.assertEqual(large_used, Decimal("120"))
        self.assertEqual(len(small_queries), 2)
        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Vendor credit queries grew from {len(small_queries)} to {len(large_queries)}.",
        )
