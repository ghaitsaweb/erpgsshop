from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Employee
from modules.hr.change_requests import transition_employee_change_request
from modules.hr.forms import EmployeeChangeRequestForm
from modules.hr.models import (
    EmployeeChangeRequest,
    EmployeeChangeRequestLine,
    HRSetting,
    HRWorkflowAudit,
)


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

    def test_multi_field_request_applies_typed_values_and_keeps_immutable_history(self):
        self.employee.passport = "OLD-PASSPORT"
        self.employee.save(update_fields=["passport", "updated_at"])
        change_request = EmployeeChangeRequest.objects.create(
            employee=self.employee,
            field_name="email",
            current_value="before@example.test",
            requested_value="after@example.test",
            reason="Synchronize personal data",
        )
        EmployeeChangeRequestLine.objects.bulk_create(
            [
                EmployeeChangeRequestLine(
                    request=change_request,
                    field_name="birth_date",
                    current_value="",
                    requested_value="1990-01-02",
                ),
                EmployeeChangeRequestLine(
                    request=change_request,
                    field_name="commute_distance_km",
                    current_value="",
                    requested_value="12.50",
                ),
                EmployeeChangeRequestLine(
                    request=change_request,
                    field_name="passport",
                    current_value="OLD-PASSPORT",
                    requested_value="",
                ),
            ]
        )

        self._action(change_request, "submit")
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, "submitted")
        self.assertIsNotNone(change_request.submitted_at)
        history = list(change_request.change_history.order_by("field_name"))
        self.assertEqual(len(history), 4)
        self.assertEqual({item.field_name for item in history}, {"email", "birth_date", "commute_distance_km", "passport"})

        self._action(change_request, "approve")
        change_request.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertEqual(change_request.status, "approved")
        self.assertIsNotNone(change_request.approved_at)
        self.assertEqual(self.employee.email, "after@example.test")
        self.assertEqual(self.employee.birth_date, date(1990, 1, 2))
        self.assertEqual(self.employee.commute_distance_km, Decimal("12.50"))
        self.assertEqual(self.employee.passport, "")
        self.assertEqual(change_request.change_history.filter(applied_at__isnull=False).count(), 4)

        immutable = history[0]
        immutable.value_after = "tampered"
        with self.assertRaises(ValidationError):
            immutable.save()
        with self.assertRaises(ValidationError):
            immutable.delete()

    def test_attachment_policy_validation_storage_and_scoped_download(self):
        HRSetting.objects.create(company=self.company, employee_change_request_attachment=True)
        no_attachment = self._request()
        response = self._action(no_attachment, "submit")
        self.assertEqual(response.status_code, 302)
        no_attachment.refresh_from_db()
        self.assertEqual(no_attachment.status, "draft")

        invalid_form = EmployeeChangeRequestForm(
            data={
                "employee": self.employee.pk,
                "field_name": "phone",
                "current_value": "forged",
                "requested_value": "201",
                "effective_date": timezone.localdate().isoformat(),
                "reason": "Invalid evidence extension",
            },
            files={
                "attachment": SimpleUploadedFile("proof.exe", b"invalid", content_type="application/octet-stream")
            },
        )
        self.assertFalse(invalid_form.is_valid())
        self.assertIn("Attachment type is not allowed.", invalid_form.errors["attachment"])

        evidence = b"%PDF-1.4\nproof"
        create_response = self.client.post(
            "/app/employee-change-requests/new/",
            {
                "employee": self.employee.pk,
                "field_name": "phone",
                "current_value": "forged",
                "requested_value": "202",
                "effective_date": timezone.localdate().isoformat(),
                "reason": "Valid evidence",
                "attachment": SimpleUploadedFile("proof.pdf", evidence, content_type="application/pdf"),
            },
        )
        self.assertEqual(create_response.status_code, 302)
        change_request = EmployeeChangeRequest.objects.get(requested_value="202")
        self.assertEqual(change_request.attachment_name, "proof.pdf")
        self.assertEqual(bytes(change_request.attachment_content), evidence)

        download = self.client.get(f"/app/employee-change-requests/{change_request.pk}/attachment/")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, evidence)
        self.assertEqual(download["Content-Type"], "application/pdf")
        self.assertEqual(download["X-Content-Type-Options"], "nosniff")
        self._action(change_request, "submit")
        self._action(change_request, "approve")
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, "approved")
        self.assertEqual(bytes(change_request.attachment_content), evidence)

    def test_change_details_are_locked_after_submission(self):
        change_request = self._request()
        line = EmployeeChangeRequestLine.objects.create(
            request=change_request,
            field_name="religion",
            current_value="",
            requested_value="Recorded",
        )
        self._action(change_request, "submit")

        edit_response = self.client.post(
            f"/app/employee-change-request-lines/{line.pk}/edit/",
            {
                "request": change_request.pk,
                "field_name": "religion",
                "current_value": "",
                "requested_value": "Tampered",
            },
        )
        self.assertEqual(edit_response.status_code, 302)
        delete_response = self.client.post(f"/app/employee-change-request-lines/{line.pk}/delete/")
        self.assertEqual(delete_response.status_code, 302)
        line.refresh_from_db()
        self.assertEqual(line.requested_value, "Recorded")

    def test_approval_backfills_snapshot_for_legacy_submitted_request(self):
        change_request = self._request(status="submitted")
        self.assertFalse(change_request.change_history.exists())
        self._action(change_request, "approve")
        change_request.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertEqual(change_request.status, "approved")
        self.assertEqual(self.employee.phone, "200")
        self.assertEqual(change_request.change_history.count(), 1)
        self.assertEqual(change_request.change_history.get().submitted_by, self.user)

    def test_submit_query_count_does_not_grow_with_change_line_count(self):
        values = [
            ("phone", "200"),
            ("gender", "female"),
            ("commute_distance_km", "5.25"),
            ("religion", "Recorded"),
            ("race", "Recorded"),
            ("marital_status", "single"),
            ("nationality", "ID"),
            ("province", "DKI Jakarta"),
            ("domicile_code", "ID-JK"),
            ("passport", "P-001"),
            ("place_of_birth", "Jakarta"),
        ]

        def query_count(line_count, suffix):
            employee = Employee.objects.create(
                company=self.company,
                employee_id=f"ECR-Q-{suffix}",
                name=f"Query Employee {suffix}",
                email=f"before-{suffix}@example.test",
                phone="100",
            )
            change_request = EmployeeChangeRequest.objects.create(
                employee=employee,
                field_name="email",
                current_value=employee.email,
                requested_value=f"after-{suffix}@example.test",
            )
            EmployeeChangeRequestLine.objects.bulk_create(
                [
                    EmployeeChangeRequestLine(
                        request=change_request,
                        field_name=field_name,
                        current_value="100" if field_name == "phone" else "",
                        requested_value=requested_value,
                    )
                    for field_name, requested_value in values[: line_count - 1]
                ]
            )
            with CaptureQueriesContext(connection) as captured:
                transition_employee_change_request(change_request, "submit", self.user)
            return len(captured)

        small = query_count(2, "small")
        large = query_count(12, "large")
        self.assertEqual(small, large)
