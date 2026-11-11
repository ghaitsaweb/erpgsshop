from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.models import Company, Contact, Invoice, Product, PurchaseOrder, PurchaseOrderLine, Warehouse
from modules.finance.models import (
    Account,
    AccountJournal,
    AccountingSettings,
    JournalEntry,
    ProductAccountMapping,
)
from modules.finance.services import post_invoice

from .models import (
    GoodsReceipt,
    PurchaseAccountingDocument,
    PurchaseOrderControl,
    PurchaseSetting,
    PurchaseThreeWayMatch,
    ThreeWayMatchStatus,
)
from .services import (
    confirm_purchase_order,
    create_goods_receipts,
    create_purchase_vendor_bill,
    inspect_goods_receipt,
    receive_goods_receipt,
)
from .three_way_match import sync_purchase_three_way_match


class PurchaseThreeWayMatchTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="purchase-match-admin",
            password="test-password",
            email="purchase-match@example.com",
        )
        self.company = Company.objects.create(code="PUR-MATCH", name="Purchase Matching")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="VEN-MATCH",
            name="Matching Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="WH-MATCH",
            name="Matching Warehouse",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="STK-MATCH",
            name="Matching Product",
            product_type=Product.ProductType.STOCKABLE,
            purchase_price=Decimal("25.00"),
        )
        self.setting = PurchaseSetting.objects.create(company=self.company)
        payable = Account.objects.create(
            company=self.company,
            code="2100-MATCH",
            name="Trade Payable",
            account_type=Account.AccountType.LIABILITY,
            allow_reconciliation=True,
        )
        receivable = Account.objects.create(
            company=self.company,
            code="1100-MATCH",
            name="Trade Receivable",
            account_type=Account.AccountType.ASSET,
            allow_reconciliation=True,
        )
        expense = Account.objects.create(
            company=self.company,
            code="5100-MATCH",
            name="Purchases",
            account_type=Account.AccountType.EXPENSE,
        )
        AccountJournal.objects.create(
            company=self.company,
            code="PUR-MATCH",
            name="Purchase Matching Journal",
            journal_type=AccountJournal.JournalType.PURCHASE,
            default_debit_account=expense,
            default_credit_account=payable,
        )
        AccountingSettings.objects.create(
            company=self.company,
            default_receivable_account=receivable,
            default_payable_account=payable,
        )
        ProductAccountMapping.objects.create(
            company=self.company,
            product=self.product,
            revenue_account=expense,
            expense_account=expense,
        )

    def _order(self, number, *, policy=PurchaseOrder.InvoicingPolicy.ORDERED, quantity=Decimal("10")):
        order = PurchaseOrder.objects.create(
            company=self.company,
            number=number,
            vendor=self.vendor,
            warehouse=self.warehouse,
            invoicing_policy=policy,
        )
        line = PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            quantity=quantity,
            unit_price=Decimal("25.00"),
        )
        confirm_purchase_order(order, self.actor)
        return order, line

    def _receive(self, order, quantity):
        create_goods_receipts(order, self.actor)
        receipt = GoodsReceipt.objects.filter(order=order, status=GoodsReceipt.Status.DRAFT).get()
        receipt_line = receipt.lines.get()
        receipt_line.received_quantity = quantity
        receipt_line.accepted_quantity = quantity
        receipt_line.rejected_quantity = Decimal("0")
        receipt_line.save(
            update_fields=["received_quantity", "accepted_quantity", "rejected_quantity", "updated_at"]
        )
        inspect_goods_receipt(receipt, self.actor)
        receive_goods_receipt(receipt, self.actor)

    def _draft_bill(self, order):
        result = create_purchase_vendor_bill(order, self.actor)
        return Invoice.objects.get(pk=result._created_vendor_bill_id)

    def test_monitoring_tracks_billing_exception_and_is_company_scoped(self):
        other_company = Company.objects.create(code="PUR-MATCH-2", name="Other Matching Company")
        PurchaseSetting.objects.create(
            company=other_company,
            purchase_three_way_match_mode=PurchaseSetting.ThreeWayMatchMode.STRICT,
        )
        order, line = self._order("PO-MONITOR")
        self._receive(order, Decimal("8"))

        invoice = self._draft_bill(order)
        posted = post_invoice(invoice, self.actor)

        line.refresh_from_db()
        snapshot = PurchaseThreeWayMatch.objects.get(order_line=line)
        control = PurchaseOrderControl.objects.get(order=order)
        self.assertEqual(posted.status, Invoice.Status.POSTED)
        self.assertEqual(snapshot.matching_billed_quantity, Decimal("10"))
        self.assertEqual(snapshot.over_billed, Decimal("2"))
        self.assertEqual(snapshot.match_status, ThreeWayMatchStatus.BILLING_EXCEPTION)
        self.assertEqual(control.match_status, ThreeWayMatchStatus.BILLING_EXCEPTION)

    def test_strict_mode_rolls_back_quantity_and_price_exceptions(self):
        self.setting.purchase_three_way_match_mode = PurchaseSetting.ThreeWayMatchMode.STRICT
        self.setting.save(update_fields=["purchase_three_way_match_mode", "updated_at"])
        order, line = self._order(
            "PO-STRICT",
            policy=PurchaseOrder.InvoicingPolicy.RECEIVED,
        )
        self._receive(order, Decimal("8"))
        invoice = self._draft_bill(order)
        invoice_line = invoice.lines.get()
        invoice_line.quantity = Decimal("9")
        invoice_line.save(update_fields=["quantity", "updated_at"])

        with self.assertRaisesMessage(ValidationError, "above received quantity"):
            post_invoice(invoice, self.actor)

        invoice.refresh_from_db()
        line.refresh_from_db()
        document = PurchaseAccountingDocument.objects.get(invoice=invoice)
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertEqual(document.status, "draft")
        self.assertEqual(line.billed_quantity, Decimal("0"))
        self.assertFalse(JournalEntry.objects.filter(source_reference=invoice.number).exists())

        invoice_line.quantity = Decimal("8")
        invoice_line.unit_price = Decimal("26")
        invoice_line.save(update_fields=["quantity", "unit_price", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "exceeds the Purchase Order price"):
            post_invoice(invoice, self.actor)

        self.setting.purchase_match_price_tolerance = Decimal("1")
        self.setting.save(update_fields=["purchase_match_price_tolerance", "updated_at"])
        posted = post_invoice(invoice, self.actor)

        line.refresh_from_db()
        snapshot = PurchaseThreeWayMatch.objects.get(order_line=line)
        self.assertEqual(posted.status, Invoice.Status.POSTED)
        self.assertEqual(line.billed_quantity, Decimal("8"))
        self.assertEqual(snapshot.match_status, ThreeWayMatchStatus.PARTIALLY_MATCHED)

    def test_settings_reject_negative_tolerances(self):
        self.setting.purchase_match_quantity_tolerance = Decimal("-0.001")
        with self.assertRaises(ValidationError):
            self.setting.save()
        self.setting.purchase_match_quantity_tolerance = Decimal("0")
        self.setting.purchase_match_price_tolerance = Decimal("-0.01")
        with self.assertRaises(ValidationError):
            self.setting.save()

    def _order_for_sync(self, number, line_count):
        order = PurchaseOrder.objects.create(
            company=self.company,
            number=number,
            vendor=self.vendor,
            warehouse=self.warehouse,
            status=PurchaseOrder.Status.CONFIRMED,
        )
        products = Product.objects.bulk_create(
            [
                Product(
                    company=self.company,
                    sku=f"{number}-{index}",
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
                    quantity=Decimal("1"),
                    unit_price=Decimal("2"),
                )
                for product in products
            ]
        )
        return order

    def test_sync_query_count_does_not_grow_with_line_count(self):
        small = self._order_for_sync("PO-MATCH-Q2", 2)
        large = self._order_for_sync("PO-MATCH-Q12", 12)

        with CaptureQueriesContext(connection) as small_queries:
            sync_purchase_three_way_match({small.pk})
        with CaptureQueriesContext(connection) as large_queries:
            sync_purchase_three_way_match({large.pk})

        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"3-way matching queries grew from {len(small_queries)} to {len(large_queries)}.",
        )
        self.assertEqual(PurchaseThreeWayMatch.objects.filter(order_line__order=large).count(), 12)
