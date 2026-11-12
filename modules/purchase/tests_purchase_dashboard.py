from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse
from modules.apphub.models import ERPApp
from modules.organization.context import OrganizationContext, reset_current_context, set_current_context

from .dashboard import build_purchase_analytics
from .models import GoodsReceipt, GoodsReceiptLine


class PurchaseReceivingDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="purchase-dashboard-admin",
            email="purchase-dashboard@example.com",
            password="test-password",
        )
        self.company = Company.objects.create(code="PDB", name="Purchase Dashboard")
        self.other_company = Company.objects.create(code="PDB-2", name="Other Purchase Dashboard")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="PDB-VENDOR",
            name="Dashboard Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.other_vendor = Contact.objects.create(
            company=self.other_company,
            code="OTHER-VENDOR",
            name="Other Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.product = Product.objects.create(company=self.company, sku="PDB-ITEM", name="Dashboard Item")
        self.other_product = Product.objects.create(
            company=self.other_company,
            sku="OTHER-ITEM",
            name="Other Item",
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            branch=self.company.branches.get(is_main=True),
            code="PDB-WH",
            name="Dashboard Warehouse",
        )
        self.other_warehouse = Warehouse.objects.create(
            company=self.other_company,
            branch=self.other_company.branches.get(is_main=True),
            code="OTHER-WH",
            name="Other Warehouse",
        )

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

    def _order(
        self,
        number,
        *,
        quantity,
        received_quantity,
        unit_price,
        company=None,
        status=PurchaseOrder.Status.CONFIRMED,
    ):
        company = company or self.company
        vendor = self.vendor if company == self.company else self.other_vendor
        product = self.product if company == self.company else self.other_product
        warehouse = self.warehouse if company == self.company else self.other_warehouse
        percentage = Decimal(received_quantity) / Decimal(quantity) * Decimal("100")
        order = PurchaseOrder.objects.create(
            company=company,
            number=number,
            vendor=vendor,
            warehouse=warehouse,
            order_date=date(2026, 11, 5),
            status=status,
            received_percentage=percentage,
        )
        total = Decimal(quantity) * Decimal(unit_price)
        PurchaseOrder.objects.filter(pk=order.pk).update(total=total)
        line = PurchaseOrderLine.objects.create(
            order=order,
            product=product,
            warehouse=warehouse,
            quantity=quantity,
            unit_price=unit_price,
            received_quantity=received_quantity,
        )
        order.refresh_from_db()
        return order, line

    def _receipt(self, number, order, line, *, quantity, delivery_status=GoodsReceipt.DeliveryStatus.ON_TIME):
        receipt = GoodsReceipt.objects.create(
            company=order.company,
            number=number,
            order=order,
            warehouse=order.warehouse,
            receipt_date=date(2026, 11, 8),
            status=GoodsReceipt.Status.RECEIVED,
            delivery_status=delivery_status,
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt,
            order_line=line,
            received_quantity=quantity,
            accepted_quantity=quantity,
        )
        return receipt

    def test_dashboard_tracks_po_to_receipt_values_and_company_scope(self):
        partial_order, partial_line = self._order(
            "PO-PARTIAL",
            quantity="10",
            received_quantity="4",
            unit_price="100",
        )
        full_order, full_line = self._order(
            "PO-FULL",
            quantity="5",
            received_quantity="5",
            unit_price="200",
            status=PurchaseOrder.Status.COMPLETED,
        )
        self._receipt("GR-PARTIAL", partial_order, partial_line, quantity="4")
        self._receipt("GR-FULL", full_order, full_line, quantity="5")
        self._order(
            "PO-OUTSIDE",
            quantity="10",
            received_quantity="10",
            unit_price="999",
            company=self.other_company,
        )
        token = set_current_context(self._context(self.company))
        try:
            analytics = build_purchase_analytics(
                "overview",
                self.user,
                {"date_from": "2026-11-01", "date_to": "2026-11-11"},
            )
        finally:
            reset_current_context(token)

        self.assertEqual(analytics["view_type"], "purchase_receiving")
        self.assertEqual(analytics["metrics"][0]["value"], "Rp 2.000")
        self.assertEqual(analytics["metrics"][1]["value"], "Rp 1.400")
        self.assertEqual(analytics["metrics"][2]["value"], "Rp 600")
        self.assertEqual(analytics["metrics"][3]["value"], 2)
        self.assertEqual(analytics["completion_label"], "70.0%")
        pipeline = {row["label"]: row["value"] for row in analytics["pipeline"]}
        self.assertEqual(pipeline["Partially Received"], 1)
        self.assertEqual(pipeline["Fully Received"], 1)
        self.assertEqual(len(analytics["branches"]), 1)
        self.assertEqual(analytics["branches"][0]["value"], Decimal("2000"))
        self.assertEqual(analytics["branches"][0]["order_count"], 2)
        self.assertEqual({row["number"] for row in analytics["recent_orders"]}, {"PO-PARTIAL", "PO-FULL"})

    def test_dashboard_rejects_unbounded_period(self):
        token = set_current_context(self._context(self.company))
        try:
            analytics = build_purchase_analytics(
                "overview",
                self.user,
                {"date_from": "2020-01-01", "date_to": "2026-11-11"},
                today=date(2026, 11, 11),
            )
        finally:
            reset_current_context(token)

        self.assertIn("at most 366 days", analytics["filter_error"])
        self.assertEqual(analytics["period_label"], "01/11/2026 – 11/11/2026")

    def test_dashboard_query_count_does_not_grow_with_order_volume(self):
        self._order("PO-SMALL-1", quantity="2", received_quantity="0", unit_price="10")
        self._order("PO-SMALL-2", quantity="2", received_quantity="1", unit_price="10")
        params = {"date_from": "2026-11-01", "date_to": "2026-11-11"}

        token = set_current_context(self._context(self.company))
        try:
            build_purchase_analytics("overview", self.user, params)
            with CaptureQueriesContext(connection) as small_queries:
                small = build_purchase_analytics("overview", self.user, params)
            for index in range(10):
                self._order(
                    f"PO-SCALE-{index:02d}",
                    quantity="1",
                    received_quantity="0",
                    unit_price="5",
                )
            with CaptureQueriesContext(connection) as large_queries:
                large = build_purchase_analytics("overview", self.user, params)
        finally:
            reset_current_context(token)

        self.assertEqual(len(large_queries), len(small_queries))
        self.assertLessEqual(len(large_queries), 12)
        self.assertEqual(small["metrics"][0]["detail"], "2 active PO in period")
        self.assertEqual(large["metrics"][0]["detail"], "12 active PO in period")
        self.assertLessEqual(len(large["recent_orders"]), 8)
        self.assertLessEqual(len(large["vendors"]), 6)

    def test_purchase_dashboard_route_renders_receiving_flow(self):
        ERPApp.objects.update_or_create(
            slug="purchase",
            defaults={
                "name": "Purchase",
                "category": "Operations",
                "summary": "Purchase operations",
                "enabled": True,
                "status": ERPApp.Status.STABLE,
            },
        )
        self.client.force_login(self.user)

        response = self.client.get(
            "/apps/purchase/feature/overview/",
            {"date_from": "2026-11-01", "date_to": "2026-11-11"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Branch network")
        self.assertContains(response, "PO value by branch")
        self.assertContains(response, "Demo fitur ini")
        self.assertContains(response, "data-purchase-tour")
        self.assertContains(response, "Purchase Order → Goods Receipt")
        self.assertContains(response, "Receiving progress")
        self.assertContains(response, "Latest Purchase Orders")
        self.assertContains(response, "Latest Goods Receipts")
