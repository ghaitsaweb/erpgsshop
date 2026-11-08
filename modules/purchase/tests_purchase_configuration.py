from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Contact, Product, PurchaseOrder, Warehouse

from .models import (
    PurchaseMilestoneTemplate,
    PurchaseMilestoneTemplateLine,
    PurchaseOrderControl,
    PurchaseOrderTemplate,
    PurchaseOrderTemplateLine,
    PurchaseOrderType,
)
from .services import (
    allocate_purchase_document_number,
    allocate_purchase_document_numbers,
    apply_purchase_milestone_template,
    apply_purchase_order_template,
    confirm_purchase_order,
)


class PurchaseConfigurationParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="purchase-config-admin",
            password="test-password",
            email="purchase-config@example.com",
        )
        self.company = Company.objects.create(code="PCFG", name="Purchase Configuration")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="PCFG-VENDOR",
            name="Configuration Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.warehouse = Warehouse.objects.create(company=self.company, code="PCFG-WH", name="Warehouse")
        self.product = Product.objects.create(
            company=self.company,
            sku="PCFG-P",
            name="Configured Product",
            purchase_price=Decimal("25"),
        )
        self.order_type = PurchaseOrderType.objects.create(company=self.company, name="Standard Purchase")
        self.order_type.companies.add(self.company)
        self.order_template = PurchaseOrderTemplate.objects.create(
            company=self.company,
            name="Standard Products",
            vendor=self.vendor,
            order_type=self.order_type,
        )
        PurchaseOrderTemplateLine.objects.create(
            order_template=self.order_template,
            product=self.product,
            quantity=Decimal("2"),
            price=Decimal("25"),
        )
        self.milestone_template = PurchaseMilestoneTemplate.objects.create(
            company=self.company,
            name="Half and Half",
        )
        PurchaseMilestoneTemplateLine.objects.bulk_create(
            [
                PurchaseMilestoneTemplateLine(
                    template=self.milestone_template,
                    sequence=10,
                    milestone_name="Mobilization",
                    contract_term_percentage=Decimal("50"),
                ),
                PurchaseMilestoneTemplateLine(
                    template=self.milestone_template,
                    sequence=20,
                    milestone_name="Completion",
                    contract_term_percentage=Decimal("50"),
                    due_days=30,
                ),
            ]
        )

    def _order(self, suffix):
        return PurchaseOrder.objects.create(
            company=self.company,
            number=f"DRAFT-{suffix}",
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=timezone.localdate(),
        )

    def test_company_sequence_allocates_monthly_document_numbers_in_one_batch(self):
        numbers = allocate_purchase_document_numbers(
            self.order_type.pk,
            self.company.pk,
            "rfq",
            count=3,
        )
        next_number = allocate_purchase_document_number(
            self.order_type.pk,
            self.company.pk,
            "rfq",
        )
        self.assertEqual([number.rsplit("/", 1)[-1] for number in numbers], ["001", "002", "003"])
        self.assertTrue(next_number.endswith("/004"))
        self.assertIn(self.order_type.prefix_sequence, next_number)

    def test_order_and_milestone_templates_apply_to_draft_purchase_order(self):
        order = self._order("APPLY")
        apply_purchase_order_template(order, self.order_template, self.actor)
        apply_purchase_milestone_template(order, self.milestone_template, self.actor)
        order.refresh_from_db()
        self.assertEqual(order.lines.get().quantity, Decimal("2.000"))
        self.assertEqual(order.total, Decimal("50.00"))
        self.assertEqual(list(order.milestones.values_list("percentage", flat=True)), [Decimal("50.00"), Decimal("50.00")])
        control = PurchaseOrderControl.objects.get(order=order)
        self.assertEqual(control.order_template, self.order_template)
        self.assertEqual(control.milestone_template, self.milestone_template)

    def test_confirmation_promotes_draft_sequence_to_purchase_order_sequence(self):
        order = self._order("CONFIRM")
        apply_purchase_order_template(order, self.order_template, self.actor)
        confirm_purchase_order(order, self.actor)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)
        self.assertTrue(order.number.startswith(f"PO/{self.order_type.prefix_sequence}/"))
        self.assertIsNotNone(order.purchase_control.confirmed_at)

    def test_template_application_query_count_does_not_grow_with_lines(self):
        baseline_order = self._order("BASE")
        with CaptureQueriesContext(connection) as baseline:
            apply_purchase_order_template(baseline_order, self.order_template, self.actor)

        PurchaseOrderTemplateLine.objects.bulk_create(
            [
                PurchaseOrderTemplateLine(
                    order_template=self.order_template,
                    sequence=20 + index,
                    product=self.product,
                    description=f"Configured Product {index}",
                    quantity=Decimal("1"),
                    uom="Unit",
                    price=Decimal("25"),
                )
                for index in range(30)
            ]
        )
        grown_order = self._order("GROWN")
        with CaptureQueriesContext(connection) as grown:
            apply_purchase_order_template(grown_order, self.order_template, self.actor)

        self.assertLessEqual(len(grown), len(baseline) + 1)
