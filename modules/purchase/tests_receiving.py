from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.models import (
    Company,
    Contact,
    Invoice,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    StockMove,
    Warehouse,
)

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrderControl,
    PurchaseOrderInvoiceAccounting,
    PurchaseOrderMilestone,
    PurchaseAccountingDocument,
    PurchaseInventoryTransfer,
    PurchaseInventoryTransferLine,
    PurchaseReturnRequest,
    PurchaseSetting,
    ServiceWorkOrder,
)
from .services import (
    cancel_purchase_order,
    close_purchase_order,
    confirm_purchase_order,
    create_goods_receipts,
    create_purchase_vendor_bill,
    create_purchase_downpayment_bill,
    create_purchase_settlement_bill,
    create_service_work_order_bill,
    inspect_goods_receipt,
    receive_goods_receipt,
    transition_service_work_order_line,
    ready_purchase_inventory_transfer,
    dispatch_purchase_inventory_transfer,
    complete_purchase_inventory_transfer,
)
from .invoice_extension import mark_purchase_vendor_bill_posted
from modules.finance.models import (
    Account,
    AccountJournal,
    AccountingSettings,
    CommercialAccountingSettings,
    JournalLine,
    ProductAccountMapping,
)
from modules.finance.services import cancel_invoice, post_invoice


class PurchaseReceivingParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="purchase-receiving-admin",
            password="test-password",
            email="purchase-receiving@example.com",
        )
        self.company = Company.objects.create(code="PUR-REC", name="Purchase Receiving")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="VEN-REC",
            name="Receiving Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="WH-REC",
            name="Receiving Warehouse",
        )
        self.destination_warehouse = Warehouse.objects.create(
            company=self.company,
            code="WH-DEST",
            name="Destination Warehouse",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="STK-REC",
            name="Stock Product",
            product_type=Product.ProductType.STOCKABLE,
            purchase_price=Decimal("25.00"),
        )

    def order_with_line(self, number="PO-REC-001", quantity=Decimal("10"), *, product=None):
        order = PurchaseOrder.objects.create(
            company=self.company,
            number=number,
            vendor=self.vendor,
            warehouse=self.warehouse,
        )
        line = PurchaseOrderLine.objects.create(
            order=order,
            product=product or self.product,
            quantity=quantity,
            unit_price=Decimal("25.00"),
        )
        return order, line

    def post_receipt(self, receipt):
        inspect_goods_receipt(receipt, self.actor)
        return receive_goods_receipt(receipt, self.actor)

    def test_partial_then_full_receipt_updates_po_and_inventory(self):
        order, order_line = self.order_with_line()
        confirmed = confirm_purchase_order(order, self.actor)

        self.assertEqual(confirmed.status, PurchaseOrder.Status.CONFIRMED)
        self.assertFalse(GoodsReceipt.objects.filter(order=order).exists())

        create_goods_receipts(order, self.actor)
        first_receipt = GoodsReceipt.objects.get(order=order)
        first_line = first_receipt.lines.get()
        first_line.received_quantity = Decimal("4")
        first_line.accepted_quantity = Decimal("4")
        first_line.save(update_fields=["received_quantity", "accepted_quantity", "updated_at"])
        self.post_receipt(first_receipt)

        order.refresh_from_db()
        order_line.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)
        self.assertEqual(order.received_percentage, Decimal("40.00"))
        self.assertEqual(order_line.received_quantity, Decimal("4"))
        self.assertEqual(self.product.current_stock, Decimal("4"))
        self.assertEqual(StockMove.objects.get(reference=first_receipt.number).quantity, Decimal("4"))

        create_goods_receipts(order, self.actor)
        second_receipt = GoodsReceipt.objects.filter(order=order, status=GoodsReceipt.Status.DRAFT).get()
        self.assertEqual(second_receipt.receiving_mode, GoodsReceipt.ReceivingMode.PARTIAL)
        self.assertEqual(second_receipt.lines.get().received_quantity, Decimal("6"))
        self.post_receipt(second_receipt)

        order.refresh_from_db()
        order_line.refresh_from_db()
        self.product.refresh_from_db()
        control = PurchaseOrderControl.objects.get(order=order)
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)
        self.assertEqual(order.received_percentage, Decimal("100.00"))
        self.assertEqual(order_line.received_quantity, Decimal("10"))
        self.assertEqual(self.product.current_stock, Decimal("10"))
        self.assertEqual(control.receiving_status, PurchaseOrderControl.ReceivingStatus.RECEIVED)

    def test_over_receipt_rolls_back_without_stock_side_effect(self):
        order, order_line = self.order_with_line(quantity=Decimal("5"))
        confirm_purchase_order(order, self.actor)
        create_goods_receipts(order, self.actor)
        receipt = GoodsReceipt.objects.get(order=order)
        receipt_line = receipt.lines.get()
        receipt_line.received_quantity = Decimal("6")
        receipt_line.accepted_quantity = Decimal("6")
        receipt_line.save(update_fields=["received_quantity", "accepted_quantity", "updated_at"])
        inspect_goods_receipt(receipt, self.actor)

        with self.assertRaisesMessage(ValidationError, "exceeds the remaining order quantity"):
            receive_goods_receipt(receipt, self.actor)

        receipt.refresh_from_db()
        order_line.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(receipt.status, GoodsReceipt.Status.INSPECTING)
        self.assertEqual(order_line.received_quantity, Decimal("0"))
        self.assertEqual(self.product.current_stock, Decimal("0"))
        self.assertFalse(StockMove.objects.exists())

    def test_auto_receiving_prepares_draft_receipt_without_posting_stock(self):
        PurchaseSetting.objects.create(
            company=self.company,
            receiving_behavior=PurchaseSetting.ReceivingBehavior.AUTO,
        )
        order, _line = self.order_with_line(number="PO-AUTO-001")

        confirm_purchase_order(order, self.actor)

        receipt = GoodsReceipt.objects.get(order=order)
        self.product.refresh_from_db()
        self.assertEqual(receipt.status, GoodsReceipt.Status.DRAFT)
        self.assertEqual(receipt.lines.get().accepted_quantity, Decimal("10"))
        self.assertEqual(self.product.current_stock, Decimal("0"))
        self.assertFalse(StockMove.objects.exists())

    def test_service_only_order_closes_without_goods_receipt(self):
        service = Product.objects.create(
            company=self.company,
            sku="SRV-REC",
            name="Installation Service",
            product_type=Product.ProductType.SERVICE,
        )
        order, _line = self.order_with_line(number="PO-SERVICE-001", product=service)
        PurchaseOrderMilestone.objects.create(
            order=order,
            sequence=10,
            name="Installation complete",
            due_date=order.order_date,
            percentage=Decimal("100"),
        )
        confirm_purchase_order(order, self.actor)
        work_order = ServiceWorkOrder.objects.get(source_order=order)
        transition_service_work_order_line(work_order.lines.get(), "done", self.actor)

        closed = close_purchase_order(order, self.actor)

        self.assertEqual(closed.status, PurchaseOrder.Status.CLOSED)
        self.assertFalse(GoodsReceipt.objects.filter(order=order).exists())

    def test_service_milestones_create_work_orders_and_split_vendor_bills(self):
        service = Product.objects.create(
            company=self.company,
            sku="SRV-SPLIT",
            name="Milestone Service",
            product_type=Product.ProductType.SERVICE,
            purchase_price=Decimal("100"),
        )
        order, order_line = self.order_with_line(
            number="PO-SERVICE-SPLIT",
            quantity=Decimal("4"),
            product=service,
        )
        order_line.unit_price = Decimal("100")
        order_line.save(update_fields=["unit_price", "updated_at"])
        first_milestone = PurchaseOrderMilestone.objects.create(
            order=order,
            sequence=10,
            name="Mobilization",
            due_date=order.order_date,
            percentage=Decimal("25"),
        )
        PurchaseOrderMilestone.objects.create(
            order=order,
            sequence=20,
            name="Completion",
            due_date=order.order_date,
            percentage=Decimal("75"),
        )

        confirm_purchase_order(order, self.actor)

        work_orders = list(ServiceWorkOrder.objects.filter(source_order=order).order_by("sequence"))
        self.assertEqual(len(work_orders), 2)
        self.assertEqual(
            [row.status for row in work_orders],
            [ServiceWorkOrder.Status.IN_PROGRESS, ServiceWorkOrder.Status.IN_PROGRESS],
        )
        self.assertEqual(
            [row.contract_amount for row in work_orders],
            [Decimal("100.00"), Decimal("300.00")],
        )
        self.assertEqual(work_orders[0].lines.get().purchase_order_line, order_line)
        with self.assertRaisesMessage(ValidationError, "linked Service Work Orders"):
            create_purchase_vendor_bill(order, self.actor)

        billed = create_service_work_order_bill(work_orders[0], self.actor)
        invoice = Invoice.objects.get(pk=billed._created_vendor_bill_id)
        self.assertEqual(invoice.total, Decimal("100.00"))
        self.assertEqual(invoice.lines.get().quantity, Decimal("1.000"))
        provenance = invoice.lines.get().purchase_provenance
        self.assertEqual(provenance.service_work_order, work_orders[0])
        invoice.status = Invoice.Status.POSTED
        invoice.save(update_fields=["status", "updated_at"])
        mark_purchase_vendor_bill_posted(invoice=invoice, actor=self.actor)

        work_orders[0].refresh_from_db()
        order_line.refresh_from_db()
        first_milestone.refresh_from_db()
        self.assertTrue(work_orders[0].is_billed)
        self.assertEqual(work_orders[0].progress_paid, Decimal("100.00"))
        self.assertEqual(order_line.billed_quantity, Decimal("1.000"))
        self.assertEqual(first_milestone.status, PurchaseOrderMilestone.Status.COMPLETED)

    def test_service_work_order_sync_query_count_does_not_grow_with_lines(self):
        def service_order(number, line_count):
            order = PurchaseOrder.objects.create(
                company=self.company,
                number=number,
                vendor=self.vendor,
                warehouse=self.warehouse,
            )
            products = Product.objects.bulk_create(
                [
                    Product(
                        company=self.company,
                        sku=f"{number}-P-{index}",
                        name=f"Service {index}",
                        product_type=Product.ProductType.SERVICE,
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
                        unit_price=Decimal("10"),
                    )
                    for product in products
                ]
            )
            PurchaseOrderMilestone.objects.create(
                order=order,
                sequence=10,
                name="Complete",
                due_date=order.order_date,
                percentage=Decimal("100"),
            )
            return order

        small = service_order("PO-SWO-QUERY-ONE", 1)
        many = service_order("PO-SWO-QUERY-MANY", 20)
        with CaptureQueriesContext(connection) as small_queries:
            confirm_purchase_order(small, self.actor)
        with CaptureQueriesContext(connection) as many_queries:
            confirm_purchase_order(many, self.actor)

        self.assertLessEqual(len(many_queries), len(small_queries) + 1)

    def test_ordered_policy_creates_one_draft_bill_and_reserves_pending_quantity(self):
        order, order_line = self.order_with_line(number="PO-BILL-ORDERED", quantity=Decimal("7"))
        confirm_purchase_order(order, self.actor)

        billed_order = create_purchase_vendor_bill(order, self.actor)
        invoice = Invoice.objects.get(pk=billed_order._created_vendor_bill_id)

        self.assertEqual(invoice.invoice_type, Invoice.InvoiceType.VENDOR)
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertEqual(invoice.partner, self.vendor)
        self.assertEqual(invoice.lines.get().quantity, Decimal("7"))
        self.assertEqual(invoice.total, Decimal("175.00"))
        self.assertEqual(invoice.purchase_accounting_documents.get().order, order)
        order_line.refresh_from_db()
        self.assertEqual(order_line.billed_quantity, Decimal("0"))
        with self.assertRaisesMessage(ValidationError, "No ordered quantity remains to bill"):
            create_purchase_vendor_bill(order, self.actor)

    def test_received_policy_bills_only_received_quantity_and_updates_on_post(self):
        order, order_line = self.order_with_line(number="PO-BILL-RECEIVED", quantity=Decimal("10"))
        order.invoicing_policy = PurchaseOrder.InvoicingPolicy.RECEIVED
        order.save(update_fields=["invoicing_policy", "updated_at"])
        confirm_purchase_order(order, self.actor)
        with self.assertRaisesMessage(ValidationError, "No received quantity remains to bill"):
            create_purchase_vendor_bill(order, self.actor)

        create_goods_receipts(order, self.actor)
        receipt = GoodsReceipt.objects.get(order=order)
        receipt_line = receipt.lines.get()
        receipt_line.received_quantity = Decimal("4")
        receipt_line.accepted_quantity = Decimal("4")
        receipt_line.save(update_fields=["received_quantity", "accepted_quantity", "updated_at"])
        self.post_receipt(receipt)
        billed_order = create_purchase_vendor_bill(order, self.actor)
        invoice = Invoice.objects.get(pk=billed_order._created_vendor_bill_id)
        self.assertEqual(invoice.lines.get().quantity, Decimal("4"))

        mark_purchase_vendor_bill_posted(invoice=invoice)

        order_line.refresh_from_db()
        document = PurchaseAccountingDocument.objects.get(invoice=invoice)
        control = PurchaseOrderControl.objects.get(order=order)
        self.assertEqual(order_line.billed_quantity, Decimal("4"))
        self.assertEqual(document.status, "approved")
        self.assertEqual(control.accounting_status, PurchaseOrderControl.AccountingStatus.BILLED)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)

    def test_full_receipt_then_draft_bill_auto_closes_order(self):
        order, _order_line = self.order_with_line(
            number="PO-AUTO-CLOSE-GR-FIRST",
            quantity=Decimal("5"),
        )
        confirm_purchase_order(order, self.actor)
        create_goods_receipts(order, self.actor)
        self.post_receipt(GoodsReceipt.objects.get(order=order))

        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)

        create_purchase_vendor_bill(order, self.actor)

        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CLOSED)

    def test_draft_bill_then_full_receipt_auto_closes_order(self):
        order, _order_line = self.order_with_line(
            number="PO-AUTO-CLOSE-BILL-FIRST",
            quantity=Decimal("5"),
        )
        confirm_purchase_order(order, self.actor)
        create_purchase_vendor_bill(order, self.actor)

        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)

        create_goods_receipts(order, self.actor)
        self.post_receipt(GoodsReceipt.objects.get(order=order))

        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CLOSED)

    def test_cancelled_draft_bill_releases_quantity_for_rebilling(self):
        order, _order_line = self.order_with_line(number="PO-BILL-CANCEL", quantity=Decimal("2"))
        confirm_purchase_order(order, self.actor)
        billed_order = create_purchase_vendor_bill(order, self.actor)
        invoice = Invoice.objects.get(pk=billed_order._created_vendor_bill_id)

        cancel_invoice(invoice, self.actor)
        replacement_order = create_purchase_vendor_bill(order, self.actor)

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.CANCELLED)
        self.assertEqual(invoice.purchase_accounting_documents.get().status, "cancelled")
        self.assertNotEqual(replacement_order._created_vendor_bill_id, invoice.pk)

    def test_purchase_downpayment_and_settlement_post_with_exact_accounts(self):
        payable = Account.objects.create(
            company=self.company,
            code="2100-DP",
            name="Trade Payable",
            account_type=Account.AccountType.LIABILITY,
            allow_reconciliation=True,
        )
        receivable = Account.objects.create(
            company=self.company,
            code="1100-DP",
            name="Trade Receivable",
            account_type=Account.AccountType.ASSET,
            allow_reconciliation=True,
        )
        downpayment_account = Account.objects.create(
            company=self.company,
            code="1300-DP",
            name="Purchase Down Payments",
            account_type=Account.AccountType.ASSET,
        )
        expense = Account.objects.create(
            company=self.company,
            code="5100-DP",
            name="Purchases",
            account_type=Account.AccountType.EXPENSE,
        )
        purchase_journal = AccountJournal.objects.create(
            company=self.company,
            code="PUR-DP",
            name="Purchase Journal",
            journal_type=AccountJournal.JournalType.PURCHASE,
            default_debit_account=expense,
            default_credit_account=payable,
        )
        AccountingSettings.objects.create(
            company=self.company,
            default_receivable_account=receivable,
            default_payable_account=payable,
        )
        CommercialAccountingSettings.objects.create(
            company=self.company,
            sales_journal=purchase_journal,
            purchase_journal=purchase_journal,
            sales_downpayment_account=downpayment_account,
            purchase_downpayment_account=downpayment_account,
            auto_post_sales=False,
            auto_post_purchase=False,
        )
        ProductAccountMapping.objects.create(
            company=self.company,
            product=self.product,
            revenue_account=expense,
            expense_account=expense,
        )
        order, order_line = self.order_with_line(number="PO-DP-001", quantity=Decimal("10"))
        confirm_purchase_order(order, self.actor)
        plan = PurchaseOrderInvoiceAccounting.objects.create(
            order=order,
            downpayment_amount=Decimal("100"),
        )

        downpayment_result = create_purchase_downpayment_bill(order, self.actor)
        downpayment_invoice = Invoice.objects.get(pk=downpayment_result._created_vendor_bill_id)
        post_invoice(downpayment_invoice, self.actor)

        plan.refresh_from_db()
        downpayment_invoice.refresh_from_db()
        downpayment_lines = JournalLine.objects.filter(
            entry__source_reference=downpayment_invoice.number
        ).select_related("account")
        self.assertEqual(plan.status, PurchaseOrderInvoiceAccounting.Status.DOWNPAYMENT_POSTED)
        self.assertEqual(downpayment_invoice.status, Invoice.Status.POSTED)
        self.assertTrue(
            downpayment_lines.filter(account=downpayment_account, debit=Decimal("100")).exists()
        )
        self.assertTrue(downpayment_lines.filter(account=payable, credit=Decimal("100")).exists())
        with self.assertRaisesMessage(ValidationError, "down-payment and settlement"):
            create_purchase_vendor_bill(order, self.actor)

        settlement_result = create_purchase_settlement_bill(order, self.actor)
        settlement_invoice = Invoice.objects.get(pk=settlement_result._created_vendor_bill_id)
        self.assertEqual(settlement_invoice.total, Decimal("150"))
        post_invoice(settlement_invoice, self.actor)

        plan.refresh_from_db()
        order_line.refresh_from_db()
        settlement_lines = JournalLine.objects.filter(
            entry__source_reference=settlement_invoice.number
        ).select_related("account")
        self.assertEqual(plan.status, PurchaseOrderInvoiceAccounting.Status.COMPLETED)
        self.assertEqual(order_line.billed_quantity, Decimal("10"))
        self.assertTrue(settlement_lines.filter(account=expense, debit=Decimal("250")).exists())
        self.assertTrue(
            settlement_lines.filter(account=downpayment_account, credit=Decimal("100")).exists()
        )
        self.assertTrue(settlement_lines.filter(account=payable, credit=Decimal("150")).exists())

        full_order, full_order_line = self.order_with_line(
            number="PO-DP-FULL-001",
            quantity=Decimal("4"),
        )
        confirm_purchase_order(full_order, self.actor)
        full_plan = PurchaseOrderInvoiceAccounting.objects.create(
            order=full_order,
            downpayment_amount=Decimal("100"),
        )
        full_downpayment = create_purchase_downpayment_bill(full_order, self.actor)
        full_downpayment_invoice = Invoice.objects.get(pk=full_downpayment._created_vendor_bill_id)
        post_invoice(full_downpayment_invoice, self.actor)

        full_result = create_purchase_settlement_bill(full_order, self.actor)
        full_settlement_invoice = Invoice.objects.get(pk=full_result._created_vendor_bill_id)
        self.assertEqual(full_settlement_invoice.total, Decimal("0"))
        post_invoice(full_settlement_invoice, self.actor)

        full_plan.refresh_from_db()
        full_order_line.refresh_from_db()
        full_settlement_lines = JournalLine.objects.filter(
            entry__source_type="account_invoice",
            entry__source_reference=full_settlement_invoice.number,
        )
        self.assertEqual(full_plan.status, PurchaseOrderInvoiceAccounting.Status.COMPLETED)
        self.assertEqual(full_plan.settlement_invoice_id, full_settlement_invoice.pk)
        self.assertEqual(full_order_line.billed_quantity, Decimal("4"))
        self.assertTrue(full_settlement_lines.filter(account=expense, debit=Decimal("100")).exists())
        self.assertTrue(
            full_settlement_lines.filter(account=downpayment_account, credit=Decimal("100")).exists()
        )
        self.assertFalse(full_settlement_lines.filter(account=payable).exists())

        multi_order, multi_order_line = self.order_with_line(
            number="PO-DP-MULTI-001",
            quantity=Decimal("10"),
        )
        confirm_purchase_order(multi_order, self.actor)
        multi_plan = PurchaseOrderInvoiceAccounting.objects.create(
            order=multi_order,
            downpayment_amount=Decimal("50"),
        )
        first_result = create_purchase_downpayment_bill(multi_order, self.actor)
        first_invoice = Invoice.objects.get(pk=first_result._created_vendor_bill_id)
        post_invoice(first_invoice, self.actor)
        multi_plan.downpayment_amount = Decimal("75")
        multi_plan.save(update_fields=["downpayment_amount", "updated_at"])
        second_result = create_purchase_downpayment_bill(multi_order, self.actor)
        second_invoice = Invoice.objects.get(pk=second_result._created_vendor_bill_id)
        with self.assertRaisesMessage(ValidationError, "all Purchase Order down-payment bills"):
            create_purchase_settlement_bill(multi_order, self.actor)
        post_invoice(second_invoice, self.actor)

        multi_result = create_purchase_settlement_bill(multi_order, self.actor)
        multi_settlement = Invoice.objects.get(pk=multi_result._created_vendor_bill_id)
        self.assertEqual(multi_settlement.total, Decimal("125"))
        self.assertEqual(
            sorted(multi_settlement.lines.filter(unit_price__lt=0).values_list("unit_price", flat=True)),
            [Decimal("-75"), Decimal("-50")],
        )
        post_invoice(multi_settlement, self.actor)

        multi_plan.refresh_from_db()
        multi_order_line.refresh_from_db()
        self.assertEqual(multi_plan.status, PurchaseOrderInvoiceAccounting.Status.COMPLETED)
        self.assertEqual(multi_order_line.billed_quantity, Decimal("10"))

        percentage_order, _percentage_order_line = self.order_with_line(
            number="PO-DP-PERCENT-001",
            quantity=Decimal("10"),
        )
        confirm_purchase_order(percentage_order, self.actor)
        PurchaseOrderInvoiceAccounting.objects.create(
            order=percentage_order,
            downpayment_type=PurchaseOrderInvoiceAccounting.DownpaymentType.PERCENTAGE,
            downpayment_percentage=Decimal("20"),
        )
        percentage_result = create_purchase_downpayment_bill(percentage_order, self.actor)
        self.assertEqual(
            Invoice.objects.get(pk=percentage_result._created_vendor_bill_id).total,
            Decimal("50"),
        )

        staged_order, staged_order_line = self.order_with_line(
            number="PO-DP-RECEIPT-001",
            quantity=Decimal("10"),
        )
        staged_order.invoicing_policy = PurchaseOrder.InvoicingPolicy.RECEIVED
        staged_order.save(update_fields=["invoicing_policy", "updated_at"])
        confirm_purchase_order(staged_order, self.actor)
        staged_plan = PurchaseOrderInvoiceAccounting.objects.create(
            order=staged_order,
            downpayment_amount=Decimal("100"),
        )
        staged_downpayment = create_purchase_downpayment_bill(staged_order, self.actor)
        post_invoice(Invoice.objects.get(pk=staged_downpayment._created_vendor_bill_id), self.actor)

        create_goods_receipts(staged_order, self.actor)
        first_receipt = GoodsReceipt.objects.get(order=staged_order)
        first_receipt_line = first_receipt.lines.get()
        first_receipt_line.received_quantity = Decimal("4")
        first_receipt_line.accepted_quantity = Decimal("4")
        first_receipt_line.save(
            update_fields=["received_quantity", "accepted_quantity", "updated_at"]
        )
        self.post_receipt(first_receipt)
        first_settlement_result = create_purchase_settlement_bill(staged_order, self.actor)
        first_settlement = Invoice.objects.get(pk=first_settlement_result._created_vendor_bill_id)
        self.assertEqual(first_settlement.total, Decimal("60"))
        self.assertEqual(first_settlement.lines.filter(unit_price__lt=0).get().unit_price, Decimal("-40"))
        post_invoice(first_settlement, self.actor)

        staged_plan.refresh_from_db()
        staged_order_line.refresh_from_db()
        self.assertEqual(staged_plan.status, PurchaseOrderInvoiceAccounting.Status.DOWNPAYMENT_POSTED)
        self.assertEqual(staged_order_line.billed_quantity, Decimal("4"))

        create_goods_receipts(staged_order, self.actor)
        self.post_receipt(
            GoodsReceipt.objects.filter(order=staged_order, status=GoodsReceipt.Status.DRAFT).get()
        )
        second_settlement_result = create_purchase_settlement_bill(staged_order, self.actor)
        second_settlement = Invoice.objects.get(pk=second_settlement_result._created_vendor_bill_id)
        self.assertEqual(second_settlement.total, Decimal("90"))
        self.assertEqual(second_settlement.lines.filter(unit_price__lt=0).get().unit_price, Decimal("-60"))
        post_invoice(second_settlement, self.actor)

        staged_plan.refresh_from_db()
        staged_order_line.refresh_from_db()
        self.assertEqual(staged_plan.status, PurchaseOrderInvoiceAccounting.Status.COMPLETED)
        self.assertEqual(staged_order_line.billed_quantity, Decimal("10"))

    def test_cancelling_received_po_requires_confirmation_and_posts_vendor_return(self):
        order, order_line = self.order_with_line(number="PO-RETURN-001", quantity=Decimal("3"))
        confirm_purchase_order(order, self.actor)
        create_goods_receipts(order, self.actor)
        receipt = GoodsReceipt.objects.get(order=order)
        self.post_receipt(receipt)

        with self.assertRaisesMessage(ValidationError, "Confirm the inventory reversal"):
            cancel_purchase_order(order, self.actor)

        cancelled = cancel_purchase_order(
            order,
            self.actor,
            acknowledge_received_return=True,
        )

        self.product.refresh_from_db()
        order_line.refresh_from_db()
        receipt_line = receipt.lines.get()
        control = PurchaseOrderControl.objects.get(order=order)
        self.assertEqual(cancelled.status, PurchaseOrder.Status.CANCELLED)
        self.assertEqual(self.product.current_stock, Decimal("0"))
        self.assertEqual(order_line.received_quantity, Decimal("0"))
        self.assertEqual(receipt_line.returned_quantity, Decimal("3"))
        self.assertIsNotNone(receipt_line.return_stock_move_id)
        self.assertEqual(control.receiving_status, PurchaseOrderControl.ReceivingStatus.RETURNED)
        self.assertEqual(PurchaseReturnRequest.objects.get(receipt=receipt).status, "approved")
        self.assertEqual(
            StockMove.objects.filter(direction=StockMove.Direction.OUT).get().quantity,
            Decimal("3"),
        )

    def _prepared_receipt(self, number, line_count):
        order = PurchaseOrder.objects.create(
            company=self.company,
            number=number,
            vendor=self.vendor,
            warehouse=self.warehouse,
            status=PurchaseOrder.Status.CONFIRMED,
        )
        products = [
            Product(
                company=self.company,
                sku=f"{number}-P-{index}",
                name=f"{number} Product {index}",
                product_type=Product.ProductType.STOCKABLE,
            )
            for index in range(line_count)
        ]
        Product.objects.bulk_create(products)
        order_lines = [
            PurchaseOrderLine(
                order=order,
                product=product,
                quantity=Decimal("1"),
                unit_price=Decimal("1"),
            )
            for product in products
        ]
        PurchaseOrderLine.objects.bulk_create(order_lines)
        receipt = GoodsReceipt.objects.create(
            company=self.company,
            number=f"GR-{number}",
            order=order,
            warehouse=self.warehouse,
            status=GoodsReceipt.Status.INSPECTING,
        )
        GoodsReceiptLine.objects.bulk_create(
            [
                GoodsReceiptLine(
                    receipt=receipt,
                    order_line=order_line,
                    received_quantity=Decimal("1"),
                    accepted_quantity=Decimal("1"),
                )
                for order_line in order_lines
            ]
        )
        return receipt

    def test_receipt_posting_query_count_does_not_grow_with_lines(self):
        small = self._prepared_receipt("PO-Q-SMALL", 1)
        large = self._prepared_receipt("PO-Q-LARGE", 25)

        with CaptureQueriesContext(connection) as small_queries:
            receive_goods_receipt(small, self.actor)
        with CaptureQueriesContext(connection) as large_queries:
            receive_goods_receipt(large, self.actor)

        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Receipt posting queries grew from {len(small_queries)} to {len(large_queries)}.",
        )

    def _confirmed_order_for_billing(self, number, line_count):
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
                    sku=f"{number}-B-{index}",
                    name=f"{number} Bill Product {index}",
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

    def test_vendor_bill_creation_query_count_does_not_grow_with_lines(self):
        small = self._confirmed_order_for_billing("PO-BQ-SMALL", 1)
        large = self._confirmed_order_for_billing("PO-BQ-LARGE", 25)

        with CaptureQueriesContext(connection) as small_queries:
            create_purchase_vendor_bill(small, self.actor)
        with CaptureQueriesContext(connection) as large_queries:
            create_purchase_vendor_bill(large, self.actor)

        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Vendor-bill creation queries grew from {len(small_queries)} to {len(large_queries)}.",
        )

    def test_internal_transfer_posts_balanced_warehouse_stock_moves(self):
        StockMove.objects.create(
            company=self.company,
            product=self.product,
            warehouse=self.warehouse,
            direction=StockMove.Direction.IN,
            quantity=Decimal("5"),
            reference="OPENING-TRANSFER",
            created_by=self.actor,
        )
        transfer = PurchaseInventoryTransfer.objects.create(
            reference="PIT-001",
            source_warehouse=self.warehouse,
            destination_warehouse=self.destination_warehouse,
        )
        transfer_line = PurchaseInventoryTransferLine.objects.create(
            transfer=transfer,
            product=self.product,
            quantity=Decimal("3"),
        )

        ready_purchase_inventory_transfer(transfer, self.actor)
        dispatch_purchase_inventory_transfer(transfer, self.actor)
        completed = complete_purchase_inventory_transfer(transfer, self.actor)

        self.product.refresh_from_db()
        transfer_line.refresh_from_db()
        self.assertEqual(completed.status, PurchaseInventoryTransfer.Status.DONE)
        self.assertEqual(self.product.current_stock, Decimal("5"))
        self.assertIsNotNone(transfer_line.source_stock_move_id)
        self.assertIsNotNone(transfer_line.destination_stock_move_id)
        self.assertEqual(
            StockMove.objects.get(pk=transfer_line.source_stock_move_id).direction,
            StockMove.Direction.OUT,
        )
        self.assertEqual(
            StockMove.objects.get(pk=transfer_line.destination_stock_move_id).warehouse,
            self.destination_warehouse,
        )

    def test_internal_transfer_insufficient_source_stock_rolls_back(self):
        transfer = PurchaseInventoryTransfer.objects.create(
            reference="PIT-NO-STOCK",
            source_warehouse=self.warehouse,
            destination_warehouse=self.destination_warehouse,
            status=PurchaseInventoryTransfer.Status.IN_TRANSIT,
        )
        PurchaseInventoryTransferLine.objects.create(
            transfer=transfer,
            product=self.product,
            quantity=Decimal("1"),
        )

        with self.assertRaisesMessage(ValidationError, "Insufficient stock"):
            complete_purchase_inventory_transfer(transfer, self.actor)

        transfer.refresh_from_db()
        self.assertEqual(transfer.status, PurchaseInventoryTransfer.Status.IN_TRANSIT)
        self.assertFalse(StockMove.objects.filter(reference=transfer.reference).exists())

    def _prepared_inventory_transfer(self, reference, line_count):
        products = Product.objects.bulk_create(
            [
                Product(
                    company=self.company,
                    sku=f"{reference}-P-{index}",
                    name=f"{reference} Product {index}",
                )
                for index in range(line_count)
            ]
        )
        for product in products:
            StockMove.objects.create(
                company=self.company,
                product=product,
                warehouse=self.warehouse,
                direction=StockMove.Direction.IN,
                quantity=Decimal("2"),
                reference=f"OPEN-{reference}",
                created_by=self.actor,
            )
        transfer = PurchaseInventoryTransfer.objects.create(
            reference=reference,
            source_warehouse=self.warehouse,
            destination_warehouse=self.destination_warehouse,
            status=PurchaseInventoryTransfer.Status.IN_TRANSIT,
        )
        PurchaseInventoryTransferLine.objects.bulk_create(
            [
                PurchaseInventoryTransferLine(
                    transfer=transfer,
                    product=product,
                    quantity=Decimal("1"),
                )
                for product in products
            ]
        )
        return transfer

    def test_inventory_transfer_query_count_does_not_grow_with_lines(self):
        small = self._prepared_inventory_transfer("PIT-Q-SMALL", 1)
        large = self._prepared_inventory_transfer("PIT-Q-LARGE", 25)

        with CaptureQueriesContext(connection) as small_queries:
            complete_purchase_inventory_transfer(small, self.actor)
        with CaptureQueriesContext(connection) as large_queries:
            complete_purchase_inventory_transfer(large, self.actor)

        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Inventory-transfer queries grew from {len(small_queries)} to {len(large_queries)}.",
        )


class PurchaseReceivingSmartActionTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="purchase-smart-admin",
            password="test-password",
            email="purchase-smart@example.com",
        )
        company = Company.objects.create(code="PUR-SMART", name="Purchase Smart Actions")
        vendor = Contact.objects.create(
            company=company,
            code="VEN-SMART",
            name="Smart Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        warehouse = Warehouse.objects.create(company=company, code="WH-SMART", name="Smart Warehouse")
        product = Product.objects.create(company=company, sku="P-SMART", name="Smart Product")
        self.company = company
        self.warehouse = warehouse
        self.product = product
        self.order = PurchaseOrder.objects.create(
            company=company,
            number="PO-SMART-001",
            vendor=vendor,
            warehouse=warehouse,
            status=PurchaseOrder.Status.CONFIRMED,
        )
        line = PurchaseOrderLine.objects.create(order=self.order, product=product, quantity=Decimal("1"))
        self.receipt = GoodsReceipt.objects.create(
            company=company,
            number="GR-SMART-001",
            order=self.order,
            warehouse=warehouse,
        )
        GoodsReceiptLine.objects.create(
            receipt=self.receipt,
            order_line=line,
            received_quantity=Decimal("1"),
            accepted_quantity=Decimal("1"),
        )
        self.client.force_login(self.actor)

    def test_po_and_receipt_expose_bidirectional_smart_actions(self):
        order_response = self.client.get(f"/app/purchases/{self.order.pk}/edit/")
        receipt_response = self.client.get(f"/app/goods-receipts/{self.receipt.pk}/edit/")

        self.assertContains(order_response, "Goods Receipts")
        self.assertContains(
            order_response,
            f"smart_action=purchases.goods-receipts&amp;source_id={self.order.pk}",
        )
        self.assertContains(receipt_response, "Purchase Order")
        self.assertContains(
            receipt_response,
            f"smart_action=goods-receipts.purchase-order&amp;source_id={self.receipt.pk}",
        )

    def test_po_lines_have_menu_smart_action_and_exact_source_breadcrumb(self):
        order_response = self.client.get(f"/app/purchases/{self.order.pk}/edit/")
        self.assertContains(order_response, "Order Lines")
        self.assertContains(
            order_response,
            f"smart_action=purchases.order-lines&amp;source_id={self.order.pk}",
        )

        line_list = self.client.get(
            f"/app/purchase-order-lines/?smart_action=purchases.order-lines&source_id={self.order.pk}"
        )
        self.assertEqual(line_list.status_code, 200)
        self.assertEqual([row["object"].pk for row in line_list.context["rows"]], [self.order.lines.get().pk])
        self.assertContains(line_list, f"/app/purchases/{self.order.pk}/edit/")

        line_response = self.client.get(f"/app/purchase-order-lines/{self.order.lines.get().pk}/edit/")
        self.assertContains(
            line_response,
            f"smart_action=purchase-order-lines.purchase-order&amp;source_id={self.order.lines.get().pk}",
        )

    def test_vendor_bill_exposes_purchase_order_smart_action(self):
        billed_order = create_purchase_vendor_bill(self.order, self.actor)
        invoice = Invoice.objects.get(pk=billed_order._created_vendor_bill_id)

        order_response = self.client.get(f"/app/purchases/{self.order.pk}/edit/")
        invoice_response = self.client.get(f"/app/invoices/{invoice.pk}/edit/")

        self.assertContains(order_response, "Vendor Bills")
        self.assertContains(
            order_response,
            f"smart_action=purchases.vendor-bills&amp;source_id={self.order.pk}",
        )
        self.assertContains(invoice_response, "Purchase Orders")
        self.assertContains(
            invoice_response,
            f"smart_action=invoices.purchase-orders&amp;source_id={invoice.pk}",
        )

    def test_inventory_transfer_and_stock_moves_are_bidirectionally_linked(self):
        inspect_goods_receipt(self.receipt, self.actor)
        receive_goods_receipt(self.receipt, self.actor)
        destination = Warehouse.objects.create(
            company=self.company,
            code="WH-SMART-DEST",
            name="Smart Destination",
        )
        transfer = PurchaseInventoryTransfer.objects.create(
            receipt=self.receipt,
            reference="PIT-SMART-001",
            source_warehouse=self.warehouse,
            destination_warehouse=destination,
            status=PurchaseInventoryTransfer.Status.IN_TRANSIT,
        )
        transfer_line = PurchaseInventoryTransferLine.objects.create(
            transfer=transfer,
            receipt_line=self.receipt.lines.get(),
            product=self.product,
            quantity=Decimal("1"),
        )
        complete_purchase_inventory_transfer(transfer, self.actor)
        transfer_line.refresh_from_db()

        transfer_response = self.client.get(f"/app/purchase-inventory-transfers/{transfer.pk}/edit/")
        stock_response = self.client.get(f"/app/stock-moves/{transfer_line.source_stock_move_id}/edit/")

        self.assertContains(transfer_response, "Goods Receipt")
        self.assertContains(transfer_response, "Stock Moves")
        self.assertContains(stock_response, "Inventory Transfer")

    def test_smart_action_breadcrumb_returns_to_exact_source_record(self):
        response = self.client.get(
            f"/app/goods-receipts/?smart_action=purchases.goods-receipts&source_id={self.order.pk}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["page"].object_list), [self.receipt])
        self.assertContains(response, f"/app/purchases/{self.order.pk}/edit/")
