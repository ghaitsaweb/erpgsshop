from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Contact, Employee, Product, Warehouse

from .models import (
    AwardStrategy,
    PurchaseQuotation,
    PurchaseSetting,
    PurchaseTender,
    PurchaseTenderLine,
    TenderAuditLog,
    TenderBid,
    TenderInvitation,
    VendorProfile,
)
from .services import (
    create_purchase_tender_rfqs,
    purchase_tender_rfq_candidates,
    resolve_purchase_tender_vendor_scope,
    transition_purchase_quotation,
)


class TenderRFQParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="tender-rfq-admin",
            email="tender-rfq@example.com",
            password="test-password",
        )
        self.company = Company.objects.create(code="TRFQ", name="Tender RFQ Parity")
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="TRFQ-WH",
            name="Tender RFQ Warehouse",
        )
        PurchaseSetting.objects.create(
            company=self.company,
            require_vendor_prequalification=False,
        )
        self.strategy = AwardStrategy.objects.create(
            company=self.company,
            name="Tender RFQ Strategy",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="TRFQ-PRODUCT",
            name="Tender RFQ Product",
            purchase_price=Decimal("100"),
        )

    def _vendor(self, suffix, *, status=VendorProfile.Status.ACTIVE, active=True):
        partner = Contact.objects.create(
            company=self.company,
            code=f"TRFQ-{suffix}",
            name=f"Tender RFQ Vendor {suffix}",
            contact_type=Contact.ContactType.VENDOR,
        )
        VendorProfile.objects.create(
            company=self.company,
            partner=partner,
            status=status,
            active=active,
        )
        return partner

    def _tender(
        self,
        suffix,
        *,
        tender_type=PurchaseTender.TenderType.OPEN,
        method=PurchaseTender.Method.OTE,
        line_count=1,
    ):
        tender = PurchaseTender.objects.create(
            company=self.company,
            number=f"TRFQ-{suffix}",
            title=f"Tender RFQ {suffix}",
            category=PurchaseTender.Category.OPEX,
            tender_type=tender_type,
            method=method,
            award_strategy=self.strategy,
            warehouse=self.warehouse,
            publish_date=timezone.localdate(),
            bid_start_date=timezone.localdate(),
            close_date=timezone.localdate() + timedelta(days=30),
            expiry_date=timezone.localdate() + timedelta(days=20),
            status=PurchaseTender.Status.BID_SUBMISSION,
        )
        PurchaseTenderLine.objects.bulk_create(
            [
                PurchaseTenderLine(
                    tender=tender,
                    product=self.product,
                    description=f"Tender RFQ Product {index}",
                    quantity=Decimal("2"),
                    target_unit_price=Decimal("100") + index,
                    delivery_date=timezone.localdate() + timedelta(days=7 + index),
                    destination_warehouse=self.warehouse,
                )
                for index in range(line_count)
            ],
            batch_size=500,
        )
        return tender

    def test_existing_configuration_maps_to_hmx_vendor_scopes(self):
        registered = self._tender("REGISTERED")
        invited = self._tender(
            "INVITED",
            tender_type=PurchaseTender.TenderType.INVITED,
            method=PurchaseTender.Method.ITE,
        )
        public = self._tender("PUBLIC", method=PurchaseTender.Method.PQTE)

        self.assertEqual(resolve_purchase_tender_vendor_scope(registered), "open_registered")
        self.assertEqual(resolve_purchase_tender_vendor_scope(invited), "invited")
        self.assertEqual(resolve_purchase_tender_vendor_scope(public), "open_public")

    def test_registered_and_invited_candidates_follow_existing_configuration(self):
        active = self._vendor("ACTIVE")
        draft = self._vendor("DRAFT", status=VendorProfile.Status.DRAFT)
        inactive = self._vendor("INACTIVE", active=False)
        registered = self._tender("CANDIDATES")

        scope, candidates = purchase_tender_rfq_candidates(registered)

        self.assertEqual(scope, "open_registered")
        self.assertEqual({vendor.pk for vendor in candidates}, {active.pk, draft.pk})
        self.assertNotIn(inactive.pk, {vendor.pk for vendor in candidates})

        invited = self._tender(
            "CANDIDATES-INVITED",
            tender_type=PurchaseTender.TenderType.INVITED,
            method=PurchaseTender.Method.ITE,
        )
        TenderInvitation.objects.create(tender=invited, vendor=active)
        invited_scope, invited_candidates = purchase_tender_rfq_candidates(invited)
        self.assertEqual(invited_scope, "invited")
        self.assertEqual([vendor.pk for vendor in invited_candidates], [active.pk])

    def test_create_tender_rfqs_links_header_lines_skips_existing_and_audits(self):
        first = self._vendor("CREATE-A")
        second = self._vendor("CREATE-B")
        tender = self._tender("CREATE", line_count=2)

        first_batch = create_purchase_tender_rfqs(
            tender,
            vendor_ids=[first.pk],
            actor=self.actor,
        )
        second_batch = create_purchase_tender_rfqs(
            tender,
            vendor_ids=[first.pk, second.pk],
            actor=self.actor,
        )

        self.assertEqual(len(first_batch), 1)
        self.assertEqual(len(second_batch), 1)
        self.assertEqual(second_batch[0].vendor_id, second.pk)
        quotations = PurchaseQuotation.objects.filter(tender=tender).order_by("vendor_id")
        self.assertEqual(quotations.count(), 2)
        for quotation in quotations:
            self.assertEqual(quotation.status, PurchaseQuotation.Status.DRAFT)
            self.assertEqual(quotation.lines.count(), 2)
            self.assertEqual(
                set(quotation.lines.values_list("tender_line__tender_id", flat=True)),
                {tender.pk},
            )
        latest_audit = TenderAuditLog.objects.filter(tender=tender, action="create-rfq").first()
        self.assertEqual(latest_audit.details["vendor_ids"], [second.pk])
        self.assertEqual(latest_audit.details["skipped_existing_vendor_ids"], [first.pk])

    def test_foreign_or_ineligible_vendor_rolls_back_whole_rfq_batch(self):
        eligible = self._vendor("ROLLBACK-OK")
        other_company = Company.objects.create(code="TRFQ-OTHER", name="Other Tender RFQ")
        foreign = Contact.objects.create(
            company=other_company,
            code="TRFQ-FOREIGN",
            name="Foreign Tender RFQ Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        tender = self._tender("ROLLBACK")

        with self.assertRaisesMessage(ValidationError, "not eligible"):
            create_purchase_tender_rfqs(
                tender,
                vendor_ids=[eligible.pk, foreign.pk],
                actor=self.actor,
            )

        self.assertFalse(PurchaseQuotation.objects.filter(tender=tender).exists())
        self.assertFalse(TenderAuditLog.objects.filter(tender=tender, action="create-rfq").exists())

    def test_received_tender_rfq_becomes_bid_and_cannot_bypass_award_to_po(self):
        vendor = self._vendor("RECEIVE")
        tender = self._tender("RECEIVE", line_count=2)
        quotation = create_purchase_tender_rfqs(
            tender,
            vendor_ids=[vendor.pk],
            actor=self.actor,
        )[0]

        transition_purchase_quotation(quotation, "send", self.actor)
        transition_purchase_quotation(quotation, "receive", self.actor)

        quotation.refresh_from_db()
        bid = TenderBid.objects.get(tender=tender, vendor=vendor)
        self.assertEqual(quotation.status, PurchaseQuotation.Status.RECEIVED)
        self.assertEqual(bid.status, TenderBid.Status.SUBMITTED)
        self.assertEqual(bid.lines.count(), 2)
        self.assertEqual(bid.bid_amount, Decimal("402.00"))
        self.assertTrue(
            TenderAuditLog.objects.filter(tender=tender, action="receive-rfq-bid").exists()
        )
        with self.assertRaisesMessage(ValidationError, "cannot create a Purchase Order directly"):
            transition_purchase_quotation(quotation, "select", self.actor)

    def test_incomplete_tender_rfq_receive_rolls_back_status_and_bid(self):
        vendor = self._vendor("INCOMPLETE")
        tender = self._tender("INCOMPLETE", line_count=2)
        quotation = create_purchase_tender_rfqs(
            tender,
            vendor_ids=[vendor.pk],
            actor=self.actor,
        )[0]
        transition_purchase_quotation(quotation, "send", self.actor)
        last_line = quotation.lines.order_by("pk").last()
        quotation.lines.filter(pk=last_line.pk).update(tender_line=None)

        with self.assertRaisesMessage(ValidationError, "exactly one response"):
            transition_purchase_quotation(quotation, "receive", self.actor)

        quotation.refresh_from_db()
        self.assertEqual(quotation.status, PurchaseQuotation.Status.SENT)
        self.assertFalse(TenderBid.objects.filter(tender=tender, vendor=vendor).exists())

    def test_submit_quotation_action_ui_and_company_scope(self):
        vendor = self._vendor("UI")
        tender = self._tender("UI")
        create_purchase_tender_rfqs(
            tender,
            vendor_ids=[vendor.pk],
            actor=self.actor,
        )
        self.client.force_login(self.actor)

        document = self.client.get(reverse("entity_edit", args=["purchase-tenders", tender.pk]))
        workspace = self.client.get(reverse("purchase_tender_submit_quotation", args=[tender.pk]))

        self.assertContains(document, "Submit Quotation")
        self.assertContains(
            document,
            reverse("purchase_tender_submit_quotation", args=[tender.pk]),
        )
        self.assertContains(workspace, vendor.name)
        self.assertContains(workspace, "Open to Registered Vendors")
        self.assertContains(document, "Quotations")

        other_company = Company.objects.create(code="TRFQ-SCOPE", name="Other RFQ Scope")
        outsider = get_user_model().objects.create_user(
            username="tender-rfq-outsider",
            password="test-password",
        )
        outsider.groups.add(Group.objects.get_or_create(name="Purchase Manager")[0])
        Employee.objects.create(
            company=other_company,
            user=outsider,
            employee_id="TRFQ-OUTSIDER",
            name="Tender RFQ Outsider",
        )
        self.client.force_login(outsider)

        response = self.client.get(reverse("purchase_tender_submit_quotation", args=[tender.pk]))
        self.assertEqual(response.status_code, 404)

    def test_candidate_and_creation_queries_do_not_grow_with_vendor_or_line_count(self):
        self._vendor("QUERY-BASE")
        small = self._tender("QUERY-SMALL", line_count=2)
        with CaptureQueriesContext(connection) as small_candidate_queries:
            purchase_tender_rfq_candidates(small)

        for index in range(10):
            self._vendor(f"QUERY-{index}")
        with CaptureQueriesContext(connection) as grown_candidate_queries:
            _scope, candidates = purchase_tender_rfq_candidates(small)

        self.assertEqual(len(candidates), 11)
        self.assertLessEqual(len(grown_candidate_queries), len(small_candidate_queries))

        small_vendor = self._vendor("QUERY-CREATE-SMALL")
        large_vendor = self._vendor("QUERY-CREATE-LARGE")
        large = self._tender("QUERY-LARGE", line_count=12)
        with CaptureQueriesContext(connection) as small_create_queries:
            create_purchase_tender_rfqs(
                small,
                vendor_ids=[small_vendor.pk],
                actor=self.actor,
            )
        with CaptureQueriesContext(connection) as large_create_queries:
            created = create_purchase_tender_rfqs(
                large,
                vendor_ids=[large_vendor.pk],
                actor=self.actor,
            )

        self.assertEqual(created[0].lines.count(), 12)
        self.assertLessEqual(
            len(large_create_queries),
            len(small_create_queries) + 1,
            f"Tender RFQ creation queries grew from {len(small_create_queries)} "
            f"to {len(large_create_queries)}.",
        )
