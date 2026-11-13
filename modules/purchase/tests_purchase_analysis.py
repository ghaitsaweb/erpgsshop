from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse
from modules.organization.context import OrganizationContext, reset_current_context, set_current_context

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrderLinePricingProvenance,
    PurchaseQuotation,
    PurchaseQuotationLine,
    PurchaseRequest,
    PurchaseRequestLine,
)
from .purchase_analysis import (
    PURCHASE_ORDER_ANALYSIS_CONTRACT,
    PURCHASE_REQUEST_ANALYSIS_CONTRACT,
    build_purchase_order_analysis,
    build_purchase_request_analysis,
)


class PurchaseAnalysisParityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="purchase-analysis",
            password="test-password",
            email="purchase-analysis@example.com",
        )
        self.company = Company.objects.create(code="PAN", name="Purchase Analysis")
        self.other_company = Company.objects.create(code="PAN2", name="Other Analysis")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="PAN-V",
            name="Analysis Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.other_vendor = Contact.objects.create(
            company=self.other_company,
            code="PAN2-V",
            name="Other Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="PAN-WH",
            name="Analysis Warehouse",
        )
        self.other_warehouse = Warehouse.objects.create(
            company=self.other_company,
            code="PAN2-WH",
            name="Other Warehouse",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="PAN-P",
            name="Analysis Product",
        )
        self.other_product = Product.objects.create(
            company=self.other_company,
            sku="PAN2-P",
            name="Other Product",
        )

    def _context(self):
        branch = self.company.branches.get(is_main=True)
        return OrganizationContext(
            company_id=self.company.pk,
            branch_id=branch.pk,
            allowed_company_ids=(self.company.pk, self.other_company.pk),
            allowed_branch_ids=tuple(
                self.company.branches.values_list("pk", flat=True)
            )
            + tuple(self.other_company.branches.values_list("pk", flat=True)),
            selected_company_ids=(self.company.pk,),
            selected_branch_ids=(branch.pk,),
        )

    def _order(self, number, *, status, company=None):
        company = company or self.company
        return PurchaseOrder.objects.create(
            company=company,
            number=number,
            vendor=self.vendor if company == self.company else self.other_vendor,
            warehouse=self.warehouse if company == self.company else self.other_warehouse,
            order_date=date(2026, 11, 5),
            status=status,
        )

    def test_analysis_contracts_match_hmx_product_month_measures(self):
        self.assertEqual(PURCHASE_ORDER_ANALYSIS_CONTRACT["row_groupby"], ["product"])
        self.assertEqual(PURCHASE_ORDER_ANALYSIS_CONTRACT["column_groupby"], ["expected_date:month"])
        self.assertEqual(
            PURCHASE_ORDER_ANALYSIS_CONTRACT["measures"],
            [
                "quantity",
                "total_discount_line_amount",
                "untaxed_amount",
                "taxes_amount",
                "total_amount",
                "subtotal_lines",
            ],
        )
        self.assertEqual(
            PURCHASE_REQUEST_ANALYSIS_CONTRACT["measures"],
            ["quantity", "approved_qty", "rfq_qty", "po_qty", "received_qty"],
        )
        self.assertNotIn("department", PURCHASE_ORDER_ANALYSIS_CONTRACT["dimensions"])

    def test_po_analysis_filters_drafts_and_company_and_calculates_real_measures(self):
        confirmed = self._order("PO-PAN-CONF", status=PurchaseOrder.Status.CONFIRMED)
        line = PurchaseOrderLine.objects.create(
            order=confirmed,
            product=self.product,
            warehouse=self.warehouse,
            quantity=Decimal("2"),
            unit_price=Decimal("100"),
        )
        PurchaseOrderLinePricingProvenance.objects.create(
            order_line=line,
            expected_date=date(2026, 11, 20),
            discount_percent=Decimal("10"),
            tax_percent=Decimal("11"),
        )
        draft = self._order("PO-PAN-DRAFT", status=PurchaseOrder.Status.DRAFT)
        PurchaseOrderLine.objects.create(
            order=draft,
            product=self.product,
            quantity=Decimal("99"),
            unit_price=Decimal("999"),
        )
        outside = self._order(
            "PO-PAN-OUTSIDE",
            status=PurchaseOrder.Status.CONFIRMED,
            company=self.other_company,
        )
        PurchaseOrderLine.objects.create(
            order=outside,
            product=self.other_product,
            quantity=Decimal("50"),
            unit_price=Decimal("500"),
        )

        token = set_current_context(self._context())
        try:
            analysis = build_purchase_order_analysis(
                self.user,
                {"date_from": "2026-11-01", "date_to": "2026-11-30"},
            )
        finally:
            reset_current_context(token)

        self.assertEqual(len(analysis["rows"]), 1)
        row = analysis["rows"][0]
        self.assertEqual(row["quantity"], Decimal("2.000"))
        self.assertEqual(row["subtotal_lines"], Decimal("200"))
        self.assertEqual(row["total_discount_line_amount"], Decimal("20"))
        self.assertEqual(row["untaxed_amount"], Decimal("180"))
        self.assertEqual(row["taxes_amount"], Decimal("19.8"))
        self.assertEqual(row["total_amount"], Decimal("199.8"))

    def test_pr_analysis_filters_unapproved_lines_and_tracks_received_quantity(self):
        request = PurchaseRequest.objects.create(
            company=self.company,
            number="PR-PAN",
            requestor=self.user,
            request_date=date(2026, 11, 1),
            required_date=date(2026, 11, 15),
            destination_warehouse=self.warehouse,
            justification="Analysis",
            status=PurchaseRequest.Status.IN_PROGRESS,
        )
        request_line = PurchaseRequestLine.objects.create(
            request=request,
            product=self.product,
            description=self.product.name,
            quantity=Decimal("10"),
            approved_quantity=Decimal("8"),
            rfq_quantity=Decimal("8"),
            ordered_quantity=Decimal("5"),
            line_status=PurchaseRequestLine.Status.PARTIALLY_ORDERED,
        )
        quotation = PurchaseQuotation.objects.create(
            company=self.company,
            number="RFQ-PAN",
            request=request,
            vendor=self.vendor,
            warehouse=self.warehouse,
            issue_date=date(2026, 11, 2),
            response_deadline=date(2026, 11, 30),
        )
        quotation_line = PurchaseQuotationLine.objects.create(
            quotation=quotation,
            request_line=request_line,
            product=self.product,
            description=self.product.name,
            quantity=Decimal("5"),
            unit_price=Decimal("100"),
        )
        order = self._order("PO-PAN-PR", status=PurchaseOrder.Status.CONFIRMED)
        order_line = PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            warehouse=self.warehouse,
            quantity=Decimal("5"),
            unit_price=Decimal("100"),
        )
        PurchaseOrderLinePricingProvenance.objects.create(
            order_line=order_line,
            quotation_line=quotation_line,
        )
        receipt = GoodsReceipt.objects.create(
            company=self.company,
            number="GR-PAN",
            order=order,
            warehouse=self.warehouse,
            receipt_date=date(2026, 11, 10),
            status=GoodsReceipt.Status.RECEIVED,
        )
        GoodsReceiptLine.objects.create(
            receipt=receipt,
            order_line=order_line,
            received_quantity=Decimal("3"),
            accepted_quantity=Decimal("3"),
        )
        rejected_request = PurchaseRequest.objects.create(
            company=self.company,
            number="PR-PAN-DRAFT",
            requestor=self.user,
            request_date=date(2026, 11, 1),
            justification="Excluded",
            status=PurchaseRequest.Status.DRAFT,
        )
        PurchaseRequestLine.objects.create(
            request=rejected_request,
            product=self.product,
            description="Excluded",
            quantity=Decimal("99"),
        )

        token = set_current_context(self._context())
        try:
            analysis = build_purchase_request_analysis(
                self.user,
                {"date_from": "2026-11-01", "date_to": "2026-11-30"},
            )
        finally:
            reset_current_context(token)

        self.assertEqual(len(analysis["rows"]), 1)
        row = analysis["rows"][0]
        self.assertEqual(row["quantity"], Decimal("10.000"))
        self.assertEqual(row["approved_qty"], Decimal("8.000"))
        self.assertEqual(row["rfq_qty"], Decimal("8.000"))
        self.assertEqual(row["po_qty"], Decimal("5.000"))
        self.assertEqual(row["received_qty"], Decimal("3.000"))

    def test_analysis_query_count_is_independent_of_group_count_and_views_are_exposed(self):
        for index in range(12):
            product = Product.objects.create(
                company=self.company,
                sku=f"PAN-Q-{index}",
                name=f"Analysis Query {index}",
            )
            order = self._order(f"PO-PAN-Q-{index}", status=PurchaseOrder.Status.CONFIRMED)
            PurchaseOrderLine.objects.create(
                order=order,
                product=product,
                quantity=Decimal("1"),
                unit_price=Decimal("10"),
            )
        token = set_current_context(self._context())
        try:
            with CaptureQueriesContext(connection) as queries:
                analysis = build_purchase_order_analysis(
                    self.user,
                    {"date_from": "2026-11-01", "date_to": "2026-11-30"},
                )
        finally:
            reset_current_context(token)
        self.assertEqual(len(analysis["rows"]), 12)
        self.assertLessEqual(len(queries), 2)

        self.client.force_login(self.user)
        response = self.client.get(
            reverse("purchase_order_analysis"),
            {"date_from": "2026-11-01", "date_to": "2026-11-30"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Purchase Order Analysis")
        self.assertEqual(
            reverse("purchase_request_analysis"),
            "/app/purchase-reports/request-analysis/",
        )
