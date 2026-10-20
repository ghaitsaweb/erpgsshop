from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Attendance, Company, Employee
from modules.hr.access import HR_MANAGER
from modules.hr.models import AttendanceChangeRequest, HRWorkflowAudit
from modules.hr.security import ensure_hr_roles

from .attendance_changes import transition_attendance_change_request


class AttendanceChangeRequestWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="attendance-correction-test", password="test-password-123", email="attendance@example.test"
        )
        self.client.force_login(self.user)
        self.company = Company.objects.create(code="ACR", name="Attendance Company")
        self.employee = Employee.objects.create(company=self.company, employee_id="ACR-1", name="Attendance Employee")
        self.today = timezone.localdate()

    def _request(self, **changes):
        values = {
            "employee": self.employee,
            "attendance_date": self.today,
            "requested_check_in": time(8, 30),
            "reason": "Clock was incorrect",
        }
        values.update(changes)
        return AttendanceChangeRequest.objects.create(**values)

    def _action(self, change_request, action):
        return self.client.post(f"/app/attendance-change-requests/{change_request.pk}/action/{action}/")

    def test_approval_changes_existing_attendance_once_and_audits_values(self):
        attendance = Attendance.objects.create(
            employee=self.employee,
            attendance_date=self.today,
            check_in=time(9),
            check_out=time(17),
            note="Original note",
        )
        request = self._request()
        self._action(request, "submit")
        request.refresh_from_db()
        self.assertEqual(request.status, "submitted")
        self.assertTrue(request.had_attendance)
        self.assertEqual(request.original_check_in, time(9))

        self._action(request, "approve")
        self._action(request, "approve")
        request.refresh_from_db()
        attendance.refresh_from_db()
        self.assertEqual(request.status, "approved")
        self.assertEqual(request.approver, self.user)
        self.assertEqual(attendance.check_in, time(8, 30))
        self.assertEqual(attendance.check_out, time(17))
        self.assertEqual(attendance.note, "Original note")
        audits = HRWorkflowAudit.objects.filter(entity="attendance-change-requests", record_id=request.pk, action="approve")
        self.assertEqual(audits.count(), 1)
        self.assertEqual(audits.get().details["before"]["check_in"], "09:00:00")
        self.assertEqual(audits.get().details["after"]["check_in"], "08:30:00")

    def test_new_daily_attendance_is_created_only_after_approval(self):
        request = self._request(requested_check_out=time(17))
        self._action(request, "submit")
        self.assertFalse(Attendance.objects.filter(employee=self.employee, attendance_date=self.today).exists())
        self._action(request, "approve")
        attendance = Attendance.objects.get(employee=self.employee, attendance_date=self.today)
        self.assertEqual(attendance.check_in, time(8, 30))
        self.assertEqual(attendance.check_out, time(17))
        self.assertEqual(attendance.status, Attendance.Status.PRESENT)

    def test_rejection_and_cancellation_leave_daily_attendance_unchanged(self):
        attendance = Attendance.objects.create(employee=self.employee, attendance_date=self.today, check_in=time(9))
        rejected = self._request()
        self._action(rejected, "submit")
        self._action(rejected, "reject")
        attendance.refresh_from_db()
        self.assertEqual(attendance.check_in, time(9))

        cancelled = self._request(requested_check_in=time(8, 15))
        self._action(cancelled, "submit")
        self._action(cancelled, "cancel")
        attendance.refresh_from_db()
        self.assertEqual(attendance.check_in, time(9))

    def test_stale_or_duplicate_correction_does_not_change_daily_attendance(self):
        attendance = Attendance.objects.create(employee=self.employee, attendance_date=self.today, check_in=time(9))
        first = self._request()
        self._action(first, "submit")
        duplicate = self._request(requested_check_in=time(8, 15))
        self._action(duplicate, "submit")
        duplicate.refresh_from_db()
        self.assertEqual(duplicate.status, "draft")

        attendance.check_in = time(9, 15)
        attendance.save(update_fields=["check_in", "updated_at"])
        self._action(first, "approve")
        first.refresh_from_db()
        attendance.refresh_from_db()
        self.assertEqual(first.status, "submitted")
        self.assertEqual(attendance.check_in, time(9, 15))

    def test_submitted_request_cannot_be_edited(self):
        request = self._request()
        self._action(request, "submit")
        response = self.client.post(
            f"/app/attendance-change-requests/{request.pk}/edit/",
            {
                "employee": self.employee.pk,
                "attendance_date": self.today.isoformat(),
                "requested_check_in": "07:00",
                "reason": "Forged change",
            },
        )
        self.assertEqual(response.status_code, 302)
        request.refresh_from_db()
        self.assertEqual(request.requested_check_in, time(8, 30))

    def test_preexisting_submitted_request_without_snapshot_fails_closed(self):
        request = self._request(status="submitted")
        self._action(request, "approve")
        request.refresh_from_db()
        self.assertEqual(request.status, "submitted")
        self.assertFalse(Attendance.objects.filter(employee=self.employee, attendance_date=self.today).exists())

    def test_company_scope_blocks_other_company_approval(self):
        other_company = Company.objects.create(code="ACR2", name="Other Attendance Company")
        other_employee = Employee.objects.create(
            company=other_company,
            employee_id="ACR-OTHER",
            name="Other Attendance Employee",
        )
        foreign_request = AttendanceChangeRequest.objects.create(
            employee=other_employee,
            attendance_date=self.today,
            requested_check_in=time(8),
            reason="Different company",
        )
        manager = get_user_model().objects.create_user(username="attendance-manager", password="test-password-123")
        ensure_hr_roles()
        manager.groups.add(Group.objects.get(name=HR_MANAGER))
        Employee.objects.create(company=self.company, user=manager, employee_id="ACR-MANAGER", name="Manager")
        self.client.force_login(manager)
        response = self._action(foreign_request, "submit")
        self.assertEqual(response.status_code, 404)
        foreign_request.refresh_from_db()
        self.assertEqual(foreign_request.status, "draft")

    def test_query_count_does_not_grow_with_attendance_history(self):
        def measure(history_size, suffix):
            employee = Employee.objects.create(
                company=self.company,
                employee_id=f"ACR-SCALE-{suffix}",
                name=f"Scale Employee {suffix}",
            )
            Attendance.objects.bulk_create(
                [
                    Attendance(
                        employee=employee,
                        attendance_date=self.today - timedelta(days=index + 1),
                        check_in=time(9),
                    )
                    for index in range(history_size)
                ]
            )
            Attendance.objects.create(employee=employee, attendance_date=self.today, check_in=time(9))
            request = AttendanceChangeRequest.objects.create(
                employee=employee,
                attendance_date=self.today,
                requested_check_in=time(8, 30),
                reason="Query growth fixture",
            )
            with CaptureQueriesContext(connection) as queries:
                transition_attendance_change_request(request, "submit", self.user)
                transition_attendance_change_request(request, "approve", self.user)
            return len(queries)

        self.assertEqual(measure(2, "SMALL"), measure(12, "LARGE"))
