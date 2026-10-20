from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.models import Company, Employee
from modules.hr.models import EmployeeChangeRequest, HRWorkflowAudit


class EmployeeChangeRequestWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="hr-change-test", password="test-password-123", email="hr@example.test"
        )
        self.client.force_login(self.user)
        self.company = Company.objects.create(code="ECR", name="Change Request Company")
        self.employee = Employee.objects.create(
            company=self.company,
            employee_id="ECR-1",
            name="Change Request Employee",
            email="before@example.test",
            phone="100",
        )

    def _request(self, **changes):
        values = {
            "employee": self.employee,
            "field_name": "phone",
            "current_value": "100",
            "requested_value": "200",
        }
        values.update(changes)
        return EmployeeChangeRequest.objects.create(**values)

    def _action(self, change_request, action):
        return self.client.post(f"/app/employee-change-requests/{change_request.pk}/action/{action}/")

    def test_form_captures_employee_value_and_approval_applies_it_once(self):
        response = self.client.post(
            "/app/employee-change-requests/new/",
            {
                "employee": self.employee.pk,
                "field_name": "email",
                "current_value": "forged@example.test",
                "requested_value": "after@example.test",
                "effective_date": timezone.localdate().isoformat(),
                "reason": "New work address",
            },
        )
        self.assertEqual(response.status_code, 302)
        change_request = EmployeeChangeRequest.objects.get(employee=self.employee)
        self.assertEqual(change_request.current_value, "before@example.test")
        self._action(change_request, "submit")
        self._action(change_request, "approve")
        self._action(change_request, "approve")
        change_request.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertEqual(change_request.status, "approved")
        self.assertEqual(change_request.approver, self.user)
        self.assertEqual(self.employee.email, "after@example.test")
        self.assertEqual(
            HRWorkflowAudit.objects.filter(entity="employee-change-requests", record_id=change_request.pk, action="approve").count(),
            1,
        )

    def test_submitted_request_cannot_be_edited_or_apply_stale_value(self):
        change_request = self._request()
        self._action(change_request, "submit")
        response = self.client.post(
            f"/app/employee-change-requests/{change_request.pk}/edit/",
            {
                "employee": self.employee.pk,
                "field_name": "phone",
                "requested_value": "999",
                "effective_date": timezone.localdate().isoformat(),
            },
        )
        self.assertEqual(response.status_code, 302)
        change_request.refresh_from_db()
        self.assertEqual(change_request.requested_value, "200")

        self.employee.phone = "300"
        self.employee.save(update_fields=["phone", "updated_at"])
        self._action(change_request, "approve")
        change_request.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertEqual(change_request.status, "submitted")
        self.assertEqual(self.employee.phone, "300")
        self.assertFalse(HRWorkflowAudit.objects.filter(entity="employee-change-requests", record_id=change_request.pk, action="approve").exists())

    def test_reject_does_not_change_profile(self):
        change_request = self._request()
        self._action(change_request, "submit")
        self._action(change_request, "reject")
        change_request.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertEqual(change_request.status, "rejected")
        self.assertEqual(change_request.approver, self.user)
        self.assertEqual(self.employee.phone, "100")

    def test_future_effective_date_and_unsupported_field_cannot_be_applied(self):
        future = self._request(effective_date=timezone.localdate() + timedelta(days=1))
        self._action(future, "submit")
        self._action(future, "approve")
        future.refresh_from_db()
        self.assertEqual(future.status, "submitted")

        unsupported = self._request(field_name="status", current_value="active", requested_value="inactive")
        self._action(unsupported, "submit")
        unsupported.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertEqual(unsupported.status, "draft")
        self.assertEqual(self.employee.status, "active")
