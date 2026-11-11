from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse
from core.views import DOCUMENT_CONFIG

from .line_merge import merge_purchase_document_lines
from .models import (
    AwardStrategy,
    BlanketOrder,
    BlanketOrderLine,
    PurchaseQuotation,
    PurchaseQuotationLine,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseTender,
    PurchaseTenderLine,
)


class PurchaseLineMergeParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="purchase-line-merge-admin",
            password="test-password",
            email="purchase-line-merge@example.com",
        )
        self.company = Company.objects.create(code="PLM", name="Purchase Line Merge")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="PLM-VENDOR",
            name="Line Merge Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="PLM-WH",
            name="Line Merge Warehouse",
        )
        self.other_warehouse = Warehouse.objects.create(
            company=self.company,
            code="PLM-WH-OTHER",
            name="Other Line Merge Warehouse",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="PLM-PRODUCT",
            name="Line Merge Product",
            purchase_price=Decimal("25"),
        )
        self.other_product = Product.objects.create(
            company=self.company,
            sku="PLM-OTHER",
            name="Other Line Merge Product",
            purchase_price=Decimal("25"),
        )
        self.award_strategy = AwardStrategy.objects.create(
            company=self.company,
            name="Line Merge Award",
            strategy=AwardStrategy.Strategy.SINGLE,
        )

    def _request(self, number="PR-PLM"):
        return PurchaseRequest.objects.create(
            company=self.company,
            number=number,
            requestor=self.actor,
            request_date=timezone.localdate(),
            required_date=timezone.localdate() + timedelta(days=2),
            destination_warehouse=self.warehouse,
            justification="Line merge parity",
        )

    def _quotation(self):
        return PurchaseQuotation.objects.create(
            company=self.company,
            number="RFQ-PLM",
            vendor=self.vendor,
            warehouse=self.warehouse,
            response_deadline=timezone.localdate() + timedelta(days=7),
        )

    def _tender(self):
        return PurchaseTender.objects.create(
            company=self.company,
            number="PT-PLM",
            title="Line Merge Tender",
            category=PurchaseTender.Category.OPEX,
            tender_type=PurchaseTender.TenderType.OPEN,
            award_strategy=self.award_strategy,
            warehouse=self.warehouse,
            close_date=timezone.localdate() + timedelta(days=7),
        )

    def _blanket(self):
        return BlanketOrder.objects.create(
            company=self.company,
            number="BO-PLM",
            vendor=self.vendor,
            warehouse=self.warehouse,
            date_start=timezone.localdate(),
            date_end=timezone.localdate() + timedelta(days=30),
        )

    def _order(self, number="PO-PLM", status=PurchaseOrder.Status.DRAFT):
        return PurchaseOrder.objects.create(
            company=self.company,
            number=number,
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=timezone.localdate(),
            status=status,
        )

    def test_matching_lines_fold_for_all_local_hmx_purchase_documents(self):
        request = self._request()
        PurchaseRequestLine.objects.bulk_create(
            [
                PurchaseRequestLine(
                    request=request,
                    product=self.product,
                    description="Matching request line",
                    specification="Grade A",
                    quantity=quantity,
                    estimated_unit_price=Decimal("25"),
                )
                for quantity in (Decimal("2"), Decimal("3"))
            ]
        )

        quotation = self._quotation()
        PurchaseQuotationLine.objects.bulk_create(
            [
                PurchaseQuotationLine(
                    quotation=quotation,
                    product=self.product,
                    description="Matching quotation line",
                    specification="Grade A",
                    quantity=quantity,
                    unit_price=Decimal("25"),
                    expiry_date=quotation.response_deadline,
                )
                for quantity in (Decimal("2"), Decimal("3"))
            ]
        )

        tender = self._tender()
        PurchaseTenderLine.objects.bulk_create(
            [
                PurchaseTenderLine(
                    tender=tender,
                    product=self.product,
                    description="Matching tender line",
                    specification="Grade A",
                    quantity=quantity,
                    target_unit_price=Decimal("25"),
                )
                for quantity in (Decimal("2"), Decimal("3"))
            ]
        )

        blanket = self._blanket()
        BlanketOrderLine.objects.bulk_create(
            [
                BlanketOrderLine(
                    blanket_order=blanket,
                    product=self.product,
                    description="Matching blanket line",
                    agreed_quantity=quantity,
                    unit_price=Decimal("25"),
                )
                for quantity in (Decimal("2"), Decimal("3"))
            ]
        )

        order = self._order()
        PurchaseOrderLine.objects.bulk_create(
            [
                PurchaseOrderLine(
                    order=order,
                    product=self.product,
                    description="Matching order line",
                    warehouse=self.warehouse,
                    quantity=quantity,
                    unit_price=Decimal("25"),
                )
                for quantity in (Decimal("2"), Decimal("3"))
            ]
        )

        for parent, relation, quantity_field in (
            (request, "lines", "quantity"),
            (quotation, "lines", "quantity"),
            (tender, "lines", "quantity"),
            (blanket, "lines", "agreed_quantity"),
            (order, "lines", "quantity"),
        ):
            with self.subTest(parent=type(parent).__name__):
                self.assertEqual(merge_purchase_document_lines(parent), 1)
                line = getattr(parent, relation).get()
                self.assertEqual(getattr(line, quantity_field), Decimal("5"))

        order.refresh_from_db()
        self.assertEqual(order.total, Decimal("125"))

    def test_direct_child_create_and_product_write_keep_changed_line(self):
        request = self._request("PR-PLM-DIRECT")
        PurchaseRequestLine.objects.create(
            request=request,
            product=self.product,
            description="Direct request line",
            quantity=Decimal("2"),
            estimated_unit_price=Decimal("25"),
        )
        created = PurchaseRequestLine.objects.create(
            request=request,
            product=self.product,
            description="Direct request line",
            quantity=Decimal("3"),
            estimated_unit_price=Decimal("25"),
        )
        self.assertEqual(request.lines.get().pk, created.pk)
        self.assertEqual(request.lines.get().quantity, Decimal("5"))

        order = self._order("PO-PLM-DIRECT")
        PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Direct order line",
            warehouse=self.warehouse,
            quantity=Decimal("2"),
            unit_price=Decimal("25"),
        )
        changed = PurchaseOrderLine.objects.create(
            order=order,
            product=self.other_product,
            description="Direct order line",
            warehouse=self.warehouse,
            quantity=Decimal("3"),
            unit_price=Decimal("25"),
        )
        changed.product = self.product
        changed.save(update_fields=["product", "updated_at"])

        self.assertEqual(order.lines.get().pk, changed.pk)
        self.assertEqual(order.lines.get().quantity, Decimal("5"))
        order.refresh_from_db()
        self.assertEqual(order.total, Decimal("125"))

    def test_merge_key_and_progress_guards_keep_materially_different_lines(self):
        order = self._order("PO-PLM-BOUNDARY")
        PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Boundary line",
            warehouse=self.warehouse,
            quantity=Decimal("2"),
            unit_price=Decimal("25"),
        )
        PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Different description",
            warehouse=self.warehouse,
            quantity=Decimal("3"),
            unit_price=Decimal("25"),
        )
        PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Boundary line",
            warehouse=self.other_warehouse,
            quantity=Decimal("4"),
            unit_price=Decimal("25"),
        )
        PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Boundary line",
            warehouse=self.warehouse,
            quantity=Decimal("5"),
            unit_price=Decimal("30"),
        )
        self.assertEqual(order.lines.count(), 4)

        confirmed = self._order("PO-PLM-CONFIRMED", PurchaseOrder.Status.CONFIRMED)
        for quantity in (Decimal("2"), Decimal("3")):
            PurchaseOrderLine.objects.create(
                order=confirmed,
                product=self.product,
                description="Locked line",
                warehouse=self.warehouse,
                quantity=quantity,
                unit_price=Decimal("25"),
            )
        self.assertEqual(confirmed.lines.count(), 2)

        blanket = self._blanket()
        BlanketOrderLine.objects.create(
            blanket_order=blanket,
            product=self.product,
            description="Released line",
            agreed_quantity=Decimal("2"),
            released_quantity=Decimal("1"),
            unit_price=Decimal("25"),
        )
        BlanketOrderLine.objects.create(
            blanket_order=blanket,
            product=self.product,
            description="Released line",
            agreed_quantity=Decimal("3"),
            released_quantity=Decimal("1"),
            unit_price=Decimal("25"),
        )
        self.assertEqual(blanket.lines.count(), 2)

    def test_document_extensions_supply_batch_saver_and_bound(self):
        for entity in (
            "purchase-requests",
            "purchase-quotations",
            "purchase-tenders",
            "blanket-orders",
            "purchases",
        ):
            with self.subTest(entity=entity):
                self.assertEqual(DOCUMENT_CONFIG[entity]["line_limit"], 500)
                self.assertTrue(callable(DOCUMENT_CONFIG[entity]["line_saver"]))

    def test_merge_query_count_is_fixed_for_two_and_twelve_lines(self):
        def create_order(number, line_count):
            order = self._order(number)
            PurchaseOrderLine.objects.bulk_create(
                [
                    PurchaseOrderLine(
                        order=order,
                        product=self.product,
                        description="Query growth line",
                        warehouse=self.warehouse,
                        quantity=Decimal("1"),
                        unit_price=Decimal("25"),
                    )
                    for _index in range(line_count)
                ]
            )
            return order

        small = create_order("PO-PLM-Q-SMALL", 2)
        large = create_order("PO-PLM-Q-LARGE", 12)

        with CaptureQueriesContext(connection) as small_queries:
            self.assertEqual(merge_purchase_document_lines(small), 1)
        with CaptureQueriesContext(connection) as large_queries:
            self.assertEqual(merge_purchase_document_lines(large), 11)

        self.assertEqual((len(small_queries), len(large_queries)), (15, 15))
        self.assertEqual(small.lines.get().quantity, Decimal("2"))
        self.assertEqual(large.lines.get().quantity, Decimal("12"))
