from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Company, Employee

from .leave_opening_balances import finalize_leave_opening_balance
from .leave_requests import transition_leave_request
from .models import EmploymentContract, LeaveAllocation, LeaveBucket, LeaveMovement, LeaveOpeningBalance, LeaveRequest, LeaveType


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class LeaveOpeningBalanceTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_superuser(
            username="opening-manager", email="opening@example.test", password="test-password-123"
        )
        self.client.force_login(self.manager)
        self.company = Company.objects.create(code="OBA", name="Opening Company")
        self.other_company = Company.objects.create(code="OBB", name="Other Opening Company")
        self.employee = Employee.objects.create(company=self.company, employee_id="OB-1", name="Opening Employee")
        self.leave_type = LeaveType.objects.create(
            company=self.company, code="AL", name="Annual Leave", day_count_by=LeaveType.DayCountBy.CALENDAR_DAY
        )
        self.today = timezone.localdate()
        self.opening = LeaveOpeningBalance.objects.create(
            employee=self.employee,
            leave_type=self.leave_type,
            year=self.today.year,
            balance_days=Decimal("8"),
            opening_used=Decimal("2"),
            cutoff_date=self.today,
            expiry_date=date(self.today.year, 12, 31),
        )

    def test_finalize_posts_exact_opening_and_request_consumes_it(self):
        result = self.client.post(f"/app/leave-opening-balances/{self.opening.pk}/action/finalize/")
        self.assertEqual(result.status_code, 302)
        self.opening.refresh_from_db()
        self.assertEqual(self.opening.status, LeaveOpeningBalance.Status.FINALIZED)
        self.assertEqual(self.opening.finalized_by, self.manager)
        self.assertIsNotNone(self.opening.finalized_at)
        allocation = LeaveAllocation.objects.get(opening_balance=self.opening)
        self.assertEqual((allocation.allocated_days, allocation.used_days), (Decimal("10"), Decimal("2")))
        bucket = LeaveBucket.objects.get(allocation=allocation)
        self.assertEqual(bucket.source, LeaveBucket.Source.OPENING)
        self.assertEqual(bucket.remaining_days, Decimal("8"))
        movements = list(self.opening.movements.order_by("movement_type").values_list("movement_type", "days"))
        self.assertEqual(movements, [("opening", Decimal("10")), ("opening_deduction", Decimal("-2"))])

        EmploymentContract.objects.create(
            employee=self.employee,
            number="OB-CONTRACT",
            contract_type=EmploymentContract.ContractType.PERMANENT,
            start_date=self.today,
            base_salary=Decimal("5000000"),
            status=EmploymentContract.Status.ACTIVE,
        )
        request = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type_record=self.leave_type,
            start_date=self.today,
            end_date=self.today,
        )
        transition_leave_request(request, "submit", self.manager)
        bucket.refresh_from_db()
        allocation.refresh_from_db()
        self.assertEqual(bucket.remaining_days, Decimal("7"))
        self.assertEqual(allocation.used_days, Decimal("3"))

    def test_duplicate_or_repeat_finalization_does_not_post_twice(self):
        finalize_leave_opening_balance(self.opening, self.manager)
        with self.assertRaises(ValidationError):
            finalize_leave_opening_balance(self.opening, self.manager)
        self.assertEqual(LeaveAllocation.objects.filter(opening_balance=self.opening).count(), 1)
        self.assertEqual(self.opening.movements.count(), 2)

    def test_existing_year_allocation_blocks_opening_without_partial_write(self):
        LeaveAllocation.objects.create(
            employee=self.employee, leave_type=self.leave_type, year=self.today.year,
            allocated_days=Decimal("10"), status="approved",
        )
        with self.assertRaises(ValidationError):
            finalize_leave_opening_balance(self.opening, self.manager)
        self.opening.refresh_from_db()
        self.assertEqual(self.opening.status, LeaveOpeningBalance.Status.DRAFT)
        self.assertFalse(LeaveBucket.objects.exists())
        self.assertFalse(LeaveMovement.objects.exists())

    def test_invalid_company_and_amount_are_rejected(self):
        foreign_type = LeaveType.objects.create(company=self.other_company, code="AL", name="Foreign Leave")
        self.opening.leave_type = foreign_type
        with self.assertRaises(ValidationError):
            self.opening.full_clean()
        self.opening.leave_type = self.leave_type
        self.opening.opening_used = Decimal("-1")
        with self.assertRaises(ValidationError):
            self.opening.full_clean()

    def test_forged_status_and_finalized_mutation_are_blocked(self):
        payload = {
            "employee": self.employee.pk,
            "leave_type": self.leave_type.pk,
            "year": self.today.year,
            "balance_days": "8",
            "opening_used": "2",
            "cutoff_date": self.today.isoformat(),
            "expiry_date": date(self.today.year, 12, 31).isoformat(),
            "notes": "Original",
            "status": "finalized",
        }
        self.client.post(f"/app/leave-opening-balances/{self.opening.pk}/edit/", payload)
        self.opening.refresh_from_db()
        self.assertEqual(self.opening.status, LeaveOpeningBalance.Status.DRAFT)
        self.assertFalse(LeaveAllocation.objects.exists())

        finalize_leave_opening_balance(self.opening, self.manager)
        payload["balance_days"] = "99"
        self.client.post(f"/app/leave-opening-balances/{self.opening.pk}/edit/", payload)
        self.client.post(f"/app/leave-opening-balances/{self.opening.pk}/delete/")
        self.opening.refresh_from_db()
        self.assertEqual(self.opening.balance_days, Decimal("8"))
        self.assertEqual(self.opening.status, LeaveOpeningBalance.Status.FINALIZED)

    def test_non_manager_cannot_finalize(self):
        employee_user = get_user_model().objects.create_user(username="opening-user", password="test-password-123")
        with self.assertRaises(ValidationError):
            finalize_leave_opening_balance(self.opening, employee_user)
        self.assertFalse(LeaveAllocation.objects.exists())
