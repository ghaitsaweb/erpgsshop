from datetime import timedelta
from decimal import Decimal
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Contact, PurchaseOrder, Warehouse

from .models import (
    AwardStrategy,
    BlanketOrder,
    PurchaseOrderCommunication,
    PurchaseDocumentAttachment,
    PurchaseQuotation,
    PurchaseRequest,
    PurchaseTeam,
    PurchaseTeamMember,
    PurchaseTender,
)
from .forms import PurchaseOrderDocumentForm, PurchaseRequestDocumentForm
from .services import purchase_order_document_tab_context


class PurchaseDocumentTabParityTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.media_override = self.settings(MEDIA_ROOT=self.media_directory.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.actor = get_user_model().objects.create_superuser(
            username="purchase-tab-admin",
            email="purchase-tab@example.com",
            password="test-password",
        )
        self.outsider = get_user_model().objects.create_user(
            username="purchase-tab-outsider",
            password="test-password",
        )
        self.company = Company.objects.create(code="PTAB", name="Purchase Tabs")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="PTAB-VENDOR",
            name="Purchase Tab Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="PTAB-WH",
            name="Purchase Tab Warehouse",
        )
        self.team = PurchaseTeam.objects.create(
            company=self.company,
            name="Mobile Procurement",
            manager=self.actor,
        )
        PurchaseTeamMember.objects.update_or_create(
            team=self.team,
            user=self.actor,
            defaults={"role": "Team Lead", "active": True},
        )
        self.request = PurchaseRequest.objects.create(
            company=self.company,
            team=self.team,
            number="PR-PTAB-001",
            requestor=self.actor,
            request_date=timezone.localdate(),
            required_date=timezone.localdate() + timedelta(days=7),
            justification="Mobile tab parity",
        )
        self.quotation = PurchaseQuotation.objects.create(
            company=self.company,
            number="RFQ-PTAB-001",
            request=self.request,
            vendor=self.vendor,
            warehouse=self.warehouse,
            issue_date=timezone.localdate(),
            response_deadline=timezone.localdate() + timedelta(days=7),
            expected_date=timezone.localdate() + timedelta(days=14),
            team=self.team,
            assigned_to=self.actor,
        )
        self.order = PurchaseOrder.objects.create(
            company=self.company,
            number="PO-PTAB-001",
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=timezone.localdate(),
        )
        self.strategy = AwardStrategy.objects.create(
            company=self.company,
            name="Purchase Tab Strategy",
        )
        self.tender = PurchaseTender.objects.create(
            company=self.company,
            number="TENDER-PTAB-001",
            title="Mobile Tender Tabs",
            category=PurchaseTender.Category.OPEX,
            tender_type=PurchaseTender.TenderType.OPEN,
            method=PurchaseTender.Method.PQTE,
            award_strategy=self.strategy,
            team=self.team,
            assigned_to=self.actor,
            status=PurchaseTender.Status.BID_SELECTION,
        )
        self.blanket = BlanketOrder.objects.create(
            company=self.company,
            number="BO-PTAB-001",
            vendor=self.vendor,
            warehouse=self.warehouse,
            date_start=timezone.localdate(),
            date_end=timezone.localdate() + timedelta(days=30),
            agreement_value=Decimal("1000"),
        )
        self.client.force_login(self.actor)

    def assert_tab_keys(self, response, keys):
        self.assertEqual(response.status_code, 200)
        for key in keys:
            self.assertContains(response, f'data-document-tab="{key}"')
            self.assertContains(response, f'data-document-tab-panel="{key}"')

    def test_hmx_document_tabs_render_as_accessible_editable_panels(self):
        request_page = self.client.get(
            reverse("entity_edit", args=["purchase-requests", self.request.pk])
        )
        quotation_page = self.client.get(
            reverse("entity_edit", args=["purchase-quotations", self.quotation.pk])
        )
        order_page = self.client.get(
            reverse("entity_edit", args=["purchases", self.order.pk])
        )
        tender_page = self.client.get(
            reverse("entity_edit", args=["purchase-tenders", self.tender.pk])
        )
        blanket_page = self.client.get(
            reverse("entity_edit", args=["blanket-orders", self.blanket.pk])
        )

        self.assert_tab_keys(
            request_page,
            ["order-lines", "attachment-file", "purchase-team", "approval-history"],
        )
        self.assert_tab_keys(
            quotation_page,
            ["order-lines", "purchase-team", "communication-history", "other-information"],
        )
        self.assert_tab_keys(
            order_page,
            [
                "order-lines",
                "milestone-contract-terms",
                "service-work-order",
                "billing-policy",
                "purchase-team",
                "communication-history",
                "attachment-file",
                "other-information",
                "approval-history",
            ],
        )
        self.assert_tab_keys(
            tender_page,
            [
                "order-lines",
                "participating-vendors",
                "document-completion",
                "vendor-scoring",
                "purchase-team",
                "approval-history",
                "other-information",
            ],
        )
        self.assert_tab_keys(
            blanket_page,
            ["order-lines", "agreement-policy", "approval-history", "other-information"],
        )
        self.assertContains(tender_page, 'role="tablist"')
        self.assertContains(tender_page, 'aria-selected="true"')
        self.assertContains(tender_page, 'name="team"')
        self.assertContains(tender_page, 'name="assigned_to"')
        self.assertContains(blanket_page, 'name="agreement_policy"')
        self.assertContains(order_page, 'name="invoicing_policy"')
        self.assertContains(order_page, 'name="downpayment_method"')
        self.assertContains(order_page, 'name="attachment_files"')

    def test_pr_attachment_upload_is_validated_persisted_and_downloadable(self):
        upload = SimpleUploadedFile(
            "purchase-brief.pdf",
            b"%PDF-1.4\nPurchase brief",
            content_type="application/pdf",
        )
        form = PurchaseRequestDocumentForm(instance=self.request)
        form.purchase_attachment_actor = self.actor
        form.cleaned_data = {
            "attachment_files": [upload],
            "remove_attachments": [],
        }
        form._save_purchase_attachments(self.request)

        attachment = PurchaseDocumentAttachment.objects.get(purchase_request=self.request)
        self.assertEqual(attachment.original_name, "purchase-brief.pdf")
        self.assertEqual(attachment.uploaded_by, self.actor)
        response = self.client.get(
            reverse(
                "purchase_document_attachment_download",
                args=["purchase-requests", self.request.pk, attachment.pk],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        wrong_parent = self.client.get(
            reverse(
                "purchase_document_attachment_download",
                args=["purchases", self.order.pk, attachment.pk],
            )
        )
        self.assertEqual(wrong_parent.status_code, 404)

        invalid = SimpleUploadedFile(
            "spoofed.pdf",
            b"not a pdf",
            content_type="application/pdf",
        )
        with self.assertRaises(ValidationError):
            form.fields["attachment_files"].clean([invalid])

    def test_sent_po_keeps_attachment_specific_edit_window(self):
        self.order.status = PurchaseOrder.Status.SENT
        self.order.save(update_fields=["status", "updated_at"])
        upload = SimpleUploadedFile(
            "sent-order.png",
            b"\x89PNG\r\n\x1a\ncontent",
            content_type="image/png",
        )

        response = self.client.post(
            reverse(
                "purchase_document_attachment_manage",
                args=["purchases", self.order.pk],
            ),
            {"attachment_files": [upload]},
        )

        self.assertRedirects(
            response,
            reverse("entity_edit", args=["purchases", self.order.pk])
            + "#tab-attachment-file",
            fetch_redirect_response=False,
        )
        self.assertTrue(
            PurchaseDocumentAttachment.objects.filter(
                purchase_order=self.order,
                original_name="sent-order.png",
            ).exists()
        )

    def test_rfq_and_tender_pic_must_be_an_active_team_member(self):
        self.quotation.assigned_to = self.outsider
        with self.assertRaisesMessage(
            ValidationError,
            "PIC must be an active member of the selected Purchase Team.",
        ):
            self.quotation.full_clean()

        self.tender.assigned_to = self.outsider
        with self.assertRaisesMessage(
            ValidationError,
            "PIC must be an active member of the selected Purchase Team.",
        ):
            self.tender.full_clean()

    def test_tender_matrix_tabs_follow_hmx_method_and_status_visibility(self):
        self.tender.method = PurchaseTender.Method.OTE
        self.tender.status = PurchaseTender.Status.DRAFT
        self.tender.save(update_fields=["method", "status", "updated_at"])

        response = self.client.get(
            reverse("entity_edit", args=["purchase-tenders", self.tender.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-document-tab="document-completion"')
        self.assertNotContains(response, 'data-document-tab="vendor-scoring"')

    def test_purchase_order_tab_queries_do_not_grow_with_communication_rows(self):
        large_order = PurchaseOrder.objects.create(
            company=self.company,
            number="PO-PTAB-QUERY-L",
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=timezone.localdate(),
        )
        PurchaseOrderCommunication.objects.bulk_create(
            [
                PurchaseOrderCommunication(
                    order=self.order,
                    direction=PurchaseOrderCommunication.Direction.OUTBOUND,
                    subject=f"Small {index}",
                    message="Bounded communication",
                )
                for index in range(2)
            ]
            + [
                PurchaseOrderCommunication(
                    order=large_order,
                    direction=PurchaseOrderCommunication.Direction.OUTBOUND,
                    subject=f"Large {index}",
                    message="Bounded communication",
                )
                for index in range(12)
            ]
        )

        with CaptureQueriesContext(connection) as small_queries:
            small = purchase_order_document_tab_context(self.order, self.actor)
        with CaptureQueriesContext(connection) as large_queries:
            large = purchase_order_document_tab_context(large_order, self.actor)

        self.assertEqual(len(small["purchase_tab_communications"]), 2)
        self.assertEqual(len(large["purchase_tab_communications"]), 12)
        self.assertEqual(len(small_queries), len(large_queries))

    def test_purchase_attachment_form_queries_do_not_grow_with_file_rows(self):
        large_order = PurchaseOrder.objects.create(
            company=self.company,
            number="PO-PTAB-ATTACH-L",
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=timezone.localdate(),
        )
        PurchaseDocumentAttachment.objects.bulk_create(
            [
                PurchaseDocumentAttachment(
                    purchase_order=self.order,
                    file=f"purchase/test/small-{index}.pdf",
                    original_name=f"small-{index}.pdf",
                    content_type="application/pdf",
                    file_size=10,
                    uploaded_by=self.actor,
                )
                for index in range(2)
            ]
            + [
                PurchaseDocumentAttachment(
                    purchase_order=large_order,
                    file=f"purchase/test/large-{index}.pdf",
                    original_name=f"large-{index}.pdf",
                    content_type="application/pdf",
                    file_size=10,
                    uploaded_by=self.actor,
                )
                for index in range(12)
            ]
        )

        with CaptureQueriesContext(connection) as small_queries:
            small_form = PurchaseOrderDocumentForm(instance=self.order)
        with CaptureQueriesContext(connection) as large_queries:
            large_form = PurchaseOrderDocumentForm(instance=large_order)

        self.assertEqual(len(small_form.purchase_attachment_rows), 2)
        self.assertEqual(len(large_form.purchase_attachment_rows), 12)
        self.assertEqual(len(small_queries), len(large_queries))
