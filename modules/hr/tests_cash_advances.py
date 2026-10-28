from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Employee

from .models import CashAdvance, CashAdvanceLine, ExpenseProduct
from .cash_advances import validate_cash_advance_lines


class CashAdvanceDocumentTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser(
            username="advance-manager", email="advance@example.test", password="test-password-123"
        )
        self.client.force_login(user)
        self.company = Company.objects.create(code="ADV", name="Advance Company")
        self.other_company = Company.objects.create(code="OTH", name="Other Company")
        self.employee = Employee.objects.create(
            company=self.company, employee_id="ADV-1", name="Advance Employee", join_date=timezone.localdate()
        )
        self.product = ExpenseProduct.objects.create(
            company=self.company, code="TRAVEL", name="Travel Advance", activity_type="advance"
        )

    def _payload(self, *, product=None, amount="125.00", quantity="2.00"):
        return {
            "employee": str(self.employee.pk),
            "number": "ADV-0001",
            "request_date": timezone.localdate().isoformat(),
            "purpose": "Travel",
            "settlement_due_date": "",
            "document_lines-TOTAL_FORMS": "1",
            "document_lines-INITIAL_FORMS": "0",
            "document_lines-MIN_NUM_FORMS": "0",
            "document_lines-MAX_NUM_FORMS": "100",
            "document_lines-0-sequence": "10",
            "document_lines-0-product": str((product or self.product).pk),
            "document_lines-0-quantity": quantity,
            "document_lines-0-request_amount": amount,
        }

    def test_saves_lines_and_derived_total_then_submits(self):
        response = self.client.post("/app/cash-advances/new/", self._payload())
        self.assertEqual(response.status_code, 302)
        advance = CashAdvance.objects.get(number="ADV-0001")
        self.assertEqual(advance.amount, Decimal("250.00"))
        self.assertEqual(advance.lines.get().subtotal, Decimal("250.00"))
        detail = self.client.get(f"/app/cash-advances/{advance.pk}/edit/")
        self.assertContains(detail, "Advance Request Lines")
        self.assertContains(detail, "Total Requested")
        self.assertNotContains(detail, "Print PDF")
        response = self.client.post(f"/app/cash-advances/{advance.pk}/action/submit/")
        self.assertEqual(response.status_code, 302)
        advance.refresh_from_db()
        self.assertEqual(advance.status, "submitted")

    def test_rejects_wrong_company_and_expense_product(self):
        wrong_company = ExpenseProduct.objects.create(
            company=self.other_company, code="FOREIGN", name="Foreign Advance", activity_type="advance"
        )
        response = self.client.post("/app/cash-advances/new/", self._payload(product=wrong_company))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CashAdvance.objects.exists())
        expense_product = ExpenseProduct.objects.create(
            company=self.company, code="MEAL", name="Meal Expense", activity_type="expense"
        )
        response = self.client.post("/app/cash-advances/new/", self._payload(product=expense_product))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CashAdvance.objects.exists())

    def test_multiple_products_sum_and_duplicate_product_is_rejected(self):
        second = ExpenseProduct.objects.create(
            company=self.company, code="HOTEL", name="Hotel Advance", activity_type="advance"
        )
        payload = self._payload()
        payload["document_lines-TOTAL_FORMS"] = "2"
        payload.update({
            "document_lines-1-sequence": "20",
            "document_lines-1-product": str(second.pk),
            "document_lines-1-quantity": "1.00",
            "document_lines-1-request_amount": "80.00",
        })
        self.assertEqual(self.client.post("/app/cash-advances/new/", payload).status_code, 302)
        advance = CashAdvance.objects.get(number="ADV-0001")
        self.assertEqual(advance.amount, Decimal("330.00"))
        self.assertEqual(advance.lines.count(), 2)

        duplicate_payload = self._payload()
        duplicate_payload["number"] = "ADV-0002"
        duplicate_payload["document_lines-TOTAL_FORMS"] = "2"
        duplicate_payload.update({
            "document_lines-1-sequence": "20",
            "document_lines-1-product": str(self.product.pk),
            "document_lines-1-quantity": "1.00",
            "document_lines-1-request_amount": "80.00",
        })
        self.assertEqual(self.client.post("/app/cash-advances/new/", duplicate_payload).status_code, 200)
        self.assertFalse(CashAdvance.objects.filter(number="ADV-0002").exists())

    def test_submit_rejects_missing_or_mismatched_lines(self):
        advance = CashAdvance.objects.create(
            employee=self.employee, number="ADV-0001", purpose="Travel", amount=Decimal("100.00")
        )
        self.client.post(f"/app/cash-advances/{advance.pk}/action/submit/")
        advance.refresh_from_db()
        self.assertEqual(advance.status, "draft")
        CashAdvanceLine.objects.create(
            advance=advance, product=self.product, quantity=1, request_amount=Decimal("50.00")
        )
        self.client.post(f"/app/cash-advances/{advance.pk}/action/submit/")
        advance.refresh_from_db()
        self.assertEqual(advance.status, "draft")

    def test_edit_updates_own_line_and_recalculates_total(self):
        advance = CashAdvance.objects.create(
            employee=self.employee, number="ADV-0001", purpose="Travel", amount=Decimal("100.00")
        )
        line = CashAdvanceLine.objects.create(
            advance=advance, product=self.product, quantity=1, request_amount=Decimal("100.00")
        )
        payload = self._payload(amount="50.00", quantity="3.00")
        payload["document_lines-INITIAL_FORMS"] = "1"
        payload["document_lines-0-id"] = str(line.pk)
        response = self.client.post(f"/app/cash-advances/{advance.pk}/edit/", payload)
        self.assertEqual(response.status_code, 302)
        advance.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(advance.amount, Decimal("150.00"))
        self.assertEqual(line.subtotal, Decimal("150.00"))
        self.assertEqual(advance.lines.count(), 1)

    def test_edit_cannot_attach_another_advance_line(self):
        advance = CashAdvance.objects.create(
            employee=self.employee, number="ADV-0001", purpose="Travel", amount=Decimal("100.00")
        )
        other = CashAdvance.objects.create(
            employee=self.employee, number="ADV-0002", purpose="Travel", amount=Decimal("100.00")
        )
        foreign_line = CashAdvanceLine.objects.create(
            advance=other, product=self.product, quantity=1, request_amount=Decimal("100.00")
        )
        payload = self._payload()
        payload["document_lines-INITIAL_FORMS"] = "1"
        payload["document_lines-0-id"] = str(foreign_line.pk)
        response = self.client.post(f"/app/cash-advances/{advance.pk}/edit/", payload)
        foreign_line.refresh_from_db()
        self.assertEqual(foreign_line.advance_id, other.pk)
        advance.refresh_from_db()
        self.assertEqual(advance.amount, Decimal("100.00"))
        self.assertEqual(response.status_code, 200, response.headers)

    def test_total_validation_query_count_does_not_grow_with_lines(self):
        query_counts = []
        for size in (2, 12):
            advance = CashAdvance.objects.create(
                employee=self.employee, number=f"ADV-{size}", purpose="Travel",
                amount=Decimal(size * 10),
            )
            lines = []
            for index in range(size):
                product = ExpenseProduct.objects.create(
                    company=self.company, code=f"ADV-{size}-{index}",
                    name=f"Advance item {size}-{index}", activity_type="advance",
                )
                lines.append(CashAdvanceLine(
                    advance=advance, product=product, quantity=1,
                    request_amount=Decimal("10.00"), subtotal=Decimal("10.00"),
                ))
            CashAdvanceLine.objects.bulk_create(lines)
            with CaptureQueriesContext(connection) as captured:
                validate_cash_advance_lines(advance)
            query_counts.append(len(captured))
        self.assertEqual(query_counts, [1, 1])

    def test_submitted_document_cannot_be_deleted_with_its_lines(self):
        advance = CashAdvance.objects.create(
            employee=self.employee, number="ADV-0001", purpose="Travel", amount=Decimal("100.00"),
            status="submitted",
        )
        CashAdvanceLine.objects.create(
            advance=advance, product=self.product, quantity=1, request_amount=Decimal("100.00")
        )
        response = self.client.post(f"/app/cash-advances/{advance.pk}/delete/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(CashAdvance.objects.filter(pk=advance.pk).exists())
        self.assertEqual(advance.lines.count(), 1)

    def test_submit_rejects_injected_cross_company_line(self):
        foreign = ExpenseProduct.objects.create(
            company=self.other_company, code="CROSS", name="Cross Company", activity_type="advance"
        )
        advance = CashAdvance.objects.create(
            employee=self.employee, number="ADV-0001", purpose="Travel", amount=Decimal("100.00")
        )
        CashAdvanceLine.objects.create(
            advance=advance, product=foreign, quantity=1, request_amount=Decimal("100.00")
        )
        self.client.post(f"/app/cash-advances/{advance.pk}/action/submit/")
        advance.refresh_from_db()
        self.assertEqual(advance.status, "draft")
