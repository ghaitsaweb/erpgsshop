from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.models import Company, Contact, SaleOrder
from modules.apphub.models import ERPApp
from modules.organization.context import OrganizationContext
from modules.organization.models import Branch
from modules.organization.record_scope import assign_record_scope

from .dashboard import build_sales_dashboard


class SalesDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="sales-dashboard-admin",
            password="test-password",
            email="sales-dashboard@example.com",
        )
        self.company = Company.objects.create(code="SD-ID", name="Sales Dashboard Indonesia")
        self.main_branch = self.company.branches.get(is_main=True)
        self.jakarta = Branch.objects.create(
            company=self.company,
            code="JKT",
            name="Jakarta",
            sequence=10,
        )
        self.customer = Contact.objects.create(
            company=self.company,
            code="SD-CUSTOMER",
            name="Dashboard Customer",
        )
        self.other_company = Company.objects.create(code="SD-OTHER", name="Outside Scope")
        self.other_customer = Contact.objects.create(
            company=self.other_company,
            code="OTHER-CUSTOMER",
            name="Outside Customer",
        )
        self.context = OrganizationContext(
            company_id=self.company.pk,
            branch_id=self.main_branch.pk,
            allowed_company_ids=(self.company.pk,),
            allowed_branch_ids=(self.main_branch.pk, self.jakarta.pk),
            selected_company_ids=(self.company.pk,),
            selected_branch_ids=(self.main_branch.pk, self.jakarta.pk),
        )

    def _order(self, number, *, total, status=SaleOrder.Status.SALE, branch=None, order_date=date(2026, 11, 5)):
        order = SaleOrder.objects.create(
            company=self.company,
            customer=self.customer,
            number=number,
            status=status,
            order_date=order_date,
        )
        SaleOrder.objects.filter(pk=order.pk).update(total=Decimal(total))
        order.refresh_from_db()
        if branch is not None:
            assign_record_scope(order, branch.pk, context=self.context)
        return order

    def test_dashboard_aggregates_revenue_by_selected_branch_and_period(self):
        self._order("SO-MAIN", total="100.00", branch=self.main_branch)
        self._order("SO-JKT", total="200.00", status=SaleOrder.Status.CLOSED, branch=self.jakarta)
        self._order("SO-DRAFT", total="900.00", status=SaleOrder.Status.DRAFT, branch=self.jakarta)
        self._order("SO-OLD", total="800.00", branch=self.main_branch, order_date=date(2026, 10, 31))
        outside = SaleOrder.objects.create(
            company=self.other_company,
            customer=self.other_customer,
            number="SO-OUTSIDE",
            status=SaleOrder.Status.SALE,
            order_date=date(2026, 11, 6),
        )
        SaleOrder.objects.filter(pk=outside.pk).update(total=Decimal("700.00"))

        dashboard = build_sales_dashboard(
            self.user,
            {"date_from": "2026-11-01", "date_to": "2026-11-11"},
            context=self.context,
        )

        self.assertEqual(dashboard["revenue"], Decimal("300.00"))
        self.assertEqual(dashboard["order_count"], 2)
        self.assertEqual(dashboard["period_label"], "01/11/2026 – 11/11/2026")
        revenue_by_name = {row["name"]: row["revenue"] for row in dashboard["branches"]}
        self.assertEqual(revenue_by_name["Jakarta"], Decimal("200.00"))
        self.assertEqual(revenue_by_name[self.main_branch.name], Decimal("100.00"))
        self.assertNotIn("Outside Scope Main Branch", revenue_by_name)
        self.assertEqual(len(dashboard["branch_links"]), 1)
        self.assertEqual(
            [metric["label"] for metric in dashboard["metrics"]],
            ["Revenue", "Sales Orders", "Avg. Order Value", "Promo Savings Given"],
        )
        self.assertEqual(len(dashboard["trend"]["points"]), 12)

    def test_dashboard_rejects_unbounded_period(self):
        dashboard = build_sales_dashboard(
            self.user,
            {"date_from": "2020-01-01", "date_to": "2026-11-11"},
            context=self.context,
            today=date(2026, 11, 11),
        )

        self.assertIn("at most 366 days", dashboard["filter_error"])
        self.assertEqual(dashboard["period_label"], "01/11/2026 – 11/11/2026")

    def test_dashboard_query_count_does_not_grow_with_orders(self):
        self._order("SO-SMALL-1", total="10.00", branch=self.main_branch)
        self._order("SO-SMALL-2", total="20.00", branch=self.main_branch)
        params = {"date_from": "2026-11-01", "date_to": "2026-11-11"}
        build_sales_dashboard(self.user, params, context=self.context)

        with CaptureQueriesContext(connection) as small_context:
            small = build_sales_dashboard(self.user, params, context=self.context)

        extra_orders = [
            SaleOrder(
                company=self.company,
                customer=self.customer,
                number=f"SO-SCALE-{index:02d}",
                status=SaleOrder.Status.SALE,
                order_date=date(2026, 11, 7),
                total=Decimal("5.00"),
            )
            for index in range(10)
        ]
        SaleOrder.objects.bulk_create(extra_orders)
        with CaptureQueriesContext(connection) as large_context:
            large = build_sales_dashboard(self.user, params, context=self.context)

        self.assertEqual(len(small_context), len(large_context))
        self.assertEqual(small["order_count"], 2)
        self.assertEqual(large["order_count"], 12)
        self.assertLessEqual(len(large["branches"]), 8)
        self.assertLessEqual(len(large["branch_links"]), 13)

    def test_sales_dashboard_route_renders_tour_and_branch_network(self):
        ERPApp.objects.update_or_create(
            slug="sales",
            defaults={
                "name": "Sales",
                "category": "Commercial",
                "summary": "Sales operations",
                "enabled": True,
                "status": ERPApp.Status.STABLE,
            },
        )
        self.client.force_login(self.user)

        response = self.client.get(
            "/apps/sales/feature/dashboard/",
            {"date_from": "2026-11-01", "date_to": "2026-11-11"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Branch network")
        self.assertContains(response, "Demo fitur ini")
        self.assertContains(response, "data-sales-tour")
        self.assertContains(response, "Revenue trend")
