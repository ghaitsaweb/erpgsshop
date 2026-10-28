from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Employee
from modules.organization.models import UserOrganizationAccess

from .access import HR_MANAGER
from .leave_encashments import transition_leave_encashment
from .leave_buckets import expire_leave_buckets
from .models import (
    EmploymentContract,
    LeaveAllocation,
    LeaveBucket,
    LeaveEncashment,
    LeaveEncashmentBucket,
    LeaveMovement,
    LeaveType,
)


class LeaveEncashmentWorkflowTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_superuser(
            username="encashment-manager", password="test-password-123", email="encashment@example.test"
        )
        self.today = timezone.localdate()
        self.company = Company.objects.create(code="LEC", name="Leave Encashment Company")
        self.employee = Employee.objects.create(
            company=self.company, employee_id="LEC-1", name="Encashment Employee"
        )
        self.leave_type = LeaveType.objects.create(
            company=self.company,
            code="ANNUAL",
            name="Annual",
            allow_leave_encashment=True,
            max_encashment_days=Decimal("5"),
            min_retained_balance=Decimal("2"),
            allow_partial_encashment=True,
            encashment_calculation_method="employee_wage",
        )
        self.allocation = LeaveAllocation.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            year=self.today.year,
            allocated_days=Decimal("10"),
            status="approved",
        )
        EmploymentContract.objects.create(
            employee=self.employee,
            number="LEC-CONTRACT",
            contract_type=EmploymentContract.ContractType.PERMANENT,
            start_date=self.today - timedelta(days=1),
            base_salary=Decimal("2200"),
            status=EmploymentContract.Status.ACTIVE,
        )

    def _request(self, days="3"):
        return LeaveEncashment.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            request_date=self.today,
            days=Decimal(days),
            reason="Cash out annual leave",
        )

    def test_submit_approve_and_cancel_restore_exact_bucket(self):
        first = LeaveBucket.objects.create(
            employee=self.employee, leave_type=self.leave_type, allocation=self.allocation,
            year=self.today.year, cycle_key="first", original_days=Decimal("4"),
            start_valid_date=self.today, expiry_date=self.today + timedelta(days=30),
        )
        second = LeaveBucket.objects.create(
            employee=self.employee, leave_type=self.leave_type, allocation=self.allocation,
            year=self.today.year, cycle_key="second", original_days=Decimal("6"),
            start_valid_date=self.today, expiry_date=self.today + timedelta(days=60),
        )
        request = self._request("5")
        transition_leave_encashment(request, "submit", self.manager)
        request.refresh_from_db()
        self.assertEqual(request.status, LeaveEncashment.Status.SUBMITTED)
        self.assertEqual(request.reference, f"ENC-{self.today.year}-{request.pk:08d}")
        self.assertEqual(request.rate_per_day, Decimal("100.00"))
        self.assertEqual(request.amount, Decimal("500.00"))
        self.assertEqual(
            list(request.bucket_details.order_by("bucket__expiry_date").values_list("days", flat=True)),
            [Decimal("4"), Decimal("1")],
        )
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.used_days, Decimal("0"))
        transition_leave_encashment(request, "approve", self.manager)
        self.allocation.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.allocation.used_days, Decimal("5"))
        self.assertEqual((first.consumed_days, second.consumed_days), (Decimal("4"), Decimal("1")))
        self.assertEqual(LeaveMovement.objects.filter(leave_encashment=request, movement_type="encashment").count(), 1)
        transition_leave_encashment(request, "cancel", self.manager)
        self.allocation.refresh_from_db()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.allocation.used_days, Decimal("0"))
        self.assertEqual((first.consumed_days, second.consumed_days), (Decimal("0"), Decimal("0")))
        self.assertEqual(
            LeaveEncashmentBucket.objects.filter(encashment=request, status="reversed").count(), 2
        )
        self.assertEqual(LeaveMovement.objects.filter(leave_encashment=request, movement_type="reversal").count(), 1)
        with self.assertRaises(ValidationError):
            transition_leave_encashment(request, "cancel", self.manager)

    def test_reserved_days_and_yearly_quota_cannot_be_reused(self):
        first = self._request("5")
        transition_leave_encashment(first, "submit", self.manager)
        with self.assertRaises(ValidationError):
            transition_leave_encashment(self._request("1"), "submit", self.manager)
        transition_leave_encashment(first, "reject", self.manager)
        next_request = self._request("5")
        transition_leave_encashment(next_request, "submit", self.manager)
        self.assertEqual(next_request.bucket_details.get().status, LeaveEncashmentBucket.Status.RESERVED)

    def test_expiry_preserves_submitted_encashment_reservation(self):
        bucket = LeaveBucket.objects.create(
            employee=self.employee, leave_type=self.leave_type, allocation=self.allocation,
            year=self.today.year, original_days=Decimal("10"),
            start_valid_date=self.today, expiry_date=self.today + timedelta(days=1),
        )
        request = self._request("2")
        transition_leave_encashment(request, "submit", self.manager)
        bucket.expiry_date = self.today - timedelta(days=1)
        bucket.save(update_fields=["expiry_date"])
        self.assertEqual(expire_leave_buckets(today=self.today), 1)
        bucket.refresh_from_db()
        self.assertEqual(bucket.forfeited_days, Decimal("8"))
        self.assertEqual(bucket.status, LeaveBucket.Status.EXPIRED)
        transition_leave_encashment(request, "approve", self.manager)
        bucket.refresh_from_db()
        self.assertEqual(bucket.consumed_days, Decimal("2"))
        self.assertEqual(bucket.status, LeaveBucket.Status.EXPIRED)
        transition_leave_encashment(request, "cancel", self.manager)
        bucket.refresh_from_db()
        self.assertEqual(bucket.consumed_days, Decimal("0"))
        self.assertEqual(bucket.forfeited_days, Decimal("10"))
        self.assertEqual(bucket.remaining_days, Decimal("0"))
        self.client.force_login(self.manager)
        response = self.client.get("/app/leave-allocations/")
        row = next(row for row in response.context["rows"] if row["object"].pk == self.allocation.pk)
        cells = {cell["name"]: cell["value"] for cell in row["cells"]}
        self.assertEqual(cells["available_days"], Decimal("0"))

    def test_only_hr_manager_can_approve_or_cancel(self):
        requester = get_user_model().objects.create_user(username="encashment-requester", password="test-password-123")
        self.employee.user = requester
        self.employee.save(update_fields=["user"])
        request = self._request("2")
        transition_leave_encashment(request, "submit", requester)
        with self.assertRaises(ValidationError):
            transition_leave_encashment(request, "approve", requester)
        with self.assertRaises(ValidationError):
            transition_leave_encashment(request, "cancel", requester)
        transition_leave_encashment(request, "approve", self.manager)

    def test_missing_reason_and_cross_company_type_do_not_reserve(self):
        request = self._request("2")
        request.reason = "  "
        request.save(update_fields=["reason"])
        with self.assertRaises(ValidationError):
            transition_leave_encashment(request, "submit", self.manager)
        self.assertFalse(LeaveEncashmentBucket.objects.filter(encashment=request).exists())

        other_company = Company.objects.create(code="LEC-OTHER", name="Other Company")
        other_employee = Employee.objects.create(
            company=other_company, employee_id="LEC-OTHER-1", name="Other Employee"
        )
        foreign_request = LeaveEncashment.objects.create(
            employee=other_employee, leave_type=self.leave_type, request_date=self.today,
            days=Decimal("1"), reason="Cash out annual leave",
        )
        with self.assertRaises(ValidationError):
            transition_leave_encashment(foreign_request, "submit", self.manager)
        self.assertFalse(LeaveEncashmentBucket.objects.filter(encashment=foreign_request).exists())

    def test_hr_manager_cannot_submit_another_company_encashment(self):
        other_company = Company.objects.create(code="LEC-FOREIGN", name="Foreign Company")
        other_employee = Employee.objects.create(
            company=other_company, employee_id="LEC-FOREIGN-1", name="Foreign Employee"
        )
        other_type = LeaveType.objects.create(
            company=other_company, code="AL", name="Annual", allow_leave_encashment=True,
            max_encashment_days=Decimal("5"), allow_partial_encashment=True,
            encashment_calculation_method="flat_amount", flat_cash_amount=Decimal("100"),
        )
        request = LeaveEncashment.objects.create(
            employee=other_employee, leave_type=other_type, request_date=self.today,
            days=Decimal("1"), reason="Foreign leave cash out",
        )
        manager = get_user_model().objects.create_user(username="encashment-hr-manager", password="test-password-123")
        manager.groups.add(Group.objects.get(name=HR_MANAGER))
        branch = self.company.branches.get(is_main=True)
        access = UserOrganizationAccess.objects.create(
            user=manager, default_company=self.company, default_branch=branch
        )
        access.companies.add(self.company)
        access.branches.add(branch)
        with self.assertRaisesMessage(ValidationError, "outside your HR access scope"):
            transition_leave_encashment(request, "submit", manager)
        self.assertFalse(LeaveEncashmentBucket.objects.filter(encashment=request).exists())

    def test_workspace_action_uses_ledger_service(self):
        self.client.force_login(self.manager)
        request = self._request("2")
        response = self.client.post(f"/app/leave-encashments/{request.pk}/action/submit/")
        self.assertEqual(response.status_code, 302)
        request.refresh_from_db()
        self.assertEqual(request.status, LeaveEncashment.Status.SUBMITTED)
        self.assertEqual(request.bucket_details.count(), 1)
        allocation_response = self.client.get("/app/leave-allocations/")
        row = next(row for row in allocation_response.context["rows"] if row["object"].pk == self.allocation.pk)
        cells = {cell["name"]: cell["value"] for cell in row["cells"]}
        self.assertEqual(cells["available_days"], Decimal("8"))
        response = self.client.get("/app/leave-encashment-buckets/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Encashment Bucket Reservations")

    def test_workspace_requires_reason_when_creating_encashment(self):
        self.client.force_login(self.manager)
        response = self.client.post("/app/leave-encashments/new/", {
            "employee": self.employee.pk,
            "leave_type": self.leave_type.pk,
            "request_date": self.today.isoformat(),
            "days": "2",
            "reason": "  ",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a reason for the encashment request")
        self.assertFalse(LeaveEncashment.objects.exists())

    def test_bucket_reservation_query_count_stays_constant(self):
        def count_for(bucket_count):
            employee = Employee.objects.create(
                company=self.company, employee_id=f"LEC-{bucket_count}", name=f"Employee {bucket_count}"
            )
            allocation = LeaveAllocation.objects.create(
                employee=employee, leave_type=self.leave_type, year=self.today.year,
                allocated_days=Decimal(bucket_count * 2), status="approved",
            )
            EmploymentContract.objects.create(
                employee=employee, number=f"LEC-CONTRACT-{bucket_count}",
                contract_type=EmploymentContract.ContractType.PERMANENT,
                start_date=self.today - timedelta(days=1), base_salary=Decimal("2200"),
                status=EmploymentContract.Status.ACTIVE,
            )
            LeaveBucket.objects.bulk_create([
                LeaveBucket(
                    employee=employee, leave_type=self.leave_type, allocation=allocation,
                    year=self.today.year, cycle_key=f"cycle-{index}", original_days=Decimal("2"),
                    start_valid_date=self.today, expiry_date=self.today + timedelta(days=index + 1),
                )
                for index in range(bucket_count)
            ])
            request = LeaveEncashment.objects.create(
                employee=employee, leave_type=self.leave_type, request_date=self.today,
                days=Decimal("2"), reason="Cash out annual leave",
            )
            with CaptureQueriesContext(connection) as queries:
                transition_leave_encashment(request, "submit", self.manager)
            return len(queries)

        small = count_for(2)
        self.assertEqual(small, count_for(12))
