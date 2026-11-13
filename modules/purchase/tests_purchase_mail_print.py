from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.document_pdf import _purchase_terms_story, _styles
from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine

from .models import (
    AwardStrategy,
    BlanketOrder,
    BlanketOrderLine,
    PurchaseMailTemplate,
    PurchaseOrderControl,
    PurchaseQuotation,
    PurchaseQuotationCommunication,
    PurchaseQuotationLine,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseTender,
    PurchaseTenderLine,
)
from .services import ensure_purchase_mail_templates, send_purchase_quotation_email


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="purchase@example.com",
)
class PurchaseMailPrintParityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="purchase-mail-print",
            email="buyer@example.com",
            password="test",
        )
        cls.company = Company.objects.create(
            code="MAILPRINT",
            name="Mail Print Company",
            email="purchase@example.com",
            address="Buyer Street 1",
        )
        cls.vendor = Contact.objects.create(
            company=cls.company,
            code="MAIL-VENDOR",
            name="Mail Vendor",
            contact_type=Contact.ContactType.VENDOR,
            email="vendor@example.com",
            address="Vendor Street 2",
        )
        cls.product = Product.objects.create(
            company=cls.company,
            sku="MAIL-PRODUCT",
            name="Mail Product",
            purchase_price=Decimal("25000"),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _quotation(self, number, line_count=1):
        quotation = PurchaseQuotation.objects.create(
            company=self.company,
            number=number,
            vendor=self.vendor,
            issue_date=timezone.localdate(),
            response_deadline=timezone.localdate() + timedelta(days=7),
            quoted_amount=Decimal(line_count * 25000),
        )
        PurchaseQuotationLine.objects.bulk_create(
            [
                PurchaseQuotationLine(
                    quotation=quotation,
                    product=self.product,
                    description=f"Mail Product {index}",
                    quantity=1,
                    unit_price=Decimal("25000"),
                )
                for index in range(line_count)
            ]
        )
        return quotation

    def test_seed_templates_are_idempotent_and_preserve_customer_edits(self):
        templates = list(ensure_purchase_mail_templates(self.company))
        self.assertEqual(len(templates), 3)
        standard = PurchaseMailTemplate.objects.get(
            company=self.company,
            code=PurchaseMailTemplate.TemplateType.STANDARD,
        )
        standard.subject_template = "Customer-owned subject {{ record.number }}"
        standard.save(update_fields=["subject_template", "updated_at"])

        ensure_purchase_mail_templates(self.company)

        standard.refresh_from_db()
        self.assertEqual(
            standard.subject_template,
            "Customer-owned subject {{ record.number }}",
        )
        self.assertEqual(PurchaseMailTemplate.objects.filter(company=self.company).count(), 3)

    def test_rfq_document_action_opens_rendered_mail_composer(self):
        quotation = self._quotation("RFQ-MAIL-UI")

        document = self.client.get(
            reverse("entity_edit", args=["purchase-quotations", quotation.pk])
        )
        composer = self.client.get(reverse("purchase_quotation_email", args=[quotation.pk]))

        self.assertEqual(document.status_code, 200)
        self.assertContains(document, reverse("purchase_quotation_email", args=[quotation.pk]))
        self.assertEqual(composer.status_code, 200)
        self.assertContains(composer, "Send RFQ RFQ-MAIL-UI")
        self.assertContains(composer, "Dear Mail Vendor")
        self.assertContains(composer, "Preview PDF")

    def test_send_email_attaches_pdf_records_history_and_marks_draft_sent(self):
        quotation = self._quotation("RFQ-MAIL-SEND")
        template = ensure_purchase_mail_templates(self.company).get(
            code=PurchaseMailTemplate.TemplateType.URGENT
        )

        communication = send_purchase_quotation_email(
            quotation,
            template=template,
            email_to="vendor@example.com",
            email_cc="buyer@example.com",
            subject="Urgent local override",
            body_html="<p>Local body override</p>",
            actor=self.user,
        )

        quotation.refresh_from_db()
        self.assertEqual(quotation.status, PurchaseQuotation.Status.SENT)
        self.assertEqual(communication.status, PurchaseQuotationCommunication.Status.SENT)
        self.assertIsNotNone(communication.sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Urgent local override")
        self.assertEqual(mail.outbox[0].to, ["vendor@example.com"])
        self.assertEqual(mail.outbox[0].cc, ["buyer@example.com"])
        self.assertEqual(mail.outbox[0].attachments[0][2], "application/pdf")
        self.assertTrue(mail.outbox[0].attachments[0][1].startswith(b"%PDF"))

    def test_failed_delivery_is_retained_without_advancing_rfq(self):
        quotation = self._quotation("RFQ-MAIL-FAIL")
        template = ensure_purchase_mail_templates(self.company).get(
            code=PurchaseMailTemplate.TemplateType.STANDARD
        )

        with patch("modules.purchase.services.EmailMultiAlternatives.send", side_effect=RuntimeError("SMTP down")):
            with self.assertRaisesMessage(ValidationError, "SMTP down"):
                send_purchase_quotation_email(
                    quotation,
                    template=template,
                    email_to="vendor@example.com",
                    actor=self.user,
                )

        quotation.refresh_from_db()
        attempt = PurchaseQuotationCommunication.objects.get(quotation=quotation)
        self.assertEqual(quotation.status, PurchaseQuotation.Status.DRAFT)
        self.assertEqual(attempt.status, PurchaseQuotationCommunication.Status.FAILED)
        self.assertIn("SMTP down", attempt.failure_reason)

    def test_company_scope_and_recipient_validation_are_enforced(self):
        quotation = self._quotation("RFQ-MAIL-SCOPE")
        other_company = Company.objects.create(code="MAIL-OTHER", name="Other", email="other@example.com")
        other_template = ensure_purchase_mail_templates(other_company).first()

        with self.assertRaisesMessage(ValidationError, "RFQ company"):
            send_purchase_quotation_email(
                quotation,
                template=other_template,
                email_to="vendor@example.com",
                actor=self.user,
            )
        own_template = ensure_purchase_mail_templates(self.company).first()
        with self.assertRaisesMessage(ValidationError, "Invalid email address"):
            send_purchase_quotation_email(
                quotation,
                template=own_template,
                email_to="not-an-email",
                actor=self.user,
            )

    def test_rfq_mail_query_shape_is_fixed_for_two_and_twelve_lines(self):
        template = ensure_purchase_mail_templates(self.company).get(
            code=PurchaseMailTemplate.TemplateType.STANDARD
        )
        small = self._quotation("RFQ-MAIL-Q2", 2)
        large = self._quotation("RFQ-MAIL-Q12", 12)

        with CaptureQueriesContext(connection) as small_queries:
            send_purchase_quotation_email(
                small,
                template=template,
                email_to="vendor@example.com",
                actor=self.user,
            )
        with CaptureQueriesContext(connection) as large_queries:
            send_purchase_quotation_email(
                large,
                template=template,
                email_to="vendor@example.com",
                actor=self.user,
            )

        self.assertEqual(len(small_queries), len(large_queries))

    def test_purchase_documents_have_scoped_pdf_reports_and_po_terms(self):
        today = timezone.localdate()
        request = PurchaseRequest.objects.create(
            company=self.company,
            number="PR-PRINT",
            requestor=self.user,
            request_date=today,
            justification="Print approval justification",
        )
        PurchaseRequestLine.objects.create(
            request=request,
            product=self.product,
            description="PR Print Line",
            quantity=2,
        )
        strategy = AwardStrategy.objects.create(
            company=self.company,
            name="Print Award",
            strategy=AwardStrategy.Strategy.SINGLE,
        )
        tender = PurchaseTender.objects.create(
            company=self.company,
            number="TENDER-PRINT",
            title="Print Tender",
            category=PurchaseTender.Category.OPEX,
            tender_type=PurchaseTender.TenderType.OPEN,
            award_strategy=strategy,
            close_date=today + timedelta(days=14),
            budget_amount=Decimal("50000"),
            description="Tender print description",
        )
        PurchaseTenderLine.objects.create(
            tender=tender,
            product=self.product,
            description="Tender Print Line",
            quantity=2,
            target_unit_price=Decimal("25000"),
        )
        blanket = BlanketOrder.objects.create(
            company=self.company,
            number="BLANKET-PRINT",
            vendor=self.vendor,
            date_start=today,
            date_end=today + timedelta(days=30),
            agreement_value=Decimal("50000"),
            terms="Blanket print terms",
        )
        BlanketOrderLine.objects.create(
            blanket_order=blanket,
            product=self.product,
            agreed_quantity=2,
            unit_price=Decimal("25000"),
        )
        order = PurchaseOrder.objects.create(
            company=self.company,
            number="PO-PRINT",
            vendor=self.vendor,
            order_date=today,
            total=Decimal("50000"),
        )
        PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            quantity=2,
            unit_price=Decimal("25000"),
        )
        control = PurchaseOrderControl.objects.create(
            order=order,
            vendor_address="Vendor snapshot address",
            bill_address="Billing snapshot address",
            payment_terms="30 days",
            delivery_terms="Delivered duty paid",
            legal_terms="Local purchase law",
            contract_terms="No substitution",
        )

        for entity, document in (
            ("purchase-requests", request),
            ("purchase-tenders", tender),
            ("blanket-orders", blanket),
            ("purchases", order),
        ):
            response = self.client.get(reverse("document_pdf", args=[entity, document.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertTrue(response.content.startswith(b"%PDF"))

        order.purchase_control = control
        terms_story = _purchase_terms_story("purchases", order, _styles())
        self.assertEqual(len(terms_story), 6)
