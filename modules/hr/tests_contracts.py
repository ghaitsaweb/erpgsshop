from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Employee

from .contracts import transition_employment_contract
from .models import (
    CompensationBenefit,
    ContractAllowance,
    ContractAllowanceHistory,
    ContractLetter,
    ContractLetterTemplate,
    EmploymentContract,
    HRWorkflowAudit,
    SalaryComponent,
    SalaryPackage,
)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
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

    def test_activation_generates_compensation_snapshot_and_rendered_letter(self):
        package = SalaryPackage.objects.create(
            company=self.company,
            code="PKG-1",
            name="Standard Package",
            base_salary=Decimal("6000000.00"),
        )
        template = ContractLetterTemplate.objects.create(
            company=self.company,
            code="LETTER-1",
            name="Standard Contract",
            content=(
                "{{ employee_name }} joins {{ company_name }} under {{ contract_number }} "
                "from {{ start_date }} with salary {{ base_salary }}."
            ),
        )
        component = SalaryComponent.objects.create(
            company=self.company,
            code="MEAL",
            name="Meal Allowance",
            component_type=SalaryComponent.ComponentType.EARNING,
        )
        contract = self._contract(
            "GENERATED",
            base_salary=Decimal("0.00"),
            salary_package=package,
            letter_template=template,
        )
        ContractAllowance.objects.create(
            contract=contract,
            component=component,
            amount=Decimal("750000.00"),
            effective_date=self.today,
        )

        self._action(contract, "activate")

        snapshot = CompensationBenefit.objects.get(contract=contract, effective_date=self.today)
        letter = ContractLetter.objects.get(contract=contract)
        audit = HRWorkflowAudit.objects.get(entity="contracts", record_id=contract.pk, action="activate")
        self.assertEqual(snapshot.basic_salary, Decimal("6000000.00"))
        self.assertEqual(snapshot.allowance_amount, Decimal("750000.00"))
        self.assertEqual(snapshot.status, "approved")
        self.assertEqual(snapshot.approver, self.user)
        self.assertEqual(letter.template, template)
        self.assertEqual(letter.generated_by, self.user)
        self.assertIn("Contract Employee joins Contract Company", letter.content)
        self.assertIn("6,000,000.00", letter.content)
        self.assertEqual(audit.details["compensation_benefit_id"], snapshot.pk)
        self.assertEqual(audit.details["contract_letter_id"], letter.pk)

    def test_active_allowance_change_appends_version_history_and_snapshot(self):
        component = SalaryComponent.objects.create(
            company=self.company,
            code="TRANSPORT",
            name="Transport Allowance",
            component_type=SalaryComponent.ComponentType.EARNING,
        )
        contract = self._contract("ALLOWANCE")
        original = ContractAllowance.objects.create(
            contract=contract,
            component=component,
            amount=Decimal("500000.00"),
            effective_date=self.today,
        )
        self._action(contract, "activate")
        next_date = self.today + timedelta(days=1)

        missing_reason = self.client.post(
            "/app/contract-allowances/new/",
            {
                "contract": contract.pk,
                "component": component.pk,
                "amount": "650000.00",
                "effective_date": next_date.isoformat(),
                "active": "on",
            },
        )
        self.assertEqual(missing_reason.status_code, 200)
        self.assertContains(missing_reason, "concrete change reason")

        response = self.client.post(
            "/app/contract-allowances/new/",
            {
                "contract": contract.pk,
                "component": component.pk,
                "amount": "650000.00",
                "effective_date": next_date.isoformat(),
                "active": "on",
                "change_reason": "Annual allowance review",
            },
        )

        self.assertEqual(response.status_code, 302)
        original.refresh_from_db()
        version = ContractAllowance.objects.get(contract=contract, component=component, effective_date=next_date)
        history = ContractAllowanceHistory.objects.get(allowance=version)
        snapshot = CompensationBenefit.objects.get(contract=contract, effective_date=next_date)
        self.assertEqual(original.end_date, self.today)
        self.assertEqual(history.previous_amount, Decimal("500000.00"))
        self.assertEqual(history.new_amount, Decimal("650000.00"))
        self.assertEqual(history.changed_by, self.user)
        self.assertEqual(snapshot.allowance_amount, Decimal("650000.00"))

    def test_contract_relations_and_allowance_components_stay_in_company(self):
        other_company = Company.objects.create(code="OTHER", name="Other Company")
        foreign_package = SalaryPackage.objects.create(
            company=other_company,
            code="FOREIGN",
            name="Foreign Package",
            base_salary=Decimal("1.00"),
        )
        foreign_component = SalaryComponent.objects.create(
            company=other_company,
            code="FOREIGN",
            name="Foreign Allowance",
            component_type=SalaryComponent.ComponentType.EARNING,
        )
        contract = self._contract("SCOPED", salary_package=foreign_package)
        with self.assertRaisesMessage(ValidationError, "salary package must belong"):
            contract.full_clean()

        allowance = ContractAllowance(
            contract=self._contract("SCOPED-ALLOWANCE"),
            component=foreign_component,
            amount=Decimal("1.00"),
            effective_date=self.today,
        )
        with self.assertRaisesMessage(ValidationError, "salary component must belong"):
            allowance.full_clean()

    def test_activation_query_count_does_not_grow_with_allowance_count(self):
        def activate_with_count(size, suffix):
            employee = Employee.objects.create(
                company=self.company,
                employee_id=f"QUERY-{suffix}",
                name=f"Query Employee {suffix}",
            )
            contract = EmploymentContract.objects.create(
                employee=employee,
                number=f"QUERY-{suffix}",
                contract_type=EmploymentContract.ContractType.PERMANENT,
                start_date=self.today,
                base_salary=Decimal("5000000.00"),
            )
            components = [
                SalaryComponent(
                    company=self.company,
                    code=f"Q-{suffix}-{index}",
                    name=f"Query Allowance {suffix}-{index}",
                    component_type=SalaryComponent.ComponentType.EARNING,
                )
                for index in range(size)
            ]
            SalaryComponent.objects.bulk_create(components)
            ContractAllowance.objects.bulk_create(
                [
                    ContractAllowance(
                        contract=contract,
                        component=component,
                        amount=Decimal("1.00"),
                        effective_date=self.today,
                    )
                    for component in components
                ]
            )
            with CaptureQueriesContext(connection) as queries:
                transition_employment_contract(contract, "activate", self.user)
            return len(queries)

        small = activate_with_count(2, "SMALL")
        large = activate_with_count(12, "LARGE")
        self.assertEqual(small, large)
