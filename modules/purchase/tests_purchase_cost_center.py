from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse
from modules.finance.models import (
    Account,
    AccountingCostCenter,
    AccountJournal,
    AccountTag,
    JournalEntry,
    JournalLine,
    JournalLineCostCenterAllocation,
)

from .invoice_extension import apply_purchase_cost_centers_to_journal_entry
from .models import (
    BlanketOrder,
    BlanketOrderLine,
    PurchaseJournalLineAnalyticSnapshot,
    PurchaseOrderControl,
    PurchaseOrderLinePricingProvenance,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseTender,
    PurchaseTenderLine,
)
from .purchase_cost_center import (
    cost_center_analytic_values,
    purchase_invoice_analytic_values,
    relation_id,
    validate_purchase_cost_center_batch,
)


class PurchaseCostCenterParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username="purchase-cost-center",
            password="test-password",
        )
        self.company = Company.objects.create(code="PCC", name="Purchase Cost Center")
        self.other_company = Company.objects.create(code="PCC2", name="Other Company")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="PCC-VENDOR",
            name="Cost Center Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="PCC-WH",
            name="Cost Center Warehouse",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="PCC-P",
            name="Cost Center Product",
        )
        self.cost_center_a = AccountingCostCenter.objects.create(
            company=self.company,
            code="CC-A",
            name="Cost Center A",
        )
        self.cost_center_b = AccountingCostCenter.objects.create(
            company=self.company,
            code="CC-B",
            name="Cost Center B",
        )
        self.other_cost_center = AccountingCostCenter.objects.create(
            company=self.other_company,
            code="CC-X",
            name="Other Cost Center",
        )
        self.tag = AccountTag.objects.create(
            company=self.company,
            code="PROJECT",
            name="Project",
        )
        self.account = Account.objects.create(
            company=self.company,
            code="6100",
            name="Purchase Expense",
            account_type=Account.AccountType.EXPENSE,
        )
        self.journal = AccountJournal.objects.create(
            company=self.company,
            code="PUR",
            name="Purchase Journal",
            journal_type=AccountJournal.JournalType.PURCHASE,
        )
        self.order = PurchaseOrder.objects.create(
            company=self.company,
            number="PO-PCC",
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=timezone.localdate(),
        )

    def _assignment(self):
        return {
            "cost_center": "",
            "cost_center_mode": "distribution",
            "cost_center_distribution_ids": [
                self.cost_center_a.pk,
                self.cost_center_b.pk,
            ],
            "cost_center_distribution_data": [
                {"cost_center_id": self.cost_center_a.pk, "percentage": "60"},
                {"cost_center_id": self.cost_center_b.pk, "percentage": "40"},
            ],
            "cost_center_analytic_tag_ids": [self.tag.pk],
        }

    def _entry(self, suffix, count):
        entry = JournalEntry.objects.create(
            company=self.company,
            journal=self.journal,
            number=f"JE-PCC-{suffix}",
            entry_date=timezone.localdate(),
        )
        JournalLine.objects.bulk_create(
            [
                JournalLine(
                    entry=entry,
                    account=self.account,
                    description=f"Line {index}",
                    debit=Decimal("1"),
                )
                for index in range(count)
            ]
        )
        return entry

    def test_structured_fields_cover_all_hmx_purchase_transaction_models(self):
        for model in (
            PurchaseRequest,
            PurchaseRequestLine,
            PurchaseTender,
            PurchaseTenderLine,
            BlanketOrder,
            BlanketOrderLine,
            PurchaseOrderControl,
            PurchaseOrderLinePricingProvenance,
        ):
            field_names = {field.name for field in model._meta.get_fields()}
            self.assertIn("cost_center_distribution_ids", field_names)
            self.assertIn("cost_center_analytic_tag_ids", field_names)
            self.assertIn("cost_center_distribution_data", field_names)
            self.assertIn("cost_center_mode", field_names)

    def test_relation_id_and_line_to_header_fallback_match_hmx_contract(self):
        self.assertEqual(relation_id(self.cost_center_a), self.cost_center_a.pk)
        self.assertEqual(relation_id(str(self.cost_center_a.pk)), self.cost_center_a.pk)
        self.assertEqual(relation_id({"value": self.cost_center_a.pk}), self.cost_center_a.pk)

        header = PurchaseRequest.objects.create(
            company=self.company,
            number="PR-PCC",
            requestor=self.actor,
            request_date=timezone.localdate(),
            justification="Cost center fallback",
            **self._assignment(),
        )
        line = PurchaseRequestLine.objects.create(
            request=header,
            product=self.product,
            description=self.product.name,
        )

        values = cost_center_analytic_values(line, parent=header)
        invoice_values = purchase_invoice_analytic_values(header, [line])
        self.assertEqual(values["cost_center_mode"], "distribution")
        self.assertEqual(
            values["cost_center_distribution_ids"],
            [self.cost_center_a.pk, self.cost_center_b.pk],
        )
        self.assertEqual(invoice_values["cost_center_analytic_tag_ids"], [self.tag.pk])

    def test_distribution_validation_is_company_safe_and_requires_exact_total(self):
        validated = validate_purchase_cost_center_batch(
            self.company.pk,
            [self._assignment()],
        )[0]
        self.assertEqual(validated["cost_center_mode"], "distribution")

        invalid_total = self._assignment()
        invalid_total["cost_center_distribution_data"][1]["percentage"] = "30"
        with self.assertRaisesMessage(ValidationError, "exactly 100%"):
            validate_purchase_cost_center_batch(self.company.pk, [invalid_total])

        wrong_company = self._assignment()
        wrong_company["cost_center_distribution_ids"] = [self.other_cost_center.pk]
        wrong_company["cost_center_distribution_data"] = [
            {"cost_center_id": self.other_cost_center.pk, "percentage": "100"}
        ]
        with self.assertRaisesMessage(ValidationError, "must belong"):
            validate_purchase_cost_center_batch(self.company.pk, [wrong_company])

    def test_purchase_assignment_materializes_finance_allocations_and_tag_snapshot(self):
        entry = self._entry("MATERIALIZE", 2)
        single = {
            "cost_center": "CC-A",
            "cost_center_mode": "single",
            "cost_center_distribution_ids": [self.cost_center_a.pk],
            "cost_center_distribution_data": [],
            "cost_center_analytic_tag_ids": [self.tag.pk],
        }

        apply_purchase_cost_centers_to_journal_entry(
            entry,
            order=self.order,
            assignments=[self._assignment(), single],
            order_line_ids=[None, None],
        )

        first, second = list(entry.lines.order_by("pk"))
        first_allocations = list(
            first.cost_center_allocations.order_by("cost_center_id").values_list(
                "cost_center_id",
                "percentage",
            )
        )
        self.assertEqual(
            first_allocations,
            [
                (self.cost_center_a.pk, Decimal("60.0000")),
                (self.cost_center_b.pk, Decimal("40.0000")),
            ],
        )
        second.refresh_from_db()
        self.assertEqual(second.cost_center, self.cost_center_a)
        self.assertEqual(
            list(second.cost_center_allocations.values_list("percentage", flat=True)),
            [Decimal("100.0000")],
        )
        snapshot = PurchaseJournalLineAnalyticSnapshot.objects.get(journal_line=first)
        self.assertEqual(snapshot.cost_center_analytic_tag_ids, [self.tag.pk])
        self.assertEqual(snapshot.cost_center_mode, "distribution")

    def test_journal_snapshot_query_count_does_not_grow_with_line_count(self):
        assignment = {
            "cost_center": "CC-A",
            "cost_center_mode": "single",
            "cost_center_distribution_ids": [self.cost_center_a.pk],
            "cost_center_distribution_data": [],
            "cost_center_analytic_tag_ids": [self.tag.pk],
        }
        baseline_entry = self._entry("BASE", 2)
        with CaptureQueriesContext(connection) as baseline:
            apply_purchase_cost_centers_to_journal_entry(
                baseline_entry,
                order=self.order,
                assignments=[assignment for _index in range(2)],
                order_line_ids=[None, None],
            )

        grown_entry = self._entry("GROWN", 12)
        with CaptureQueriesContext(connection) as grown:
            apply_purchase_cost_centers_to_journal_entry(
                grown_entry,
                order=self.order,
                assignments=[assignment for _index in range(12)],
                order_line_ids=[None for _index in range(12)],
            )

        self.assertLessEqual(len(grown), len(baseline) + 1)
        self.assertEqual(
            JournalLineCostCenterAllocation.objects.filter(journal_line__entry=grown_entry).count(),
            12,
        )
