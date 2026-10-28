from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Employee

from .contracts import transition_employment_contract
from .models import (
    CompensationBenefit,
    CompensationBenefitLine,
    ContractAllowance,
    ContractAllowanceHistory,
    ContractAttachment,
    ContractLetter,
    ContractLetterTemplate,
    ContractOvertimeComponent,
    EmploymentContract,
    HRWorkflowAudit,
    PayrollPeriod,
    PayrollRun,
    Payslip,
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
        if action == "activate":
            submit = self.client.post(f"/app/contracts/{contract.pk}/action/submit/")
            self.assertEqual(submit.status_code, 302)
            action = "approve"
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

    def test_contract_create_and_edit_share_the_contract_workspace(self):
        response = self.client.get("/app/contracts/new/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "hr/contract_workspace.html")
        self.assertContains(response, "New employment contract")
        self.assertContains(response, "Employment agreement")
        self.assertContains(response, "Compensation")
        self.assertContains(response, "Save draft terms")

        existing = self._contract("SAME-EDITOR")
        response = self.client.get(f"/app/contracts/{existing.pk}/edit/?tab=overview")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "hr/contract_workspace.html")
        self.assertContains(response, "Employment agreement")

        response = self.client.post(
            "/app/contracts/new/",
            {
                "employee": self.employee.pk,
                "number": "WORKSPACE-CREATE",
                "contract_type": "permanent",
                "start_date": self.today.isoformat(),
                "base_salary": "5000000.00",
                "notes": "Created through the dedicated contract workspace.",
            },
        )
        created = EmploymentContract.objects.get(number="WORKSPACE-CREATE")
        self.assertRedirects(response, f"/app/contracts/{created.pk}/edit/?tab=overview")
        self.assertEqual(created.status, EmploymentContract.Status.DRAFT)
        self.assertTrue(
            HRWorkflowAudit.objects.filter(entity="contracts", record_id=created.pk, action="create").exists()
        )

    def test_activation_blocks_overlap_and_active_edit(self):
        first = self._contract("FIRST", end_date=self.today + timedelta(days=30))
        self._action(first, "activate")
        first.refresh_from_db()
        self.assertEqual(first.status, "active")
        self.assertEqual(HRWorkflowAudit.objects.filter(entity="contracts", record_id=first.pk, action="approve").count(), 1)

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
        audit = HRWorkflowAudit.objects.get(entity="contracts", record_id=contract.pk, action="approve")
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
        self.assertEqual(snapshot.component_lines.count(), 2)
        self.assertEqual(
            snapshot.component_lines.filter(category=CompensationBenefitLine.Category.ALLOWANCE).get().amount,
            Decimal("750000.00"),
        )

    def test_contract_workspace_manages_related_records_and_exposes_audit_tabs(self):
        component = SalaryComponent.objects.create(
            company=self.company,
            code="OT",
            name="Overtime Earning",
            component_type=SalaryComponent.ComponentType.EARNING,
        )
        contract = self._contract("WORKSPACE")

        overview = self.client.get(f"/app/contracts/{contract.pk}/edit/")
        self.assertEqual(overview.status_code, 200)
        self.assertContains(overview, "Compensation")
        self.assertContains(overview, "Payroll impact")
        self.assertContains(overview, "Audit trail")

        overtime = self.client.post(
            f"/app/contracts/{contract.pk}/edit/?tab=overtime",
            {
                "workspace_action": "add_overtime_component",
                "overtime-component": component.pk,
                "overtime-rate_multiplier": "1.500",
                "overtime-effective_date": self.today.isoformat(),
                "overtime-active": "on",
                "overtime-notes": "Weekend overtime rate",
            },
        )
        self.assertEqual(overtime.status_code, 302)
        overtime_component = ContractOvertimeComponent.objects.get(contract=contract)
        self.assertEqual(overtime_component.rate_multiplier, Decimal("1.500"))

        attachment = self.client.post(
            f"/app/contracts/{contract.pk}/edit/?tab=attachments",
            {
                "workspace_action": "add_attachment",
                "attachment-title": "Signed agreement",
                "attachment-category": "compliance",
                "attachment-file_url": "https://files.example.test/contracts/workspace.pdf",
                "attachment-issued_date": self.today.isoformat(),
                "attachment-verified": "on",
            },
        )
        self.assertEqual(attachment.status_code, 302)
        self.assertTrue(ContractAttachment.objects.filter(contract=contract, verified=True).exists())
        self.assertTrue(
            HRWorkflowAudit.objects.filter(entity="contracts", record_id=contract.pk, action="add_attachment").exists()
        )

        self._action(contract, "activate")
        compensation = self.client.get(f"/app/contracts/{contract.pk}/edit/?tab=compensation")
        self.assertEqual(compensation.status_code, 200)
        self.assertContains(compensation, "Frozen component lines")
        self.assertContains(compensation, "Basic salary")

    def test_payslip_contract_and_run_links_are_validated_and_audited(self):
        contract = self._contract("PAYROLL-LINK", end_date=self.today + timedelta(days=30))
        self._action(contract, "activate")
        period = PayrollPeriod.objects.create(
            company=self.company,
            name="Contract payroll period",
            date_start=self.today,
            date_end=self.today + timedelta(days=14),
            cutoff_date=self.today,
        )
        run = PayrollRun.objects.create(
            company=self.company,
            period=period,
            number="RUN-CONTRACT-LINK",
        )
        payslip = Payslip.objects.create(
            employee=self.employee,
            number="PAY-CONTRACT-LINK",
            period_start=self.today,
            period_end=self.today + timedelta(days=14),
            basic_salary=Decimal("5000000.00"),
            payroll_run=run,
        )

        link = self.client.post(
            f"/app/contracts/{contract.pk}/edit/?tab=payroll",
            {"workspace_action": "link_payslip", "payslip_id": payslip.pk},
        )
        self.assertEqual(link.status_code, 302)
        payslip.refresh_from_db()
        self.assertEqual(payslip.contract, contract)
        self.assertEqual(payslip.payroll_run, run)
        self.assertTrue(
            HRWorkflowAudit.objects.filter(entity="contracts", record_id=contract.pk, action="link_payslip").exists()
        )
        payroll = self.client.get(f"/app/contracts/{contract.pk}/edit/?tab=payroll")
        self.assertEqual(payroll.status_code, 200)
        self.assertContains(payroll, "Direct payroll traceability")
        self.assertContains(payroll, "PAY-CONTRACT-LINK")
        self.assertContains(payroll, "RUN-CONTRACT-LINK")

        other_employee = Employee.objects.create(
            company=self.company,
            employee_id="HRC-2",
            name="Other Contract Employee",
        )
        invalid_contract = Payslip(
            employee=other_employee,
            contract=contract,
            number="PAY-WRONG-CONTRACT",
            period_start=self.today,
            period_end=self.today,
        )
        with self.assertRaisesMessage(ValidationError, "must belong to the selected employee"):
            invalid_contract.full_clean()

        invalid_run_period = Payslip(
            employee=self.employee,
            contract=contract,
            payroll_run=run,
            number="PAY-WRONG-RUN-PERIOD",
            period_start=self.today - timedelta(days=1),
            period_end=self.today,
        )
        with self.assertRaisesMessage(ValidationError, "must be inside the payroll run period"):
            invalid_run_period.full_clean()

    def test_contract_attachment_upload_is_downloaded_inside_contract_scope(self):
        contract = self._contract("STORED-ATTACHMENT")
        upload = SimpleUploadedFile("signed-contract.pdf", b"contract-proof", content_type="application/pdf")
        response = self.client.post(
            f"/app/contracts/{contract.pk}/edit/?tab=attachments",
            {
                "workspace_action": "add_attachment",
                "attachment-title": "Signed contract",
                "attachment-category": "compliance",
                "attachment-attachment": upload,
                "attachment-issued_date": self.today.isoformat(),
                "attachment-verified": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        attachment = ContractAttachment.objects.get(contract=contract)
        self.assertEqual(attachment.attachment_name, "signed-contract.pdf")
        self.assertEqual(bytes(attachment.attachment_content), b"contract-proof")
        download = self.client.get(
            f"/app/contracts/{contract.pk}/attachments/{attachment.pk}/download/"
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(b"".join(download.streaming_content) if hasattr(download, "streaming_content") else download.content, b"contract-proof")
        self.assertEqual(download["X-Content-Type-Options"], "nosniff")

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
                transition_employment_contract(contract, "submit", self.user)
                transition_employment_contract(contract, "activate", self.user)
            return len(queries)

        small = activate_with_count(2, "SMALL")
        large = activate_with_count(12, "LARGE")
        self.assertEqual(small, large)
