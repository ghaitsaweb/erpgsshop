from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import ForeignKey, OneToOneField
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Contact, Invoice, Product, PurchaseOrder, PurchaseOrderLine, StockMove, Warehouse
from core.smart_actions import SMART_ACTIONS
from core.views import ENTITY_CONFIG

from .entity_config import PURCHASE_ENTITY_CONFIG

from .models import (
    AwardStrategy,
    BlanketOrder,
    DirectPurchase,
    GoodsReceipt,
    PurchaseOrderControl,
    PurchaseQuotation,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseSetting,
    PurchaseTender,
    TenderAwardAllocation,
    TenderBid,
    TenderBidLine,
    VendorPricelist,
    VendorPricelistAmendment,
    VendorPricelistAmendmentLine,
    VendorPricelistItem,
    VendorPricelistTier,
    VendorProfile,
)
from .services import (
    _vendor_pricelist_prices,
    approve_purchase_request_lines,
    create_blanket_order_rfq,
    create_purchase_request_blanket_order,
    create_purchase_request_direct_purchase,
    create_purchase_request_rfqs,
    create_purchase_request_re_request,
    create_purchase_request_tender,
    confirm_purchase_order,
    purchase_request_before_workflow,
    transition_blanket_order,
    transition_direct_purchase,
    transition_purchase_quotation,
    transition_purchase_tender,
    transition_tender_award_response,
    transition_vendor_pricelist,
    transition_vendor_pricelist_amendment,
)


class PurchaseSourcingParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="purchase-sourcing-admin",
            password="test-password",
            email="purchase-sourcing@example.com",
        )
        self.company = Company.objects.create(code="PUR-SRC", name="Purchase Sourcing")
        self.warehouse = Warehouse.objects.create(company=self.company, code="SRC-WH", name="Sourcing Warehouse")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="SRC-VEN-1",
            name="Sourcing Vendor One",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.other_vendor = Contact.objects.create(
            company=self.company,
            code="SRC-VEN-2",
            name="Sourcing Vendor Two",
            contact_type=Contact.ContactType.VENDOR,
        )
        PurchaseSetting.objects.create(
            company=self.company,
            require_vendor_prequalification=False,
        )
        self.award_strategy = AwardStrategy.objects.create(
            company=self.company,
            name="Lowest compliant offer",
            strategy=AwardStrategy.Strategy.SINGLE,
        )

    def _request(self, number, line_count=1, quantity=Decimal("10")):
        request = PurchaseRequest.objects.create(
            company=self.company,
            number=number,
            requestor=self.actor,
            request_date=timezone.localdate(),
            required_date=timezone.localdate(),
            destination_warehouse=self.warehouse,
            justification="Sourcing test",
            status=PurchaseRequest.Status.APPROVED,
        )
        products = Product.objects.bulk_create(
            [
                Product(
                    company=self.company,
                    sku=f"{number}-P-{index}",
                    name=f"{number} Product {index}",
                    purchase_price=Decimal("25.00"),
                )
                for index in range(line_count)
            ]
        )
        lines = PurchaseRequestLine.objects.bulk_create(
            [
                PurchaseRequestLine(
                    request=request,
                    product=product,
                    description=product.name,
                    quantity=quantity,
                    approved_quantity=quantity,
                    estimated_unit_price=Decimal("25.00"),
                    line_status=PurchaseRequestLine.Status.APPROVED,
                )
                for product in products
            ]
        )
        return request, lines

    def _allocations(self, lines, quantity, vendors=None):
        vendors = vendors or [self.vendor]
        return [
            {
                "line_id": line.pk,
                "quantity": quantity,
                "vendor_ids": [vendor.pk for vendor in vendors],
            }
            for line in lines
        ]

    def test_every_purchase_cross_model_link_has_two_way_smart_actions(self):
        purchase_entities = set(PURCHASE_ENTITY_CONFIG) | {"purchases", "invoices", "stock-moves"}
        model_to_entities = {}
        for entity, config in ENTITY_CONFIG.items():
            model_to_entities.setdefault(config["model"], []).append(entity)
        action_pairs = {(definition.source_entity, definition.target_entity) for definition in SMART_ACTIONS}
        missing = []
        for source_entity in sorted(purchase_entities):
            source_config = ENTITY_CONFIG[source_entity]
            for field in source_config["model"]._meta.concrete_fields:
                if not isinstance(field, (ForeignKey, OneToOneField)):
                    continue
                for target_entity in model_to_entities.get(field.remote_field.model, []):
                    if target_entity not in purchase_entities:
                        continue
                    if (source_entity, target_entity) not in action_pairs:
                        missing.append(f"{source_entity}.{field.name} -> {target_entity}")
                    if (target_entity, source_entity) not in action_pairs:
                        missing.append(f"{target_entity} -> {source_entity}.{field.name}")
        self.assertEqual(missing, [])

    def test_vendor_pricelist_tiers_moq_and_amendment_follow_hmx_lifecycle(self):
        profile = VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor,
            status=VendorProfile.Status.ACTIVE,
        )
        request, lines = self._request("PR-PRICE-001", quantity=Decimal("10"))
        pricelist = VendorPricelist.objects.create(
            company=self.company,
            name="Primary Vendor Prices",
            code="VPL-001",
            vendor=profile,
            date_start=timezone.localdate(),
            date_end=timezone.localdate() + timedelta(days=30),
        )
        item = VendorPricelistItem.objects.create(
            pricelist=pricelist,
            product=lines[0].product,
            uom="Unit",
            unit_price=Decimal("32"),
            lead_time_days=Decimal("3"),
            enforce_moq=True,
        )
        first_tier = VendorPricelistTier.objects.create(
            item=item,
            sequence=1,
            minimum_quantity=Decimal("5"),
            maximum_quantity=Decimal("9"),
            tier_price=Decimal("30"),
        )
        second_tier = VendorPricelistTier.objects.create(
            item=item,
            sequence=2,
            minimum_quantity=Decimal("10"),
            maximum_quantity=Decimal("0"),
            tier_price=Decimal("1"),
            discount_from_tier=first_tier,
            discount_formula="10%",
        )

        transition_vendor_pricelist(pricelist, "submit", self.actor)
        transition_vendor_pricelist(pricelist, "approve", self.actor)
        blanket = create_purchase_request_blanket_order(
            request,
            vendor_id=self.vendor.pk,
            allocations=self._allocations(lines, Decimal("10")),
            date_end=timezone.localdate() + timedelta(days=30),
            actor=self.actor,
        )

        pricelist.refresh_from_db()
        self.assertEqual(pricelist.status, VendorPricelist.Status.ACTIVE)
        self.assertEqual(blanket.lines.get().unit_price, Decimal("27"))

        order = PurchaseOrder.objects.create(
            company=self.company,
            number="PO-MOQ-001",
            vendor=self.vendor,
            warehouse=self.warehouse,
        )
        PurchaseOrderLine.objects.create(
            order=order,
            product=lines[0].product,
            quantity=Decimal("4"),
            unit_price=Decimal("30"),
        )
        with self.assertRaisesMessage(ValidationError, "below the vendor MOQ"):
            confirm_purchase_order(order, self.actor)

        amendment = VendorPricelistAmendment.objects.create(
            pricelist=pricelist,
            reason="Annual vendor price revision",
        )
        VendorPricelistAmendmentLine.objects.create(
            amendment=amendment,
            target_type=VendorPricelistAmendmentLine.TargetType.TIER,
            tier=first_tier,
            field_key="tier_price",
            old_value="30.00",
            new_value="40.00",
        )
        transition_vendor_pricelist_amendment(amendment, "submit", self.actor)
        transition_vendor_pricelist_amendment(amendment, "approve", self.actor)

        amendment.refresh_from_db()
        first_tier.refresh_from_db()
        second_tier.refresh_from_db()
        self.assertEqual(amendment.status, "approved")
        self.assertEqual(amendment.approver, self.actor)
        self.assertEqual(first_tier.tier_price, Decimal("40"))
        self.assertEqual(second_tier.effective_price, Decimal("36"))

    def test_vendor_pricelist_lookup_query_count_does_not_grow_with_lines(self):
        profile = VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor,
            status=VendorProfile.Status.ACTIVE,
        )
        _request, lines = self._request("PR-PRICE-SCALE", line_count=20, quantity=Decimal("10"))
        pricelist = VendorPricelist.objects.create(
            company=self.company,
            vendor=profile,
            name="Scale-safe prices",
            status=VendorPricelist.Status.ACTIVE,
        )
        items = VendorPricelistItem.objects.bulk_create(
            [
                VendorPricelistItem(
                    pricelist=pricelist,
                    product=line.product,
                    unit_price=Decimal("30"),
                    lead_time_days=Decimal("2"),
                )
                for line in lines
            ]
        )
        VendorPricelistTier.objects.bulk_create(
            [
                VendorPricelistTier(
                    item=item,
                    sequence=1,
                    minimum_quantity=Decimal("1"),
                    tier_price=Decimal("25"),
                )
                for item in items
            ]
        )

        with CaptureQueriesContext(connection) as one_line_queries:
            _vendor_pricelist_prices(
                self.company.pk,
                self.vendor.pk,
                {lines[0]: Decimal("10")},
            )
        with CaptureQueriesContext(connection) as many_line_queries:
            prices = _vendor_pricelist_prices(
                self.company.pk,
                self.vendor.pk,
                {line: Decimal("10") for line in lines},
            )

        self.assertEqual(len(prices), 20)
        self.assertLessEqual(len(many_line_queries), len(one_line_queries))

    def test_partial_then_full_rfq_tracks_line_progress_and_provenance(self):
        request, lines = self._request("PR-RFQ-001")
        first = create_purchase_request_rfqs(
            request,
            allocations=self._allocations(lines, Decimal("4")),
            response_deadline=timezone.localdate(),
            actor=self.actor,
        )

        request.refresh_from_db()
        lines[0].refresh_from_db()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].status, PurchaseQuotation.Status.DRAFT)
        self.assertEqual(first[0].warehouse, self.warehouse)
        self.assertEqual(first[0].lines.get().request_line, lines[0])
        self.assertEqual(lines[0].rfq_quantity, Decimal("4"))
        self.assertEqual(lines[0].remaining_to_source, Decimal("6"))
        self.assertEqual(lines[0].line_status, PurchaseRequestLine.Status.PARTIALLY_RFQ)
        self.assertEqual(request.status, PurchaseRequest.Status.IN_PROGRESS)

        create_purchase_request_rfqs(
            request,
            allocations=self._allocations(lines, Decimal("6")),
            response_deadline=timezone.localdate(),
            actor=self.actor,
        )
        lines[0].refresh_from_db()
        self.assertEqual(lines[0].rfq_quantity, Decimal("10"))
        self.assertEqual(lines[0].remaining_to_source, Decimal("0"))
        self.assertEqual(lines[0].line_status, PurchaseRequestLine.Status.RFQ)

    def test_rfq_over_conversion_rolls_back(self):
        request, lines = self._request("PR-RFQ-OVER", quantity=Decimal("5"))
        with self.assertRaisesMessage(ValidationError, "cannot exceed remaining quantity"):
            create_purchase_request_rfqs(
                request,
                allocations=self._allocations(lines, Decimal("6")),
                response_deadline=timezone.localdate(),
                actor=self.actor,
            )
        lines[0].refresh_from_db()
        request.refresh_from_db()
        self.assertEqual(lines[0].rfq_quantity, Decimal("0"))
        self.assertEqual(request.status, PurchaseRequest.Status.APPROVED)
        self.assertFalse(PurchaseQuotation.objects.filter(request=request).exists())

    def test_multi_vendor_conversion_creates_one_draft_rfq_per_vendor(self):
        request, lines = self._request("PR-RFQ-MULTI")
        quotations = create_purchase_request_rfqs(
            request,
            allocations=self._allocations(lines, Decimal("3"), [self.vendor, self.other_vendor]),
            response_deadline=timezone.localdate(),
            actor=self.actor,
        )

        lines[0].refresh_from_db()
        self.assertEqual(len(quotations), 2)
        self.assertEqual({quote.vendor_id for quote in quotations}, {self.vendor.pk, self.other_vendor.pk})
        self.assertEqual(lines[0].rfq_quantity, Decimal("6"))

    def test_select_received_rfq_creates_linked_draft_po_and_order_progress(self):
        request, lines = self._request("PR-RFQ-SELECT", quantity=Decimal("5"))
        quotation = create_purchase_request_rfqs(
            request,
            allocations=self._allocations(lines, Decimal("5")),
            response_deadline=timezone.localdate(),
            actor=self.actor,
        )[0]
        transition_purchase_quotation(quotation, "send", self.actor)
        transition_purchase_quotation(quotation, "receive", self.actor)

        selected = transition_purchase_quotation(quotation, "select", self.actor)
        order = PurchaseOrder.objects.get(pk=selected._created_purchase_order_id)

        quotation.refresh_from_db()
        lines[0].refresh_from_db()
        self.assertEqual(quotation.status, PurchaseQuotation.Status.SELECTED)
        self.assertEqual(order.status, PurchaseOrder.Status.DRAFT)
        self.assertEqual(order.vendor, self.vendor)
        self.assertEqual(order.warehouse, self.warehouse)
        self.assertEqual(order.lines.get().quantity, Decimal("5"))
        self.assertEqual(PurchaseOrderControl.objects.get(order=order).quotation, quotation)
        self.assertEqual(lines[0].ordered_quantity, Decimal("5"))
        self.assertEqual(lines[0].line_status, PurchaseRequestLine.Status.FULLY_ORDERED)

    def test_cancel_rfq_recomputes_quantity_but_preserves_forward_status(self):
        request, lines = self._request("PR-RFQ-CANCEL", quantity=Decimal("5"))
        quotation = create_purchase_request_rfqs(
            request,
            allocations=self._allocations(lines, Decimal("5")),
            response_deadline=timezone.localdate(),
            actor=self.actor,
        )[0]

        transition_purchase_quotation(quotation, "cancel", self.actor)

        lines[0].refresh_from_db()
        self.assertEqual(lines[0].rfq_quantity, Decimal("0"))
        self.assertEqual(lines[0].line_status, PurchaseRequestLine.Status.RFQ)

    def test_approval_hook_sets_line_approved_quantity(self):
        request, lines = self._request("PR-APPROVAL", quantity=Decimal("4"))
        request.status = PurchaseRequest.Status.PENDING
        request.save(update_fields=["status", "updated_at"])
        lines[0].approved_quantity = Decimal("0")
        lines[0].line_status = PurchaseRequestLine.Status.PENDING
        lines[0].save(update_fields=["approved_quantity", "line_status", "updated_at"])

        purchase_request_before_workflow(instance=request, action="approve", actor=self.actor)

        lines[0].refresh_from_db()
        self.assertEqual(lines[0].approved_quantity, Decimal("4"))
        self.assertEqual(lines[0].line_status, PurchaseRequestLine.Status.APPROVED)

    def test_rfq_creation_query_count_does_not_grow_with_lines(self):
        small_request, small_lines = self._request("PR-RFQ-Q-SMALL", line_count=2, quantity=Decimal("2"))
        large_request, large_lines = self._request("PR-RFQ-Q-LARGE", line_count=12, quantity=Decimal("2"))

        with CaptureQueriesContext(connection) as small_queries:
            create_purchase_request_rfqs(
                small_request,
                allocations=self._allocations(small_lines, Decimal("1")),
                response_deadline=timezone.localdate(),
                actor=self.actor,
            )
        with CaptureQueriesContext(connection) as large_queries:
            create_purchase_request_rfqs(
                large_request,
                allocations=self._allocations(large_lines, Decimal("1")),
                response_deadline=timezone.localdate(),
                actor=self.actor,
            )

        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"RFQ conversion queries grew from {len(small_queries)} to {len(large_queries)}.",
        )

    def test_smart_actions_and_wizard_use_exact_purchase_request(self):
        request, lines = self._request("PR-RFQ-UI", quantity=Decimal("2"))
        self.client.force_login(self.actor)
        response = self.client.post(f"/app/purchase-requests/{request.pk}/action/create-rfq/")
        self.assertRedirects(response, f"/app/purchase-requests/{request.pk}/convert-to-rfq/")

        wizard = self.client.get(response.url)
        self.assertEqual(wizard.status_code, 200)
        self.assertContains(wizard, "Create one draft RFQ per vendor")

        quotation = create_purchase_request_rfqs(
            request,
            allocations=self._allocations(lines, Decimal("1")),
            response_deadline=timezone.localdate(),
            actor=self.actor,
        )[0]
        request_page = self.client.get(f"/app/purchase-requests/{request.pk}/edit/")
        quotation_page = self.client.get(f"/app/purchase-quotations/{quotation.pk}/edit/")
        self.assertContains(
            request_page,
            f"smart_action=purchase-requests.quotations&amp;source_id={request.pk}",
        )
        self.assertContains(
            quotation_page,
            f"smart_action=purchase-quotations.purchase-request&amp;source_id={quotation.pk}",
        )

    def test_blanket_partial_release_stays_running_and_cancel_restores_quantity(self):
        request, lines = self._request("PR-BO-001", quantity=Decimal("10"))
        blanket = create_purchase_request_blanket_order(
            request,
            vendor_id=self.vendor.pk,
            allocations=self._allocations(lines, Decimal("10")),
            date_end=timezone.localdate(),
            actor=self.actor,
        )
        transition_blanket_order(blanket, "submit", self.actor)
        transition_blanket_order(blanket, "approve", self.actor)
        blanket_line = blanket.lines.get()

        quotation = create_blanket_order_rfq(
            blanket,
            releases={blanket_line.pk: Decimal("4")},
            actor=self.actor,
        )

        blanket.refresh_from_db()
        blanket_line.refresh_from_db()
        lines[0].refresh_from_db()
        self.assertEqual(blanket.status, BlanketOrder.Status.RUNNING)
        self.assertFalse(blanket.auto_closed)
        self.assertEqual(blanket_line.released_quantity, Decimal("4"))
        self.assertEqual(quotation.lines.get().blanket_order_line, blanket_line)
        self.assertEqual(lines[0].blanket_quantity, Decimal("6"))
        self.assertEqual(lines[0].rfq_quantity, Decimal("4"))

        transition_purchase_quotation(quotation, "cancel", self.actor)
        blanket_line.refresh_from_db()
        lines[0].refresh_from_db()
        self.assertEqual(blanket_line.released_quantity, Decimal("0"))
        self.assertEqual(lines[0].blanket_quantity, Decimal("10"))
        self.assertEqual(lines[0].rfq_quantity, Decimal("0"))

    def test_blanket_auto_closes_only_after_full_release_rfq_becomes_po(self):
        request, lines = self._request("PR-BO-CLOSE", quantity=Decimal("5"))
        blanket = create_purchase_request_blanket_order(
            request,
            vendor_id=self.vendor.pk,
            allocations=self._allocations(lines, Decimal("5")),
            date_end=timezone.localdate(),
            actor=self.actor,
        )
        transition_blanket_order(blanket, "submit", self.actor)
        transition_blanket_order(blanket, "approve", self.actor)
        quotation = create_blanket_order_rfq(blanket, actor=self.actor)

        blanket.refresh_from_db()
        self.assertEqual(blanket.status, BlanketOrder.Status.RUNNING)
        self.assertFalse(blanket.auto_closed)

        transition_purchase_quotation(quotation, "send", self.actor)
        transition_purchase_quotation(quotation, "receive", self.actor)
        transition_purchase_quotation(quotation, "select", self.actor)

        blanket.refresh_from_db()
        self.assertEqual(blanket.status, BlanketOrder.Status.CLOSED)
        self.assertTrue(blanket.auto_closed)

    def test_blanket_release_wizard_uses_exact_record_and_partial_quantities(self):
        request, lines = self._request("PR-BO-UI", quantity=Decimal("3"))
        blanket = create_purchase_request_blanket_order(
            request,
            vendor_id=self.vendor.pk,
            allocations=self._allocations(lines, Decimal("3")),
            date_end=timezone.localdate(),
            actor=self.actor,
        )
        transition_blanket_order(blanket, "submit", self.actor)
        transition_blanket_order(blanket, "approve", self.actor)
        self.client.force_login(self.actor)

        response = self.client.post(f"/app/blanket-orders/{blanket.pk}/action/create-rfq/")

        self.assertRedirects(response, f"/app/blanket-orders/{blanket.pk}/convert-to-rfq/")
        wizard = self.client.get(response.url)
        self.assertEqual(wizard.status_code, 200)
        self.assertContains(wizard, blanket.number)
        self.assertContains(wizard, "Release Qty")

    def test_tender_award_creates_linked_po_and_updates_request_progress(self):
        request, lines = self._request("PR-TNR-001", quantity=Decimal("8"))
        tender = create_purchase_request_tender(
            request,
            allocations=self._allocations(lines, Decimal("8")),
            title="Tender from PR",
            tender_type=PurchaseTender.TenderType.OPEN,
            method=PurchaseTender.Method.OTE,
            award_strategy_id=self.award_strategy.pk,
            close_date=timezone.localdate(),
            actor=self.actor,
        )
        transition_purchase_tender(tender, "submit", self.actor)
        transition_purchase_tender(tender, "approve", self.actor)
        transition_purchase_tender(tender, "publish", self.actor)
        transition_purchase_tender(tender, "open-submission", self.actor)
        transition_purchase_tender(tender, "evaluate", self.actor)
        TenderBid.objects.create(
            tender=tender,
            vendor=self.vendor,
            bid_amount=Decimal("200"),
            allocation_percent=Decimal("100"),
            status=TenderBid.Status.AWARDED,
        )

        awarded = transition_purchase_tender(tender, "award", self.actor)
        self.assertEqual(awarded._created_purchase_order_ids, [])
        self.assertFalse(PurchaseOrderControl.objects.filter(tender=tender).exists())
        transition_tender_award_response(awarded.award_responses.get(), "accept", self.actor)
        transition_purchase_tender(tender, "submit-decision", self.actor)
        with self.assertRaisesMessage(ValidationError, "at least 10 characters"):
            transition_purchase_tender(tender, "reject-decision", self.actor)
        transition_purchase_tender(
            tender,
            "reject-decision",
            self.actor,
            reason="Commercial evaluation must be revised.",
        )
        tender.refresh_from_db()
        self.assertEqual(tender.decision_rejection_reason, "Commercial evaluation must be revised.")
        transition_purchase_tender(tender, "submit-decision", self.actor)
        transition_purchase_tender(tender, "approve-decision", self.actor)
        closed = transition_purchase_tender(tender, "close", self.actor)
        order = PurchaseOrder.objects.get(pk=closed._created_purchase_order_ids[0])

        lines[0].refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.DRAFT)
        self.assertEqual(order.lines.get().quantity, Decimal("8"))
        self.assertEqual(PurchaseOrderControl.objects.get(order=order).tender, tender)
        self.assertEqual(lines[0].ordered_quantity, Decimal("8"))
        self.assertEqual(lines[0].line_status, PurchaseRequestLine.Status.FULLY_ORDERED)

    def test_tender_split_award_uses_line_level_vendor_quantities_and_prices(self):
        request, lines = self._request("PR-TNR-SPLIT", line_count=2, quantity=Decimal("10"))
        tender = create_purchase_request_tender(
            request,
            allocations=self._allocations(lines, Decimal("10")),
            title="Split award tender",
            tender_type=PurchaseTender.TenderType.OPEN,
            method=PurchaseTender.Method.OTE,
            award_strategy_id=self.award_strategy.pk,
            close_date=timezone.localdate(),
            actor=self.actor,
        )
        for action in ("submit", "approve", "publish", "open-submission", "evaluate"):
            transition_purchase_tender(tender, action, self.actor)
        first_bid = TenderBid.objects.create(
            tender=tender,
            vendor=self.vendor,
            status=TenderBid.Status.AWARDED,
        )
        second_bid = TenderBid.objects.create(
            tender=tender,
            vendor=self.other_vendor,
            status=TenderBid.Status.AWARDED,
        )
        tender_lines = list(tender.lines.order_by("pk"))
        bid_lines = TenderBidLine.objects.bulk_create(
            [
                TenderBidLine(
                    bid=bid,
                    tender_line=tender_line,
                    offered_quantity=Decimal("10"),
                    unit_price=price,
                )
                for tender_line in tender_lines
                for bid, price in (
                    (first_bid, Decimal("20")),
                    (second_bid, Decimal("22")),
                )
            ]
        )
        bid_line_by_key = {(row.tender_line_id, row.bid_id): row for row in bid_lines}
        allocations = []
        for index, tender_line in enumerate(tender_lines):
            first_quantity, second_quantity = (
                (Decimal("6"), Decimal("4"))
                if index == 0
                else (Decimal("3"), Decimal("7"))
            )
            allocations.extend(
                [
                    TenderAwardAllocation(
                        tender=tender,
                        tender_line=tender_line,
                        bid_line=bid_line_by_key[(tender_line.pk, first_bid.pk)],
                        allocated_quantity=first_quantity,
                        unit_price=Decimal("20"),
                        award_strategy=TenderAwardAllocation.Strategy.MANUAL,
                        rank=1,
                    ),
                    TenderAwardAllocation(
                        tender=tender,
                        tender_line=tender_line,
                        bid_line=bid_line_by_key[(tender_line.pk, second_bid.pk)],
                        allocated_quantity=second_quantity,
                        unit_price=Decimal("22"),
                        award_strategy=TenderAwardAllocation.Strategy.MANUAL,
                        rank=2,
                    ),
                ]
            )
        TenderAwardAllocation.objects.bulk_create(allocations)

        awarded = transition_purchase_tender(tender, "award", self.actor)
        for response in awarded.award_responses.all():
            transition_tender_award_response(response, "accept", self.actor)
        transition_purchase_tender(tender, "submit-decision", self.actor)
        transition_purchase_tender(tender, "approve-decision", self.actor)
        closed = transition_purchase_tender(tender, "close", self.actor)

        orders = {
            order.vendor_id: order
            for order in PurchaseOrder.objects.filter(pk__in=closed._created_purchase_order_ids).prefetch_related("lines")
        }
        first_quantities = list(orders[self.vendor.pk].lines.order_by("pk").values_list("quantity", flat=True))
        second_quantities = list(orders[self.other_vendor.pk].lines.order_by("pk").values_list("quantity", flat=True))
        self.assertEqual(first_quantities, [Decimal("6"), Decimal("3")])
        self.assertEqual(second_quantities, [Decimal("4"), Decimal("7")])
        self.assertEqual(orders[self.vendor.pk].total, Decimal("180"))
        self.assertEqual(orders[self.other_vendor.pk].total, Decimal("242"))
        for line in lines:
            line.refresh_from_db()
            self.assertEqual(line.ordered_quantity, Decimal("10"))
            self.assertEqual(line.line_status, PurchaseRequestLine.Status.FULLY_ORDERED)

    def test_tender_split_award_rejects_incomplete_line_allocation_atomically(self):
        request, lines = self._request("PR-TNR-SPLIT-BAD", quantity=Decimal("10"))
        tender = create_purchase_request_tender(
            request,
            allocations=self._allocations(lines, Decimal("10")),
            title="Incomplete split award",
            tender_type=PurchaseTender.TenderType.OPEN,
            method=PurchaseTender.Method.OTE,
            award_strategy_id=self.award_strategy.pk,
            close_date=timezone.localdate(),
            actor=self.actor,
        )
        for action in ("submit", "approve", "publish", "open-submission", "evaluate"):
            transition_purchase_tender(tender, action, self.actor)
        bid = TenderBid.objects.create(tender=tender, vendor=self.vendor, status=TenderBid.Status.AWARDED)
        tender_line = tender.lines.get()
        bid_line = TenderBidLine.objects.create(
            bid=bid,
            tender_line=tender_line,
            offered_quantity=Decimal("10"),
            unit_price=Decimal("20"),
        )
        TenderAwardAllocation.objects.create(
            tender=tender,
            tender_line=tender_line,
            bid_line=bid_line,
            allocated_quantity=Decimal("9"),
            unit_price=Decimal("20"),
        )

        with self.assertRaisesMessage(ValidationError, "allocations must equal"):
            transition_purchase_tender(tender, "award", self.actor)

        tender.refresh_from_db()
        lines[0].refresh_from_db()
        self.assertEqual(tender.status, PurchaseTender.Status.BID_SELECTION)
        self.assertEqual(lines[0].ordered_quantity, Decimal("0"))
        self.assertFalse(PurchaseOrder.objects.filter(purchase_control__tender=tender).exists())

    def test_tender_close_query_count_does_not_grow_with_award_lines(self):
        def prepared_tender(number, line_count):
            request, lines = self._request(number, line_count=line_count, quantity=Decimal("2"))
            tender = create_purchase_request_tender(
                request,
                allocations=self._allocations(lines, Decimal("2")),
                title=f"{number} tender",
                tender_type=PurchaseTender.TenderType.OPEN,
                method=PurchaseTender.Method.OTE,
                award_strategy_id=self.award_strategy.pk,
                close_date=timezone.localdate(),
                actor=self.actor,
            )
            for action in ("submit", "approve", "publish", "open-submission", "evaluate"):
                transition_purchase_tender(tender, action, self.actor)
            bid = TenderBid.objects.create(
                tender=tender,
                vendor=self.vendor,
                status=TenderBid.Status.AWARDED,
            )
            tender_lines = list(tender.lines.order_by("pk"))
            bid_lines = TenderBidLine.objects.bulk_create(
                [
                    TenderBidLine(
                        bid=bid,
                        tender_line=tender_line,
                        offered_quantity=Decimal("2"),
                        unit_price=Decimal("20"),
                    )
                    for tender_line in tender_lines
                ]
            )
            TenderAwardAllocation.objects.bulk_create(
                [
                    TenderAwardAllocation(
                        tender=tender,
                        tender_line=tender_line,
                        bid_line=bid_line,
                        allocated_quantity=Decimal("2"),
                        unit_price=Decimal("20"),
                    )
                    for tender_line, bid_line in zip(tender_lines, bid_lines)
                ]
            )
            transition_purchase_tender(tender, "award", self.actor)
            tender.award_responses.update(
                status="accepted",
                responded_at=timezone.now(),
                responded_by=self.actor,
            )
            transition_purchase_tender(tender, "submit-decision", self.actor)
            transition_purchase_tender(tender, "approve-decision", self.actor)
            return tender

        small_tender = prepared_tender("PR-TNR-Q-SMALL", 2)
        large_tender = prepared_tender("PR-TNR-Q-LARGE", 12)

        with CaptureQueriesContext(connection) as small_queries:
            transition_purchase_tender(small_tender, "close", self.actor)
        with CaptureQueriesContext(connection) as large_queries:
            transition_purchase_tender(large_tender, "close", self.actor)

        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Tender close queries grew from {len(small_queries)} to {len(large_queries)}.",
        )

    def test_direct_purchase_manual_approval_creates_po_receipt_and_draft_bill(self):
        request, lines = self._request("PR-DPO-001", quantity=Decimal("4"))
        direct = create_purchase_request_direct_purchase(
            request,
            vendor_id=self.vendor.pk,
            allocations=self._allocations(lines, Decimal("4")),
            justification="Urgent local purchase",
            actor=self.actor,
        )
        transition_direct_purchase(direct, "submit", self.actor)

        approved = transition_direct_purchase(direct, "approve", self.actor)
        approved.refresh_from_db()
        order = approved.purchase_order

        self.assertEqual(approved.status, "approved")
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)
        self.assertEqual(order.goods_receipts.get().status, GoodsReceipt.Status.DRAFT)
        bill = Invoice.objects.get(purchase_accounting_documents__order=order)
        self.assertEqual(bill.invoice_type, Invoice.InvoiceType.VENDOR)
        self.assertEqual(bill.status, Invoice.Status.DRAFT)
        self.assertEqual(bill.total, Decimal("100"))

    def test_direct_purchase_auto_receiving_posts_inventory_once(self):
        setting = PurchaseSetting.objects.get(company=self.company)
        setting.receiving_behavior = PurchaseSetting.ReceivingBehavior.AUTO
        setting.save(update_fields=["receiving_behavior", "updated_at"])
        request, lines = self._request("PR-DPO-AUTO", quantity=Decimal("2"))
        product = lines[0].product
        direct = create_purchase_request_direct_purchase(
            request,
            vendor_id=self.vendor.pk,
            allocations=self._allocations(lines, Decimal("2")),
            justification="Auto receipt test",
            actor=self.actor,
        )
        transition_direct_purchase(direct, "submit", self.actor)

        approved = transition_direct_purchase(direct, "approve", self.actor)

        product.refresh_from_db()
        receipt = GoodsReceipt.objects.get(order=approved.purchase_order)
        self.assertEqual(receipt.status, GoodsReceipt.Status.RECEIVED)
        self.assertEqual(product.current_stock, Decimal("2"))
        self.assertEqual(StockMove.objects.filter(reference=receipt.number).count(), 1)

    def test_blanket_creation_query_count_does_not_grow_with_lines(self):
        small_request, small_lines = self._request("PR-BO-Q-SMALL", line_count=2, quantity=Decimal("2"))
        large_request, large_lines = self._request("PR-BO-Q-LARGE", line_count=12, quantity=Decimal("2"))

        with CaptureQueriesContext(connection) as small_queries:
            create_purchase_request_blanket_order(
                small_request,
                vendor_id=self.vendor.pk,
                allocations=self._allocations(small_lines, Decimal("1")),
                date_end=timezone.localdate(),
                actor=self.actor,
            )
        with CaptureQueriesContext(connection) as large_queries:
            create_purchase_request_blanket_order(
                large_request,
                vendor_id=self.vendor.pk,
                allocations=self._allocations(large_lines, Decimal("1")),
                date_end=timezone.localdate(),
                actor=self.actor,
            )

        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Blanket conversion queries grew from {len(small_queries)} to {len(large_queries)}.",
        )

    def test_line_approval_supports_partial_reject_and_above_requested_quantity(self):
        request, lines = self._request("PR-APP-LINES", line_count=3, quantity=Decimal("10"))
        request.status = PurchaseRequest.Status.PENDING
        request.save(update_fields=["status", "updated_at"])
        PurchaseRequestLine.objects.filter(request=request).update(
            approved_quantity=Decimal("0"),
            line_status=PurchaseRequestLine.Status.PENDING,
        )

        approve_purchase_request_lines(
            request,
            decisions=[
                {
                    "line_id": lines[0].pk,
                    "action": "approve",
                    "approved_quantity": Decimal("4"),
                    "reason": "Only four units are currently required",
                },
                {
                    "line_id": lines[1].pk,
                    "action": "reject",
                    "approved_quantity": Decimal("0"),
                    "reason": "Duplicate requirement is not needed",
                },
                {
                    "line_id": lines[2].pk,
                    "action": "approve",
                    "approved_quantity": Decimal("12"),
                    "reason": "Approve increased quantity for buffer stock",
                },
            ],
            actor=self.actor,
        )

        request.refresh_from_db()
        for line in lines:
            line.refresh_from_db()
        self.assertEqual(request.status, PurchaseRequest.Status.APPROVED)
        self.assertEqual(lines[0].line_status, PurchaseRequestLine.Status.PARTIALLY_APPROVED)
        self.assertEqual(lines[0].approved_quantity, Decimal("4"))
        self.assertEqual(lines[0].remaining_to_source, Decimal("4"))
        self.assertEqual(lines[1].line_status, PurchaseRequestLine.Status.REJECTED)
        self.assertEqual(lines[1].approved_quantity, Decimal("0"))
        self.assertEqual(lines[1].remaining_to_source, Decimal("0"))
        self.assertEqual(lines[2].line_status, PurchaseRequestLine.Status.APPROVED)
        self.assertEqual(lines[2].approved_quantity, Decimal("12"))
        self.assertEqual(lines[2].remaining_to_source, Decimal("12"))

    def test_line_approval_reason_validation_rolls_back(self):
        request, lines = self._request("PR-APP-ROLLBACK", quantity=Decimal("5"))
        request.status = PurchaseRequest.Status.PENDING
        request.save(update_fields=["status", "updated_at"])
        lines[0].approved_quantity = Decimal("0")
        lines[0].line_status = PurchaseRequestLine.Status.PENDING
        lines[0].save(update_fields=["approved_quantity", "line_status", "updated_at"])

        with self.assertRaisesMessage(ValidationError, "at least 10 characters"):
            approve_purchase_request_lines(
                request,
                decisions=[
                    {
                        "line_id": lines[0].pk,
                        "action": "approve",
                        "approved_quantity": Decimal("6"),
                        "reason": "short",
                    }
                ],
                actor=self.actor,
            )

        request.refresh_from_db()
        lines[0].refresh_from_db()
        self.assertEqual(request.status, PurchaseRequest.Status.PENDING)
        self.assertEqual(lines[0].approved_quantity, Decimal("0"))

    def test_re_request_creates_linked_draft_and_tracks_remaining_quantity(self):
        request, lines = self._request("PR-REREQ-001", quantity=Decimal("10"))
        lines[0].approved_quantity = Decimal("4")
        lines[0].line_status = PurchaseRequestLine.Status.PARTIALLY_APPROVED
        lines[0].save(update_fields=["approved_quantity", "line_status", "updated_at"])

        re_request = create_purchase_request_re_request(
            request,
            allocations=[{"line_id": lines[0].pk, "quantity": Decimal("3")}],
            actor=self.actor,
        )

        lines[0].refresh_from_db()
        new_line = re_request.lines.get()
        self.assertEqual(re_request.status, PurchaseRequest.Status.DRAFT)
        self.assertEqual(new_line.origin_request_line, lines[0])
        self.assertEqual(new_line.quantity, Decimal("3"))
        self.assertEqual(lines[0].re_request_quantity, Decimal("3"))
        self.assertEqual(lines[0].remaining_to_re_request, Decimal("3"))

        self.client.force_login(self.actor)
        source_page = self.client.get(f"/app/purchase-requests/{request.pk}/edit/")
        new_page = self.client.get(f"/app/purchase-requests/{re_request.pk}/edit/")
        self.assertContains(
            source_page,
            f"smart_action=purchase-requests.re-requests&amp;source_id={request.pk}",
        )
        self.assertContains(
            new_page,
            f"smart_action=purchase-requests.original-requests&amp;source_id={re_request.pk}",
        )

    def test_line_approval_query_count_does_not_grow_with_lines(self):
        small_request, small_lines = self._request("PR-APP-Q-SMALL", line_count=2)
        large_request, large_lines = self._request("PR-APP-Q-LARGE", line_count=12)
        for request in (small_request, large_request):
            request.status = PurchaseRequest.Status.PENDING
            request.save(update_fields=["status", "updated_at"])
            PurchaseRequestLine.objects.filter(request=request).update(
                approved_quantity=Decimal("0"),
                line_status=PurchaseRequestLine.Status.PENDING,
            )

        def decisions(lines):
            return [
                {
                    "line_id": line.pk,
                    "action": "approve",
                    "approved_quantity": line.quantity,
                    "reason": "",
                }
                for line in lines
            ]

        with CaptureQueriesContext(connection) as small_queries:
            approve_purchase_request_lines(
                small_request,
                decisions=decisions(small_lines),
                actor=self.actor,
            )
        with CaptureQueriesContext(connection) as large_queries:
            approve_purchase_request_lines(
                large_request,
                decisions=decisions(large_lines),
                actor=self.actor,
            )

        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Approval queries grew from {len(small_queries)} to {len(large_queries)}.",
        )
