from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.forms import EmployeeForm
from core.models import Company, Employee
from modules.organization.models import Branch
from modules.organization.context import (
    ACTIVE_BRANCH_SESSION_KEY,
    ACTIVE_COMPANY_SESSION_KEY,
    SELECTED_BRANCHES_SESSION_KEY,
    SELECTED_COMPANIES_SESSION_KEY,
)

from .career_transitions import transition_career_request
from .models import (
    ApprovalStatus,
    CareerTransitionChangeHistory,
    CareerTransitionRequest,
    CareerTransitionType,
    Department,
    EmploymentContract,
    HRWorkflowAudit,
    JobPosition,
)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class EmployeeOrganizationWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="employee-org-test",
            password="test-password-123",
            email="employee-org@example.test",
        )
        self.client.force_login(self.user)
        self.company = Company.objects.create(code="ORG", name="Organization Company")
        self.branch_a = self.company.branches.get(is_main=True)
        self.branch_b = Branch.objects.create(company=self.company, code="B", name="Branch B")
        self.department_a = Department.objects.create(company=self.company, code="DA", name="Department A")
        self.department_b = Department.objects.create(company=self.company, code="DB", name="Department B")
        self.job_a = JobPosition.objects.create(
            company=self.company,
            department=self.department_a,
            code="JA",
            name="Job A",
        )
        self.job_b = JobPosition.objects.create(
            company=self.company,
            department=self.department_b,
            code="JB",
            name="Job B",
        )
        self.manager_a = Employee.objects.create(
            company=self.company,
            branch=self.branch_a,
            department_record=self.department_a,
            job_position=self.job_a,
            employee_id="ORG-MA",
            name="Manager A",
            department=self.department_a.name,
            position=self.job_a.name,
        )
        self.manager_b = Employee.objects.create(
            company=self.company,
            branch=self.branch_b,
            department_record=self.department_b,
            job_position=self.job_b,
            employee_id="ORG-MB",
            name="Manager B",
            department=self.department_b.name,
            position=self.job_b.name,
        )
        self.employee = Employee.objects.create(
            company=self.company,
            branch=self.branch_a,
            department_record=self.department_a,
            job_position=self.job_a,
            supervisor=self.manager_a,
            employee_id="ORG-1",
            name="Organization Employee",
            department=self.department_a.name,
            position=self.job_a.name,
        )
        self.transfer_type = CareerTransitionType.objects.create(
            company=self.company,
            code="TRANSFER",
            name="Transfer",
            category=CareerTransitionType.Category.TRANSFER,
        )
        self.exit_type = CareerTransitionType.objects.create(
            company=self.company,
            code="EXIT",
            name="Termination",
            category=CareerTransitionType.Category.TERMINATION,
        )
        session = self.client.session
        session[ACTIVE_COMPANY_SESSION_KEY] = self.company.pk
        session[ACTIVE_BRANCH_SESSION_KEY] = self.branch_a.pk
        session[SELECTED_COMPANIES_SESSION_KEY] = [self.company.pk]
        session[SELECTED_BRANCHES_SESSION_KEY] = [self.branch_a.pk, self.branch_b.pk]
        session.save()

    def _action(self, transition, action):
        return self.client.post(f"/app/career-transition-requests/{transition.pk}/action/{action}/")

    def test_organization_chart_reassigns_reporting_line_and_blocks_cycles(self):
        call_command("sync_apps", verbosity=0)
        response = self.client.get("/apps/hr/feature/organization-chart/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reporting structure")
        self.assertContains(response, "Enable drag &amp; drop")
        self.assertContains(response, "Enable drag &amp; drop")
        self.assertContains(response, "Change manager without drag-and-drop")
        self.assertContains(response, "organization-chart-editor-4")
        self.assertContains(response, 'data-org-card')
        self.assertContains(response, self.employee.name)

        edit_response = self.client.get("/apps/hr/feature/organization-chart/?edit=1")
        self.assertContains(edit_response, "Disable drag &amp; drop")
        self.assertContains(edit_response, 'data-editing="true"')

        response = self.client.post(
            "/apps/hr/feature/organization-chart/",
            {"employee_id": self.employee.pk, "supervisor_id": self.manager_b.pk},
        )
        self.assertRedirects(response, "/apps/hr/feature/organization-chart/")
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.supervisor, self.manager_b)
        audit = HRWorkflowAudit.objects.get(
            entity="employees",
            record_id=self.employee.pk,
            action="organization_chart_reassign",
        )
        self.assertEqual(audit.details["previous_supervisor_id"], self.manager_a.pk)
        self.assertEqual(audit.details["supervisor_id"], self.manager_b.pk)

        response = self.client.post(
            "/apps/hr/feature/organization-chart/",
            {"employee_id": self.employee.pk, "supervisor_id": ""},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "changed": True,
                "employee": self.employee.name,
                "destination": "Top level",
            },
        )
        self.employee.refresh_from_db()
        self.assertIsNone(self.employee.supervisor)

        self.employee.supervisor = self.manager_b
        self.employee.save(update_fields=["supervisor", "updated_at"])

        response = self.client.post(
            "/apps/hr/feature/organization-chart/",
            {"employee_id": self.manager_b.pk, "supervisor_id": self.employee.pk},
            follow=True,
        )
        self.assertContains(response, "circular reporting line")
        self.manager_b.refresh_from_db()
        self.assertIsNone(self.manager_b.supervisor)


    def test_employee_form_uses_master_organization_and_blocks_direct_status_change(self):
        self.assertNotIn("status", EmployeeForm().fields)
        response = self.client.post(
            f"/app/employees/{self.employee.pk}/edit/",
            {
                "company": self.company.pk,
                "branch": self.branch_b.pk,
                "supervisor": self.manager_b.pk,
                "employee_id": self.employee.employee_id,
                "name": self.employee.name,
                "email": "employee@example.test",
                "phone": "123",
                "department_record": self.department_b.pk,
                "job_position": self.job_b.pk,
                "join_date": self.employee.join_date.isoformat(),
                "status": Employee.Status.INACTIVE,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.department, self.department_b.name)
        self.assertEqual(self.employee.position, self.job_b.name)
        self.assertEqual(self.employee.status, Employee.Status.ACTIVE)
        delete_response = self.client.post(f"/app/employees/{self.employee.pk}/delete/")
        self.assertEqual(delete_response.status_code, 302)
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())

    def test_company_and_department_boundaries_are_validated(self):
        other = Company.objects.create(code="OTHER-ORG", name="Other Organization")
        foreign_branch = Branch.objects.create(company=other, code="FOREIGN", name="Foreign Branch")
        foreign_department = Department.objects.create(company=other, code="FOREIGN", name="Foreign Department")
        employee = Employee(
            company=self.company,
            branch=foreign_branch,
            employee_id="INVALID",
            name="Invalid Employee",
        )
        with self.assertRaisesMessage(ValidationError, "Branch must belong"):
            employee.full_clean()

        employee.branch = self.branch_a
        employee.department_record = self.department_a
        employee.job_position = self.job_b
        with self.assertRaisesMessage(ValidationError, "selected department"):
            employee.full_clean()

        transition = CareerTransitionRequest(
            employee=self.employee,
            number="INVALID-TRANSITION",
            transition_type=self.transfer_type,
            effective_date=timezone.localdate(),
            target_department=foreign_department,
        )
        with self.assertRaisesMessage(ValidationError, "must belong to the employee company"):
            transition.full_clean()

    def test_approved_transfer_updates_employee_and_writes_history_and_audit(self):
        transition = CareerTransitionRequest.objects.create(
            employee=self.employee,
            number="TR-1",
            transition_type=self.transfer_type,
            effective_date=timezone.localdate(),
            current_position=self.employee.position,
            target_branch=self.branch_b,
            target_department=self.department_b,
            target_job_position=self.job_b,
            target_supervisor=self.manager_b,
            reason="Organization restructure",
        )
        session = self.client.session
        session[SELECTED_BRANCHES_SESSION_KEY] = [self.branch_a.pk]
        session.save()
        self._action(transition, "submit")
        self._action(transition, "approve")

        transition.refresh_from_db()
        self.employee.refresh_from_db()
        audit = HRWorkflowAudit.objects.get(
            entity="career-transition-requests",
            record_id=transition.pk,
            action="approve",
        )
        self.assertEqual(transition.status, ApprovalStatus.APPROVED)
        self.assertEqual(transition.applied_by, self.user)
        self.assertEqual(self.employee.branch, self.branch_b)
        self.assertEqual(self.employee.department_record, self.department_b)
        self.assertEqual(self.employee.job_position, self.job_b)
        self.assertEqual(self.employee.supervisor, self.manager_b)
        self.assertEqual(self.employee.department, self.department_b.name)
        self.assertEqual(self.employee.position, self.job_b.name)
        self.assertEqual(
            set(CareerTransitionChangeHistory.objects.filter(request=transition).values_list("field_name", flat=True)),
            {"branch", "department", "department_name", "job_position", "position_name", "supervisor"},
        )
        self.assertEqual(audit.details["employee_id"], self.employee.pk)

    def test_exit_inactivates_employee_and_terminates_active_contracts(self):
        contract = EmploymentContract.objects.create(
            employee=self.employee,
            number="EXIT-CONTRACT",
            contract_type=EmploymentContract.ContractType.PERMANENT,
            start_date=timezone.localdate(),
            base_salary=Decimal("5000000.00"),
            status=EmploymentContract.Status.ACTIVE,
        )
        transition = CareerTransitionRequest.objects.create(
            employee=self.employee,
            number="EXIT-1",
            transition_type=self.exit_type,
            effective_date=timezone.localdate(),
            current_position=self.employee.position,
            reason="Employment ended",
            status=ApprovalStatus.SUBMITTED,
        )

        transition_career_request(transition, "approve", self.user)

        self.employee.refresh_from_db()
        contract.refresh_from_db()
        self.assertEqual(self.employee.status, Employee.Status.INACTIVE)
        self.assertEqual(self.employee.inactive_date, timezone.localdate())
        self.assertEqual(contract.status, EmploymentContract.Status.TERMINATED)

    def test_future_transition_is_not_applied(self):
        transition = CareerTransitionRequest.objects.create(
            employee=self.employee,
            number="FUTURE-1",
            transition_type=self.transfer_type,
            effective_date=timezone.localdate() + timedelta(days=1),
            target_branch=self.branch_b,
            status=ApprovalStatus.SUBMITTED,
        )
        with self.assertRaisesMessage(ValidationError, "before its effective date"):
            transition_career_request(transition, "approve", self.user)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.branch, self.branch_a)

    def test_exit_query_count_does_not_grow_with_contract_count(self):
        def apply_exit(contract_count, suffix):
            employee = Employee.objects.create(
                company=self.company,
                employee_id=f"EXIT-{suffix}",
                name=f"Exit Employee {suffix}",
            )
            EmploymentContract.objects.bulk_create(
                [
                    EmploymentContract(
                        employee=employee,
                        number=f"EXIT-{suffix}-{index}",
                        contract_type=EmploymentContract.ContractType.PERMANENT,
                        start_date=timezone.localdate(),
                        status=EmploymentContract.Status.ACTIVE,
                    )
                    for index in range(contract_count)
                ]
            )
            transition = CareerTransitionRequest.objects.create(
                employee=employee,
                number=f"EXIT-REQUEST-{suffix}",
                transition_type=self.exit_type,
                effective_date=timezone.localdate(),
                reason="Contract query growth check",
                status=ApprovalStatus.SUBMITTED,
            )
            with CaptureQueriesContext(connection) as queries:
                transition_career_request(transition, "approve", self.user)
            return len(queries)

        self.assertEqual(apply_exit(2, "SMALL"), apply_exit(12, "LARGE"))
