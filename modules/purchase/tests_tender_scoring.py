from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Contact, Product

from .models import (
    AwardStrategy,
    PurchaseSetting,
    PurchaseTender,
    PurchaseTenderLine,
    TenderAwardAllocation,
    TenderAuditLog,
    TenderBid,
    TenderBidLine,
    VendorProfile,
)
from .services import (
    apply_tender_award_strategy,
    score_purchase_tender_bids,
    transition_purchase_tender,
)


class TenderScoringParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="tender-scoring-admin",
            email="tender-scoring@example.com",
            password="test-password",
        )
        self.company = Company.objects.create(code="TSP", name="Tender Scoring Parity")
        PurchaseSetting.objects.create(
            company=self.company,
            require_vendor_prequalification=False,
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="TSP-PRODUCT",
            name="Tender Scored Product",
            purchase_price=Decimal("100"),
        )

    def _strategy(self, suffix, strategy=AwardStrategy.Strategy.SINGLE):
        return AwardStrategy.objects.create(
            company=self.company,
            name=f"Tender strategy {suffix}",
            strategy=strategy,
        )

    def _tender(
        self,
        suffix,
        *,
        strategy=AwardStrategy.Strategy.SINGLE,
        status=PurchaseTender.Status.BID_SUBMISSION,
        quantity=Decimal("10"),
    ):
        tender = PurchaseTender.objects.create(
            company=self.company,
            number=f"TSP-{suffix}",
            title=f"Tender scoring {suffix}",
            category=PurchaseTender.Category.OPEX,
            tender_type=PurchaseTender.TenderType.OPEN,
            method=PurchaseTender.Method.OTE,
            award_strategy=self._strategy(suffix, strategy),
            bid_start_date=timezone.localdate(),
            close_date=timezone.localdate() + timedelta(days=30),
            status=status,
        )
        line = PurchaseTenderLine.objects.create(
            tender=tender,
            product=self.product,
            description=self.product.name,
            quantity=quantity,
            target_unit_price=Decimal("100"),
            delivery_date=timezone.localdate() + timedelta(days=10),
        )
        return tender, line

    def _bid(
        self,
        tender,
        tender_line,
        suffix,
        *,
        unit_price,
        quantity=Decimal("10"),
        delivery_days=10,
        status=TenderBid.Status.SUBMITTED,
    ):
        vendor = Contact.objects.create(
            company=self.company,
            code=f"TSP-V-{suffix}",
            name=f"Tender Vendor {suffix}",
            contact_type=Contact.ContactType.VENDOR,
        )
        VendorProfile.objects.create(
            company=self.company,
            partner=vendor,
            payment_terms="30 days",
            status=VendorProfile.Status.ACTIVE,
        )
        bid = TenderBid.objects.create(
            tender=tender,
            vendor=vendor,
            submitted_at=timezone.now(),
            status=status,
        )
        bid_line = TenderBidLine.objects.create(
            bid=bid,
            tender_line=tender_line,
            offered_quantity=quantity,
            unit_price=unit_price,
            delivery_days=delivery_days,
            compliant=True,
        )
        return bid, bid_line

    def _two_ranked_bids(self, suffix, *, strategy=AwardStrategy.Strategy.SINGLE):
        tender, line = self._tender(suffix, strategy=strategy)
        first = self._bid(tender, line, f"{suffix}-A", unit_price=Decimal("100"))
        second = self._bid(tender, line, f"{suffix}-B", unit_price=Decimal("125"))
        transition_purchase_tender(tender, "evaluate", self.actor)
        return tender, line, first, second

    def test_evaluation_weights_must_total_exactly_one_hundred_before_submit(self):
        tender, _line = self._tender(
            "WEIGHT",
            status=PurchaseTender.Status.DRAFT,
        )
        tender.evaluation_history_weight = 9
        tender.save(update_fields=["evaluation_history_weight"])

        with self.assertRaisesMessage(ValidationError, "must total exactly 100%"):
            transition_purchase_tender(tender, "submit", self.actor)

        tender.refresh_from_db()
        self.assertEqual(tender.status, PurchaseTender.Status.DRAFT)

    def test_scoring_matches_hmx_weighted_price_example_and_persists_breakdown(self):
        tender, _line, (first_bid, first_line), (second_bid, second_line) = (
            self._two_ranked_bids("SCORE")
        )

        first_bid.refresh_from_db()
        second_bid.refresh_from_db()
        first_line.refresh_from_db()
        second_line.refresh_from_db()
        self.assertEqual(first_bid.price_score, Decimal("100.00"))
        self.assertEqual(first_bid.total_score, Decimal("100.00"))
        self.assertEqual(first_bid.rank, 1)
        self.assertEqual(second_bid.price_score, Decimal("80.00"))
        self.assertEqual(second_bid.total_score, Decimal("90.00"))
        self.assertEqual(second_bid.rank, 2)
        self.assertEqual(first_line.total_score, Decimal("100.00"))
        self.assertEqual(first_line.rank, 1)
        self.assertEqual(second_line.total_score, Decimal("90.00"))
        self.assertEqual(second_line.rank, 2)
        self.assertIsNotNone(first_bid.scored_at)
        tender.refresh_from_db()
        self.assertEqual(tender.status, PurchaseTender.Status.BID_SELECTION)

    def test_single_rank_strategy_uses_product_line_rank_not_vendor_average(self):
        tender, first_tender_line = self._tender("LINE-RANK")
        first_bid, first_bid_line = self._bid(
            tender,
            first_tender_line,
            "LINE-RANK-A",
            unit_price=Decimal("100"),
        )
        second_bid, second_bid_line = self._bid(
            tender,
            first_tender_line,
            "LINE-RANK-B",
            unit_price=Decimal("125"),
        )
        second_product = Product.objects.create(
            company=self.company,
            sku="TSP-LINE-RANK-PRODUCT",
            name="Tender Line Rank Product",
            purchase_price=Decimal("100"),
        )
        second_tender_line = PurchaseTenderLine.objects.create(
            tender=tender,
            product=second_product,
            description=second_product.name,
            quantity=Decimal("10"),
            target_unit_price=Decimal("100"),
            delivery_date=timezone.localdate() + timedelta(days=10),
        )
        first_second_line = TenderBidLine.objects.create(
            bid=first_bid,
            tender_line=second_tender_line,
            offered_quantity=Decimal("10"),
            unit_price=Decimal("150"),
            delivery_days=10,
        )
        second_second_line = TenderBidLine.objects.create(
            bid=second_bid,
            tender_line=second_tender_line,
            offered_quantity=Decimal("10"),
            unit_price=Decimal("100"),
            delivery_days=10,
        )

        transition_purchase_tender(tender, "evaluate", self.actor)
        transition_purchase_tender(tender, "award", self.actor)
        apply_tender_award_strategy(tender, self.actor)

        for bid_line in (
            first_bid_line,
            second_bid_line,
            first_second_line,
            second_second_line,
        ):
            bid_line.refresh_from_db()
        allocations = {
            allocation.tender_line_id: allocation.bid_line_id
            for allocation in TenderAwardAllocation.objects.filter(tender=tender)
        }
        self.assertEqual(first_bid_line.rank, 1)
        self.assertEqual(second_bid_line.rank, 2)
        self.assertEqual(first_second_line.rank, 2)
        self.assertEqual(second_second_line.rank, 1)
        self.assertEqual(allocations, {
            first_tender_line.pk: first_bid_line.pk,
            second_tender_line.pk: second_second_line.pk,
        })

    def test_single_rank_strategy_allocates_the_highest_rank_and_creates_response(self):
        tender, line, (first_bid, _), (second_bid, _) = self._two_ranked_bids("SINGLE")
        transition_purchase_tender(tender, "award", self.actor)

        applied = apply_tender_award_strategy(tender, self.actor)

        allocation = TenderAwardAllocation.objects.get(tender=tender)
        first_bid.refresh_from_db()
        second_bid.refresh_from_db()
        self.assertEqual(allocation.tender_line, line)
        self.assertEqual(allocation.bid_line.bid, first_bid)
        self.assertEqual(allocation.allocated_quantity, Decimal("10"))
        self.assertEqual(allocation.award_strategy, TenderAwardAllocation.Strategy.SINGLE_RANK)
        self.assertEqual(first_bid.status, TenderBid.Status.AWARDED)
        self.assertEqual(second_bid.status, TenderBid.Status.SHORTLISTED)
        self.assertEqual(first_bid.allocation_percent, Decimal("100.00"))
        self.assertEqual(applied.award_responses.count(), 1)

    def test_split_ratio_strategy_uses_hmx_60_30_10_then_rank_fill(self):
        tender, _line, (first_bid, _), (second_bid, _) = self._two_ranked_bids(
            "SPLIT",
            strategy=AwardStrategy.Strategy.SPLIT_RATIO,
        )
        transition_purchase_tender(tender, "award", self.actor)

        apply_tender_award_strategy(tender, self.actor)

        allocations = {
            allocation.bid_line.bid_id: allocation.allocated_quantity
            for allocation in TenderAwardAllocation.objects.filter(tender=tender).select_related(
                "bid_line"
            )
        }
        first_bid.refresh_from_db()
        second_bid.refresh_from_db()
        self.assertEqual(allocations, {
            first_bid.pk: Decimal("7.000"),
            second_bid.pk: Decimal("3.000"),
        })
        self.assertEqual(first_bid.allocation_percent, Decimal("70.00"))
        self.assertEqual(second_bid.allocation_percent, Decimal("30.00"))
        self.assertEqual(tender.award_responses.count(), 2)

    def test_insufficient_capacity_rolls_back_strategy_allocations_and_bid_updates(self):
        tender, line = self._tender("SHORT", strategy=AwardStrategy.Strategy.SPLIT_RATIO)
        first_bid, _ = self._bid(
            tender,
            line,
            "SHORT-A",
            unit_price=Decimal("100"),
            quantity=Decimal("4"),
        )
        second_bid, _ = self._bid(
            tender,
            line,
            "SHORT-B",
            unit_price=Decimal("125"),
            quantity=Decimal("4"),
        )
        transition_purchase_tender(tender, "evaluate", self.actor)
        transition_purchase_tender(tender, "award", self.actor)

        with self.assertRaisesMessage(ValidationError, "short by 2"):
            apply_tender_award_strategy(tender, self.actor)

        first_bid.refresh_from_db()
        second_bid.refresh_from_db()
        self.assertFalse(TenderAwardAllocation.objects.filter(tender=tender).exists())
        self.assertEqual(first_bid.status, TenderBid.Status.SUBMITTED)
        self.assertEqual(second_bid.status, TenderBid.Status.SUBMITTED)
        self.assertFalse(tender.award_responses.exists())

    def test_manual_strategy_preserves_existing_line_allocations(self):
        tender, line = self._tender(
            "MANUAL",
            strategy=AwardStrategy.Strategy.MANUAL,
            status=PurchaseTender.Status.AWARDED,
        )
        bid, bid_line = self._bid(
            tender,
            line,
            "MANUAL-A",
            unit_price=Decimal("115"),
            status=TenderBid.Status.AWARDED,
        )
        bid.allocation_percent = Decimal("100")
        bid.save(update_fields=["allocation_percent"])
        allocation = TenderAwardAllocation.objects.create(
            tender=tender,
            tender_line=line,
            bid_line=bid_line,
            allocated_quantity=Decimal("10"),
            unit_price=Decimal("115"),
            award_strategy=TenderAwardAllocation.Strategy.MANUAL,
        )

        apply_tender_award_strategy(tender, self.actor)

        self.assertEqual(
            list(tender.award_allocations.values_list("pk", flat=True)),
            [allocation.pk],
        )
        self.assertEqual(tender.award_responses.count(), 1)

    def test_scoring_query_count_does_not_grow_with_vendor_count(self):
        def prepared(suffix, vendor_count):
            tender, line = self._tender(suffix)
            for index in range(vendor_count):
                self._bid(
                    tender,
                    line,
                    f"{suffix}-{index}",
                    unit_price=Decimal("100") + index,
                )
            return tender

        small = prepared("QUERY-SMALL", 2)
        large = prepared("QUERY-LARGE", 12)
        with CaptureQueriesContext(connection) as small_queries:
            score_purchase_tender_bids(small, self.actor)
        with CaptureQueriesContext(connection) as large_queries:
            score_purchase_tender_bids(large, self.actor)

        self.assertLessEqual(len(large_queries), len(small_queries))

    def test_document_page_shows_scoring_breakdown_and_apply_strategy_action(self):
        tender, _line, _first, _second = self._two_ranked_bids("UI")
        transition_purchase_tender(tender, "award", self.actor)
        self.client.force_login(self.actor)

        response = self.client.get(reverse("entity_edit", args=["purchase-tenders", tender.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vendor ranking and award criteria")
        self.assertContains(response, "Tender Vendor UI-A")
        self.assertContains(response, "Apply Award Strategy")
        self.assertContains(response, "<strong>Pricing</strong>", html=True)
        self.assertContains(response, "50%")

        action_response = self.client.post(
            reverse(
                "document_action",
                args=["purchase-tenders", tender.pk, "apply-award-strategy"],
            )
        )

        self.assertEqual(action_response.status_code, 302)
        audit = TenderAuditLog.objects.get(
            tender=tender,
            action="apply-award-strategy",
        )
        self.assertEqual(audit.details["award_strategy"], "single_rank")
        self.assertEqual(audit.details["allocation_count"], 1)
        self.assertEqual(len(audit.details["winning_vendor_ids"]), 1)
