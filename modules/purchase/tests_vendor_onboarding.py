from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Contact

from .models import (
    AwardStrategy,
    PurchaseSetting,
    PurchaseTender,
    TenderDocument,
    VendorDocumentReview,
    VendorLegalDocument,
    VendorPrequalification,
    VendorProfile,
)
from .services import (
    _validate_rfq_vendors,
    transition_vendor_document_review,
    transition_vendor_legal_document,
    transition_vendor_prequalification,
    transition_vendor_profile,
)


class VendorOnboardingParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="vendor-onboarding-admin",
            password="test-password",
            email="vendor-onboarding@example.com",
        )
        self.company = Company.objects.create(code="VEN-ONB", name="Vendor Onboarding")
        self.contact = Contact.objects.create(
            company=self.company,
            code="VEN-ONB-1",
            name="Onboarding Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.setting = PurchaseSetting.objects.create(
            company=self.company,
            require_vendor_prequalification=False,
            rfq_vendor_eligibility=PurchaseSetting.RFQVendorEligibility.APPROVED_ONLY,
        )
        self.strategy = AwardStrategy.objects.create(
            company=self.company,
            name="Vendor onboarding strategy",
        )

    def _profile(self, *, contact=None, status=VendorProfile.Status.DRAFT):
        return VendorProfile.objects.create(
            company=self.company,
            partner=contact or self.contact,
            status=status,
        )

    def _legal_document(self, profile, *, document_type="Business License", attachment=True):
        return VendorLegalDocument.objects.create(
            vendor=profile,
            document_type=document_type,
            document_number=f"DOC-{profile.pk}",
            attachment_url="https://example.com/license.pdf" if attachment else "",
            mandatory=True,
            is_blocking=True,
            expiry_required=True,
            issue_date=timezone.localdate(),
            expiry_date=timezone.localdate() + timedelta(days=365),
        )

    def _approve_document(self, document):
        transition_vendor_legal_document(document, "submit", self.actor)
        return transition_vendor_legal_document(document, "approve", self.actor)

    def test_vendor_approval_requires_approved_blocking_documents(self):
        profile = self._profile()
        document = self._legal_document(profile, attachment=False)

        with self.assertRaisesMessage(ValidationError, "blocking legal documents"):
            transition_vendor_profile(profile, "submit", self.actor)

        document.attachment_url = "https://example.com/license.pdf"
        document.save(update_fields=["attachment_url", "updated_at"])
        self._approve_document(document)
        transition_vendor_profile(profile, "submit", self.actor)
        profile = transition_vendor_profile(profile, "approve", self.actor)

        self.assertEqual(profile.status, VendorProfile.Status.ACTIVE)
        self.assertEqual(profile.approved_by, self.actor)
        self.assertIsNotNone(profile.approved_at)
        self.assertTrue(profile.registration_number.startswith("VEN-"))

    def test_legal_document_renewal_supersedes_previous_approved_version(self):
        profile = self._profile()
        previous = self._approve_document(self._legal_document(profile))
        renewal = self._legal_document(profile)
        renewal.renewal_source = "vendor"
        renewal.save(update_fields=["renewal_source", "updated_at"])

        renewal = self._approve_document(renewal)
        previous.refresh_from_db()

        self.assertEqual(previous.review_status, VendorLegalDocument.ReviewStatus.SUPERSEDED)
        self.assertEqual(previous.status, VendorLegalDocument.Status.SUPERSEDED)
        self.assertEqual(renewal.previous_version, previous)
        self.assertEqual(renewal.review_status, VendorLegalDocument.ReviewStatus.APPROVED)

    def test_blacklist_and_unblacklist_restore_previous_vendor_status(self):
        profile = self._profile(status=VendorProfile.Status.ACTIVE)

        profile = transition_vendor_profile(profile, "blacklist", self.actor, reason="Quality incident")
        self.assertEqual(profile.status_before_blacklist, VendorProfile.Status.ACTIVE)
        self.assertEqual(profile.status, VendorProfile.Status.BLACKLISTED)
        self.assertEqual(profile.vendor_blacklist_user, self.actor)

        profile = transition_vendor_profile(profile, "unblacklist", self.actor, reason="Issue resolved")
        self.assertEqual(profile.status, VendorProfile.Status.ACTIVE)
        self.assertEqual(profile.vendor_blacklist_reason, "Issue resolved")

    def test_rfq_vendor_policy_matches_hmx_status_population(self):
        profile = self._profile(status=VendorProfile.Status.DRAFT)

        with self.assertRaisesMessage(ValidationError, "company approval policy"):
            _validate_rfq_vendors(self.company.pk, [self.contact.pk])

        self.setting.rfq_vendor_eligibility = PurchaseSetting.RFQVendorEligibility.ALLOW_DRAFT
        self.setting.save(update_fields=["rfq_vendor_eligibility", "updated_at"])
        selected = _validate_rfq_vendors(self.company.pk, [self.contact.pk])
        self.assertEqual(selected, [self.contact])

        profile.status = VendorProfile.Status.BLACKLISTED
        profile.save(update_fields=["status", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "company approval policy"):
            _validate_rfq_vendors(self.company.pk, [self.contact.pk])

    def test_tender_prequalification_builds_and_reviews_document_requirements(self):
        profile = self._profile(status=VendorProfile.Status.ACTIVE)
        legal_document = self._approve_document(self._legal_document(profile))
        tender = PurchaseTender.objects.create(
            company=self.company,
            number="TND-PQ-001",
            title="Prequalification Tender",
            category=PurchaseTender.Category.OPEX,
            tender_type=PurchaseTender.TenderType.OPEN,
            method=PurchaseTender.Method.PQTE,
            award_strategy=self.strategy,
            close_date=timezone.localdate() + timedelta(days=30),
            status=PurchaseTender.Status.DOCUMENT_SUBMISSION,
        )
        TenderDocument.objects.create(
            tender=tender,
            document_type=legal_document.document_type,
            title="Required Business License",
            attachment_url="https://example.com/template.pdf",
            mandatory=True,
        )
        prequalification = VendorPrequalification.objects.create(
            tender=tender,
            vendor=profile,
        )

        transition_vendor_prequalification(prequalification, "submit", self.actor)
        line = prequalification.document_reviews.get()
        self.assertEqual(line.document, legal_document)
        self.assertEqual(line.requirement_type, VendorLegalDocument.RequirementType.REQUIRED)

        transition_vendor_document_review(line, "submit", self.actor)
        transition_vendor_document_review(line, "approve", self.actor)
        prequalification.refresh_from_db()

        self.assertEqual(prequalification.status, VendorPrequalification.Status.APPROVED)
        self.assertEqual(prequalification.document_fulfillment_percentage, 100)
        self.assertTrue(prequalification.number.startswith("PQ-"))

    def test_prequalification_review_is_limited_to_document_submission_phase(self):
        profile = self._profile(status=VendorProfile.Status.ACTIVE)
        tender = PurchaseTender.objects.create(
            company=self.company,
            number="TND-PQ-LOCK",
            title="Locked Prequalification Tender",
            category=PurchaseTender.Category.OPEX,
            tender_type=PurchaseTender.TenderType.OPEN,
            method=PurchaseTender.Method.PQTE,
            award_strategy=self.strategy,
            close_date=timezone.localdate() + timedelta(days=30),
            status=PurchaseTender.Status.BID_SUBMISSION,
        )
        prequalification = VendorPrequalification.objects.create(tender=tender, vendor=profile)
        review = VendorDocumentReview.objects.create(
            prequalification=prequalification,
            document_type="Safety Certificate",
            attachment_url="https://example.com/safety.pdf",
            status=VendorDocumentReview.Status.DRAFT,
        )

        with self.assertRaisesMessage(ValidationError, "Tender Document Submission"):
            transition_vendor_document_review(review, "submit", self.actor)

    def test_vendor_document_validation_query_count_does_not_grow_with_documents(self):
        second_contact = Contact.objects.create(
            company=self.company,
            code="VEN-ONB-2",
            name="Onboarding Vendor Two",
            contact_type=Contact.ContactType.VENDOR,
        )
        small_profile = self._profile()
        large_profile = self._profile(contact=second_contact)
        for index in range(2):
            self._approve_document(
                self._legal_document(small_profile, document_type=f"Small License {index}")
            )
        for index in range(20):
            self._approve_document(
                self._legal_document(large_profile, document_type=f"Large License {index}")
            )

        with CaptureQueriesContext(connection) as small_queries:
            transition_vendor_profile(small_profile, "submit", self.actor)
        with CaptureQueriesContext(connection) as large_queries:
            transition_vendor_profile(large_profile, "submit", self.actor)

        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Vendor validation queries grew from {len(small_queries)} to {len(large_queries)}.",
        )

    def test_workflow_and_tender_smart_actions_are_visible(self):
        profile = self._profile(status=VendorProfile.Status.ACTIVE)
        tender = PurchaseTender.objects.create(
            company=self.company,
            number="TND-PQ-UI",
            title="Prequalification UI Tender",
            category=PurchaseTender.Category.OPEX,
            tender_type=PurchaseTender.TenderType.OPEN,
            method=PurchaseTender.Method.PQTE,
            award_strategy=self.strategy,
            close_date=timezone.localdate() + timedelta(days=30),
        )
        prequalification = VendorPrequalification.objects.create(tender=tender, vendor=profile)
        self.client.force_login(self.actor)

        tender_page = self.client.get(f"/app/purchase-tenders/{tender.pk}/edit/")
        prequalification_page = self.client.get(
            f"/app/vendor-prequalifications/{prequalification.pk}/edit/"
        )
        self.assertContains(
            tender_page,
            f"smart_action=purchase-tenders.prequalifications&amp;source_id={tender.pk}",
        )
        self.assertContains(
            prequalification_page,
            f"smart_action=vendor-prequalifications.tender&amp;source_id={prequalification.pk}",
        )
