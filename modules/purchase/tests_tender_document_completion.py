from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Contact
from modules.organization.context import switch_context

from .access import PURCHASE_MANAGER, PURCHASE_USER, can_purchase_entity_action
from .models import (
    AwardStrategy,
    PurchaseLegalDocument,
    PurchaseTender,
    TenderBid,
    TenderDocument,
    TenderDocumentCompletionLine,
    VendorCategory,
    VendorDocumentReview,
    VendorLegalDocument,
    VendorPrequalification,
    VendorProfile,
)
from .services import (
    sync_tender_document_completion,
    transition_purchase_tender,
    transition_vendor_document_review,
)


class TenderDocumentCompletionParityTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(username="tender-completion-manager")
        self.manager.groups.add(Group.objects.get_or_create(name=PURCHASE_MANAGER)[0])
        self.user = get_user_model().objects.create_user(username="tender-completion-user")
        self.user.groups.add(Group.objects.get_or_create(name=PURCHASE_USER)[0])
        self.admin = get_user_model().objects.create_superuser(
            username="tender-completion-admin",
            email="tender-completion@example.com",
            password="test-password",
        )
        self.company = Company.objects.create(code="TDC", name="Tender Completion")
        self.other_company = Company.objects.create(code="TDC-2", name="Other Tender Completion")
        self.category = VendorCategory.objects.create(
            company=self.company,
            code="TDC",
            name="Tender Vendors",
        )
        self.strategy = AwardStrategy.objects.create(
            company=self.company,
            name="Tender Completion Strategy",
        )
        self.requirement = PurchaseLegalDocument.objects.create(
            company=self.company,
            name="Audited Financial Statement",
            document_usage=PurchaseLegalDocument.DocumentUsage.TENDER_DOCUMENT,
            requirement_type=PurchaseLegalDocument.RequirementType.REQUIRED,
        )

    def _profile(self, suffix):
        partner = Contact.objects.create(
            company=self.company,
            code=f"TDC-{suffix}",
            name=f"Tender Vendor {suffix}",
            contact_type=Contact.ContactType.VENDOR,
        )
        return VendorProfile.objects.create(
            company=self.company,
            partner=partner,
            category=self.category,
            status=VendorProfile.Status.ACTIVE,
        )

    def _tender(self, suffix, *, status=PurchaseTender.Status.DOCUMENT_SUBMISSION):
        tender = PurchaseTender.objects.create(
            company=self.company,
            number=f"TDC-{suffix}",
            title=f"Tender Completion {suffix}",
            category=PurchaseTender.Category.OPEX,
            tender_type=PurchaseTender.TenderType.OPEN,
            method=PurchaseTender.Method.PQTE,
            award_strategy=self.strategy,
            close_date=timezone.localdate() + timedelta(days=30),
            status=status,
        )
        TenderDocument.objects.create(
            tender=tender,
            legal_document=self.requirement,
            document_type="",
            title="",
        )
        return tender

    def _prequalification(self, tender, profile, *, review_status=VendorDocumentReview.Status.DRAFT):
        prequalification = VendorPrequalification.objects.create(tender=tender, vendor=profile)
        review = VendorDocumentReview.objects.create(
            prequalification=prequalification,
            sequence=10,
            document_type=self.requirement.name,
            attachment_url="https://example.com/audited-statement.pdf",
            requirement_type=VendorLegalDocument.RequirementType.REQUIRED,
            status=review_status,
        )
        return prequalification, review

    def test_matrix_uses_tender_requirements_and_prequalification_vendors(self):
        tender = self._tender("MATRIX")
        first_profile = self._profile("A")
        second_profile = self._profile("B")
        first, first_review = self._prequalification(
            tender,
            first_profile,
            review_status=VendorDocumentReview.Status.ACCEPTED,
        )
        second, second_review = self._prequalification(tender, second_profile)

        matrix = sync_tender_document_completion(tender)
        lines = list(matrix.lines.order_by("sequence"))

        self.assertEqual([column["column_name"] for column in matrix.document_columns], [
            self.requirement.name,
            "Pre-Qualification",
        ])
        self.assertEqual([line.vendor_id for line in lines], [first_profile.pk, second_profile.pk])
        self.assertEqual(lines[0].completion_summary, "1/1 Valid")
        self.assertEqual(lines[0].document_cells[0]["line_status"], "approved")
        self.assertEqual(lines[0].document_cells[0]["document_line_id"], first_review.pk)
        self.assertEqual(lines[1].completion_summary, "0/1 Valid")
        self.assertEqual(lines[1].document_cells[0]["line_status"], "draft")
        self.assertEqual(lines[1].document_cells[0]["document_line_id"], second_review.pk)
        self.assertEqual(lines[0].document_cells[-1]["action_res_id"], first.pk)
        self.assertEqual(lines[1].document_cells[-1]["action_res_id"], second.pk)

    def test_bid_submission_requires_at_least_one_qualified_vendor_and_rolls_back(self):
        tender = self._tender("GATE")
        _prequalification, review = self._prequalification(
            tender,
            self._profile("GATE"),
            review_status=VendorDocumentReview.Status.PENDING,
        )

        with self.assertRaisesMessage(ValidationError, "At least one Document Completion row"):
            transition_purchase_tender(tender, "open-bids", self.manager)
        tender.refresh_from_db()
        self.assertEqual(tender.status, PurchaseTender.Status.DOCUMENT_SUBMISSION)

        transition_vendor_document_review(review, "approve", self.manager)
        tender = transition_purchase_tender(tender, "open-bids", self.manager)

        self.assertEqual(tender.status, PurchaseTender.Status.BID_SUBMISSION)
        self.assertEqual(tender.document_completion_lines.get().completion_summary, "1/1 Valid")

    def test_only_purchase_manager_can_decide_prequalification_document_review(self):
        tender = self._tender("PERMISSION")
        _prequalification, review = self._prequalification(
            tender,
            self._profile("PERMISSION"),
            review_status=VendorDocumentReview.Status.PENDING,
        )

        with self.assertRaisesMessage(ValidationError, "Only Purchase Managers"):
            transition_vendor_document_review(review, "approve", self.user)

        review.refresh_from_db()
        self.assertEqual(review.status, VendorDocumentReview.Status.PENDING)
        approved = transition_vendor_document_review(review, "approve", self.manager)
        self.assertEqual(approved.status, VendorDocumentReview.Status.ACCEPTED)

    def test_only_qualified_vendors_can_enter_prequalification_tender_bids(self):
        tender = self._tender("BID-GUARD")
        qualified_profile = self._profile("BID-OK")
        unqualified_profile = self._profile("BID-NO")
        _qualified, review = self._prequalification(
            tender,
            qualified_profile,
            review_status=VendorDocumentReview.Status.PENDING,
        )
        self._prequalification(tender, unqualified_profile)
        transition_vendor_document_review(review, "approve", self.manager)
        transition_purchase_tender(tender, "open-bids", self.manager)
        tender.refresh_from_db()

        qualified_bid = TenderBid(tender=tender, vendor=qualified_profile.partner)
        qualified_bid.full_clean()

        unqualified_bid = TenderBid(tender=tender, vendor=unqualified_profile.partner)
        with self.assertRaisesMessage(ValidationError, "must pass Tender Document Completion"):
            unqualified_bid.full_clean()

    def test_tender_requirement_inherits_master_document_configuration(self):
        tender = self._tender("MASTER")
        document = tender.documents.get()

        self.assertEqual(document.document_type, self.requirement.name)
        self.assertEqual(document.title, self.requirement.name)
        self.assertTrue(document.mandatory)

        other_requirement = PurchaseLegalDocument.objects.create(
            company=self.other_company,
            name="Other Company Requirement",
            document_usage=PurchaseLegalDocument.DocumentUsage.TENDER_DOCUMENT,
        )
        invalid = TenderDocument(
            tender=tender,
            legal_document=other_requirement,
            document_type="",
            title="",
        )
        with self.assertRaisesMessage(ValidationError, "Tender company"):
            invalid.full_clean()

    def test_matrix_sync_query_count_is_constant_for_two_and_twelve_vendors(self):
        small_tender = self._tender("SMALL")
        large_tender = self._tender("LARGE")
        for index in range(2):
            self._prequalification(
                small_tender,
                self._profile(f"S-{index}"),
                review_status=VendorDocumentReview.Status.ACCEPTED,
            )
        for index in range(12):
            self._prequalification(
                large_tender,
                self._profile(f"L-{index}"),
                review_status=VendorDocumentReview.Status.ACCEPTED,
            )

        with CaptureQueriesContext(connection) as small_queries:
            sync_tender_document_completion(small_tender)
        with CaptureQueriesContext(connection) as large_queries:
            sync_tender_document_completion(large_tender)

        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Matrix sync queries grew from {len(small_queries)} to {len(large_queries)}.",
        )

    def test_completion_entities_are_read_only_and_visible_from_tender(self):
        tender = self._tender("UI")
        self._prequalification(tender, self._profile("UI"))
        sync_tender_document_completion(tender)

        self.assertTrue(
            can_purchase_entity_action(self.user, "tender-document-completion-lines", "view")
        )
        self.assertFalse(
            can_purchase_entity_action(self.manager, "tender-document-completion-lines", "change")
        )

        self.client.force_login(self.admin)
        branch = self.company.branches.get(is_main=True)
        session = self.client.session
        switch_context(self.admin, session, self.company.pk, branch.pk)
        session.save()
        page = self.client.get(reverse("entity_edit", args=["purchase-tenders", tender.pk]))
        self.assertContains(page, "Document Completion")
        self.assertContains(
            page,
            f"smart_action=purchase-tenders.document-completion-lines&amp;source_id={tender.pk}",
        )
