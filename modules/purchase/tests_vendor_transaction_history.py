from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse

from .models import (
    AwardStrategy,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrderControl,
    PurchaseReturnRequest,
    PurchaseTender,
    TenderBid,
    VendorCategory,
    VendorProfile,
    VendorTransactionHistory,
)
from .tasks import sync_vendor_transaction_history_task
from .vendor_transaction_history import (
    prepare_vendor_transaction_history,
    sync_vendor_transaction_history_page,
)


class VendorTransactionHistoryParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="vendor-history-admin",
            email="vendor-history@example.com",
            password="test-password",
        )
        self.company = Company.objects.create(code="VTH", name="Vendor History")
        self.category = VendorCategory.objects.create(
            company=self.company,
            code="VTH",
            name="History Vendors",
        )
        self.contact = Contact.objects.create(
            company=self.company,
            code="VTH-VENDOR",
            name="History Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.vendor = VendorProfile.objects.create(
            company=self.company,
            partner=self.contact,
            category=self.category,
            status=VendorProfile.Status.ACTIVE,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="VTH-WH",
            name="History Warehouse",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="VTH-PRODUCT",
            name="History Product",
        )
        today = timezone.localdate()
        self.order = PurchaseOrder.objects.create(
            company=self.company,
            number="PO-VTH-001",
            vendor=self.contact,
            warehouse=self.warehouse,
            order_date=today - timedelta(days=5),
            status=PurchaseOrder.Status.CONFIRMED,
        )
        self.order_line = PurchaseOrderLine.objects.create(
            order=self.order,
            product=self.product,
            description="History Product Line",
            quantity=Decimal("10"),
            unit_price=Decimal("20"),
            received_quantity=Decimal("10"),
        )
        PurchaseOrderControl.objects.create(
            order=self.order,
            expected_date=today - timedelta(days=1),
        )
        self.receipt = GoodsReceipt.objects.create(
            company=self.company,
            number="GR-VTH-001",
            order=self.order,
            warehouse=self.warehouse,
            receipt_date=today,
            delivery_status=GoodsReceipt.DeliveryStatus.LATE,
            status=GoodsReceipt.Status.RECEIVED,
        )
        self.receipt_line = GoodsReceiptLine.objects.create(
            receipt=self.receipt,
            order_line=self.order_line,
            received_quantity=Decimal("10"),
            accepted_quantity=Decimal("8"),
            rejected_quantity=Decimal("2"),
            returned_quantity=Decimal("2"),
            rejection_reason="Damaged packaging",
        )
        self.return_request = PurchaseReturnRequest.objects.create(
            company=self.company,
            number="PRTN-VTH-001",
            receipt=self.receipt,
            return_date=today,
            reason="Return damaged goods",
            status="approved",
            approver=self.actor,
        )
        strategy = AwardStrategy.objects.create(
            company=self.company,
            name="History Tender Strategy",
        )
        self.tender = PurchaseTender.objects.create(
            company=self.company,
            number="TENDER-VTH-001",
            title="History Tender",
            category=PurchaseTender.Category.OPEX,
            tender_type=PurchaseTender.TenderType.OPEN,
            award_strategy=strategy,
            publish_date=today - timedelta(days=10),
            close_date=today + timedelta(days=10),
            status=PurchaseTender.Status.BID_SUBMISSION,
        )
        self.bid = TenderBid.objects.create(
            tender=self.tender,
            vendor=self.contact,
            submitted_at=timezone.now(),
            bid_amount=Decimal("200"),
            status=TenderBid.Status.SUBMITTED,
        )

    def test_smart_action_materializes_all_local_transaction_sources_read_only(self):
        self.client.force_login(self.actor)
        profile_response = self.client.get(
            reverse("entity_edit", args=["vendor-profiles", self.vendor.pk])
        )
        self.assertContains(
            profile_response,
            "smart_action=vendor-profiles.transaction-history",
        )
        self.assertEqual(VendorTransactionHistory.objects.count(), 0)

        response = self.client.get(
            reverse("entity_list", args=["vendor-transaction-history"]),
            {
                "smart_action": "vendor-profiles.transaction-history",
                "source_id": self.vendor.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(VendorTransactionHistory.objects.count(), 4)
        self.assertContains(response, "PO-VTH-001")
        self.assertContains(response, "GR-VTH-001")
        self.assertContains(response, "PRTN-VTH-001")
        self.assertContains(response, "TENDER-VTH-001")
        self.assertFalse(response.context["can_add"])
        self.assertFalse(response.context["can_delete"])

    def test_materialized_history_matches_hmx_breakdown_and_is_idempotent(self):
        prepare_vendor_transaction_history(self.vendor)
        prepare_vendor_transaction_history(self.vendor)

        self.assertEqual(VendorTransactionHistory.objects.count(), 4)
        purchase = VendorTransactionHistory.objects.get(transaction_type="Purchase Order")
        self.assertEqual(purchase.source_document, self.order.number)
        self.assertEqual(purchase.product, self.product)
        self.assertEqual(purchase.value, Decimal("200.000000"))
        self.assertEqual(purchase.ordered_quantity, Decimal("10.000000"))
        self.assertEqual(purchase.received_quantity, Decimal("10.000000"))
        self.assertEqual(purchase.remaining_quantity, Decimal("0.000000"))
        self.assertEqual(purchase.delivery_status, "Late")
        self.assertEqual(purchase.lead_time_variance, 1)
        self.assertTrue(purchase.rejection_indicator)

        receipt = VendorTransactionHistory.objects.get(transaction_type="Goods Receipt")
        self.assertEqual(receipt.rejection_reason, "Damaged packaging")
        self.assertTrue(receipt.rejection_indicator)
        returned = VendorTransactionHistory.objects.get(transaction_type="Vendor Return")
        self.assertEqual(returned.received_quantity, Decimal("2.000000"))
        self.assertTrue(returned.rejection_indicator)

    def test_history_is_company_and_vendor_isolated(self):
        other_company = Company.objects.create(code="VTH2", name="Other Vendor History")
        other_contact = Contact.objects.create(
            company=other_company,
            code="VTH2-VENDOR",
            name="Other History Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        other_vendor = VendorProfile.objects.create(
            company=other_company,
            partner=other_contact,
            status=VendorProfile.Status.ACTIVE,
        )

        prepare_vendor_transaction_history(other_vendor)
        self.assertFalse(VendorTransactionHistory.objects.exists())
        prepare_vendor_transaction_history(self.vendor)
        self.assertEqual(
            set(VendorTransactionHistory.objects.values_list("vendor_id", flat=True)),
            {self.vendor.pk},
        )

    def test_purchase_history_query_count_does_not_grow_with_lines(self):
        with CaptureQueriesContext(connection) as small_queries:
            sync_vendor_transaction_history_page(self.vendor, "purchase")

        PurchaseOrderLine.objects.bulk_create(
            [
                PurchaseOrderLine(
                    order=self.order,
                    product=self.product,
                    description=f"Growth line {index}",
                    quantity=Decimal("1"),
                    unit_price=Decimal("10"),
                )
                for index in range(20)
            ]
        )
        with CaptureQueriesContext(connection) as many_queries:
            sync_vendor_transaction_history_page(self.vendor, "purchase")

        self.assertLessEqual(len(many_queries), len(small_queries) + 1)

    def test_prepare_queues_keyset_backfill_after_one_hundred_recent_rows(self):
        PurchaseOrderLine.objects.bulk_create(
            [
                PurchaseOrderLine(
                    order=self.order,
                    product=self.product,
                    description=f"Paged line {index}",
                    quantity=Decimal("1"),
                    unit_price=Decimal("10"),
                )
                for index in range(100)
            ]
        )

        with patch.object(sync_vendor_transaction_history_task, "apply_async") as queued:
            result = prepare_vendor_transaction_history(self.vendor)

        self.assertTrue(result["has_more"])
        queued.assert_called_once()
        kwargs = queued.call_args.kwargs["kwargs"]
        self.assertEqual(kwargs["vendor_id"], self.vendor.pk)
        self.assertEqual(kwargs["source"], "purchase")
        self.assertGreater(kwargs["before_id"], 0)
        self.assertEqual(queued.call_args.kwargs["countdown"], 2)

    def test_background_task_continues_with_keyset_cursor(self):
        PurchaseOrderLine.objects.bulk_create(
            [
                PurchaseOrderLine(
                    order=self.order,
                    product=self.product,
                    description=f"Task line {index}",
                    quantity=Decimal("1"),
                    unit_price=Decimal("10"),
                )
                for index in range(100)
            ]
        )
        first = sync_vendor_transaction_history_page(self.vendor, "purchase")
        self.assertTrue(first["has_more"])

        with patch.object(sync_vendor_transaction_history_task, "apply_async") as queued:
            result = sync_vendor_transaction_history_task.run(
                vendor_id=self.vendor.pk,
                source="purchase",
                before_id=first["next_before_id"],
            )

        self.assertFalse(result["has_more"])
        self.assertEqual(result["processed"], 1)
        queued.assert_not_called()
        self.assertEqual(
            VendorTransactionHistory.objects.filter(transaction_type="Purchase Order").count(),
            101,
        )
