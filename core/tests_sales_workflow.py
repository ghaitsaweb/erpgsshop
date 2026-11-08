from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from modules.approval_workflow.models import ApprovalInstance

from modules.finance.models import (
    AccountJournal,
    AccountPayment,
    PaymentAllocation,
    SaleOrderInvoiceAccounting,
    SalesInvoiceAccounting,
    SalesInvoiceLineProvenance,
)

from .models import (
    Company,
    CompanySaleSetting,
    Contact,
    Invoice,
    InvoiceLine,
    Product,
    SaleOrder,
    SaleOrderLine,
    SaleOrderType,
    SaleOrderTypeSequence,
    SaleQuotationEmail,
)
from .sales import (
    can_sale_workflow_action,
    expire_due_sales_quotations,
    merge_sale_quotations,
    recalculate_sale_order_totals,
    send_sale_quotation_email,
    transition_sale_order,
)


class SalesWorkflowParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="sales-workflow-admin",
            password="test-password",
            email="sales-workflow@example.com",
        )
        self.company = Company.objects.create(code="SALE-WF", name="Sales Workflow")
        self.customer = Contact.objects.create(
            company=self.company,
            code="CUS-WF",
            name="Workflow Customer",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="PRD-WF",
            name="Workflow Product",
            sale_price=Decimal("100.00"),
        )

    def order_with_lines(self, number, count=1, *, status=SaleOrder.Status.DRAFT):
        order = SaleOrder.objects.create(
            company=self.company,
            number=number,
            customer=self.customer,
            status=status,
        )
        SaleOrderLine.objects.bulk_create(
            [
                SaleOrderLine(
                    order=order,
                    product=self.product,
                    quantity=1,
                    unit_price=Decimal("100.00"),
                )
                for _index in range(count)
            ]
        )
        recalculate_sale_order_totals(order)
        order.refresh_from_db()
        return order

    def test_hmx_quotation_submit_and_approve_sequence(self):
        order = self.order_with_lines("SQ-WF-001")

        submitted = transition_sale_order(order, "submit", self.actor)

        self.assertEqual(submitted.status, SaleOrder.Status.TO_APPROVE)
        approval = ApprovalInstance.objects.get(object_id=order.pk)
        self.assertEqual(approval.status, ApprovalInstance.Status.PENDING)

        approved = transition_sale_order(submitted, "approve", self.actor)

        approval.refresh_from_db()
        self.assertEqual(approved.status, SaleOrder.Status.SALE)
        self.assertEqual(approved.quotation_reference, "SQ-WF-001")
        self.assertIsNotNone(approved.confirmation_date)
        self.assertEqual(approval.status, ApprovalInstance.Status.APPROVED)

    def test_submit_validation_and_rejection_reason_roll_back_safely(self):
        empty = SaleOrder.objects.create(
            company=self.company,
            number="SQ-WF-EMPTY",
            customer=self.customer,
        )
        with self.assertRaisesMessage(
            ValidationError,
            "At least one Order Line is required",
        ):
            transition_sale_order(empty, "submit", self.actor)
        empty.refresh_from_db()
        self.assertEqual(empty.status, SaleOrder.Status.DRAFT)
        self.assertFalse(ApprovalInstance.objects.filter(object_id=empty.pk).exists())

        order = self.order_with_lines("SQ-WF-REJECT")
        transition_sale_order(order, "submit", self.actor)
        rejected = transition_sale_order(
            order,
            "reject",
            self.actor,
            reason="Customer credit terms are incomplete.",
        )
        self.assertEqual(rejected.status, SaleOrder.Status.REJECTED)
        self.assertEqual(rejected.reject_reason, "Customer credit terms are incomplete.")

    def test_cancel_revision_and_close_keep_hmx_audit(self):
        order = self.order_with_lines("SO-WF-REV", status=SaleOrder.Status.SALE)
        cancelled = transition_sale_order(
            order,
            "cancel",
            self.actor,
            reason="Customer requested a revised quotation.",
        )
        revised = transition_sale_order(cancelled, "revise", self.actor)

        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, SaleOrder.Status.REVISED)
        self.assertEqual(cancelled.cancel_reason, "Customer requested a revised quotation.")
        self.assertEqual(revised.status, SaleOrder.Status.DRAFT)
        self.assertEqual(revised.previous_revision, cancelled)
        self.assertEqual(revised.revision_root, cancelled)
        self.assertEqual(revised.number, "SO-WF-REV-R01")
        self.assertEqual(revised.lines.count(), 1)

        service_product = Product.objects.create(
            company=self.company,
            sku="SRV-WF",
            name="Workflow Service",
            product_type=Product.ProductType.SERVICE,
        )
        close_order = SaleOrder.objects.create(
            company=self.company,
            number="SO-WF-CLOSE",
            customer=self.customer,
            status=SaleOrder.Status.SALE,
        )
        SaleOrderLine.objects.create(
            order=close_order,
            product=service_product,
            quantity=1,
            unit_price=Decimal("50.00"),
        )
        closed = transition_sale_order(
            close_order,
            "close",
            self.actor,
            reason=SaleOrder.CloseReason.COMPLETED,
            note="Service delivered and billing reviewed.",
        )
        self.assertEqual(closed.status, SaleOrder.Status.CLOSED)
        self.assertEqual(closed.close_reason, SaleOrder.CloseReason.COMPLETED)
        self.assertEqual(closed.close_note, "Service delivered and billing reviewed.")

    def test_close_requires_delivery_acknowledgement_and_full_billing(self):
        order = self.order_with_lines("SO-WF-GUARD", status=SaleOrder.Status.SALE)
        line = order.lines.get()
        line.delivered_quantity = Decimal("1")
        line.save(update_fields=["delivered_quantity", "updated_at"])

        with self.assertRaisesMessage(ValidationError, "fully invoiced"):
            transition_sale_order(
                order,
                "close",
                self.actor,
                reason=SaleOrder.CloseReason.PARTIAL_RELEASE,
                acknowledge_incomplete=True,
            )

        line.delivered_quantity = Decimal("0")
        line.save(update_fields=["delivered_quantity", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "Acknowledge"):
            transition_sale_order(
                order,
                "close",
                self.actor,
                reason=SaleOrder.CloseReason.PARTIAL_RELEASE,
            )

        closed = transition_sale_order(
            order,
            "close",
            self.actor,
            reason=SaleOrder.CloseReason.PARTIAL_RELEASE,
            acknowledge_incomplete=True,
        )
        self.assertEqual(closed.status, SaleOrder.Status.CLOSED)

    def test_expiry_is_bounded_and_submit_query_count_does_not_grow(self):
        expired_date = timezone.localdate() - timedelta(days=1)
        for index in range(3):
            SaleOrder.objects.create(
                company=self.company,
                number=f"SQ-WF-EXP-{index}",
                customer=self.customer,
                validity_date=expired_date,
            )

        self.assertEqual(expire_due_sales_quotations(batch_size=2), 2)
        self.assertEqual(
            SaleOrder.objects.filter(status=SaleOrder.Status.EXPIRED).count(),
            2,
        )
        self.assertEqual(expire_due_sales_quotations(batch_size=2), 1)

        transition_sale_order(self.order_with_lines("SQ-WF-WARM"), "submit", self.actor)
        small = self.order_with_lines("SQ-WF-SMALL", 2)
        with CaptureQueriesContext(connection) as small_context:
            transition_sale_order(small, "submit", self.actor)
        large = self.order_with_lines("SQ-WF-LARGE", 40)
        with CaptureQueriesContext(connection) as large_context:
            transition_sale_order(large, "submit", self.actor)

        self.assertEqual(len(small_context), len(large_context))

    def test_expiry_includes_pending_approval_and_reset_uses_hmx_default(self):
        order = self.order_with_lines("SQ-WF-EXP-PENDING")
        transition_sale_order(order, "submit", self.actor)
        SaleOrder.objects.filter(pk=order.pk).update(
            validity_date=timezone.localdate() - timedelta(days=1)
        )

        self.assertEqual(expire_due_sales_quotations(), 1)
        order.refresh_from_db()
        self.assertEqual(order.status, SaleOrder.Status.EXPIRED)

        reset = transition_sale_order(order, "reset-draft", self.actor)
        self.assertEqual(reset.status, SaleOrder.Status.DRAFT)
        self.assertEqual(reset.validity_date, timezone.localdate() + timedelta(days=7))

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="sales@example.com",
    )
    def test_send_by_email_attaches_pdf_and_marks_quotation_sent(self):
        order = self.order_with_lines("SQ-WF-EMAIL")

        attempt = send_sale_quotation_email(
            order,
            self.actor,
            email_to="customer@example.com",
            email_cc="finance@example.com, manager@example.com",
            subject="Quotation SQ-WF-EMAIL for Workflow Customer",
            body_html="<p>Please review the attached quotation.</p>",
        )

        order.refresh_from_db()
        self.assertEqual(order.status, SaleOrder.Status.SENT)
        self.assertEqual(attempt.status, SaleQuotationEmail.Status.SENT)
        self.assertIsNotNone(attempt.sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["customer@example.com"])
        self.assertEqual(
            mail.outbox[0].cc,
            ["finance@example.com", "manager@example.com"],
        )
        pdf_attachments = [
            attachment
            for attachment in mail.outbox[0].attachments
            if attachment.mimetype == "application/pdf"
        ]
        self.assertEqual(len(pdf_attachments), 1)
        self.assertTrue(pdf_attachments[0].content.startswith(b"%PDF"))

    def test_send_by_email_failure_is_audited_without_state_change(self):
        order = self.order_with_lines("SQ-WF-EMAIL-FAIL")

        with patch(
            "core.sales.EmailMultiAlternatives.send",
            side_effect=OSError("SMTP unavailable"),
        ):
            attempt = send_sale_quotation_email(
                order,
                self.actor,
                email_to="customer@example.com",
                subject="Quotation delivery",
                body_html="Please review the quotation.",
            )

        order.refresh_from_db()
        self.assertEqual(order.status, SaleOrder.Status.DRAFT)
        self.assertEqual(attempt.status, SaleQuotationEmail.Status.FAILED)
        self.assertIn("SMTP unavailable", attempt.failure_reason)

    def test_send_by_email_rejects_invalid_recipient_before_audit(self):
        order = self.order_with_lines("SQ-WF-EMAIL-INVALID")

        with self.assertRaisesMessage(ValidationError, "Invalid To email address"):
            send_sale_quotation_email(
                order,
                self.actor,
                email_to="not-an-email",
                subject="Quotation delivery",
                body_html="Please review the quotation.",
            )

        order.refresh_from_db()
        self.assertEqual(order.status, SaleOrder.Status.DRAFT)
        self.assertFalse(SaleQuotationEmail.objects.filter(order=order).exists())

    def test_company_sales_settings_control_expiry_and_revision(self):
        CompanySaleSetting.objects.create(
            company=self.company,
            quotation_expiry_days=14,
            default_delivery_date=3,
            allow_order_revision=False,
        )
        source = self.order_with_lines("SQ-WF-SETTING")

        duplicate = transition_sale_order(source, "duplicate", self.actor)

        self.assertEqual(
            duplicate.validity_date,
            timezone.localdate() + timedelta(days=14),
        )
        source.status = SaleOrder.Status.CANCELLED
        source.save(update_fields=["status", "updated_at"])
        self.assertFalse(can_sale_workflow_action(self.actor, "revise", source))
        with self.assertRaisesMessage(ValidationError, "disabled in Sales Settings"):
            transition_sale_order(source, "revise", self.actor)

        expired = self.order_with_lines(
            "SQ-WF-SETTING-RESET",
            status=SaleOrder.Status.EXPIRED,
        )
        reset = transition_sale_order(expired, "reset-draft", self.actor)
        self.assertEqual(
            reset.validity_date,
            timezone.localdate() + timedelta(days=14),
        )

    def test_company_sales_setting_rejects_non_positive_expiry(self):
        setting = CompanySaleSetting(
            company=self.company,
            quotation_expiry_days=0,
        )

        with self.assertRaises(ValidationError):
            setting.full_clean()

    def test_order_type_allocates_quotation_and_sale_sequences(self):
        order_type = SaleOrderType.objects.create(
            name="Direct Sale",
            description="Counter sale",
        )
        order_type.companies.add(self.company)
        order = SaleOrder.objects.create(
            company=self.company,
            customer=self.customer,
            order_type=order_type,
        )
        SaleOrderLine.objects.create(
            order=order,
            product=self.product,
            quantity=1,
            unit_price=Decimal("100"),
        )
        recalculate_sale_order_totals(order)
        quotation_number = order.number

        self.assertTrue(
            quotation_number.startswith(f"SQ/{order_type.prefix_sequence}/{self.company.code}/")
        )
        transition_sale_order(order, "submit", self.actor)
        confirmed = transition_sale_order(order, "approve", self.actor)

        self.assertEqual(confirmed.quotation_reference, quotation_number)
        self.assertTrue(
            confirmed.number.startswith(f"SO/{order_type.prefix_sequence}/{self.company.code}/")
        )
        sequence = SaleOrderTypeSequence.objects.get(
            order_type=order_type,
            company=self.company,
        )
        self.assertEqual(sequence.quotation_next, 2)
        self.assertEqual(sequence.order_next, 2)

    def test_order_type_prefix_lookup_is_bounded_and_query_count_does_not_grow(self):
        SaleOrderType.objects.bulk_create(
            [
                SaleOrderType(name="Existing 1", prefix_sequence="BPT"),
                SaleOrderType(name="Existing 2", prefix_sequence="BPT2"),
            ]
        )
        probe = SaleOrderType(name="Bulk Prefix Type")
        with CaptureQueriesContext(connection) as small_context:
            small_candidate = probe._generate_prefix()

        SaleOrderType.objects.bulk_create(
            [
                SaleOrderType(name=f"Existing {index}", prefix_sequence=f"BPT{index}")
                for index in range(3, 43)
            ]
        )
        with CaptureQueriesContext(connection) as large_context:
            large_candidate = probe._generate_prefix()

        self.assertEqual(small_candidate, "BPT3")
        self.assertEqual(large_candidate, "BPT43")
        self.assertEqual(len(small_context), len(large_context))

    def test_used_order_type_blocks_company_change_and_delete(self):
        order_type = SaleOrderType.objects.create(name="Protected Type")
        order_type.companies.add(self.company)
        order = self.order_with_lines("SQ-WF-TYPE-PROTECTED")
        order.order_type = order_type
        order.save(update_fields=["order_type", "updated_at"])

        with self.assertRaisesMessage(ValidationError, "Companies field cannot be modified"):
            with transaction.atomic():
                order_type.companies.clear()
        with self.assertRaisesMessage(ValidationError, "already used in Sale Order"):
            order_type.delete()

    def test_new_sequence_revision_rule_uses_order_type_quotation_sequence(self):
        order_type = SaleOrderType.objects.create(name="Revision Sequence")
        order_type.companies.add(self.company)
        setting = CompanySaleSetting.objects.create(
            company=self.company,
            revised_sequence_rule=CompanySaleSetting.RevisionSequenceRule.NEW_SEQUENCE,
        )
        source = self.order_with_lines("SO-WF-NEW-REV", status=SaleOrder.Status.CANCELLED)
        source.order_type = order_type
        source.save(update_fields=["order_type", "updated_at"])

        revision = transition_sale_order(source, "revise", self.actor)

        self.assertEqual(setting.revised_sequence_rule, "new_sequence")
        self.assertTrue(
            revision.number.startswith(f"SQ/{order_type.prefix_sequence}/{self.company.code}/")
        )
        self.assertEqual(revision.previous_revision, source)

    def test_invoice_smart_action_is_counted_and_scoped_to_current_order(self):
        order = self.order_with_lines("SO-WF-SMART-INVOICE", status=SaleOrder.Status.SALE)
        other_order = self.order_with_lines(
            "SO-WF-SMART-INVOICE-OTHER",
            status=SaleOrder.Status.SALE,
        )
        self.client.force_login(self.actor)

        empty_response = self.client.get(f"/app/sales/{order.pk}/edit/")
        self.assertNotContains(
            empty_response,
            f"smart_action=sales.invoices&amp;source_id={order.pk}",
        )

        link = SaleOrderInvoiceAccounting.objects.create(order=order)
        invoice = Invoice.objects.create(
            company=self.company,
            number="INV-WF-SMART",
            partner=self.customer,
            due_date=timezone.localdate(),
        )
        link.settlement_invoice = invoice
        link.save(update_fields=["settlement_invoice", "updated_at"])
        SalesInvoiceAccounting.objects.create(
            invoice=invoice,
            order_accounting=link,
            invoice_kind=SalesInvoiceAccounting.InvoiceKind.SETTLEMENT,
        )

        other_link = SaleOrderInvoiceAccounting.objects.create(order=other_order)
        other_invoice = Invoice.objects.create(
            company=self.company,
            number="INV-WF-SMART-OTHER",
            partner=self.customer,
            due_date=timezone.localdate(),
        )
        other_link.settlement_invoice = other_invoice
        other_link.save(update_fields=["settlement_invoice", "updated_at"])
        SalesInvoiceAccounting.objects.create(
            invoice=other_invoice,
            order_accounting=other_link,
            invoice_kind=SalesInvoiceAccounting.InvoiceKind.SETTLEMENT,
        )

        detail_response = self.client.get(f"/app/sales/{order.pk}/edit/")
        self.assertContains(detail_response, 'class="smart-button-box"')
        self.assertContains(detail_response, 'data-smart-action="sales.invoices"')
        self.assertContains(
            detail_response,
            f"/app/invoices/?smart_action=sales.invoices&amp;source_id={order.pk}",
        )
        self.assertContains(detail_response, "Invoices")

        invoice_response = self.client.get(
            f"/app/invoices/?smart_action=sales.invoices&source_id={order.pk}"
        )
        self.assertContains(invoice_response, invoice.number)
        self.assertNotContains(invoice_response, other_invoice.number)
        self.assertFalse(invoice_response.context["can_add"])
        self.assertFalse(invoice_response.context["can_delete"])
        self.assertEqual(invoice_response.context["smart_action_scope"]["source"], order)
        self.assertEqual(
            [crumb["label"] for crumb in invoice_response.context["breadcrumbs"]],
            ["Home", "Sales Orders", order.number, "Invoices"],
        )
        self.assertEqual(
            invoice_response.context["breadcrumbs"][2]["url"],
            f"/app/sales/{order.pk}/edit/",
        )

        mismatch_response = self.client.get(
            f"/app/sales/?smart_action=sales.invoices&source_id={order.pk}"
        )
        self.assertEqual(mismatch_response.status_code, 404)

    def test_revised_documents_smart_action_uses_the_revision_root(self):
        root = self.order_with_lines("SO-WF-SMART-REV", status=SaleOrder.Status.CANCELLED)
        revision = transition_sale_order(root, "revise", self.actor)
        self.client.force_login(self.actor)

        root_response = self.client.get(f"/app/sales/{root.pk}/edit/")
        self.assertContains(root_response, "Revised Documents")
        self.assertContains(
            root_response,
            f"/app/sales/?smart_action=sales.revised-documents&amp;source_id={root.pk}",
        )

        revision_response = self.client.get(f"/app/sales/{revision.pk}/edit/")
        self.assertContains(revision_response, "Revised Documents")

        list_response = self.client.get(
            f"/app/sales/?smart_action=sales.revised-documents&source_id={revision.pk}"
        )
        self.assertContains(list_response, revision.number)
        self.assertEqual(
            [row["object"].pk for row in list_response.context["rows"]],
            [revision.pk],
        )
        self.assertFalse(list_response.context["can_add"])
        self.assertFalse(list_response.context["can_delete"])

    def test_order_type_products_smart_button_matches_hmx_read_only_action(self):
        order_type = SaleOrderType.objects.create(name="Retail Products")
        self.client.force_login(self.actor)

        detail_response = self.client.get(
            f"/app/sales-order-types/{order_type.pk}/edit/"
        )
        self.assertContains(detail_response, 'class="smart-button-box"')
        self.assertContains(
            detail_response,
            'data-smart-action="sales-order-types.products"',
        )
        self.assertContains(detail_response, "List of Products")

        product_response = self.client.get(
            "/app/products/"
            f"?smart_action=sales-order-types.products&source_id={order_type.pk}"
        )
        self.assertContains(product_response, self.product.sku)
        self.assertFalse(product_response.context["can_add"])
        self.assertFalse(product_response.context["can_change"])
        self.assertFalse(product_response.context["can_delete"])

    def test_invoice_payment_reconciliation_trace_and_bidirectional_smart_buttons(self):
        journal = AccountJournal.objects.create(
            company=self.company,
            code="BNK-WF",
            name="Workflow Bank",
            journal_type=AccountJournal.JournalType.BANK,
        )
        invoice = Invoice.objects.create(
            company=self.company,
            number="INV-WF-PAYMENT-TRACE",
            partner=self.customer,
            due_date=timezone.localdate(),
            status=Invoice.Status.POSTED,
            total=Decimal("100.00"),
            paid_amount=Decimal("40.00"),
        )
        payment = AccountPayment.objects.create(
            company=self.company,
            number="RCPT-WF-TRACE",
            partner=self.customer,
            journal=journal,
            direction=AccountPayment.Direction.RECEIVE,
            amount=Decimal("40.00"),
            status=AccountPayment.Status.RECONCILED,
        )
        PaymentAllocation.objects.create(
            payment=payment,
            invoice=invoice,
            amount=Decimal("40.00"),
        )
        self.client.force_login(self.actor)

        invoice_response = self.client.get(f"/app/invoices/{invoice.pk}/edit/")
        self.assertContains(invoice_response, "Payment reconciliation")
        self.assertContains(invoice_response, "Partial")
        self.assertContains(invoice_response, payment.number)
        self.assertContains(invoice_response, f"/app/invoices/{invoice.pk}/add-payment/")
        self.assertContains(invoice_response, 'data-smart-action="invoices.payments"')

        payment_list = self.client.get(
            "/app/account-payments/"
            f"?smart_action=invoices.payments&source_id={invoice.pk}"
        )
        self.assertEqual(
            [row["object"].pk for row in payment_list.context["rows"]],
            [payment.pk],
        )
        self.assertEqual(
            [crumb["label"] for crumb in payment_list.context["breadcrumbs"]],
            ["Home", "Invoices & Bills", invoice.number, "Payments"],
        )

        payment_response = self.client.get(
            f"/app/account-payments/{payment.pk}/edit/"
        )
        self.assertContains(
            payment_response,
            'data-smart-action="account-payments.invoices"',
        )
        invoice_list = self.client.get(
            "/app/invoices/"
            f"?smart_action=account-payments.invoices&source_id={payment.pk}"
        )
        self.assertEqual(
            [row["object"].pk for row in invoice_list.context["rows"]],
            [invoice.pk],
        )

    def test_paid_fully_invoiced_service_order_auto_closes(self):
        service_product = Product.objects.create(
            company=self.company,
            sku="SRV-WF-AUTO-CLOSE",
            name="Auto Close Service",
            product_type=Product.ProductType.SERVICE,
        )
        order = SaleOrder.objects.create(
            company=self.company,
            number="SO-WF-AUTO-CLOSE",
            customer=self.customer,
            status=SaleOrder.Status.SALE,
        )
        order_line = SaleOrderLine.objects.create(
            order=order,
            product=service_product,
            quantity=Decimal("2"),
            unit_price=Decimal("100"),
        )
        invoice = Invoice.objects.create(
            company=self.company,
            number="INV-WF-AUTO-CLOSE",
            partner=self.customer,
            due_date=timezone.localdate(),
            status=Invoice.Status.POSTED,
            total=Decimal("200"),
        )
        link = SaleOrderInvoiceAccounting.objects.create(
            order=order,
            settlement_invoice=invoice,
        )
        sales_invoice = SalesInvoiceAccounting.objects.create(
            invoice=invoice,
            order_accounting=link,
            invoice_kind=SalesInvoiceAccounting.InvoiceKind.SETTLEMENT,
        )
        invoice_line = InvoiceLine.objects.create(
            invoice=invoice,
            product=service_product,
            description=service_product.name,
            quantity=Decimal("2"),
            unit_price=Decimal("100"),
        )
        SalesInvoiceLineProvenance.objects.create(
            invoice_line=invoice_line,
            sales_invoice=sales_invoice,
            sale_order_line=order_line,
        )

        invoice.paid_amount = Decimal("199")
        invoice.save(update_fields=["paid_amount", "updated_at"])
        order.refresh_from_db()
        self.assertEqual(order.status, SaleOrder.Status.SALE)

        invoice.paid_amount = Decimal("200")
        invoice.save(update_fields=["paid_amount", "updated_at"])
        order.refresh_from_db()
        order_line.refresh_from_db()
        self.assertEqual(order.status, SaleOrder.Status.CLOSED)
        self.assertEqual(order_line.invoiced_quantity, Decimal("2"))

    def test_duplicate_resets_transactional_state_without_changing_source(self):
        source = self.order_with_lines("SO-WF-DUP", status=SaleOrder.Status.SALE)
        source_line = source.lines.get()
        source_line.delivered_quantity = Decimal("1")
        source_line.invoiced_quantity = Decimal("1")
        source_line.save(
            update_fields=["delivered_quantity", "invoiced_quantity", "updated_at"]
        )

        duplicate = transition_sale_order(source, "duplicate", self.actor)

        source.refresh_from_db()
        duplicate_line = duplicate.lines.get()
        self.assertEqual(source.status, SaleOrder.Status.SALE)
        self.assertEqual(duplicate.status, SaleOrder.Status.DRAFT)
        self.assertNotEqual(duplicate.number, source.number)
        self.assertEqual(duplicate_line.quantity, source_line.quantity)
        self.assertEqual(duplicate_line.delivered_quantity, Decimal("0"))
        self.assertEqual(duplicate_line.invoiced_quantity, Decimal("0"))
        self.assertIsNone(duplicate.confirmation_date)
        self.assertFalse(hasattr(duplicate, "invoice_accounting"))

    def test_merge_quotations_aggregates_matching_lines_and_cancels_sources(self):
        first = self.order_with_lines("SQ-WF-MERGE-1")
        second = self.order_with_lines("SQ-WF-MERGE-2")
        second_line = second.lines.get()
        second_line.quantity = Decimal("3")
        second_line.save(update_fields=["quantity", "updated_at"])
        recalculate_sale_order_totals(second)

        merged = merge_sale_quotations([first.pk, second.pk], self.actor)

        first.refresh_from_db()
        second.refresh_from_db()
        merged_line = merged.lines.get()
        self.assertEqual(merged.status, SaleOrder.Status.DRAFT)
        self.assertEqual(merged_line.quantity, Decimal("4"))
        self.assertEqual(merged.global_discount_percent, Decimal("0"))
        self.assertEqual(first.status, SaleOrder.Status.CANCELLED)
        self.assertEqual(second.status, SaleOrder.Status.CANCELLED)
        self.assertIn(merged.number, first.cancel_reason)

    def test_merge_validation_is_atomic_and_non_draft_delete_is_blocked(self):
        first = self.order_with_lines("SQ-WF-MERGE-A")
        other_company = Company.objects.create(code="SALE-WF-OTHER", name="Other Sales")
        other_customer = Contact.objects.create(
            company=other_company,
            code="CUS-WF-OTHER",
            name="Other Customer",
        )
        second = SaleOrder.objects.create(
            company=other_company,
            number="SQ-WF-MERGE-B",
            customer=other_customer,
        )
        SaleOrderLine.objects.create(
            order=second,
            product=Product.objects.create(
                company=other_company,
                sku="PRD-WF-OTHER",
                name="Other Product",
            ),
            quantity=1,
            unit_price=Decimal("100"),
        )

        with self.assertRaisesMessage(ValidationError, "same customer"):
            merge_sale_quotations([first.pk, second.pk], self.actor)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, SaleOrder.Status.DRAFT)
        self.assertEqual(second.status, SaleOrder.Status.DRAFT)

        first.status = SaleOrder.Status.SALE
        first.save(update_fields=["status", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "no longer in Draft"):
            first.delete()
