from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.models import Company, Contact

from .entity_config import WORKFLOW_OVERRIDES
from .models import VendorChangeProposal, VendorProfile
from .services import (
    create_vendor_change_proposal,
    find_similar_vendor_profiles,
    transition_vendor_change_proposal,
)


class VendorChangeParityTests(TestCase):
    def setUp(self):
        self.requester = get_user_model().objects.create_user(
            username="vendor-change-requester",
            password="test-password",
        )
        self.reviewer = get_user_model().objects.create_user(
            username="vendor-change-reviewer",
            password="test-password",
        )
        self.company = Company.objects.create(code="VCH", name="Vendor Change")
        self.other_company = Company.objects.create(code="VCH2", name="Other Vendor Change")
        self.partner = Contact.objects.create(
            company=self.company,
            code="VCH-V1",
            name="Original Vendor",
            contact_type=Contact.ContactType.VENDOR,
            email="shared@example.com",
            phone="021-555-0100",
            tax_id="TAX-SHARED",
            address="Original address",
        )
        self.vendor = VendorProfile.objects.create(
            company=self.company,
            partner=self.partner,
            registration_number="VCH-V1",
            tax_number="NPWP-SHARED",
            mobile_number="0812-0000-0001",
            bank_account="BANK-SHARED",
            status=VendorProfile.Status.ACTIVE,
        )

    def _similar(self, suffix, *, status=VendorProfile.Status.ACTIVE):
        partner = Contact.objects.create(
            company=self.company,
            code=f"VCH-{suffix}",
            name=f"Similar Vendor {suffix}",
            contact_type=Contact.ContactType.VENDOR,
            email="SHARED@example.com",
            phone=f"021-{suffix}",
        )
        return VendorProfile.objects.create(
            company=self.company,
            partner=partner,
            registration_number=f"VCH-{suffix}",
            bank_account="BANK-SHARED",
            status=status,
        )

    def test_similarity_matches_email_phone_tax_mobile_bank_and_includes_blacklisted(self):
        email_bank_match = self._similar("SIM", status=VendorProfile.Status.BLACKLISTED)
        mobile_partner = Contact.objects.create(
            company=self.company,
            code="VCH-MOBILE",
            name="Mobile Match",
            contact_type=Contact.ContactType.VENDOR,
        )
        mobile_match = VendorProfile.objects.create(
            company=self.company,
            partner=mobile_partner,
            registration_number="VCH-MOBILE",
            mobile_number=self.vendor.mobile_number,
            status=VendorProfile.Status.ACTIVE,
        )
        outside_partner = Contact.objects.create(
            company=self.other_company,
            code="VCH-OUT",
            name="Outside Match",
            contact_type=Contact.ContactType.VENDOR,
            email=self.partner.email,
        )
        VendorProfile.objects.create(
            company=self.other_company,
            partner=outside_partner,
            registration_number="VCH-OUT",
            status=VendorProfile.Status.ACTIVE,
        )

        results = find_similar_vendor_profiles(self.vendor)
        by_id = {row["vendor"].pk: row["reasons"] for row in results}
        self.assertEqual(set(by_id), {email_bank_match.pk, mobile_match.pk})
        self.assertEqual(set(by_id[email_bank_match.pk]), {"email", "bank_account"})
        self.assertEqual(by_id[mobile_match.pk], ["mobile_number"])

    def test_change_proposal_snapshots_identity_and_only_promotes_on_approval(self):
        proposal = create_vendor_change_proposal(
            self.vendor,
            {
                "name": "Approved Vendor Name",
                "email": "approved@example.com",
                "payment_terms": "Net 60",
            },
            self.requester,
        )
        self.partner.refresh_from_db()
        self.vendor.refresh_from_db()
        self.assertEqual(self.partner.name, "Original Vendor")
        self.assertEqual(proposal.original_snapshot["name"], "Original Vendor")
        self.assertEqual(proposal.vendor_id, self.vendor.pk)

        transition_vendor_change_proposal(proposal, "submit", self.requester)
        transition_vendor_change_proposal(proposal, "approve", self.reviewer)

        self.partner.refresh_from_db()
        self.vendor.refresh_from_db()
        proposal.refresh_from_db()
        self.assertEqual(self.partner.name, "Approved Vendor Name")
        self.assertEqual(self.partner.email, "approved@example.com")
        self.assertEqual(self.vendor.payment_terms, "Net 60")
        self.assertEqual(proposal.status, VendorChangeProposal.Status.APPROVED)
        self.assertEqual(proposal.reviewed_by, self.reviewer)

    def test_rejection_and_concurrent_live_change_do_not_overwrite_vendor(self):
        rejected = create_vendor_change_proposal(
            self.vendor,
            {"name": "Rejected Name"},
            self.requester,
        )
        transition_vendor_change_proposal(rejected, "submit", self.requester)
        transition_vendor_change_proposal(
            rejected,
            "reject",
            self.reviewer,
            reason="Insufficient evidence",
        )
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.name, "Original Vendor")

        pending = create_vendor_change_proposal(
            self.vendor,
            {"name": "Pending Name"},
            self.requester,
        )
        transition_vendor_change_proposal(pending, "submit", self.requester)
        self.partner.name = "Out-of-band change"
        self.partner.save(update_fields=["name", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "changed after"):
            transition_vendor_change_proposal(pending, "approve", self.reviewer)
        pending.refresh_from_db()
        self.assertEqual(pending.status, VendorChangeProposal.Status.WAITING_APPROVAL)

    def test_only_confirmed_active_vendor_can_propose_and_one_open_change_is_allowed(self):
        first = create_vendor_change_proposal(
            self.vendor,
            {"phone": "021-555-0199"},
            self.requester,
        )
        with self.assertRaisesMessage(ValidationError, "already has an open"):
            create_vendor_change_proposal(
                self.vendor,
                {"address": "New address"},
                self.requester,
            )
        transition_vendor_change_proposal(first, "cancel", self.requester)
        self.vendor.active = False
        self.vendor.save(update_fields=["active", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "active Confirmed"):
            create_vendor_change_proposal(
                self.vendor,
                {"address": "New address"},
                self.requester,
            )

    def test_similarity_query_count_and_workflow_contract_are_bounded(self):
        baseline_source = self._similar("BASE-SOURCE")
        baseline_source.partner.email = "baseline@example.com"
        baseline_source.partner.save(update_fields=["email", "updated_at"])
        with CaptureQueriesContext(connection) as baseline:
            find_similar_vendor_profiles(baseline_source)

        for index in range(12):
            partner = Contact.objects.create(
                company=self.company,
                code=f"VCH-Q-{index}",
                name=f"Query Match {index}",
                contact_type=Contact.ContactType.VENDOR,
                email="shared@example.com",
            )
            VendorProfile.objects.create(
                company=self.company,
                partner=partner,
                registration_number=f"VCH-Q-{index}",
                status=VendorProfile.Status.ACTIVE,
            )
        with CaptureQueriesContext(connection) as grown:
            results = find_similar_vendor_profiles(self.vendor)

        self.assertEqual(len(results), 12 + 1)
        self.assertEqual(len(baseline), len(grown))
        self.assertEqual(
            set(WORKFLOW_OVERRIDES["vendor-change-proposals"]),
            {"submit", "approve", "reject", "cancel"},
        )
