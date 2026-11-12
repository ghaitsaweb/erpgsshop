from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Contact, Employee, Product, Warehouse
from modules.approval_workflow.models import ApprovalInstance, ApprovalWorkflow
from modules.approval_workflow.services import execute_registered_approval_action
from modules.organization.models import UserOrganizationAccess

from .models import (
    AwardStrategy,
    BlanketOrder,
    BlanketOrderLine,
    PurchaseKPITarget,
    PurchaseKPITemplate,
    PurchaseKPITemplateLine,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseSetting,
    PurchaseTeam,
    PurchaseTender,
    PurchaseTenderLine,
    TenderBid,
    VendorCategory,
    VendorPerformanceEvaluation,
    VendorPerformanceTemplate,
    VendorPerformanceTemplateLine,
    VendorPricelist,
    VendorPricelistAmendment,
    VendorProfile,
)
from .services import (
    approve_purchase_request_lines,
    transition_purchase_tender,
)


class PurchaseApprovalWorkflowParityTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.requester = user_model.objects.create_user("parity-requester", password="test-password")
        self.manager = user_model.objects.create_user("parity-manager", password="test-password")
        self.requestor_manager = user_model.objects.create_user(
            "parity-requestor-manager",
            password="test-password",
        )
        self.requester.groups.add(Group.objects.get(name="Purchase User"))
        self.manager.groups.add(Group.objects.get(name="Purchase Manager"))
        self.requestor_manager.groups.add(Group.objects.get(name="Purchase Requestor Manager"))

        self.company = Company.objects.create(code="PAP", name="Purchase Approval Parity")
        for user in (self.requester, self.manager, self.requestor_manager):
            access = UserOrganizationAccess.objects.create(user=user, all_branches=True)
            access.companies.add(self.company)
            Employee.objects.create(
                company=self.company,
                user=user,
                employee_id=f"PAP-{user.pk}",
                name=user.username,
            )
        PurchaseSetting.objects.create(
            company=self.company,
            require_vendor_prequalification=False,
            restrict_product_by_vendor_pricelist=False,
        )
        self.category = VendorCategory.objects.create(
            company=self.company,
            code="PAP",
            name="Parity Vendors",
        )
        self.vendor_contact = Contact.objects.create(
            company=self.company,
            code="PAP-VENDOR",
            name="Parity Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.vendor = VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor_contact,
            category=self.category,
            registration_number="PAP-VENDOR",
            status=VendorProfile.Status.ACTIVE,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="PAP-WH",
            name="Parity Warehouse",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="PAP-PRODUCT",
            name="Parity Product",
        )

    def transition(self, target, actor, action, *, entity, reason=""):
        return execute_registered_approval_action(
            target,
            actor,
            entity=entity,
            action=action,
            reason=reason,
        )

    def test_all_nine_hmx_purchase_workflows_are_active(self):
        expected = {
            ("core", "purchaseorder"),
            ("gsshop_purchase", "vendorprofile"),
            ("gsshop_purchase", "vendorpricelist"),
            ("gsshop_purchase", "vendorpricelistamendment"),
            ("gsshop_purchase", "blanketorder"),
            ("gsshop_purchase", "purchaserequest"),
            ("gsshop_purchase", "vendorperformanceevaluation"),
            ("gsshop_purchase", "purchasekpitarget"),
            ("gsshop_purchase", "purchasetender"),
        }
        configured = set(
            ApprovalWorkflow.objects.filter(active=True).values_list(
                "content_type__app_label",
                "content_type__model",
            )
        )
        self.assertTrue(expected.issubset(configured))
        for app_label, model in expected:
            workflow = ApprovalWorkflow.objects.get(
                content_type__app_label=app_label,
                content_type__model=model,
            )
            self.assertTrue(workflow.rules.filter(state="associated", levels__active=True).exists())

    def test_vendor_and_pricelist_approval_preserve_domain_side_effects(self):
        draft_contact = Contact.objects.create(
            company=self.company,
            code="PAP-NEW-VENDOR",
            name="New Approval Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        profile = VendorProfile.objects.create(
            company=self.company,
            partner=draft_contact,
            category=self.category,
            registration_number="PAP-NEW-VENDOR",
        )
        self.transition(profile, self.requester, "submit", entity="vendor-profiles")
        self.transition(profile, self.manager, "approve", entity="vendor-profiles")
        profile.refresh_from_db()
        self.assertEqual(profile.status, VendorProfile.Status.ACTIVE)
        self.assertEqual(profile.approved_by, self.manager)
        self.assertIsNotNone(profile.approved_at)

        pricelist = VendorPricelist.objects.create(
            company=self.company,
            name="Parity Pricelist",
            code="PAP-PL",
            vendor=self.vendor,
            product=self.product,
            unit_price=Decimal("20"),
        )
        self.transition(pricelist, self.requester, "submit", entity="vendor-pricelists")
        self.transition(pricelist, self.manager, "approve", entity="vendor-pricelists")
        pricelist.refresh_from_db()
        self.assertEqual(pricelist.status, VendorPricelist.Status.ACTIVE)

        amendment = VendorPricelistAmendment.objects.create(
            pricelist=pricelist,
            reason="Annual price adjustment",
            previous_price=Decimal("20"),
            new_price=Decimal("25"),
        )
        submitted = self.transition(
            amendment,
            self.requester,
            "submit",
            entity="vendor-pricelist-amendments",
        )
        amendment.refresh_from_db()
        self.assertEqual(amendment.status, VendorPricelistAmendment.Status.WAITING_APPROVAL)
        self.assertEqual(submitted.instance.company, self.company)
        self.transition(
            amendment,
            self.manager,
            "approve",
            entity="vendor-pricelist-amendments",
        )
        amendment.refresh_from_db()
        pricelist.refresh_from_db()
        self.assertEqual(amendment.status, VendorPricelistAmendment.Status.APPROVED)
        self.assertEqual(amendment.approver, self.manager)
        self.assertEqual(pricelist.unit_price, Decimal("25"))

    def test_blanket_and_purchase_request_use_engine_with_line_decisions(self):
        today = timezone.localdate()
        blanket = BlanketOrder.objects.create(
            company=self.company,
            number="PAP-BO",
            vendor=self.vendor_contact,
            warehouse=self.warehouse,
            date_start=today,
            date_end=today + timedelta(days=30),
            agreement_value=Decimal("1000"),
        )
        BlanketOrderLine.objects.create(
            blanket_order=blanket,
            product=self.product,
            description=self.product.name,
            agreed_quantity=Decimal("10"),
            unit_price=Decimal("20"),
        )
        self.transition(blanket, self.requester, "submit", entity="blanket-orders")
        self.transition(blanket, self.manager, "approve", entity="blanket-orders")
        blanket.refresh_from_db()
        self.assertEqual(blanket.status, BlanketOrder.Status.CONFIRMED)

        purchase_request = PurchaseRequest.objects.create(
            company=self.company,
            number="PAP-PR",
            requestor=self.requester,
            required_date=today + timedelta(days=7),
            destination_warehouse=self.warehouse,
            justification="Approval parity request",
        )
        line = PurchaseRequestLine.objects.create(
            request=purchase_request,
            product=self.product,
            description=self.product.name,
            quantity=Decimal("10"),
            estimated_unit_price=Decimal("20"),
        )
        submitted = self.transition(
            purchase_request,
            self.requester,
            "submit",
            entity="purchase-requests",
        )
        purchase_request.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(purchase_request.status, PurchaseRequest.Status.PENDING)
        self.assertEqual(line.line_status, PurchaseRequestLine.Status.PENDING)
        self.assertIn(self.requestor_manager.pk, submitted.instance.active_level["resolved_user_ids"])

        approved = approve_purchase_request_lines(
            purchase_request,
            decisions=[
                {
                    "line_id": line.pk,
                    "action": "approve",
                    "approved_quantity": Decimal("8"),
                    "reason": "Eight units match the current requirement",
                }
            ],
            actor=self.requestor_manager,
        )
        purchase_request.refresh_from_db()
        line.refresh_from_db()
        submitted.instance.refresh_from_db()
        self.assertFalse(approved._approval_advanced)
        self.assertEqual(purchase_request.status, PurchaseRequest.Status.APPROVED)
        self.assertEqual(line.line_status, PurchaseRequestLine.Status.PARTIALLY_APPROVED)
        self.assertEqual(line.approved_quantity, Decimal("8"))
        self.assertEqual(submitted.instance.status, ApprovalInstance.Status.APPROVED)

    def test_purchase_request_approval_detail_requires_domain_wizard(self):
        today = timezone.localdate()
        purchase_request = PurchaseRequest.objects.create(
            company=self.company,
            number="PAP-PR-UI",
            requestor=self.requester,
            required_date=today + timedelta(days=7),
            destination_warehouse=self.warehouse,
            justification="Approval UI contract",
        )
        PurchaseRequestLine.objects.create(
            request=purchase_request,
            product=self.product,
            description=self.product.name,
            quantity=Decimal("1"),
            estimated_unit_price=Decimal("20"),
        )
        approval = self.transition(
            purchase_request,
            self.requester,
            "submit",
            entity="purchase-requests",
        ).instance
        self.client.force_login(self.manager)
        page = self.client.get(reverse("approval_detail", args=[approval.pk]))
        self.assertContains(page, "Open approval workspace")
        self.assertNotContains(page, ">Approve</button>", html=False)
        response = self.client.post(
            reverse("approval_action", args=[approval.pk]),
            {"action": "approve", "reason": "Reviewed"},
        )
        self.assertRedirects(
            response,
            reverse("purchase_request_approve", args=[purchase_request.pk]),
            fetch_redirect_response=False,
        )
        approval.refresh_from_db()
        self.assertEqual(approval.status, ApprovalInstance.Status.PENDING)

    def test_cancelling_waiting_document_cancels_approval_transaction(self):
        today = timezone.localdate()
        blanket = BlanketOrder.objects.create(
            company=self.company,
            number="PAP-BO-CANCEL",
            vendor=self.vendor_contact,
            warehouse=self.warehouse,
            date_start=today,
            date_end=today + timedelta(days=30),
            agreement_value=Decimal("1000"),
        )
        BlanketOrderLine.objects.create(
            blanket_order=blanket,
            product=self.product,
            description=self.product.name,
            agreed_quantity=Decimal("10"),
            unit_price=Decimal("20"),
        )
        approval = self.transition(
            blanket,
            self.requester,
            "submit",
            entity="blanket-orders",
        ).instance
        self.client.force_login(self.requester)
        response = self.client.post(
            reverse("document_action", args=["blanket-orders", blanket.pk, "cancel"]),
        )
        self.assertRedirects(
            response,
            reverse("entity_edit", args=["blanket-orders", blanket.pk]),
            fetch_redirect_response=False,
        )
        blanket.refresh_from_db()
        approval.refresh_from_db()
        self.assertEqual(blanket.status, BlanketOrder.Status.CANCELLED)
        self.assertEqual(approval.status, ApprovalInstance.Status.CANCELLED)

    def test_vendor_performance_and_kpi_approval_apply_confirmation_metadata(self):
        today = timezone.localdate()
        performance_template = VendorPerformanceTemplate.objects.create(
            company=self.company,
            name="Parity Evaluation",
            qualitative_weight=Decimal("40"),
            quantitative_weight=Decimal("60"),
            delivery_accuracy_weight=Decimal("50"),
            rejection_rate_weight=Decimal("50"),
            minimum_transaction_threshold=0,
        )
        VendorPerformanceTemplateLine.objects.create(
            template=performance_template,
            sequence=10,
            parameter_name="Service quality",
            weight=Decimal("100"),
            maximum_score=Decimal("5"),
        )
        evaluation = VendorPerformanceEvaluation.objects.create(
            company=self.company,
            vendor=self.vendor,
            template=performance_template,
            evaluator=self.requester,
            period_start=today - timedelta(days=30),
            period_end=today,
        )
        evaluation.lines.update(assessment="Good")
        self.transition(
            evaluation,
            self.requester,
            "submit",
            entity="vendor-performance-evaluations",
        )
        self.transition(
            evaluation,
            self.manager,
            "approve",
            entity="vendor-performance-evaluations",
        )
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, VendorPerformanceEvaluation.Status.CONFIRMED)
        self.assertEqual(evaluation.confirmed_by, self.manager)
        self.assertIsNotNone(evaluation.confirmed_at)

        team = PurchaseTeam.objects.create(
            company=self.company,
            name="Parity Purchasing",
            manager=self.manager,
        )
        kpi_template = PurchaseKPITemplate.objects.create(
            company=self.company,
            name="Parity KPI",
        )
        PurchaseKPITemplateLine.objects.create(
            template=kpi_template,
            sequence=10,
            metric="po_count",
            weight=Decimal("100"),
            default_target=Decimal("2"),
        )
        kpi_template.refresh_from_db()
        target = PurchaseKPITarget.objects.create(
            company=self.company,
            name="Parity KPI Target",
            team=team,
            template=kpi_template,
            period_start=today,
            period_end=today + timedelta(days=30),
        )
        self.transition(target, self.requester, "submit", entity="purchase-kpi-targets")
        self.transition(target, self.manager, "approve", entity="purchase-kpi-targets")
        target.refresh_from_db()
        target_line = target.lines.get()
        self.assertEqual(target.status, PurchaseKPITarget.Status.ACTIVE)
        self.assertEqual(target.activated_by, self.manager)
        self.assertEqual(target_line.activated_by, self.manager)
        self.assertEqual(target_line.activated_at, target.activated_at)

    def test_tender_initial_and_decision_cycles_create_separate_approvals(self):
        today = timezone.localdate()
        strategy = AwardStrategy.objects.create(
            company=self.company,
            name="Parity single award",
            strategy=AwardStrategy.Strategy.SINGLE,
        )
        tender = PurchaseTender.objects.create(
            company=self.company,
            number="PAP-TENDER",
            title="Approval parity tender",
            category=PurchaseTender.Category.OPEX,
            tender_type=PurchaseTender.TenderType.OPEN,
            method=PurchaseTender.Method.OTE,
            award_strategy=strategy,
            warehouse=self.warehouse,
            close_date=today + timedelta(days=14),
            budget_amount=Decimal("500"),
        )
        PurchaseTenderLine.objects.create(
            tender=tender,
            product=self.product,
            description=self.product.name,
            quantity=Decimal("10"),
            target_unit_price=Decimal("20"),
        )
        initial = self.transition(tender, self.requester, "submit", entity="purchase-tenders")
        self.transition(tender, self.manager, "approve", entity="purchase-tenders")
        for action in ("publish", "open-submission", "evaluate"):
            transition_purchase_tender(tender, action, self.manager)
        TenderBid.objects.create(
            tender=tender,
            vendor=self.vendor_contact,
            bid_amount=Decimal("200"),
            allocation_percent=Decimal("100"),
            status=TenderBid.Status.AWARDED,
        )
        transition_purchase_tender(tender, "award", self.manager)

        decision = self.transition(
            tender,
            self.requester,
            "submit",
            entity="purchase-tenders",
        )
        self.assertNotEqual(initial.instance.pk, decision.instance.pk)
        self.transition(
            tender,
            self.manager,
            "reject",
            entity="purchase-tenders",
            reason="Commercial allocation must be reviewed again.",
        )
        tender.refresh_from_db()
        decision.instance.refresh_from_db()
        self.assertEqual(tender.status, PurchaseTender.Status.AWARDED)
        self.assertEqual(tender.decision_approval_status, PurchaseTender.DecisionStatus.REJECTED)
        self.assertEqual(
            tender.decision_rejection_reason,
            "Commercial allocation must be reviewed again.",
        )
        self.assertEqual(decision.instance.status, ApprovalInstance.Status.REJECTED)

    def test_purchase_request_submit_query_count_is_constant(self):
        ContentType.objects.get_for_model(PurchaseRequest, for_concrete_model=False)

        def request_with_lines(suffix, line_count):
            purchase_request = PurchaseRequest.objects.create(
                company=self.company,
                number=f"PAP-PR-Q-{suffix}",
                requestor=self.requester,
                required_date=timezone.localdate() + timedelta(days=7),
                destination_warehouse=self.warehouse,
                justification="Scale guard",
            )
            PurchaseRequestLine.objects.bulk_create(
                [
                    PurchaseRequestLine(
                        request=purchase_request,
                        product=self.product,
                        description=f"Line {index}",
                        quantity=Decimal("1"),
                        estimated_unit_price=Decimal("20"),
                    )
                    for index in range(line_count)
                ]
            )
            return purchase_request

        self.transition(
            request_with_lines("WARM", 1),
            self.requester,
            "submit",
            entity="purchase-requests",
        )

        def measured(suffix, line_count):
            target = request_with_lines(suffix, line_count)
            with CaptureQueriesContext(connection) as queries:
                self.transition(
                    target,
                    self.requester,
                    "submit",
                    entity="purchase-requests",
                )
            return len(queries)

        self.assertEqual(measured("SMALL", 2), measured("LARGE", 12))
