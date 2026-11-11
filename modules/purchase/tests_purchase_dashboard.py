from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company
from modules.organization.context import OrganizationContext, reset_current_context, set_current_context

from .dashboard import build_purchase_analytics
from .models import PurchaseRequest


class PurchaseLifecycleDashboardParityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="purchase-lifecycle-admin",
            email="purchase-lifecycle@example.com",
            password="test-password",
        )
        self.company = Company.objects.create(code="PLC", name="Purchase Lifecycle")
        self.other_company = Company.objects.create(code="PLC-2", name="Other Purchase Lifecycle")

    def _context(self, company):
        branch = company.branches.get(is_main=True)
        return OrganizationContext(
            company_id=company.pk,
            branch_id=branch.pk,
            allowed_company_ids=(self.company.pk, self.other_company.pk),
            allowed_branch_ids=tuple(
                self.company.branches.values_list("pk", flat=True)
            )
            + tuple(self.other_company.branches.values_list("pk", flat=True)),
            selected_company_ids=(company.pk,),
            selected_branch_ids=(branch.pk,),
        )

    def _requests(self, company, count, prefix):
        PurchaseRequest.objects.bulk_create(
            [
                PurchaseRequest(
                    company=company,
                    number=f"{prefix}-{index:03d}",
                    requestor=self.user,
                    required_date=timezone.localdate() + timedelta(days=7),
                    justification=f"Lifecycle request {index}",
                )
                for index in range(count)
            ]
        )

    def test_overview_exposes_hmx_lifecycle_stages_and_company_scope(self):
        self._requests(self.company, 2, "PLC")
        self._requests(self.other_company, 3, "OTHER")
        token = set_current_context(self._context(self.company))
        try:
            analytics = build_purchase_analytics("overview", self.user)
        finally:
            reset_current_context(token)

        self.assertEqual(analytics["breakdown_title"], "Purchase lifecycle stages")
        self.assertEqual(
            [row["label"] for row in analytics["breakdown"]],
            [
                "Purchase Request",
                "Request for Quotation",
                "Purchase Order",
                "Direct Purchase",
                "Purchase Tender",
                "Blanket Order",
            ],
        )
        self.assertEqual(analytics["breakdown"][0]["total"], 2)
        self.assertEqual(analytics["metrics"][0]["value"], 2)

    def test_lifecycle_query_count_does_not_grow_with_request_volume(self):
        self._requests(self.company, 2, "SMALL")
        self._requests(self.other_company, 20, "LARGE")

        small_token = set_current_context(self._context(self.company))
        try:
            with CaptureQueriesContext(connection) as small_queries:
                build_purchase_analytics("overview", self.user)
        finally:
            reset_current_context(small_token)

        large_token = set_current_context(self._context(self.other_company))
        try:
            with CaptureQueriesContext(connection) as large_queries:
                build_purchase_analytics("overview", self.user)
        finally:
            reset_current_context(large_token)

        self.assertEqual(len(large_queries), len(small_queries))
