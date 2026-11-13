from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse

from .models import (
    GoodsReceipt,
    PurchaseOrderControl,
    PurchaseOrderLinePricingProvenance,
    PurchaseOrderRevision,
    PurchaseOrderType,
    PurchaseProductRentalProfile,
    PurchaseSetting,
    VendorPricelist,
    VendorProfile,
)
from .purchase_revision import revise_purchase_order
from .services import sync_purchase_order_rental_controls
from .workflow_extensions import purchase_revision_action_available


class PurchaseOrderRevisionParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="purchase-revision-admin",
            password="test-password",
            email="purchase-revision@example.com",
        )
        self.company = Company.objects.create(code="PREV", name="Purchase Revision")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="PREV-VENDOR",
            name="Revision Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.profile = VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor,
            registration_number="PREV-VENDOR",
            status=VendorProfile.Status.ACTIVE,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="PREV-WH",
            name="Revision Warehouse",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="PREV-PRODUCT",
            name="Revision Product",
            product_type=Product.ProductType.STOCKABLE,
        )
        self.setting = PurchaseSetting.objects.create(
            company=self.company,
            allow_order_revision=True,
            order_revision_sequence_mode=PurchaseSetting.RevisionSequenceMode.APPEND_SUFFIX,
        )

    def _order(self, number, *, line_count=1, status=PurchaseOrder.Status.CONFIRMED):
        order = PurchaseOrder.objects.create(
            company=self.company,
            number=number,
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=timezone.localdate(),
            status=status,
        )
        products = [self.product]
        if line_count > 1:
            products = Product.objects.bulk_create(
                [
                    Product(
                        company=self.company,
                        sku=f"{number}-P-{index}",
                        name=f"{number} Product {index}",
                        product_type=Product.ProductType.STOCKABLE,
                    )
                    for index in range(line_count)
                ]
            )
        PurchaseOrderLine.objects.bulk_create(
            [
                PurchaseOrderLine(
                    order=order,
                    product=product,
                    quantity=Decimal("2"),
                    unit_price=Decimal("15") + index,
                )
                for index, product in enumerate(products)
            ]
        )
        order.total = sum((line.subtotal for line in order.lines.all()), Decimal("0"))
        order.save(update_fields=["total", "updated_at"])
        PurchaseOrderControl.objects.create(order=order)
        return order

    def test_revision_creates_new_draft_and_preserves_immutable_source_snapshot(self):
        source = self._order("PO-PREV-001")
        source.purchase_control.payment_terms = "Net 30"
        source.purchase_control.delivery_terms = "DAP Jakarta"
        source.purchase_control.legal_terms = "Indonesian law"
        source.purchase_control.vendor_address = "Vendor address snapshot"
        source.purchase_control.bill_address = "Billing address snapshot"
        source.purchase_control.save(
            update_fields=[
                "payment_terms",
                "delivery_terms",
                "legal_terms",
                "vendor_address",
                "bill_address",
                "updated_at",
            ]
        )

        revised = revise_purchase_order(source, self.actor, reason="Correct negotiated price")

        source.refresh_from_db()
        revised.refresh_from_db()
        revision = PurchaseOrderRevision.objects.select_related("original_order").get(order=source)
        revised_line = revised.lines.get()
        self.assertEqual(source.status, PurchaseOrder.Status.REVISED)
        self.assertEqual(revised.status, PurchaseOrder.Status.DRAFT)
        self.assertEqual(revised.number, "PO-PREV-001/R1")
        self.assertEqual(revision.revised_order, revised)
        self.assertEqual(revision.original_order, source)
        self.assertEqual(revision.reason, "Correct negotiated price")
        self.assertEqual(revision.snapshot["previous_status"], PurchaseOrder.Status.CONFIRMED)
        self.assertEqual(len(revision.snapshot["lines"]), 1)
        self.assertEqual(revised_line.quantity, Decimal("2"))
        self.assertEqual(revised_line.unit_price, Decimal("15"))
        self.assertEqual(revised_line.received_quantity, Decimal("0"))
        self.assertEqual(revised.purchase_control.revision_number, 1)
        self.assertEqual(revised.purchase_control.payment_terms, "Net 30")
        self.assertEqual(revised.purchase_control.delivery_terms, "DAP Jakarta")
        self.assertEqual(revised.purchase_control.legal_terms, "Indonesian law")
        self.assertEqual(revised.purchase_control.vendor_address, "Vendor address snapshot")
        self.assertEqual(revised.purchase_control.bill_address, "Billing address snapshot")

        with self.assertRaises(ValidationError):
            revise_purchase_order(source, self.actor)

    def test_revision_chain_keeps_root_and_increments_suffix(self):
        root = self._order("PO-PREV-CHAIN")
        first = revise_purchase_order(root, self.actor)
        first_line = first.lines.get()
        first_line.unit_price = Decimal("12")
        first_line.save(update_fields=["unit_price", "updated_at"])
        first.status = PurchaseOrder.Status.CONFIRMED
        first.total = first_line.subtotal
        first.save(update_fields=["status", "total", "updated_at"])

        second = revise_purchase_order(first, self.actor)
        first.refresh_from_db()

        link = PurchaseOrderRevision.objects.get(revised_order=second)
        self.assertEqual(first.status, PurchaseOrder.Status.REVISED)
        self.assertEqual(second.number, "PO-PREV-CHAIN/R2")
        self.assertEqual(link.order, first)
        self.assertEqual(link.original_order, root)
        self.assertEqual(second.lines.get().unit_price, Decimal("12"))
        self.assertEqual(second.purchase_control.revision_number, 2)

    def test_revision_preserves_rental_timeline_and_pricing_provenance(self):
        rental_type = PurchaseOrderType.objects.create(
            company=self.company,
            name="Rental",
        )
        PurchaseProductRentalProfile.objects.create(product=self.product, is_rental=True)
        source = self._order(
            "PO-PREV-RENTAL",
            status=PurchaseOrder.Status.DRAFT,
        )
        control = source.purchase_control
        control.order_type = rental_type
        control.expected_date = timezone.localdate()
        control.expiry_date = timezone.localdate()
        control.rental_duration = 2
        control.duration_unit = PurchaseOrderControl.DurationUnit.WEEK
        control.save(
            update_fields=[
                "order_type",
                "expected_date",
                "expiry_date",
                "rental_duration",
                "duration_unit",
                "updated_at",
            ]
        )
        sync_purchase_order_rental_controls(source)
        source.status = PurchaseOrder.Status.CONFIRMED
        source.save(update_fields=["status", "updated_at"])

        revised = revise_purchase_order(source, self.actor)

        source_provenance = PurchaseOrderLinePricingProvenance.objects.get(
            order_line__order=source
        )
        revised_provenance = PurchaseOrderLinePricingProvenance.objects.get(
            order_line__order=revised
        )
        self.assertTrue(revised_provenance.is_rental)
        self.assertEqual(revised_provenance.rent_duration, source_provenance.rent_duration)
        self.assertEqual(revised_provenance.rental_period, source_provenance.rental_period)
        self.assertEqual(revised_provenance.expected_date, source_provenance.expected_date)
        self.assertEqual(revised_provenance.expiry_date, source_provenance.expiry_date)
        self.assertEqual(revised.purchase_control.rental_duration, 2)
        self.assertEqual(revised.purchase_control.duration_unit, PurchaseOrderControl.DurationUnit.WEEK)
        self.assertEqual(revised_provenance.tax_snapshot, {})

    def test_revision_disabled_blacklisted_expired_and_receipt_guards(self):
        disabled = self._order("PO-PREV-DISABLED")
        self.setting.allow_order_revision = False
        self.setting.save(update_fields=["allow_order_revision", "updated_at"])
        self.assertFalse(purchase_revision_action_available(disabled))
        with self.assertRaisesMessage(ValidationError, "Revision is disabled"):
            revise_purchase_order(disabled, self.actor)

        self.setting.allow_order_revision = True
        self.setting.save(update_fields=["allow_order_revision", "updated_at"])
        blacklisted = self._order("PO-PREV-BLACKLIST")
        self.profile.status = VendorProfile.Status.BLACKLISTED
        self.profile.save(update_fields=["status", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "blacklisted vendor"):
            revise_purchase_order(blacklisted, self.actor)
        self.profile.status = VendorProfile.Status.ACTIVE
        self.profile.save(update_fields=["status", "updated_at"])

        expired = self._order("PO-PREV-EXPIRED-PL")
        pricelist = VendorPricelist.objects.create(
            company=self.company,
            vendor=self.profile,
            code="PREV-EXPIRED",
            name="Expired Revision Pricelist",
            date_start=timezone.localdate(),
            status=VendorPricelist.Status.EXPIRED,
        )
        expired.purchase_control.vendor_pricelist = pricelist
        expired.purchase_control.save(update_fields=["vendor_pricelist", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "expired vendor pricelist"):
            revise_purchase_order(expired, self.actor)

        receipt_source = self._order("PO-PREV-RECEIPT")
        GoodsReceipt.objects.create(
            company=self.company,
            number="GR-PREV-ACTIVE",
            order=receipt_source,
            warehouse=self.warehouse,
            status=GoodsReceipt.Status.DRAFT,
        )
        with self.assertRaisesMessage(ValidationError, "already has a goods receipt"):
            revise_purchase_order(receipt_source, self.actor)
        receipt_source.refresh_from_db()
        self.assertEqual(receipt_source.status, PurchaseOrder.Status.CONFIRMED)
        self.assertFalse(PurchaseOrderRevision.objects.filter(order=receipt_source).exists())

    def test_follow_sequence_uses_purchase_order_type_sequence(self):
        self.setting.order_revision_sequence_mode = PurchaseSetting.RevisionSequenceMode.FOLLOW_SEQUENCE
        self.setting.save(update_fields=["order_revision_sequence_mode", "updated_at"])
        order_type = PurchaseOrderType.objects.create(
            company=self.company,
            name="Revision Sequence Type",
        )
        source = self._order("PO-PREV-SEQUENCE")
        source.purchase_control.order_type = order_type
        source.purchase_control.save(update_fields=["order_type", "updated_at"])

        revised = revise_purchase_order(source, self.actor)

        self.assertNotEqual(revised.number, f"{source.number}/R1")
        self.assertIn(order_type.prefix_sequence, revised.number)

    def test_document_workflow_action_redirects_to_new_revision(self):
        source = self._order("PO-PREV-UI")
        self.client.force_login(self.actor)

        response = self.client.post(
            reverse("document_action", args=["purchases", source.pk, "revise"]),
            {"reason": "UI revision"},
        )

        revision = PurchaseOrderRevision.objects.get(order=source)
        source.refresh_from_db()
        self.assertRedirects(
            response,
            reverse("entity_edit", args=["purchases", revision.revised_order_id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(source.status, PurchaseOrder.Status.REVISED)
        self.assertEqual(revision.reason, "UI revision")

    def test_revision_query_count_does_not_grow_with_line_count(self):
        small = self._order("PO-PREV-Q2", line_count=2)
        large = self._order("PO-PREV-Q12", line_count=12)

        with CaptureQueriesContext(connection) as small_queries:
            small_revision = revise_purchase_order(small, self.actor)
        with CaptureQueriesContext(connection) as large_queries:
            large_revision = revise_purchase_order(large, self.actor)

        self.assertEqual(small_revision.lines.count(), 2)
        self.assertEqual(large_revision.lines.count(), 12)
        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Revision queries grew from {len(small_queries)} to {len(large_queries)}.",
        )
