from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.models import Company, Employee

from .models import EmploymentContract, HRWorkflowAudit


class EmploymentContractWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="hr-contract-test", password="test-password-123", email="contract@example.test"
        )
        self.client.force_login(self.user)
        self.company = Company.objects.create(code="HRC", name="Contract Company")
        self.employee = Employee.objects.create(company=self.company, employee_id="HRC-1", name="Contract Employee")
        self.today = timezone.localdate()

    def _contract(self, number, **changes):
        values = {
            "employee": self.employee,
            "number": number,
            "contract_type": EmploymentContract.ContractType.PERMANENT,
            "start_date": self.today,
            "base_salary": Decimal("5000000.00"),
        }
        values.update(changes)
        return EmploymentContract.objects.create(**values)

    def _action(self, contract, action):
        return self.client.post(f"/app/contracts/{contract.pk}/action/{action}/")

    def test_form_rejects_invalid_dates_salary_and_manual_status(self):
        response = self.client.post(
            "/app/contracts/new/",
            {
                "employee": self.employee.pk,
                "number": "BAD-CONTRACT",
                "contract_type": "fixed_term",
                "start_date": self.today.isoformat(),
                "end_date": (self.today - timedelta(days=1)).isoformat(),
                "base_salary": "-1.00",
                "status": "active",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "End date must be on or after start date")
        self.assertContains(response, "Base salary cannot be negative")
        self.assertFalse(EmploymentContract.objects.filter(number="BAD-CONTRACT").exists())

        response = self.client.post(
            "/app/contracts/new/",
            {
                "employee": self.employee.pk,
                "number": "GOOD-CONTRACT",
                "contract_type": "fixed_term",
                "start_date": self.today.isoformat(),
                "end_date": (self.today + timedelta(days=30)).isoformat(),
                "base_salary": "5000000.00",
                "status": "active",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(EmploymentContract.objects.get(number="GOOD-CONTRACT").status, "draft")

    def test_activation_blocks_overlap_and_active_edit(self):
        first = self._contract("FIRST", end_date=self.today + timedelta(days=30))
        self._action(first, "activate")
        first.refresh_from_db()
        self.assertEqual(first.status, "active")
        self.assertEqual(HRWorkflowAudit.objects.filter(entity="contracts", record_id=first.pk, action="activate").count(), 1)

        second = self._contract("SECOND", start_date=self.today + timedelta(days=15))
        self._action(second, "activate")
        second.refresh_from_db()
        self.assertEqual(second.status, "draft")

        response = self.client.post(
            f"/app/contracts/{first.pk}/edit/",
            {
                "employee": self.employee.pk,
                "number": "ALTERED",
                "contract_type": "permanent",
                "start_date": self.today.isoformat(),
                "end_date": (self.today + timedelta(days=30)).isoformat(),
                "base_salary": "1.00",
            },
        )
        self.assertEqual(response.status_code, 302)
        first.refresh_from_db()
        self.assertEqual(first.number, "FIRST")
        self.assertEqual(first.base_salary, Decimal("5000000.00"))

    def test_expiry_requires_end_date_and_allows_later_contract(self):
        first = self._contract("ENDING", end_date=self.today + timedelta(days=2))
        self._action(first, "activate")
        self._action(first, "expire")
        first.refresh_from_db()
        self.assertEqual(first.status, "active")

        with patch("modules.hr.contracts.timezone.localdate", return_value=self.today + timedelta(days=3)):
            self._action(first, "expire")
        first.refresh_from_db()
        self.assertEqual(first.status, "expired")

        later = self._contract("LATER", start_date=self.today + timedelta(days=3))
        self._action(later, "activate")
        later.refresh_from_db()
        self.assertEqual(later.status, "active")
