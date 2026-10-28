from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Employee
from modules.organization.models import UserOrganizationAccess

from .access import HR_MANAGER

from .leave_requests import transition_leave_request
from .leave_buckets import expire_leave_buckets
from .models import (
    CompanyHoliday,
    EmploymentContract,
    HRWorkflowAudit,
    LeaveAllocation,
    LeaveBlackoutPeriod,
    LeaveBucket,
    LeaveBucketConsumption,
    LeaveMovement,
    LeaveRequest,
    LeaveType,
    ScheduleAssignment,
    Shift,
    WorkSchedule,
)


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class LeaveRequestWorkflowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="leave-workflow-test", password="test-password-123", email="leave@example.test"
        )
        self.client.force_login(self.user)
        self.company = Company.objects.create(code="LVA", name="Leave Company")
        self.other_company = Company.objects.create(code="LVB", name="Other Company")
        self.employee = self._employee("LVA-1")
        today = timezone.localdate()
        self.monday = today + timedelta(days=14 + (7 - today.weekday()) % 7)
        self.leave_type = LeaveType.objects.create(company=self.company, code="AL", name="Annual Leave")
        self.shift = Shift.objects.create(
            company=self.company, code="DAY", name="Day Shift", start_time="09:00", end_time="17:00"
        )
        self.schedule = WorkSchedule.objects.create(
            company=self.company, code="WEEK", name="Weekdays", shift=self.shift
        )
        self._assignment(self.employee)
        self._allocation(self.employee, self.monday.year)

    def _employee(self, code):
        employee = Employee.objects.create(company=self.company, employee_id=code, name=f"Employee {code}")
        EmploymentContract.objects.create(
            employee=employee,
            number=f"CONTRACT-{code}",
            contract_type=EmploymentContract.ContractType.PERMANENT,
            start_date=timezone.localdate(),
            base_salary=Decimal("5000000"),
            status=EmploymentContract.Status.ACTIVE,
        )
        return employee

    def _assignment(self, employee):
        return ScheduleAssignment.objects.create(
            employee=employee, schedule=self.schedule, date_start=timezone.localdate()
        )

    def _allocation(self, employee, year, days="10"):
        return LeaveAllocation.objects.create(
            employee=employee,
            leave_type=self.leave_type,
            year=year,
            allocated_days=Decimal(days),
            status="approved",
        )

    def _request(self, *, employee=None, start=None, end=None, portion="full", leave_type=None):
        return LeaveRequest.objects.create(
            employee=employee or self.employee,
            leave_type_record=leave_type or self.leave_type,
            start_date=start or self.monday,
            end_date=end or (start or self.monday),
            portion=portion,
        )

    def _action(self, request, action):
        payload = {"reason": "Manager reviewed leave request"} if action == "reject" else {}
        return self.client.post(f"/app/leave-requests/{request.pk}/action/{action}/", payload)

    def test_workdays_holidays_and_exact_reversal(self):
        CompanyHoliday.objects.create(
            company=self.company, name="Company Holiday", holiday_date=self.monday + timedelta(days=1)
        )
        request = self._request(end=self.monday + timedelta(days=6))
        self._action(request, "submit")
        request.refresh_from_db()
        allocation = LeaveAllocation.objects.get(employee=self.employee, leave_type=self.leave_type)
        self.assertEqual(request.days, Decimal("4"))
        self.assertEqual(request.status, "submitted")
        self.assertEqual(allocation.used_days, Decimal("4"))
        debit = LeaveMovement.objects.get(leave_request=request, movement_type="usage")
        self.assertEqual(debit.days, Decimal("-4"))
        self.assertEqual(LeaveBucketConsumption.objects.get(leave_request=request).days, Decimal("4"))

        self._action(request, "approve")
        self._action(request, "approve")
        self._action(request, "cancel")
        self._action(request, "cancel")
        request.refresh_from_db()
        allocation.refresh_from_db()
        self.assertEqual(request.status, "cancelled")
        self.assertEqual(allocation.used_days, Decimal("0"))
        self.assertEqual(LeaveMovement.objects.filter(leave_request=request, movement_type="reversal").count(), 1)
        self.assertIsNotNone(LeaveBucketConsumption.objects.get(leave_request=request).reversed_at)

    def test_earliest_expiring_bucket_is_consumed_first_and_reversed_exactly(self):
        prior = self._allocation(self.employee, self.monday.year - 1, days="2")
        prior.valid_from = self.monday - timedelta(days=7)
        prior.expires_on = self.monday + timedelta(days=7)
        prior.save(update_fields=["valid_from", "expires_on", "updated_at"])
        current = LeaveAllocation.objects.get(employee=self.employee, year=self.monday.year)
        current.expires_on = self.monday + timedelta(days=30)
        current.save(update_fields=["expires_on", "updated_at"])
        request = self._request(end=self.monday + timedelta(days=2))

        transition_leave_request(request, "submit", self.user)
        request.refresh_from_db()
        consumptions = list(
            LeaveBucketConsumption.objects.filter(leave_request=request)
            .select_related("bucket")
            .order_by("bucket__expiry_date")
        )
        self.assertEqual([entry.days for entry in consumptions], [Decimal("2"), Decimal("1")])
        self.assertEqual([entry.bucket.allocation_id for entry in consumptions], [prior.pk, current.pk])
        self.assertEqual(
            LeaveMovement.objects.filter(leave_request=request, movement_type="usage").count(), 2
        )

        transition_leave_request(request, "cancel", self.user)
        for allocation in (prior, current):
            allocation.refresh_from_db()
            self.assertEqual(allocation.used_days, Decimal("0"))
        self.assertEqual(
            list(LeaveBucket.objects.filter(employee=self.employee).values_list("consumed_days", flat=True)),
            [Decimal("0"), Decimal("0")],
        )
        self.assertEqual(
            LeaveMovement.objects.filter(leave_request=request, movement_type="reversal").count(), 2
        )

    def test_expired_allocation_bucket_is_not_consumed(self):
        prior = self._allocation(self.employee, self.monday.year - 1, days="2")
        prior.valid_from = self.monday - timedelta(days=30)
        prior.expires_on = self.monday - timedelta(days=1)
        prior.save(update_fields=["valid_from", "expires_on", "updated_at"])
        request = self._request()
        transition_leave_request(request, "submit", self.user)
        self.assertEqual(
            list(LeaveBucketConsumption.objects.filter(leave_request=request).values_list("bucket__allocation_id", flat=True)),
            [LeaveAllocation.objects.get(employee=self.employee, year=self.monday.year).pk],
        )

    def test_bucket_count_does_not_increase_reservation_query_count(self):
        counts = []
        for index, bucket_count in enumerate((2, 12), start=20):
            employee = self._employee(f"LVA-{index}")
            self._assignment(employee)
            allocation = self._allocation(employee, self.monday.year, days=str(bucket_count))
            LeaveBucket.objects.bulk_create([
                LeaveBucket(
                    employee=employee,
                    leave_type=self.leave_type,
                    allocation=allocation,
                    year=self.monday.year,
                    cycle_key=f"test-{sequence}",
                    original_days=Decimal("1"),
                    start_valid_date=self.monday - timedelta(days=1),
                    expiry_date=self.monday + timedelta(days=sequence + 2),
                )
                for sequence in range(bucket_count)
            ])
            request = self._request(employee=employee, end=self.monday + timedelta(days=1))
            with CaptureQueriesContext(connection) as queries:
                transition_leave_request(request, "submit", self.user)
            counts.append(len(queries))
        self.assertEqual(counts[0], counts[1])

    def test_leave_type_policy_blocks_ineligible_or_blackout_requests_before_reservation(self):
        request = self._request(end=self.monday + timedelta(days=1))
        self.leave_type.eligible_gender = LeaveType.EligibleGender.FEMALE
        self.leave_type.save(update_fields=["eligible_gender", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "gender policy"):
            transition_leave_request(request, "submit", self.user)

        self.leave_type.eligible_gender = LeaveType.EligibleGender.ALL
        self.leave_type.min_tenure_value = 10000
        self.leave_type.save(update_fields=["eligible_gender", "min_tenure_value", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "minimum service"):
            transition_leave_request(request, "submit", self.user)

        self.leave_type.min_tenure_value = 0
        self.leave_type.max_days_per_request = 1
        self.leave_type.save(update_fields=["min_tenure_value", "max_days_per_request", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "maximum days"):
            transition_leave_request(request, "submit", self.user)

        self.leave_type.max_days_per_request = 0
        self.leave_type.notice_flat_days = 1000
        self.leave_type.save(update_fields=["max_days_per_request", "notice_flat_days", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "advance notice"):
            transition_leave_request(request, "submit", self.user)

        self.leave_type.notice_flat_days = 0
        self.leave_type.save(update_fields=["notice_flat_days", "updated_at"])
        blackout = LeaveBlackoutPeriod.objects.create(
            leave_type=self.leave_type,
            date_start=self.monday,
            date_end=self.monday + timedelta(days=1),
        )
        with self.assertRaisesMessage(ValidationError, "blackout"):
            transition_leave_request(request, "submit", self.user)
        self.assertFalse(LeaveBucketConsumption.objects.filter(leave_request=request).exists())
        self.assertEqual(LeaveAllocation.objects.get(employee=self.employee).used_days, Decimal("0"))

        blackout.active = False
        blackout.save(update_fields=["active", "updated_at"])
        transition_leave_request(request, "submit", self.user)
        self.assertEqual(LeaveAllocation.objects.get(employee=self.employee).used_days, Decimal("2"))

    def test_leave_type_cooling_period_blocks_followup_without_overlap(self):
        self.leave_type.cooling_period_days = 4
        self.leave_type.save(update_fields=["cooling_period_days", "updated_at"])
        first = self._request()
        transition_leave_request(first, "submit", self.user)
        followup = self._request(start=self.monday + timedelta(days=2))
        with self.assertRaisesMessage(ValidationError, "cooling period"):
            transition_leave_request(followup, "submit", self.user)
        self.assertFalse(LeaveBucketConsumption.objects.filter(leave_request=followup).exists())

    def test_expiry_forfeits_only_unconsumed_days_once(self):
        allocation = LeaveAllocation.objects.get(employee=self.employee)
        bucket = LeaveBucket.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            allocation=allocation,
            year=allocation.year,
            original_days=Decimal("10"),
            consumed_days=Decimal("2"),
            start_valid_date=timezone.localdate() - timedelta(days=30),
            expiry_date=timezone.localdate() - timedelta(days=1),
        )
        self.assertEqual(expire_leave_buckets(batch_size=1), 1)
        self.assertEqual(expire_leave_buckets(batch_size=1), 0)
        bucket.refresh_from_db()
        self.assertEqual(bucket.forfeited_days, Decimal("8"))
        self.assertEqual(bucket.remaining_days, Decimal("0"))
        self.assertEqual(bucket.status, LeaveBucket.Status.EXPIRED)

    def test_expiry_defers_buckets_beyond_batch_limit_without_skipping_them(self):
        allocation = LeaveAllocation.objects.get(employee=self.employee)
        LeaveBucket.objects.bulk_create([
            LeaveBucket(
                employee=self.employee, leave_type=self.leave_type, allocation=allocation,
                year=allocation.year, cycle_key=f"old-{index}", original_days=Decimal("1"),
                start_valid_date=timezone.localdate() - timedelta(days=30),
                expiry_date=timezone.localdate() - timedelta(days=1),
            )
            for index in range(3)
        ])
        self.assertEqual(expire_leave_buckets(batch_size=1, max_batches=2), 2)
        self.assertEqual(expire_leave_buckets(batch_size=1, max_batches=2), 1)
        self.assertEqual(LeaveBucket.objects.filter(status=LeaveBucket.Status.EXPIRED).count(), 3)

    def test_rejection_after_bucket_expiry_forfeits_restored_days(self):
        request = self._request()
        transition_leave_request(request, "submit", self.user)
        consumption = LeaveBucketConsumption.objects.get(leave_request=request)
        bucket = consumption.bucket
        bucket.expiry_date = timezone.localdate() - timedelta(days=1)
        bucket.save(update_fields=["expiry_date", "updated_at"])
        self.assertEqual(expire_leave_buckets(today=timezone.localdate()), 1)
        transition_leave_request(request, "reject", self.user, reason="No longer needed")
        bucket.refresh_from_db()
        self.assertEqual(bucket.consumed_days, Decimal("0"))
        self.assertEqual(bucket.forfeited_days, Decimal("10"))
        self.assertEqual(bucket.remaining_days, Decimal("0"))

    def test_half_day_reject_and_overlap(self):
        request = self._request(portion="half")
        self._action(request, "submit")
        request.refresh_from_db()
        self.assertEqual(request.days, Decimal("0.5"))
        overlap = self._request()
        self._action(overlap, "submit")
        overlap.refresh_from_db()
        self.assertEqual(overlap.status, "draft")
        self._action(request, "reject")
        self.assertEqual(LeaveAllocation.objects.get(employee=self.employee).used_days, Decimal("0"))
        audit = HRWorkflowAudit.objects.get(entity="leave-requests", record_id=request.pk, action="reject")
        self.assertEqual(audit.details["reason"], "Manager reviewed leave request")
        self._action(overlap, "submit")
        overlap.refresh_from_db()
        self.assertEqual(overlap.status, "submitted")

    def test_insufficient_balance_and_missing_schedule_have_no_side_effects(self):
        allocation = LeaveAllocation.objects.get(employee=self.employee)
        allocation.allocated_days = Decimal("1")
        allocation.save(update_fields=["allocated_days", "updated_at"])
        request = self._request(end=self.monday + timedelta(days=2))
        self._action(request, "submit")
        request.refresh_from_db()
        allocation.refresh_from_db()
        self.assertEqual(request.status, "draft")
        self.assertEqual(allocation.used_days, Decimal("0"))
        self.assertFalse(LeaveMovement.objects.filter(leave_request=request).exists())

        other = self._employee("LVA-2")
        self._allocation(other, self.monday.year)
        no_schedule = self._request(employee=other)
        self._action(no_schedule, "submit")
        no_schedule.refresh_from_db()
        self.assertEqual(no_schedule.status, "draft")

    def test_cross_company_and_workflow_immutability(self):
        foreign_type = LeaveType.objects.create(company=self.other_company, code="AL", name="Other Leave")
        response = self.client.post(
            "/app/leave-requests/new/",
            {
                "employee": self.employee.pk,
                "leave_type_record": foreign_type.pk,
                "start_date": self.monday.isoformat(),
                "end_date": self.monday.isoformat(),
                "portion": "full",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("leave_type_record", response.context["form"].errors)
        self.assertFalse(LeaveRequest.objects.exists())
        with self.assertRaisesMessage(ValidationError, "Leave type must belong to the employee company"):
            LeaveRequest(
                employee=self.employee,
                leave_type_record=foreign_type,
                start_date=self.monday,
                end_date=self.monday,
            ).full_clean()

        request = self._request()
        self._action(request, "submit")
        response = self.client.post(
            f"/app/leave-requests/{request.pk}/edit/",
            {
                "employee": self.employee.pk,
                "leave_type_record": self.leave_type.pk,
                "start_date": (self.monday + timedelta(days=1)).isoformat(),
                "end_date": (self.monday + timedelta(days=1)).isoformat(),
                "portion": "full",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.client.post(f"/app/leave-requests/{request.pk}/delete/")
        request.refresh_from_db()
        self.assertEqual(request.start_date, self.monday)
        self.assertEqual(request.status, "submitted")

    def test_calendar_days_and_query_count_do_not_grow_with_duration(self):
        self.leave_type.day_count_by = LeaveType.DayCountBy.CALENDAR_DAY
        self.leave_type.save(update_fields=["day_count_by", "updated_at"])
        counts = []
        for index, span in enumerate((2, 12), start=2):
            employee = self._employee(f"LVA-{index}")
            self._allocation(employee, self.monday.year, days="20")
            request = self._request(employee=employee, end=self.monday + timedelta(days=span - 1))
            with CaptureQueriesContext(connection) as queries:
                transition_leave_request(request, "submit", self.user)
            counts.append(len(queries))
            request.refresh_from_db()
            self.assertEqual(request.days, Decimal(span))
        self.assertEqual(counts[0], counts[1])

    def test_calendar_leave_spanning_years_uses_each_year_allocation(self):
        self.leave_type.day_count_by = LeaveType.DayCountBy.CALENDAR_DAY
        self.leave_type.save(update_fields=["day_count_by", "updated_at"])
        year_end = date(self.monday.year, 12, 31)
        self._allocation(self.employee, year_end.year + 1)
        request = self._request(start=year_end, end=year_end + timedelta(days=1))
        self._action(request, "submit")
        request.refresh_from_db()
        self.assertEqual(request.status, "submitted")
        self.assertEqual(request.days, Decimal("2"))
        self.assertEqual(
            list(LeaveAllocation.objects.filter(employee=self.employee).order_by("year").values_list("used_days", flat=True)),
            [Decimal("1"), Decimal("1")],
        )
        self._action(request, "reject")
        self.assertEqual(
            list(LeaveAllocation.objects.filter(employee=self.employee).order_by("year").values_list("used_days", flat=True)),
            [Decimal("0"), Decimal("0")],
        )

    def test_legacy_submitted_request_without_reservation_cannot_be_approved(self):
        request = self._request()
        request.status = LeaveRequest.Status.SUBMITTED
        request.save(update_fields=["status", "updated_at"])
        self._action(request, "approve")
        request.refresh_from_db()
        self.assertEqual(request.status, "submitted")
        self.assertEqual(LeaveAllocation.objects.get(employee=self.employee).used_days, Decimal("0"))

    def test_required_evidence_is_validated_stored_and_scoped(self):
        self.leave_type.requires_attachment = True
        self.leave_type.save(update_fields=["requires_attachment", "updated_at"])
        payload = {
            "employee": self.employee.pk,
            "leave_type_record": self.leave_type.pk,
            "start_date": self.monday.isoformat(),
            "end_date": self.monday.isoformat(),
            "portion": "full",
        }
        no_file = self.client.post("/app/leave-requests/new/", payload)
        self.assertIn("attachment", no_file.context["form"].errors)
        bad_file = self.client.post(
            "/app/leave-requests/new/",
            {**payload, "attachment": SimpleUploadedFile("evidence.exe", b"bad")},
        )
        self.assertIn("attachment", bad_file.context["form"].errors)
        response = self.client.post(
            "/app/leave-requests/new/",
            {**payload, "attachment": SimpleUploadedFile("evidence.pdf", b"%PDF-1.4 test", content_type="application/pdf")},
        )
        self.assertEqual(response.status_code, 302)
        request = LeaveRequest.objects.get(employee=self.employee)
        self.assertEqual(request.attachment_name, "evidence.pdf")
        self._action(request, "submit")
        request.refresh_from_db()
        self.assertEqual(request.status, "submitted")
        download = self.client.get(f"/app/leave-requests/{request.pk}/attachment/")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, b"%PDF-1.4 test")
        self.assertEqual(download["X-Content-Type-Options"], "nosniff")

        outsider = get_user_model().objects.create_user(username="leave-outsider", password="test-password-123")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(f"/app/leave-requests/{request.pk}/attachment/").status_code, 404)

    def test_attachment_threshold_uses_workdays_and_configured_size(self):
        self.leave_type.requires_attachment = True
        self.leave_type.attachment_trigger = "exceed_n_days"
        self.leave_type.attachment_exceed_n_days = 2
        self.leave_type.max_file_size_mb = 1
        self.leave_type.save(update_fields=[
            "requires_attachment", "attachment_trigger", "attachment_exceed_n_days",
            "max_file_size_mb", "updated_at",
        ])
        one_day = self._request()
        transition_leave_request(one_day, "submit", self.user)
        one_day.refresh_from_db()
        self.assertEqual(one_day.days, Decimal("1"))

        three_days = self._request(
            start=self.monday + timedelta(days=7),
            end=self.monday + timedelta(days=9),
        )
        with self.assertRaisesMessage(ValidationError, "Evidence is required"):
            transition_leave_request(three_days, "submit", self.user)
        three_days.attachment_content = b"x" * (1024 * 1024 + 1)
        three_days.save(update_fields=["attachment_content", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "cannot exceed 1 MB"):
            transition_leave_request(three_days, "submit", self.user)
        three_days.attachment_content = b"evidence"
        three_days.save(update_fields=["attachment_content", "updated_at"])
        transition_leave_request(three_days, "submit", self.user)
        three_days.refresh_from_db()
        self.assertEqual(three_days.days, Decimal("3"))

    def test_leave_list_does_not_load_attachment_binary(self):
        request = self._request()
        request.attachment_name = "evidence.pdf"
        request.attachment_content = b"%PDF-1.4" + b"x" * 1024
        request.save(update_fields=["attachment_name", "attachment_content", "updated_at"])
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/app/leave-requests/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "evidence.pdf")
        binary_column = '"gsshop_hr_leaverequest"."attachment_content"'
        self.assertFalse(any(binary_column in query["sql"] for query in queries))

    def test_allocation_list_query_count_stays_fixed_for_2_and_12_rows(self):
        def count_with(total):
            existing = LeaveAllocation.objects.filter(employee=self.employee).count()
            for index in range(existing, total):
                leave_type = LeaveType.objects.create(
                    company=self.company, code=f"TYPE-{index}", name=f"Leave Type {index}"
                )
                LeaveAllocation.objects.create(
                    employee=self.employee, leave_type=leave_type, year=self.monday.year,
                    allocated_days=Decimal("10"), status="approved",
                )
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get("/app/leave-allocations/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context["rows"]), total)
            return len(queries)

        self.assertEqual(count_with(2), count_with(12))

    def test_admin_cannot_bypass_leave_balance_workflow(self):
        request = self._request()
        allocation = LeaveAllocation.objects.get(employee=self.employee)
        leave_response = self.client.post(
            f"/admin/gsshop_hr/leaverequest/{request.pk}/change/",
            {"status": "approved"},
        )
        allocation_response = self.client.post(
            f"/admin/gsshop_hr/leaveallocation/{allocation.pk}/change/",
            {"status": "approved", "used_days": "9"},
        )
        self.assertEqual((leave_response.status_code, allocation_response.status_code), (403, 403))
        request.refresh_from_db()
        allocation.refresh_from_db()
        self.assertEqual(request.status, LeaveRequest.Status.DRAFT)
        self.assertEqual(allocation.used_days, Decimal("0"))

    def test_hr_manager_cannot_read_or_approve_another_company_request(self):
        foreign_employee = Employee.objects.create(
            company=self.other_company, employee_id="LVB-1", name="Other Employee"
        )
        foreign_type = LeaveType.objects.create(company=self.other_company, code="AL", name="Other Leave")
        foreign_request = LeaveRequest.objects.create(
            employee=foreign_employee,
            leave_type_record=foreign_type,
            start_date=self.monday,
            end_date=self.monday,
            status=LeaveRequest.Status.SUBMITTED,
            attachment_name="private.pdf",
            attachment_content_type="application/pdf",
            attachment_content=b"private",
        )
        manager = get_user_model().objects.create_user(username="leave-manager", password="test-password-123")
        manager.groups.add(Group.objects.get(name=HR_MANAGER))
        branch = self.company.branches.get(is_main=True)
        access = UserOrganizationAccess.objects.create(
            user=manager, default_company=self.company, default_branch=branch
        )
        access.companies.add(self.company)
        access.branches.add(branch)
        self.client.force_login(manager)
        self.assertEqual(self.client.get(f"/app/leave-requests/{foreign_request.pk}/attachment/").status_code, 404)
        self.assertEqual(self._action(foreign_request, "approve").status_code, 404)
        foreign_request.refresh_from_db()
        self.assertEqual(foreign_request.status, LeaveRequest.Status.SUBMITTED)

    def test_approved_past_leave_cannot_be_cancelled(self):
        past = timezone.localdate() - timedelta(days=2)
        request = self._request(start=past, end=past)
        request.status = LeaveRequest.Status.APPROVED
        request.days = Decimal("1")
        request.save(update_fields=["status", "days", "updated_at"])
        with self.assertRaises(ValidationError):
            transition_leave_request(request, "cancel", self.user)
        request.refresh_from_db()
        self.assertEqual(request.status, "approved")
