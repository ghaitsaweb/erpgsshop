from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Company,
    Contact,
    Employee,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    Warehouse,
)
from modules.approval_workflow.models import ApprovalAction, ApprovalInstance
from modules.approval_workflow.services import execute_registered_approval_action
from modules.organization.models import UserOrganizationAccess

from .models import PurchaseOrderControl, VendorProfile


class PurchaseOrderApprovalParityTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.requester = user_model.objects.create_user(
            "purchase-approval-requester",
            password="test-password-123",
        )
        self.approver = user_model.objects.create_user(
            "purchase-approval-manager",
            password="test-password-123",
        )
        self.outsider = user_model.objects.create_user(
            "purchase-approval-outsider",
            password="test-password-123",
        )
        self.requester.groups.add(Group.objects.get(name="Purchase User"))
        self.approver.groups.add(Group.objects.get(name="Purchase Manager"))
        self.outsider.groups.add(Group.objects.get(name="Purchase Manager"))

        self.company = Company.objects.create(code="PO-APR", name="Purchase Approval")
        self.other_company = Company.objects.create(code="PO-OTHER", name="Other Purchase Company")
        for user, company in (
            (self.requester, self.company),
            (self.approver, self.company),
            (self.outsider, self.other_company),
        ):
            access = UserOrganizationAccess.objects.create(user=user, all_branches=True)
            access.companies.add(company)
            Employee.objects.create(
                company=company,
                user=user,
                employee_id=f"PO-APR-{user.pk}",
                name=user.username,
            )

        self.vendor = Contact.objects.create(
            company=self.company,
            code="PO-APR-VENDOR",
            name="Approval Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor,
            registration_number="PO-APR-VENDOR",
            status=VendorProfile.Status.ACTIVE,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="PO-APR-WH",
            name="Approval Warehouse",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="PO-APR-PRODUCT",
            name="Approval Product",
            product_type=Product.ProductType.STOCKABLE,
        )

    def _order(self, number, *, status=PurchaseOrder.Status.DRAFT, line_count=1):
        order = PurchaseOrder.objects.create(
            company=self.company,
            number=number,
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=timezone.localdate(),
            status=status,
        )
        PurchaseOrderLine.objects.bulk_create(
            [
                PurchaseOrderLine(
                    order=order,
                    product=self.product,
                    quantity=Decimal("1"),
                    unit_price=Decimal("25") + index,
                )
                for index in range(line_count)
            ]
        )
        return order

    def _transition(self, order, actor, action, reason=""):
        return execute_registered_approval_action(
            order,
            actor,
            entity="purchases",
            action=action,
            reason=reason,
        )

    def test_hmx_draft_submit_and_final_approval_preserve_purchase_side_effects(self):
        order = self._order("PO-APR-DRAFT")

        submitted = self._transition(order, self.requester, "submit")

        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.WAITING_APPROVAL)
        self.assertEqual(order.total, Decimal("25"))
        self.assertEqual(submitted.instance.status, ApprovalInstance.Status.PENDING)
        self.assertIn(self.approver.pk, submitted.instance.active_level["resolved_user_ids"])
        self.assertNotIn(self.outsider.pk, submitted.instance.active_level["resolved_user_ids"])

        approved = self._transition(
            order,
            self.approver,
            "approve",
            reason="Vendor, price, and delivery terms reviewed.",
        )

        order.refresh_from_db()
        approved.instance.refresh_from_db()
        control = PurchaseOrderControl.objects.get(order=order)
        self.assertTrue(approved.completed)
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)
        self.assertIsNotNone(control.confirmed_at)
        self.assertEqual(approved.instance.status, ApprovalInstance.Status.APPROVED)
        self.assertTrue(
            approved.instance.actions.filter(
                action=ApprovalAction.Action.APPROVE,
                performed_by=self.approver,
            ).exists()
        )

    def test_sent_order_can_submit_and_rejection_requires_reason(self):
        order = self._order("PO-APR-SENT", status=PurchaseOrder.Status.SENT)
        submitted = self._transition(order, self.requester, "submit")

        with self.assertRaisesMessage(ValidationError, "rejection reason is required"):
            self._transition(order, self.approver, "reject")

        rejected = self._transition(
            order,
            self.approver,
            "reject",
            reason="Commercial terms need revision.",
        )
        order.refresh_from_db()
        rejected.instance.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.REJECTED)
        self.assertEqual(rejected.instance.status, ApprovalInstance.Status.REJECTED)
        self.assertEqual(rejected.instance.rejection_reason, "Commercial terms need revision.")
        self.assertEqual(submitted.instance.pk, rejected.instance.pk)

    def test_company_scoped_non_approver_cannot_decide(self):
        order = self._order("PO-APR-SCOPE")
        self._transition(order, self.requester, "submit")

        with self.assertRaisesMessage(ValidationError, "not an approver"):
            self._transition(
                order,
                self.outsider,
                "approve",
                reason="Should not be accepted.",
            )

        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.WAITING_APPROVAL)

    def test_document_actions_use_engine_and_render_bounded_history(self):
        order = self._order("PO-APR-UI")
        self.client.force_login(self.requester)

        submitted_response = self.client.post(
            reverse("document_action", args=["purchases", order.pk, "request-approval"]),
        )
        self.assertRedirects(
            submitted_response,
            reverse("entity_edit", args=["purchases", order.pk]),
            fetch_redirect_response=False,
        )
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.WAITING_APPROVAL)
        page = self.client.get(reverse("entity_edit", args=["purchases", order.pk]))
        self.assertContains(page, "Approval workflow")
        self.assertContains(page, "Purchase Order Default")

        self.client.force_login(self.approver)
        approved_response = self.client.post(
            reverse("document_action", args=["purchases", order.pk, "confirm"]),
            {"reason": "Approved from the Purchase Order."},
        )
        self.assertRedirects(
            approved_response,
            reverse("entity_edit", args=["purchases", order.pk]),
            fetch_redirect_response=False,
        )
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)

    def test_submit_query_count_is_constant_for_two_and_twelve_lines(self):
        ContentType.objects.get_for_model(PurchaseOrder, for_concrete_model=False)
        self._transition(self._order("PO-APR-Q-WARM"), self.requester, "submit")

        def measured(line_count):
            order = self._order(f"PO-APR-Q-{line_count}", line_count=line_count)
            with CaptureQueriesContext(connection) as queries:
                self._transition(order, self.requester, "submit")
            return len(queries)

        small_count = measured(2)
        large_count = measured(12)
        self.assertEqual(small_count, large_count)
