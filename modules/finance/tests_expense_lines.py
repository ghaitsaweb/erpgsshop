from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Employee
from modules.hr.models import ExpenseProduct

from .expense_lines import validate_expense_claim_lines
from .models import ExpenseClaim, ExpenseClaimLine


class ExpenseClaimLineDocumentTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser(
            username="expense-manager", email="expense@example.test", password="test-password-123"
        )
        self.client.force_login(user)
        self.company = Company.objects.create(code="EXP", name="Expense Company")
        self.other_company = Company.objects.create(code="XCO", name="Other Company")
        self.employee = Employee.objects.create(
            company=self.company, employee_id="EXP-1", name="Expense Employee", join_date=timezone.localdate()
        )
        self.product = ExpenseProduct.objects.create(
            company=self.company, code="MEAL", name="Meal Expense", activity_type="expense",
            standard_price=Decimal("50.00"),
        )

    def _payload(self, *, product=None):
        today = timezone.localdate().isoformat()
        return {
            "company": str(self.company.pk), "employee": str(self.employee.pk),
            "cash_advance": "", "reference": "EXP-0001", "claim_date": today,
            "category": "Travel", "description": "Business trip",
            "document_lines-TOTAL_FORMS": "1", "document_lines-INITIAL_FORMS": "0",
            "document_lines-MIN_NUM_FORMS": "0", "document_lines-MAX_NUM_FORMS": "100",
            "document_lines-0-date": today,
            "document_lines-0-description": "Meal",
            "document_lines-0-product": str((product or self.product).pk),
            "document_lines-0-quantity": "2.00", "document_lines-0-unit_price": "50.00",
        }

    def test_two_lines_derive_total_and_submit(self):
        second = ExpenseProduct.objects.create(
            company=self.company, code="TAXI", name="Taxi Expense", activity_type="expense"
        )
        payload = self._payload()
        payload["document_lines-TOTAL_FORMS"] = "2"
        payload.update({
            "document_lines-1-date": timezone.localdate().isoformat(),
            "document_lines-1-description": "Taxi",
            "document_lines-1-product": str(second.pk),
            "document_lines-1-quantity": "3.00", "document_lines-1-unit_price": "20.00",
        })
        response = self.client.post("/app/expenses/new/", payload)
        self.assertEqual(response.status_code, 302)
        expense = ExpenseClaim.objects.get(reference="EXP-0001")
        self.assertEqual(expense.amount, Decimal("160.00"))
        self.assertEqual(expense.lines.count(), 2)
        detail = self.client.get(f"/app/expenses/{expense.pk}/edit/")
        self.assertContains(detail, "Expense Lines")
        self.assertNotContains(detail, "Print PDF")
        self.client.post(f"/app/expenses/{expense.pk}/action/submit/")
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseClaim.Status.SUBMITTED)

    def test_wrong_company_or_advance_product_rejected(self):
        foreign = ExpenseProduct.objects.create(
            company=self.other_company, code="FOREIGN", name="Foreign Expense", activity_type="expense"
        )
        self.assertEqual(self.client.post("/app/expenses/new/", self._payload(product=foreign)).status_code, 200)
        advance = ExpenseProduct.objects.create(
            company=self.company, code="ADV", name="Advance", activity_type="advance"
        )
        self.assertEqual(self.client.post("/app/expenses/new/", self._payload(product=advance)).status_code, 200)
        self.assertFalse(ExpenseClaim.objects.exists())

    def test_submit_requires_matching_lines(self):
        expense = ExpenseClaim.objects.create(
            company=self.company, employee=self.employee, reference="EXP-0001", category="Travel",
            amount=Decimal("100.00"),
        )
        self.client.post(f"/app/expenses/{expense.pk}/action/submit/")
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseClaim.Status.DRAFT)
        ExpenseClaimLine.objects.create(
            expense=expense, product=self.product, quantity=1, unit_price=Decimal("50.00")
        )
        self.client.post(f"/app/expenses/{expense.pk}/action/submit/")
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseClaim.Status.DRAFT)

    def test_cannot_attach_another_claim_line(self):
        own = ExpenseClaim.objects.create(
            company=self.company, employee=self.employee, reference="EXP-0001", category="Travel",
            amount=Decimal("100.00"),
        )
        other = ExpenseClaim.objects.create(
            company=self.company, employee=self.employee, reference="EXP-0002", category="Travel",
            amount=Decimal("100.00"),
        )
        foreign_line = ExpenseClaimLine.objects.create(
            expense=other, product=self.product, quantity=2, unit_price=Decimal("50.00")
        )
        payload = self._payload()
        payload["document_lines-INITIAL_FORMS"] = "1"
        payload["document_lines-0-id"] = str(foreign_line.pk)
        response = self.client.post(f"/app/expenses/{own.pk}/edit/", payload)
        self.assertEqual(response.status_code, 200)
        own.refresh_from_db()
        foreign_line.refresh_from_db()
        self.assertEqual(own.amount, Decimal("100.00"))
        self.assertEqual(foreign_line.expense_id, other.pk)

    def test_product_search_returns_expense_price(self):
        ExpenseProduct.objects.create(
            company=self.company, code="TRAVEL", name="Travel Advance", activity_type="advance"
        )
        response = self.client.get("/api/expense-products/claim-search/?q=Meal")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 1)
        self.assertEqual(response.json()["results"][0]["standard_price"], "50.00")

    def test_total_validation_query_count_is_constant(self):
        counts = []
        for size in (2, 12):
            expense = ExpenseClaim.objects.create(
                company=self.company, employee=self.employee, reference=f"EXP-{size}",
                category="Travel", amount=Decimal(size * 10),
            )
            lines = []
            for index in range(size):
                product = ExpenseProduct.objects.create(
                    company=self.company, code=f"EX-{size}-{index}", name=f"Expense {size}-{index}",
                    activity_type="expense",
                )
                lines.append(ExpenseClaimLine(
                    expense=expense, product=product, quantity=1, unit_price=Decimal("10.00"),
                    subtotal=Decimal("10.00"),
                ))
            ExpenseClaimLine.objects.bulk_create(lines)
            with CaptureQueriesContext(connection) as captured:
                validate_expense_claim_lines(expense)
            counts.append(len(captured))
        self.assertEqual(counts, [1, 1])

    def test_submit_rejects_injected_cross_company_line(self):
        foreign = ExpenseProduct.objects.create(
            company=self.other_company, code="CROSS", name="Cross Company", activity_type="expense"
        )
        expense = ExpenseClaim.objects.create(
            company=self.company, employee=self.employee, reference="EXP-0001", category="Travel",
            amount=Decimal("100.00"),
        )
        ExpenseClaimLine.objects.create(
            expense=expense, product=foreign, quantity=1, unit_price=Decimal("100.00")
        )
        self.client.post(f"/app/expenses/{expense.pk}/action/submit/")
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseClaim.Status.DRAFT)
