from datetime import date, datetime, time, timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.models import Company, Employee, Product, StockMove, Warehouse
from modules.apphub.domain_dashboards import build_inventory_dashboard, build_manufacturing_dashboard
from modules.apphub.models import ERPApp
from modules.finance.dashboard import build_accounting_analytics
from modules.finance.models import JournalEntry
from modules.hr.dashboard import build_hr_analytics
from modules.operations.models import BillOfMaterial, ManufacturingOrder
from modules.organization.context import OrganizationContext, reset_current_context, set_current_context


class BranchDashboardTests(TestCase):
    params = {"date_from": "2026-11-01", "date_to": "2026-11-12"}

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="branch-dashboard-admin", email="dashboards@example.com", password="test-password"
        )
        self.company = Company.objects.create(code="DBR", name="Dashboard Branch")
        self.branch = self.company.branches.get(is_main=True)
        self.product = Product.objects.create(
            company=self.company, sku="DBR-ITEM", name="Dashboard Item",
            product_type=Product.ProductType.STOCKABLE, reorder_level=Decimal("5"),
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company, branch=self.branch, code="DBR-WH", name="Dashboard Warehouse"
        )
        self.bom = BillOfMaterial.objects.create(
            company=self.company, code="DBR-BOM", product=self.product, output_quantity=1
        )
        for slug, name in (
            ("inventory", "Inventory"), ("hr", "Human Resources"),
            ("manufacturing", "Manufacturing"), ("accounting", "Accounting"),
        ):
            ERPApp.objects.update_or_create(
                slug=slug,
                defaults={"name": name, "category": "Operations", "summary": name, "enabled": True, "status": ERPApp.Status.STABLE},
            )

    def _context(self):
        return OrganizationContext(
            company_id=self.company.pk,
            branch_id=self.branch.pk,
            allowed_company_ids=(self.company.pk,),
            allowed_branch_ids=(self.branch.pk,),
            selected_company_ids=(self.company.pk,),
            selected_branch_ids=(self.branch.pk,),
        )

    def _seed(self, suffix="1"):
        StockMove.objects.create(
            company=self.company, product=self.product, warehouse=self.warehouse,
            direction=StockMove.Direction.IN, quantity=1, reference=f"MOVE-{suffix}",
            movement_date=date(2026, 11, 5), created_by=self.user,
        )
        ManufacturingOrder.objects.create(
            company=self.company, number=f"MO-{suffix}", bom=self.bom, quantity=1,
            warehouse=self.warehouse,
            planned_start=datetime.combine(date(2026, 11, 5), time(8), tzinfo=dt_timezone.utc),
            planned_end=datetime.combine(date(2026, 11, 5), time(17), tzinfo=dt_timezone.utc),
            status=ManufacturingOrder.Status.CONFIRMED,
        )
        Employee.objects.create(
            company=self.company, branch=self.branch, employee_id=f"EMP-{suffix}",
            name=f"Employee {suffix}", department="Operations", join_date=date(2026, 1, 1),
        )
        JournalEntry.objects.create(
            company=self.company, number=f"JE-{suffix}", entry_date=date(2026, 11, 5),
            status=JournalEntry.Status.POSTED, total_debit=100, total_credit=100,
        )

    def test_four_dashboard_routes_render_branch_network_and_demo(self):
        self._seed()
        self.client.force_login(self.user)
        expectations = (
            ("/apps/inventory/feature/dashboard/", "Inventory Dashboard"),
            ("/apps/hr/feature/dashboard/", "Human Resources Dashboard"),
            ("/apps/manufacturing/feature/dashboard/", "Manufacturing Dashboard"),
            ("/apps/accounting/feature/dashboard/", "Accounting Dashboard"),
        )
        for url, title in expectations:
            with self.subTest(url=url):
                response = self.client.get(url, self.params)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, title)
                self.assertContains(response, "Branch network")
                self.assertContains(response, "Demo fitur ini")
                self.assertContains(response, "branch-dashboard-tour-steps")

    def test_dashboard_query_counts_do_not_grow_with_record_volume(self):
        self._seed("SMALL")
        builders = (
            lambda: build_inventory_dashboard(self.user, self.params),
            lambda: build_manufacturing_dashboard(self.user, self.params),
            lambda: build_hr_analytics("dashboard", self.user, self.params),
            lambda: build_accounting_analytics("dashboard", self.user, self.params),
        )
        token = set_current_context(self._context())
        try:
            small_counts = []
            for builder in builders:
                builder()
                with CaptureQueriesContext(connection) as queries:
                    payload = builder()
                small_counts.append(len(queries))
                self.assertLessEqual(len(payload["recent_rows"]), 8)
                self.assertLessEqual(len(payload["branches"]), 8)

            for index in range(10):
                self._seed(f"SCALE-{index:02d}")

            large_counts = []
            for builder in builders:
                with CaptureQueriesContext(connection) as queries:
                    payload = builder()
                large_counts.append(len(queries))
                self.assertLessEqual(len(payload["recent_rows"]), 8)
                self.assertLessEqual(len(payload["branches"]), 8)
        finally:
            reset_current_context(token)

        self.assertEqual(large_counts, small_counts)
        self.assertTrue(all(count <= 18 for count in large_counts))

    def test_period_is_bounded_for_all_dashboards(self):
        params = {"date_from": "2020-01-01", "date_to": "2026-11-12"}
        token = set_current_context(self._context())
        try:
            payloads = (
                build_inventory_dashboard(self.user, params),
                build_manufacturing_dashboard(self.user, params),
                build_hr_analytics("dashboard", self.user, params),
                build_accounting_analytics("dashboard", self.user, params),
            )
        finally:
            reset_current_context(token)
        for payload in payloads:
            self.assertIn("at most 366 days", payload["filter_error"])
