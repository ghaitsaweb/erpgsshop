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

from core.models import Company, Contact, Employee, Product

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
    save_tender_award_allocations,
    score_purchase_tender_bids,
    transition_purchase_tender,
)
from .tender_award_allocation import build_tender_award_allocation_workspace
from .tender_bid_comparison import build_tender_bid_comparison


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
        tender = transition_purchase_tender(tender, "award", self.actor)
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

    def test_manual_award_matrix_creates_updates_and_unlinks_with_audit(self):
        tender, _line, (first_bid, first_line), (second_bid, second_line) = (
            self._two_ranked_bids(
                "MATRIX-AUDIT",
                strategy=AwardStrategy.Strategy.MANUAL,
            )
        )
        tender = transition_purchase_tender(tender, "award", self.actor)

        initial = build_tender_award_allocation_workspace(tender)
        self.assertTrue(initial["initial_defaults"])
        self.assertEqual(
            [candidate["allocated_quantity"] for candidate in initial["line_rows"][0]["candidates"]],
            [Decimal("10"), Decimal("10")],
        )

        save_tender_award_allocations(
            tender,
            {first_line.pk: Decimal("6"), second_line.pk: Decimal("4")},
            self.actor,
        )

        allocations = {
            row.bid_line_id: row.allocated_quantity
            for row in TenderAwardAllocation.objects.filter(tender=tender)
        }
        self.assertEqual(
            allocations,
            {first_line.pk: Decimal("6.000"), second_line.pk: Decimal("4.000")},
        )
        first_bid.refresh_from_db()
        second_bid.refresh_from_db()
        self.assertEqual(first_bid.status, TenderBid.Status.AWARDED)
        self.assertEqual(second_bid.status, TenderBid.Status.AWARDED)
        self.assertEqual(first_bid.allocation_percent, Decimal("60.00"))
        self.assertEqual(second_bid.allocation_percent, Decimal("40.00"))
        create_logs = TenderAuditLog.objects.filter(
            tender=tender,
            action="award-allocation-create",
        )
        self.assertEqual(create_logs.count(), 2)
        self.assertEqual(
            {log.details["source"] for log in create_logs},
            {"allocation-matrix"},
        )

        save_tender_award_allocations(
            tender,
            {first_line.pk: Decimal("10"), second_line.pk: Decimal("0")},
            self.actor,
        )

        allocation = TenderAwardAllocation.objects.get(tender=tender)
        self.assertEqual(allocation.bid_line_id, first_line.pk)
        self.assertEqual(allocation.allocated_quantity, Decimal("10.000"))
        write_log = TenderAuditLog.objects.get(
            tender=tender,
            action="award-allocation-write",
        )
        unlink_log = TenderAuditLog.objects.get(
            tender=tender,
            action="award-allocation-unlink",
        )
        self.assertEqual(write_log.details["old_value"], "6.000")
        self.assertEqual(write_log.details["new_value"], "10.000")
        self.assertEqual(unlink_log.details["old_value"], "4.000")
        self.assertEqual(unlink_log.details["new_value"], "")

        audit_count = TenderAuditLog.objects.filter(tender=tender).count()
        save_tender_award_allocations(
            tender,
            {first_line.pk: Decimal("10"), second_line.pk: Decimal("0")},
            self.actor,
        )
        self.assertEqual(TenderAuditLog.objects.filter(tender=tender).count(), audit_count)

    def test_manual_award_matrix_rejects_incomplete_or_foreign_payload_atomically(self):
        tender, _line, (_first_bid, first_line), (_second_bid, second_line) = (
            self._two_ranked_bids(
                "MATRIX-ROLLBACK",
                strategy=AwardStrategy.Strategy.MANUAL,
            )
        )
        transition_purchase_tender(tender, "award", self.actor)
        other_tender, _other_line, (_other_bid, foreign_line), _other_second = (
            self._two_ranked_bids(
                "MATRIX-FOREIGN",
                strategy=AwardStrategy.Strategy.MANUAL,
            )
        )
        transition_purchase_tender(other_tender, "award", self.actor)

        with self.assertRaisesMessage(ValidationError, "must equal the required quantity"):
            save_tender_award_allocations(
                tender,
                {first_line.pk: Decimal("6"), second_line.pk: Decimal("3")},
                self.actor,
            )
        with self.assertRaisesMessage(ValidationError, "without adding other Bid lines"):
            save_tender_award_allocations(
                tender,
                {
                    first_line.pk: Decimal("6"),
                    second_line.pk: Decimal("4"),
                    foreign_line.pk: Decimal("1"),
                },
                self.actor,
            )

        self.assertFalse(TenderAwardAllocation.objects.filter(tender=tender).exists())
        self.assertFalse(
            TenderAuditLog.objects.filter(
                tender=tender,
                action__startswith="award-allocation-",
            ).exists()
        )

    def test_bid_comparison_award_opens_manual_allocation_workspace(self):
        tender, _line, (_first_bid, first_line), (_second_bid, second_line) = (
            self._two_ranked_bids(
                "MATRIX-UI",
                strategy=AwardStrategy.Strategy.MANUAL,
            )
        )
        self.client.force_login(self.actor)

        started = self.client.post(reverse("purchase_tender_start_award", args=[tender.pk]))

        self.assertRedirects(
            started,
            reverse("purchase_tender_award_allocation", args=[tender.pk]),
            fetch_redirect_response=False,
        )
        tender.refresh_from_db()
        self.assertEqual(tender.status, PurchaseTender.Status.AWARDED)
        workspace = self.client.get(reverse("purchase_tender_award_allocation", args=[tender.pk]))
        self.assertEqual(workspace.status_code, 200)
        self.assertContains(workspace, "Bid Allocation Matrix")
        self.assertContains(workspace, "Initial HMX defaults")
        self.assertContains(workspace, f'allocation_{first_line.pk}')
        self.assertContains(workspace, f'allocation_{second_line.pk}')

        saved = self.client.post(
            reverse("purchase_tender_award_allocation", args=[tender.pk]),
            {
                f"allocation_{first_line.pk}": "7",
                f"allocation_{second_line.pk}": "3",
            },
        )
        self.assertRedirects(
            saved,
            reverse("purchase_tender_award_allocation", args=[tender.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(TenderAwardAllocation.objects.filter(tender=tender).count(), 2)

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

    def test_bid_comparison_payload_matches_hmx_workspace_contract(self):
        tender, line, (first_bid, _first_line), (second_bid, _second_line) = (
            self._two_ranked_bids("COMPARISON")
        )
        tender.refresh_from_db()

        comparison = build_tender_bid_comparison(tender)

        self.assertEqual(comparison["code"], tender.number)
        self.assertEqual(comparison["award_action"], "award")
        self.assertEqual(comparison["vendor_count"], 2)
        self.assertEqual(comparison["line_count"], 1)
        self.assertEqual(
            [vendor["vendor_id"] for vendor in comparison["vendors"]],
            [first_bid.vendor_id, second_bid.vendor_id],
        )
        self.assertTrue(comparison["vendors"][0]["recommended"])
        self.assertEqual(comparison["vendors"][0]["total_quote"], Decimal("1000"))
        self.assertEqual(comparison["vendors"][0]["avg_lead_time_display"], "10 Days")
        row = comparison["line_rows"][0]
        self.assertEqual(row["line_id"], line.pk)
        self.assertEqual([cell["quoted"] for cell in row["cells"]], [True, True])
        self.assertEqual([cell["is_lowest_price"] for cell in row["cells"]], [True, False])
        self.assertEqual([cell["score"] for cell in row["cells"]], [Decimal("100.00"), Decimal("90.00")])

    def test_bid_comparison_rejects_pre_selection_state(self):
        tender, _line = self._tender(
            "COMPARISON-DRAFT",
            status=PurchaseTender.Status.DRAFT,
        )

        with self.assertRaisesMessage(ValidationError, "only available from Bid Selection"):
            build_tender_bid_comparison(tender)

    def test_bid_comparison_query_count_does_not_grow_with_vendor_count(self):
        def prepared(suffix, vendor_count):
            tender, line = self._tender(suffix)
            for index in range(vendor_count):
                self._bid(
                    tender,
                    line,
                    f"{suffix}-{index}",
                    unit_price=Decimal("100") + index,
                )
            transition_purchase_tender(tender, "evaluate", self.actor)
            tender.refresh_from_db()
            return tender

        small = prepared("CMP-Q-S", 2)
        large = prepared("CMP-Q-L", 12)
        with CaptureQueriesContext(connection) as small_queries:
            build_tender_bid_comparison(small)
        with CaptureQueriesContext(connection) as large_queries:
            comparison = build_tender_bid_comparison(large)

        self.assertEqual(comparison["vendor_count"], 12)
        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Comparison queries grew from {len(small_queries)} to {len(large_queries)}.",
        )

    def test_award_allocation_workspace_query_count_does_not_grow_with_vendor_count(self):
        def prepared(suffix, vendor_count):
            tender, line = self._tender(
                suffix,
                strategy=AwardStrategy.Strategy.MANUAL,
            )
            for index in range(vendor_count):
                self._bid(
                    tender,
                    line,
                    f"{suffix}-{index}",
                    unit_price=Decimal("100") + index,
                )
            transition_purchase_tender(tender, "evaluate", self.actor)
            transition_purchase_tender(tender, "award", self.actor)
            tender.refresh_from_db()
            return tender

        small = prepared("MATRIX-Q-S", 2)
        large = prepared("MATRIX-Q-L", 12)
        with CaptureQueriesContext(connection) as small_queries:
            build_tender_award_allocation_workspace(small)
        with CaptureQueriesContext(connection) as large_queries:
            matrix = build_tender_award_allocation_workspace(large)

        self.assertEqual(matrix["vendor_count"], 12)
        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Allocation workspace queries grew from {len(small_queries)} to {len(large_queries)}.",
        )

    def test_bid_comparison_workspace_and_document_action_are_available(self):
        tender, _line, _first, _second = self._two_ranked_bids("COMPARISON-UI")
        tender.refresh_from_db()
        self.client.force_login(self.actor)

        document = self.client.get(reverse("entity_edit", args=["purchase-tenders", tender.pk]))
        comparison = self.client.get(reverse("purchase_tender_bid_comparison", args=[tender.pk]))

        self.assertContains(document, "Bid Comparison")
        self.assertContains(document, reverse("purchase_tender_bid_comparison", args=[tender.pk]))
        self.assertEqual(comparison.status_code, 200)
        self.assertContains(comparison, "Bid Comparison Analysis")
        self.assertContains(comparison, "Tender Vendor COMPARISON-UI-A")
        self.assertContains(comparison, "Lowest Price")
        self.assertContains(comparison, "Export PDF")
        self.assertContains(comparison, "Export Excel")
        self.assertContains(comparison, "Award Contract")

    def test_bid_comparison_workspace_preserves_company_scope(self):
        tender, _line, _first, _second = self._two_ranked_bids("COMPARISON-SCOPE")
        tender.refresh_from_db()
        other_company = Company.objects.create(code="TSP-OTHER", name="Other Tender Company")
        outsider = get_user_model().objects.create_user(
            username="tender-comparison-outsider",
            password="test-password",
        )
        outsider.groups.add(Group.objects.get(name="Purchase User"))
        Employee.objects.create(
            company=other_company,
            user=outsider,
            employee_id="TSP-OUTSIDER",
            name="Tender Comparison Outsider",
        )
        self.client.force_login(outsider)

        response = self.client.get(reverse("purchase_tender_bid_comparison", args=[tender.pk]))

        self.assertEqual(response.status_code, 404)

    def test_award_allocation_workspace_preserves_company_scope(self):
        tender, _line, _first, _second = self._two_ranked_bids(
            "MATRIX-SCOPE",
            strategy=AwardStrategy.Strategy.MANUAL,
        )
        transition_purchase_tender(tender, "award", self.actor)
        other_company = Company.objects.create(code="TSP-MATRIX-OTHER", name="Other Matrix Company")
        outsider = get_user_model().objects.create_user(
            username="tender-matrix-outsider",
            password="test-password",
        )
        outsider.groups.add(Group.objects.get(name="Purchase Manager"))
        Employee.objects.create(
            company=other_company,
            user=outsider,
            employee_id="TSP-MATRIX-OUTSIDER",
            name="Tender Matrix Outsider",
        )
        self.client.force_login(outsider)

        response = self.client.get(reverse("purchase_tender_award_allocation", args=[tender.pk]))

        self.assertEqual(response.status_code, 404)

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
        self.assertEqual(len(audit.details["allocation_changes"]), 1)
        allocation_change = audit.details["allocation_changes"][0]
        self.assertEqual(allocation_change["action"], "create")
        self.assertEqual(allocation_change["old_value"], "")
        self.assertEqual(allocation_change["new_value"], "10.000")
