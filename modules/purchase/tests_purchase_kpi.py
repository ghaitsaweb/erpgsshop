from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse

from .models import (
    PurchaseKPITarget,
    PurchaseKPITemplate,
    PurchaseKPITemplateLine,
    PurchaseOrderControl,
    PurchaseTeam,
)
from .services import recalculate_purchase_kpi_target, transition_purchase_kpi_target


class PurchaseKPIParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="purchase-kpi-admin",
            password="test-password",
            email="purchase-kpi@example.com",
        )
        self.company = Company.objects.create(code="PKPI", name="Purchase KPI")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="PKPI-VENDOR",
            name="KPI Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.warehouse = Warehouse.objects.create(company=self.company, code="PKPI-WH", name="KPI Warehouse")
        self.product = Product.objects.create(company=self.company, sku="PKPI-P", name="KPI Product")
        self.team = PurchaseTeam.objects.create(
            company=self.company,
            name="Operational Buying",
            manager=self.actor,
        )
        self.template = PurchaseKPITemplate.objects.create(
            company=self.company,
            name="Balanced Procurement",
        )
        PurchaseKPITemplateLine.objects.create(
            template=self.template,
            sequence=10,
            metric="po_count",
            weight=Decimal("50"),
            default_target=Decimal("2"),
        )
        PurchaseKPITemplateLine.objects.create(
            template=self.template,
            sequence=20,
            metric="supplier_compliance",
            weight=Decimal("50"),
            default_target=Decimal("90"),
        )
        self.template.refresh_from_db()
        today = timezone.localdate()
        self.target = PurchaseKPITarget.objects.create(
            company=self.company,
            name="November procurement target",
            team=self.team,
            template=self.template,
            period_start=today - timedelta(days=15),
            period_end=today + timedelta(days=15),
        )

    def _order(self, suffix, *, compliant=True):
        order = PurchaseOrder.objects.create(
            company=self.company,
            number=f"PO-PKPI-{suffix}",
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=timezone.localdate(),
            status=PurchaseOrder.Status.CONFIRMED,
        )
        PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            quantity=Decimal("10"),
            unit_price=Decimal("20"),
            received_quantity=Decimal("10") if compliant else Decimal("5"),
        )
        PurchaseOrderControl.objects.create(
            order=order,
            team=self.team,
            assigned_to=self.actor,
            confirmed_at=timezone.now(),
        )
        return order

    def test_template_snapshots_weighted_lines_and_lifecycle(self):
        self.assertTrue(self.team.code.startswith("PT-"))
        self.assertEqual(self.target.template_version, self.template.version)
        self.assertEqual(
            list(self.target.lines.values_list("metric", "snapshot_weight")),
            [("po_count", Decimal("50.00")), ("supplier_compliance", Decimal("50.00"))],
        )

        transition_purchase_kpi_target(self.target, "submit", self.actor)
        transition_purchase_kpi_target(self.target, "approve", self.actor)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, PurchaseKPITarget.Status.ACTIVE)
        self.assertEqual(self.target.activated_by, self.actor)

        with self.assertRaisesMessage(ValidationError, "Only a Draft"):
            self.target.template = PurchaseKPITemplate.objects.create(
                company=self.company,
                name="Replacement",
            )
            self.target.full_clean()

    def test_calculation_uses_database_aggregates_and_finalizes(self):
        self._order("001", compliant=True)
        self._order("002", compliant=False)
        transition_purchase_kpi_target(self.target, "submit", self.actor)
        transition_purchase_kpi_target(self.target, "approve", self.actor)
        recalculate_purchase_kpi_target(self.target, self.actor)

        results = dict(self.target.lines.values_list("metric", "actual_value"))
        self.assertEqual(results["po_count"], Decimal("2.0000"))
        self.assertEqual(results["supplier_compliance"], Decimal("50.0000"))

        transition_purchase_kpi_target(self.target, "close", self.actor)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, PurchaseKPITarget.Status.CLOSED)
        self.assertEqual(self.target.closed_by, self.actor)

    def test_calculation_query_count_does_not_grow_with_purchase_orders(self):
        self._order("BASE")
        transition_purchase_kpi_target(self.target, "submit", self.actor)
        transition_purchase_kpi_target(self.target, "approve", self.actor)
        with CaptureQueriesContext(connection) as baseline:
            recalculate_purchase_kpi_target(self.target, self.actor)

        for index in range(20):
            self._order(f"GROW-{index:02d}", compliant=index % 2 == 0)
        with CaptureQueriesContext(connection) as grown:
            recalculate_purchase_kpi_target(self.target, self.actor)

        self.assertLessEqual(len(grown), len(baseline) + 1)

    def test_smart_actions_keep_exact_template_and_target_breadcrumbs(self):
        self.client.force_login(self.actor)
        template_response = self.client.get(f"/app/purchase-kpi-templates/{self.template.pk}/edit/")
        target_response = self.client.get(f"/app/purchase-kpi-targets/{self.target.pk}/edit/")
        self.assertContains(template_response, "KPI Parameters")
        self.assertContains(target_response, "KPI Results")

        line_list = self.client.get(
            f"/app/purchase-kpi-target-lines/?smart_action=purchase-kpi-targets.lines&source_id={self.target.pk}"
        )
        self.assertEqual(line_list.status_code, 200)
        self.assertContains(line_list, f"/app/purchase-kpi-targets/{self.target.pk}/edit/")

    def test_invalid_template_weight_is_rejected_before_submission(self):
        self.template.lines.filter(metric="supplier_compliance").update(weight=Decimal("40"))
        with self.assertRaisesMessage(ValidationError, "exactly 100%"):
            transition_purchase_kpi_target(self.target, "submit", self.actor)
