from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Contact
from modules.organization.context import switch_context

from .access import PURCHASE_MANAGER, PURCHASE_USER, can_purchase_entity_action
from .models import (
    PurchaseLegalDocument,
    PurchaseLegalDocumentAttachment,
    VendorCategory,
    VendorLegalDocument,
    VendorProfile,
)
from .services import (
    applicable_purchase_legal_documents,
    sync_vendor_legal_document_requirements,
    transition_vendor_legal_document,
    transition_vendor_profile,
)


class PurchaseLegalDocumentParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="purchase-legal-admin",
            email="purchase-legal@example.com",
            password="test-password",
        )
        self.company = Company.objects.create(code="PLD", name="Purchase Legal Documents")
        self.other_company = Company.objects.create(code="PLD-2", name="Other Legal Documents")
        self.category = VendorCategory.objects.create(
            company=self.company,
            code="FOOD",
            name="Food Vendors",
        )
        self.other_category = VendorCategory.objects.create(
            company=self.company,
            code="OTHER",
            name="Other Vendors",
        )
        self.vendor_a = self._profile("A", self.category)
        self.vendor_b = self._profile("B", self.other_category)

    def _profile(self, suffix, category, *, company=None):
        company = company or self.company
        contact = Contact.objects.create(
            company=company,
            code=f"VEN-PLD-{suffix}",
            name=f"Legal Vendor {suffix}",
            contact_type=Contact.ContactType.VENDOR,
        )
        return VendorProfile.objects.create(
            company=company,
            partner=contact,
            category=category,
        )

    def _requirement(self, name, **values):
        defaults = {
            "company": self.company,
            "name": name,
            "document_usage": PurchaseLegalDocument.DocumentUsage.STANDARD_VENDOR,
            "requirement_type": PurchaseLegalDocument.RequirementType.REQUIRED,
        }
        defaults.update(values)
        document = PurchaseLegalDocument(**defaults)
        document.full_clean()
        document.save()
        return document

    def test_required_optional_and_additional_applicability_matches_hmx(self):
        required = self._requirement("Business License")
        optional = self._requirement(
            "Sustainability Statement",
            requirement_type=PurchaseLegalDocument.RequirementType.OPTIONAL,
            is_blocking=False,
        )
        by_vendor = self._requirement(
            "Vendor A Warranty",
            requirement_type=PurchaseLegalDocument.RequirementType.ADDITIONAL,
            apply_to=PurchaseLegalDocument.ApplyTo.VENDOR,
        )
        by_vendor.applicable_vendors.add(self.vendor_a)
        by_category = self._requirement(
            "Food Safety Certificate",
            requirement_type=PurchaseLegalDocument.RequirementType.ADDITIONAL,
            apply_to=PurchaseLegalDocument.ApplyTo.CATEGORY,
        )
        by_category.applicable_categories.add(self.category)

        vendor_a_ids = set(applicable_purchase_legal_documents(self.vendor_a).values_list("pk", flat=True))
        vendor_b_ids = set(applicable_purchase_legal_documents(self.vendor_b).values_list("pk", flat=True))

        self.assertEqual(vendor_a_ids, {required.pk, optional.pk, by_vendor.pk, by_category.pk})
        self.assertEqual(vendor_b_ids, {required.pk, optional.pk})

    def test_optional_requirement_cannot_block_vendor(self):
        document = PurchaseLegalDocument(
            company=self.company,
            name="Invalid Optional",
            requirement_type=PurchaseLegalDocument.RequirementType.OPTIONAL,
            is_blocking=True,
        )

        with self.assertRaisesMessage(ValidationError, "Optional documents cannot be blocking"):
            document.full_clean()

    def test_master_requirement_materializes_and_blocks_submission_until_approved(self):
        requirement = self._requirement(
            "Tax Certificate",
            expiry_applicable=True,
            expiry_date_required=True,
            default_validity_months=12,
            reminder_before_expiry_days=30,
        )
        sync_result = sync_vendor_legal_document_requirements(self.vendor_a)
        self.assertEqual(sync_result["created"], 1)

        with self.assertRaisesMessage(ValidationError, "blocking legal documents"):
            transition_vendor_profile(self.vendor_a, "submit", self.actor)

        document = VendorLegalDocument.objects.get(
            vendor=self.vendor_a,
            legal_document=requirement,
        )
        self.assertEqual(document.document_type, requirement.name)
        self.assertTrue(document.expiry_required)

        document.issue_date = timezone.localdate()
        document.expiry_date = timezone.localdate() + timedelta(days=365)
        document.attachment_url = "https://example.com/tax-certificate.pdf"
        document.save()
        transition_vendor_legal_document(document, "submit", self.actor)
        transition_vendor_legal_document(document, "approve", self.actor)

        submitted = transition_vendor_profile(self.vendor_a, "submit", self.actor)
        self.assertEqual(submitted.status, VendorProfile.Status.WAITING_APPROVAL)

    def test_default_validity_uses_calendar_month_semantics(self):
        requirement = self._requirement(
            "Monthly License",
            expiry_applicable=True,
            default_validity_months=1,
        )
        document = VendorLegalDocument.objects.create(
            vendor=self.vendor_a,
            legal_document=requirement,
            issue_date=date(2024, 1, 31),
        )

        self.assertEqual(document.expiry_date, date(2024, 2, 29))

    def test_sync_links_matching_legacy_document_and_is_idempotent(self):
        requirement = self._requirement("Legacy License")
        legacy = VendorLegalDocument.objects.create(
            vendor=self.vendor_a,
            document_type="Legacy License",
            attachment_url="https://example.com/legacy.pdf",
        )

        first = sync_vendor_legal_document_requirements(self.vendor_a)
        second = sync_vendor_legal_document_requirements(self.vendor_a)
        legacy.refresh_from_db()

        self.assertEqual(first, {"requirements": 1, "linked": 1, "created": 0})
        self.assertEqual(second, {"requirements": 1, "linked": 0, "created": 0})
        self.assertEqual(legacy.legal_document, requirement)
        self.assertEqual(
            VendorLegalDocument.objects.filter(vendor=self.vendor_a, legal_document=requirement).count(),
            1,
        )

    def test_company_scope_excludes_other_company_requirements(self):
        self._requirement("Local Requirement")
        PurchaseLegalDocument.objects.create(
            company=self.other_company,
            name="Other Company Requirement",
        )

        names = set(applicable_purchase_legal_documents(self.vendor_a).values_list("name", flat=True))

        self.assertEqual(names, {"Local Requirement"})

    def test_attachment_rejects_non_pdf_content(self):
        requirement = self._requirement("Attachment Contract")
        attachment = PurchaseLegalDocumentAttachment(
            legal_document=requirement,
            file=SimpleUploadedFile("contract.pdf", b"not-a-pdf", content_type="application/pdf"),
        )

        with self.assertRaisesMessage(ValidationError, "valid PDF"):
            attachment.full_clean()

    def test_sync_query_count_does_not_grow_with_requirement_count(self):
        small_vendor = self._profile("SMALL", self.category)
        large_vendor = self._profile("LARGE", self.category)
        PurchaseLegalDocument.objects.bulk_create(
            [
                PurchaseLegalDocument(company=self.company, name=f"Requirement {index}")
                for index in range(20)
            ]
        )

        with CaptureQueriesContext(connection) as small_queries:
            sync_vendor_legal_document_requirements(small_vendor)
        VendorLegalDocument.objects.filter(vendor=small_vendor).exclude(
            legal_document__name__in=["Requirement 0", "Requirement 1"]
        ).delete()
        PurchaseLegalDocument.objects.filter(name__in=[f"Requirement {index}" for index in range(2, 20)]).update(
            active=False
        )
        with CaptureQueriesContext(connection) as two_requirement_queries:
            sync_vendor_legal_document_requirements(self.vendor_b)
        PurchaseLegalDocument.objects.update(active=True)
        with CaptureQueriesContext(connection) as twenty_requirement_queries:
            sync_vendor_legal_document_requirements(large_vendor)

        self.assertGreater(len(small_queries), 0)
        self.assertLessEqual(
            len(twenty_requirement_queries),
            len(two_requirement_queries),
            f"Requirement sync queries grew from {len(two_requirement_queries)} to {len(twenty_requirement_queries)}.",
        )

    def test_purchase_users_read_master_configuration_but_only_managers_modify(self):
        user = get_user_model().objects.create_user(username="purchase-legal-user")
        user.groups.add(Group.objects.get_or_create(name=PURCHASE_USER)[0])
        manager = get_user_model().objects.create_user(username="purchase-legal-manager")
        manager.groups.add(Group.objects.get_or_create(name=PURCHASE_MANAGER)[0])

        self.assertTrue(can_purchase_entity_action(user, "purchase-legal-documents", "view"))
        self.assertFalse(can_purchase_entity_action(user, "purchase-legal-documents", "add"))
        self.assertTrue(can_purchase_entity_action(manager, "purchase-legal-documents", "add"))

    def test_master_form_and_vendor_requirement_smart_action_are_available(self):
        self.client.force_login(self.actor)
        branch = self.company.branches.get(is_main=True)
        session = self.client.session
        switch_context(self.actor, session, self.company.pk, branch.pk)
        session.save()
        response = self.client.post(
            reverse("entity_create", args=["purchase-legal-documents"]),
            {
                "company": self.company.pk,
                "organization_branch": branch.pk,
                "name": "UI Safety Certificate",
                "description": "Required from food vendors",
                "document_usage": PurchaseLegalDocument.DocumentUsage.STANDARD_VENDOR,
                "requirement_type": PurchaseLegalDocument.RequirementType.ADDITIONAL,
                "is_blocking": "on",
                "apply_to": PurchaseLegalDocument.ApplyTo.CATEGORY,
                "applicable_categories": [self.category.pk],
                "active": "on",
            },
        )

        if response.status_code != 302:
            self.fail(response.context["form"].errors.as_json())
        self.assertEqual(response.status_code, 302)
        requirement = PurchaseLegalDocument.objects.get(name="UI Safety Certificate")
        self.assertEqual(list(requirement.applicable_categories.all()), [self.category])

        profile_page = self.client.get(
            reverse("entity_edit", args=["vendor-profiles", self.vendor_a.pk])
        )
        self.assertContains(profile_page, "Legal Document Requirements")
        self.assertContains(
            profile_page,
            f"smart_action=vendor-profiles.legal-document-requirements&amp;source_id={self.vendor_a.pk}",
        )
