from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Attendance, Company, Employee

from .models import AttendanceChangeRequest, HRWorkflowAudit, RawAttendance, ScheduleAssignment, Shift, WorkSchedule
from .raw_attendance import process_raw_attendance


class RawAttendanceProcessingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="raw-attendance-test", password="test-password-123", email="raw@example.test"
        )
        self.client.force_login(self.user)
        self.company = Company.objects.create(code="RAW", name="Raw Attendance Company")
        self.employee = Employee.objects.create(company=self.company, employee_id="RAW-1", name="Raw Employee")
        self.work_date = timezone.localdate()
        self.shift = Shift.objects.create(
            company=self.company, code="DAY", name="Day", start_time=time(8), end_time=time(17)
        )
        self.schedule = WorkSchedule.objects.create(
            company=self.company, code="EVERY", name="Every Day", shift=self.shift,
            working_days="Mon,Tue,Wed,Thu,Fri,Sat,Sun",
        )
        ScheduleAssignment.objects.create(
            employee=self.employee, schedule=self.schedule, date_start=self.work_date - timedelta(days=30)
        )

    def _punch(self, hour, event_type, *, employee=None, day=None):
        captured = datetime.combine(day or self.work_date, time(hour), tzinfo=ZoneInfo("Asia/Jakarta"))
        return RawAttendance.objects.create(
            employee=employee or self.employee, captured_at=captured, event_type=event_type
        )

    def _action(self, punch):
        return self.client.post(f"/app/raw-attendance/{punch.pk}/action/process/")

    def test_process_pair_creates_daily_attendance_once_with_audit(self):
        first = self._punch(8, RawAttendance.EventType.CHECK_IN)
        second = self._punch(17, RawAttendance.EventType.CHECK_OUT)

        self._action(first)
        first.refresh_from_db()
        second.refresh_from_db()
        attendance = Attendance.objects.get(employee=self.employee, attendance_date=self.work_date)
        self.assertEqual((attendance.check_in, attendance.check_out), (time(8), time(17)))
        self.assertTrue(attendance.from_raw_attendance)
        self.assertEqual(timezone.localtime(attendance.check_in_at).date(), self.work_date)
        self.assertEqual(timezone.localtime(attendance.check_out_at).date(), self.work_date)
        self.assertEqual(attendance.status, Attendance.Status.PRESENT)
        self.assertTrue(first.processed)
        self.assertTrue(second.processed)
        audit = HRWorkflowAudit.objects.get(entity="raw-attendance", action="process")
        self.assertEqual((audit.previous_status, audit.new_status), ("unprocessed", "processed"))
        self.assertEqual(audit.details["raw_ids"], [first.pk, second.pk])

        self._action(second)
        self.assertEqual(Attendance.objects.filter(employee=self.employee, attendance_date=self.work_date).count(), 1)
        self.assertEqual(HRWorkflowAudit.objects.filter(entity="raw-attendance", action="process").count(), 1)

    def test_duplicate_or_reverse_punches_fail_without_partial_changes(self):
        first = self._punch(8, RawAttendance.EventType.CHECK_IN)
        self._punch(9, RawAttendance.EventType.CHECK_IN)
        self._punch(17, RawAttendance.EventType.CHECK_OUT)
        self._action(first)
        self.assertFalse(Attendance.objects.filter(employee=self.employee).exists())
        self.assertEqual(RawAttendance.objects.filter(employee=self.employee, processed=True).count(), 0)

        RawAttendance.objects.filter(employee=self.employee).update(captured_at=timezone.now() - timedelta(days=2))
        reverse = self._punch(8, RawAttendance.EventType.CHECK_OUT)
        self._punch(17, RawAttendance.EventType.CHECK_IN)
        self._action(reverse)
        self.assertFalse(Attendance.objects.filter(employee=self.employee).exists())

    def test_existing_daily_record_or_pending_correction_is_preserved(self):
        first = self._punch(8, RawAttendance.EventType.CHECK_IN)
        self._punch(17, RawAttendance.EventType.CHECK_OUT)
        existing = Attendance.objects.create(employee=self.employee, attendance_date=self.work_date, check_in=time(9))
        self._action(first)
        existing.refresh_from_db()
        self.assertEqual(existing.check_in, time(9))
        self.assertFalse(RawAttendance.objects.filter(employee=self.employee, processed=True).exists())

        existing.delete()
        AttendanceChangeRequest.objects.create(
            employee=self.employee, attendance_date=self.work_date, requested_check_in=time(9),
            reason="Pending correction", status="submitted",
        )
        self._action(first)
        self.assertFalse(Attendance.objects.filter(employee=self.employee).exists())

    def test_schedule_company_and_overnight_fail_closed(self):
        first = self._punch(8, RawAttendance.EventType.CHECK_IN)
        self._punch(17, RawAttendance.EventType.CHECK_OUT)
        self.shift.end_time = time(6)
        self.shift.save(update_fields=["end_time"])
        self._action(first)
        self.assertFalse(Attendance.objects.filter(employee=self.employee).exists())

        self.shift.end_time = self.shift.start_time
        self.shift.save(update_fields=["end_time"])
        self._action(first)
        self.assertFalse(Attendance.objects.filter(employee=self.employee).exists())

        self.shift.end_time = time(17)
        self.shift.save(update_fields=["end_time"])
        other_company = Company.objects.create(code="RAW2", name="Other Company")
        self.schedule.company = other_company
        self.schedule.save(update_fields=["company"])
        self._action(first)
        self.assertFalse(Attendance.objects.filter(employee=self.employee).exists())

    def test_overlapping_assignments_fail_closed(self):
        first = self._punch(8, RawAttendance.EventType.CHECK_IN)
        self._punch(17, RawAttendance.EventType.CHECK_OUT)
        ScheduleAssignment.objects.create(
            employee=self.employee, schedule=self.schedule, date_start=self.work_date
        )
        self._action(first)
        self.assertFalse(Attendance.objects.filter(employee=self.employee).exists())

    def test_assignment_form_rejects_overlap_and_cross_company_schedule(self):
        response = self.client.post(
            "/app/schedule-assignments/new/",
            {
                "employee": self.employee.pk,
                "schedule": self.schedule.pk,
                "date_start": self.work_date.isoformat(),
                "active": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ScheduleAssignment.objects.filter(employee=self.employee).count(), 1)

        assignment = ScheduleAssignment.objects.get(employee=self.employee)
        assignment.date_end = self.work_date + timedelta(days=30)
        assignment.save(update_fields=["date_end"])

        other_company = Company.objects.create(code="RAW-FORM", name="Other Schedule Company")
        other_shift = Shift.objects.create(
            company=other_company, code="DAY", name="Other Day", start_time=time(8), end_time=time(17)
        )
        other_schedule = WorkSchedule.objects.create(
            company=other_company, code="DAY", name="Other Schedule", shift=other_shift
        )
        candidate = ScheduleAssignment(
            employee=self.employee, schedule=other_schedule, date_start=self.work_date + timedelta(days=31)
        )
        with self.assertRaisesMessage(ValidationError, "Schedule and shift must belong"):
            candidate.full_clean()

        valid = ScheduleAssignment(
            employee=self.employee, schedule=self.schedule, date_start=self.work_date + timedelta(days=31)
        )
        valid.full_clean()

    def test_local_midnight_uses_work_date_not_utc_date(self):
        self.shift.start_time = time(0)
        self.shift.end_time = time(2)
        self.shift.save(update_fields=["start_time", "end_time"])
        first = self._punch(0, RawAttendance.EventType.CHECK_IN)
        self._punch(1, RawAttendance.EventType.CHECK_OUT)
        self._action(first)
        attendance = Attendance.objects.get(employee=self.employee, attendance_date=self.work_date)
        self.assertEqual((attendance.check_in, attendance.check_out), (time(0), time(1)))

    def test_overnight_pair_uses_check_in_work_date_even_when_checkout_clicked(self):
        self.shift.start_time = time(22)
        self.shift.end_time = time(6)
        self.shift.save(update_fields=["start_time", "end_time"])
        first = self._punch(22, RawAttendance.EventType.CHECK_IN)
        second = self._punch(6, RawAttendance.EventType.CHECK_OUT, day=self.work_date + timedelta(days=1))

        self._action(second)
        attendance = Attendance.objects.get(employee=self.employee, attendance_date=self.work_date)
        self.assertEqual((attendance.check_in, attendance.check_out), (time(22), time(6)))
        self.assertEqual(timezone.localtime(attendance.check_in_at).date(), self.work_date)
        self.assertEqual(timezone.localtime(attendance.check_out_at).date(), self.work_date + timedelta(days=1))
        self.assertFalse(Attendance.objects.filter(employee=self.employee, attendance_date=self.work_date + timedelta(days=1)).exists())
        self._action(first)
        self.assertEqual(Attendance.objects.filter(employee=self.employee, attendance_date=self.work_date).count(), 1)

    def test_overnight_correction_preserves_checkout_date_and_blocks_direct_edit(self):
        self.shift.start_time = time(22)
        self.shift.end_time = time(6)
        self.shift.save(update_fields=["start_time", "end_time"])
        first = self._punch(22, RawAttendance.EventType.CHECK_IN)
        self._punch(6, RawAttendance.EventType.CHECK_OUT, day=self.work_date + timedelta(days=1))
        self._action(first)
        attendance = Attendance.objects.get(employee=self.employee, attendance_date=self.work_date)

        self.client.post(
            f"/app/attendance/{attendance.pk}/edit/",
            {
                "employee": self.employee.pk, "attendance_date": self.work_date.isoformat(),
                "check_in": "22:00", "check_out": "12:00", "status": "present", "note": "forged",
            },
        )
        self.client.post(f"/app/attendance/{attendance.pk}/delete/")
        attendance.refresh_from_db()
        self.assertEqual(attendance.check_out, time(6))

        correction = AttendanceChangeRequest.objects.create(
            employee=self.employee, attendance_date=self.work_date,
            requested_check_out=time(7), reason="Checkout device clock was one hour slow",
        )
        self.client.post(f"/app/attendance-change-requests/{correction.pk}/action/submit/")
        self.client.post(f"/app/attendance-change-requests/{correction.pk}/action/approve/")
        correction.refresh_from_db()
        attendance.refresh_from_db()
        self.assertEqual(correction.status, "approved")
        self.assertEqual(attendance.check_out, time(7))
        self.assertEqual(timezone.localtime(attendance.check_out_at).date(), self.work_date + timedelta(days=1))
        self.assertEqual(timezone.localtime(attendance.check_out_at).time().replace(tzinfo=None), time(7))

    def test_correction_detects_changed_raw_timestamp_after_submission(self):
        first = self._punch(8, RawAttendance.EventType.CHECK_IN)
        self._punch(17, RawAttendance.EventType.CHECK_OUT)
        self._action(first)
        attendance = Attendance.objects.get(employee=self.employee, attendance_date=self.work_date)
        correction = AttendanceChangeRequest.objects.create(
            employee=self.employee, attendance_date=self.work_date,
            requested_check_out=time(18), reason="Device timestamp needs review",
        )
        self.client.post(f"/app/attendance-change-requests/{correction.pk}/action/submit/")
        attendance.check_out_at += timedelta(minutes=1)
        attendance.save(update_fields=["check_out_at", "updated_at"])
        self.client.post(f"/app/attendance-change-requests/{correction.pk}/action/approve/")
        correction.refresh_from_db()
        attendance.refresh_from_db()
        self.assertEqual(correction.status, "submitted")
        self.assertEqual(attendance.check_out, time(17))

    def test_overnight_correction_rejects_duration_over_24_hours(self):
        self.shift.start_time = time(22)
        self.shift.end_time = time(6)
        self.shift.save(update_fields=["start_time", "end_time"])
        first = self._punch(22, RawAttendance.EventType.CHECK_IN)
        self._punch(6, RawAttendance.EventType.CHECK_OUT, day=self.work_date + timedelta(days=1))
        self._action(first)
        correction = AttendanceChangeRequest.objects.create(
            employee=self.employee, attendance_date=self.work_date,
            requested_check_in=time(5), reason="Invalid long shift correction",
        )
        self.client.post(f"/app/attendance-change-requests/{correction.pk}/action/submit/")
        correction.refresh_from_db()
        self.assertEqual(correction.status, "draft")
        attendance = Attendance.objects.get(employee=self.employee, attendance_date=self.work_date)
        self.assertEqual(attendance.check_in, time(22))

    def test_overnight_extra_punch_fails_without_consuming_any_event(self):
        self.shift.start_time = time(22)
        self.shift.end_time = time(6)
        self.shift.save(update_fields=["start_time", "end_time"])
        first = self._punch(22, RawAttendance.EventType.CHECK_IN)
        self._punch(6, RawAttendance.EventType.CHECK_OUT, day=self.work_date + timedelta(days=1))
        self._punch(7, RawAttendance.EventType.CHECK_IN, day=self.work_date + timedelta(days=1))
        self._action(first)
        self.assertFalse(Attendance.objects.filter(employee=self.employee).exists())
        self.assertFalse(RawAttendance.objects.filter(employee=self.employee, processed=True).exists())

    def test_consecutive_overnight_days_do_not_reuse_prior_checkout(self):
        self.shift.start_time = time(22)
        self.shift.end_time = time(6)
        self.shift.save(update_fields=["start_time", "end_time"])
        first = self._punch(22, RawAttendance.EventType.CHECK_IN)
        self._punch(6, RawAttendance.EventType.CHECK_OUT, day=self.work_date + timedelta(days=1))
        self._action(first)

        second = self._punch(22, RawAttendance.EventType.CHECK_IN, day=self.work_date + timedelta(days=1))
        self._punch(6, RawAttendance.EventType.CHECK_OUT, day=self.work_date + timedelta(days=2))
        self._action(second)
        self.assertEqual(
            list(Attendance.objects.filter(employee=self.employee).order_by("attendance_date").values_list("attendance_date", flat=True)),
            [self.work_date, self.work_date + timedelta(days=1)],
        )

    def test_processed_field_cannot_be_forged_and_processed_event_is_immutable(self):
        first = self._punch(8, RawAttendance.EventType.CHECK_IN)
        self._punch(17, RawAttendance.EventType.CHECK_OUT)
        payload = {
            "employee": self.employee.pk,
            "captured_at": first.captured_at.isoformat(),
            "event_type": RawAttendance.EventType.CHECK_IN,
            "source": "manual",
            "device_identifier": "",
            "note": "forged",
            "processed": "on",
        }
        response = self.client.post(f"/app/raw-attendance/{first.pk}/edit/", payload)
        first.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(first.note, "forged")
        self.assertFalse(first.processed)
        self._action(first)
        payload["note"] = "changed after processing"
        self.client.post(f"/app/raw-attendance/{first.pk}/edit/", payload)
        first.refresh_from_db()
        self.assertNotEqual(first.note, payload["note"])

    def test_user_without_hr_change_permission_cannot_process(self):
        first = self._punch(8, RawAttendance.EventType.CHECK_IN)
        self._punch(17, RawAttendance.EventType.CHECK_OUT)
        ordinary = get_user_model().objects.create_user(username="raw-no-access", password="test-password-123")
        self.client.force_login(ordinary)
        self._action(first)
        self.assertFalse(Attendance.objects.filter(employee=self.employee, attendance_date=self.work_date).exists())
        first.refresh_from_db()
        self.assertFalse(first.processed)

    def test_company_change_after_selection_fails_before_processing(self):
        first = self._punch(8, RawAttendance.EventType.CHECK_IN)
        self._punch(17, RawAttendance.EventType.CHECK_OUT)
        other_company = Company.objects.create(code="RAW-STALE", name="Stale Company")
        with self.assertRaisesMessage(ValidationError, "Employee company changed"):
            process_raw_attendance(first, expected_company_id=other_company.pk)
        self.assertFalse(Attendance.objects.filter(employee=self.employee).exists())

    def test_query_count_does_not_grow_with_employee_history(self):
        counts = []
        for index, history_size in enumerate((2, 12)):
            employee = Employee.objects.create(
                company=self.company, employee_id=f"RAW-H{index}", name=f"History Employee {index}"
            )
            ScheduleAssignment.objects.create(
                employee=employee, schedule=self.schedule, date_start=self.work_date - timedelta(days=30)
            )
            for offset in range(1, history_size + 1):
                Attendance.objects.create(
                    employee=employee, attendance_date=self.work_date - timedelta(days=offset), check_in=time(8)
                )
            first = self._punch(8, RawAttendance.EventType.CHECK_IN, employee=employee)
            self._punch(17, RawAttendance.EventType.CHECK_OUT, employee=employee)
            with CaptureQueriesContext(connection) as queries:
                process_raw_attendance(first)
            counts.append(len(queries))
        self.assertEqual(counts[0], counts[1])

    def test_overnight_query_count_does_not_grow_with_prior_raw_history(self):
        self.shift.start_time = time(22)
        self.shift.end_time = time(6)
        self.shift.save(update_fields=["start_time", "end_time"])
        counts = []
        for index, history_size in enumerate((2, 12)):
            employee = Employee.objects.create(
                company=self.company, employee_id=f"RAW-N{index}", name=f"Night Employee {index}"
            )
            ScheduleAssignment.objects.create(
                employee=employee, schedule=self.schedule, date_start=self.work_date - timedelta(days=30)
            )
            RawAttendance.objects.bulk_create([
                RawAttendance(
                    employee=employee,
                    captured_at=datetime.combine(
                        self.work_date - timedelta(days=offset), time(6), tzinfo=ZoneInfo("Asia/Jakarta")
                    ),
                    event_type=RawAttendance.EventType.CHECK_OUT,
                    processed=True,
                )
                for offset in range(1, history_size + 1)
            ])
            self._punch(22, RawAttendance.EventType.CHECK_IN, employee=employee)
            checkout = self._punch(
                6, RawAttendance.EventType.CHECK_OUT,
                employee=employee, day=self.work_date + timedelta(days=1),
            )
            with CaptureQueriesContext(connection) as queries:
                process_raw_attendance(checkout)
            counts.append(len(queries))
        self.assertEqual(counts[0], counts[1])

    def test_attendance_list_query_count_does_not_grow_per_employee_row(self):
        counts = []
        for start, end in ((0, 2), (2, 12)):
            Attendance.objects.bulk_create([
                Attendance(
                    employee=self.employee,
                    attendance_date=self.work_date - timedelta(days=offset),
                    check_in=time(8),
                )
                for offset in range(start, end)
            ])
            self.client.get("/app/attendance/")
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get("/app/attendance/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context["rows"]), end)
            self.assertContains(response, "Raw Check Out At")
            counts.append(len(queries))
        self.assertEqual(counts[0], counts[1])
