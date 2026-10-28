from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Attendance, Company, Employee
from modules.organization.models import UserOrganizationAccess
from modules.finance.models import (
    Account, AccountJournal, AccountingIntegrationEvent, PayrollAccountingSettings,
)

from .access import PAYROLL_ADMIN
from .models import (
    ApprovalStatus,
    CompensationBenefit,
    EmploymentContract,
    LeaveRequest,
    LeaveEncashment,
    LeaveType,
    OvertimeRequest,
    PayrollPeriod,
    PayrollRun,
    PayrollRunChecklistItem,
    PayrollRunPreviewLine,
    PayrollAccountingEvent,
    PayrollDisbursement,
    PayrollDisbursementLine,
    Payslip,
)
from .payroll_payments import (
    mark_payroll_disbursement_failed,
    prepare_payroll_disbursement,
    prepare_payroll_disbursement_batch,
    post_payroll_disbursement,
)
from .payroll_runs import (
    check_payroll_run,
    process_payroll_generation_batch,
    process_payroll_preview_batch,
    post_generated_payroll_accrual,
    start_payroll_generation,
    start_payroll_preview,
)


class PayrollRunChecklistTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_superuser(
            username="payroll-check-manager", email="payroll-check@example.test", password="test-password-123"
        )
        self.client.force_login(self.manager)
        self.company = Company.objects.create(code="PCA", name="A Payroll Check Company")
        self.other_company = Company.objects.create(code="PCB", name="Z Other Payroll Company")
        self.today = timezone.localdate()
        self.period = PayrollPeriod.objects.create(
            company=self.company,
            name="Payroll Readiness Period",
            date_start=self.today,
            date_end=self.today + timedelta(days=30),
            cutoff_date=self.today + timedelta(days=30),
        )
        self.run = PayrollRun.objects.create(company=self.company, period=self.period, number="RUN-CHECK-001")

    def _employee(self, number, *, company=None, with_snapshot=True):
        company = company or self.company
        employee = Employee.objects.create(
            company=company, employee_id=f"PC-{number}", name=f"Payroll Employee {number}", join_date=self.today
        )
        contract = EmploymentContract.objects.create(
            employee=employee,
            number=f"PC-CONTRACT-{number}",
            contract_type=EmploymentContract.ContractType.PERMANENT,
            start_date=self.today,
            base_salary=Decimal("5000000"),
            status=EmploymentContract.Status.ACTIVE,
        )
        if with_snapshot:
            CompensationBenefit.objects.create(
                employee=employee,
                contract=contract,
                effective_date=self.today,
                basic_salary=contract.base_salary,
                status=ApprovalStatus.APPROVED,
            )
        return employee

    def _post_run(self, employee_count=1):
        for number in range(employee_count):
            self._employee(100 + number)
        salary_account = Account.objects.create(
            company=self.company, code="5100", name="Payroll Expense", account_type=Account.AccountType.EXPENSE
        )
        payable_account = Account.objects.create(
            company=self.company, code="2110", name="Payroll Liability", account_type=Account.AccountType.LIABILITY
        )
        bank_account = Account.objects.create(
            company=self.company, code="1110", name="Payroll Bank", account_type=Account.AccountType.ASSET
        )
        journal = AccountJournal.objects.create(
            company=self.company, code="PBA", name="Payroll Bank Journal",
            default_debit_account=salary_account, default_credit_account=payable_account,
        )
        PayrollAccountingSettings.objects.create(
            company=self.company, journal=journal, salary_expense_account=salary_account,
            payroll_payable_account=payable_account, bank_account=bank_account,
        )
        check_payroll_run(self.run, self.manager)
        check_payroll_run(self.run, self.manager, mark_ready=True)
        start_payroll_preview(self.run, self.manager, schedule=False)
        process_payroll_preview_batch(self.run.pk, schedule_next=False)
        start_payroll_generation(self.run, self.manager, schedule=False)
        process_payroll_generation_batch(self.run.pk, schedule_next=False)
        post_generated_payroll_accrual(self.run, self.manager)
        self.run.refresh_from_db()
        return payable_account, bank_account

    def test_checklist_scopes_company_and_promotes_only_when_clear(self):
        self._employee(1)
        self._employee(2, company=self.other_company)
        response = self.client.post(f"/app/payroll-runs/{self.run.pk}/action/calculate/")
        self.assertEqual(response.status_code, 302)
        self.run.refresh_from_db()
        self.assertEqual(self.run.employee_count, 1)
        self.assertEqual(self.run.checklist_blocking_count, 0)
        self.assertEqual(self.run.status, PayrollRun.Status.CALCULATING)
        self.assertEqual(self.run.checklist_items.count(), 14)
        self.assertFalse(self.run.checklist_items.filter(state=PayrollRunChecklistItem.State.FAILED).exists())

        self.client.post(f"/app/payroll-runs/{self.run.pk}/action/mark-ready/")
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, PayrollRun.Status.READY)

    def test_missing_snapshot_pending_leave_and_duplicate_slip_block_readiness(self):
        employee = self._employee(3, with_snapshot=False)
        LeaveRequest.objects.create(
            employee=employee,
            start_date=self.today,
            end_date=self.today,
            status=LeaveRequest.Status.SUBMITTED,
        )
        Payslip.objects.create(
            employee=employee,
            number="PC-PAYSLIP-3",
            period_start=self.period.date_start,
            period_end=self.period.date_end,
        )
        check_payroll_run(self.run, self.manager)
        self.run.refresh_from_db()
        self.assertEqual(self.run.checklist_blocking_count, 3)
        self.assertEqual(
            set(self.run.checklist_items.filter(state="failed").values_list("code", flat=True)),
            {"compensation", "leave", "duplicate_payslip"},
        )
        check_payroll_run(self.run, self.manager, mark_ready=True)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, PayrollRun.Status.CALCULATING)
        self.assertEqual(self.run.checklist_items.count(), 14)

    def test_unvalued_pay_components_block_generation(self):
        employee = self._employee(40)
        employee.join_date = self.today + timedelta(days=1)
        employee.save(update_fields=["join_date", "updated_at"])
        OvertimeRequest.objects.create(
            employee=employee, overtime_date=self.today + timedelta(days=1),
            hours=Decimal("2"), status=LeaveRequest.Status.APPROVED,
        )
        Attendance.objects.create(
            employee=employee, attendance_date=self.today + timedelta(days=1),
            status=Attendance.Status.ABSENT,
        )
        check_payroll_run(self.run, self.manager)
        self.assertEqual(
            set(self.run.checklist_items.filter(state="failed").values_list("code", flat=True)),
            {"partial_employment", "approved_overtime", "absence"},
        )
        with self.assertRaises(ValidationError):
            start_payroll_preview(self.run, self.manager, schedule=False)

    def test_company_mismatch_and_unprivileged_actor_fail_closed(self):
        self._employee(4)
        ordinary = get_user_model().objects.create_user(username="payroll-check-user", password="test-password-123")
        with self.assertRaises(ValidationError):
            check_payroll_run(self.run, ordinary)
        self.period.company = self.other_company
        self.period.save(update_fields=["company", "updated_at"])
        with self.assertRaises(ValidationError):
            check_payroll_run(self.run, self.manager)
        self.assertFalse(self.run.checklist_items.exists())

    def test_payroll_administrator_cannot_check_another_company(self):
        self._employee(50)
        administrator = get_user_model().objects.create_user(
            username="scoped-payroll-admin", password="test-password-123"
        )
        administrator.groups.add(Group.objects.get_or_create(name=PAYROLL_ADMIN)[0])
        branch = self.company.branches.get(is_main=True)
        access = UserOrganizationAccess.objects.create(
            user=administrator, default_company=self.company, default_branch=branch
        )
        access.companies.add(self.company)
        access.branches.add(branch)
        check_payroll_run(self.run, administrator)
        foreign_period = PayrollPeriod.objects.create(
            company=self.other_company, name="Foreign Payroll Period",
            date_start=self.today, date_end=self.period.date_end, cutoff_date=self.period.cutoff_date,
        )
        foreign_run = PayrollRun.objects.create(
            company=self.other_company, period=foreign_period, number="RUN-FOREIGN-001"
        )
        with self.assertRaises(ValidationError):
            check_payroll_run(foreign_run, administrator)
        self.assertFalse(foreign_run.checklist_items.exists())

    def test_payroll_audit_rows_cannot_be_created_through_workspace(self):
        for entity in ("payroll-run-checklist", "payroll-run-preview", "payroll-accounting-events"):
            response = self.client.post(f"/app/{entity}/new/", {
                "run": self.run.pk,
                "payroll_run": self.run.pk,
                "employee": 999,
                "code": "FORGED",
                "reference": "FORGED",
                "state": "passed",
                "status": "posted",
            })
            self.assertEqual(response.status_code, 302)
        self.assertFalse(PayrollRunChecklistItem.objects.exists())
        self.assertFalse(PayrollRunPreviewLine.objects.exists())
        self.assertFalse(PayrollAccountingEvent.objects.exists())

    def test_query_count_does_not_grow_with_employee_cohort(self):
        for number in range(2):
            self._employee(number)
        with CaptureQueriesContext(connection) as small:
            check_payroll_run(self.run, self.manager)
        for number in range(2, 12):
            self._employee(number)
        with CaptureQueriesContext(connection) as large:
            check_payroll_run(self.run, self.manager)
        self.assertEqual(len(small), len(large))
        self.run.refresh_from_db()
        self.assertEqual(self.run.employee_count, 12)

    def test_preview_and_generation_are_batched_and_link_encashment(self):
        first = self._employee(20)
        self._employee(21)
        self._employee(22)
        leave_type = LeaveType.objects.create(company=self.company, code="AL", name="Annual Leave")
        encashment = LeaveEncashment.objects.create(
            employee=first,
            leave_type=leave_type,
            request_date=self.today,
            days=Decimal("1"),
            amount=Decimal("75000"),
            status=LeaveEncashment.Status.WAITING_FOR_PAYROLL,
        )
        check_payroll_run(self.run, self.manager)
        check_payroll_run(self.run, self.manager, mark_ready=True)
        start_payroll_preview(self.run, self.manager, schedule=False)
        self.assertTrue(process_payroll_preview_batch(self.run.pk, batch_size=2, schedule_next=False))
        self.assertFalse(process_payroll_preview_batch(self.run.pk, batch_size=2, schedule_next=False))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, PayrollRun.Status.PREVIEWED)
        self.assertEqual(self.run.preview_count, 3)
        self.assertEqual(self.run.preview_blocking_count, 0)
        self.assertEqual(self.run.net_amount, Decimal("15075000"))
        first_line = PayrollRunPreviewLine.objects.get(run=self.run, employee=first)
        self.assertEqual(first_line.encashment_amount, Decimal("75000"))

        start_payroll_generation(self.run, self.manager, schedule=False)
        self.assertTrue(process_payroll_generation_batch(self.run.pk, batch_size=2, schedule_next=False))
        self.assertFalse(process_payroll_generation_batch(self.run.pk, batch_size=2, schedule_next=False))
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, PayrollRun.Status.GENERATED)
        self.assertEqual(self.run.generated_count, 3)
        self.assertEqual(Payslip.objects.filter(payroll_run=self.run).count(), 3)
        payslip = Payslip.objects.get(payroll_run=self.run, employee=first)
        self.assertEqual((payslip.allowances, payslip.net_salary), (Decimal("75000"), Decimal("5075000")))
        encashment.refresh_from_db()
        self.assertEqual(encashment.status, LeaveEncashment.Status.PAYROLL_GENERATED)
        self.assertEqual(encashment.payslip_id, payslip.pk)
        self.assertFalse(process_payroll_generation_batch(self.run.pk, batch_size=2, schedule_next=False))
        self.assertEqual(Payslip.objects.filter(payroll_run=self.run).count(), 3)
        self.client.post(f"/app/payslips/{payslip.pk}/edit/", {"basic_salary": "999"})
        payslip.refresh_from_db()
        self.assertEqual(payslip.basic_salary, Decimal("5000000"))

    def test_generation_stops_when_encashment_changes_after_preview(self):
        employee = self._employee(30)
        leave_type = LeaveType.objects.create(company=self.company, code="AL", name="Annual Leave")
        encashment = LeaveEncashment.objects.create(
            employee=employee, leave_type=leave_type, request_date=self.today,
            days=Decimal("1"), amount=Decimal("100"),
            status=LeaveEncashment.Status.WAITING_FOR_PAYROLL,
        )
        check_payroll_run(self.run, self.manager)
        check_payroll_run(self.run, self.manager, mark_ready=True)
        start_payroll_preview(self.run, self.manager, schedule=False)
        process_payroll_preview_batch(self.run.pk, schedule_next=False)
        start_payroll_generation(self.run, self.manager, schedule=False)
        encashment.amount = Decimal("200")
        encashment.save(update_fields=["amount", "updated_at"])
        with self.assertRaises(ValidationError):
            process_payroll_generation_batch(self.run.pk, schedule_next=False)
        self.assertFalse(Payslip.objects.filter(payroll_run=self.run).exists())

    def test_generation_stops_when_compensation_changes_after_preview(self):
        employee = self._employee(31)
        check_payroll_run(self.run, self.manager)
        check_payroll_run(self.run, self.manager, mark_ready=True)
        start_payroll_preview(self.run, self.manager, schedule=False)
        process_payroll_preview_batch(self.run.pk, schedule_next=False)
        start_payroll_generation(self.run, self.manager, schedule=False)
        snapshot = CompensationBenefit.objects.get(employee=employee)
        snapshot.basic_salary += Decimal("100")
        snapshot.save(update_fields=["basic_salary", "updated_at"])
        with self.assertRaises(ValidationError):
            process_payroll_generation_batch(self.run.pk, schedule_next=False)
        self.assertFalse(Payslip.objects.filter(payroll_run=self.run).exists())

    def test_posting_uses_balanced_finance_accrual_once(self):
        employee = self._employee(60)
        salary_account = Account.objects.create(
            company=self.company, code="5000", name="Salary Expense", account_type=Account.AccountType.EXPENSE
        )
        payable_account = Account.objects.create(
            company=self.company, code="2100", name="Payroll Payable", account_type=Account.AccountType.LIABILITY
        )
        bank_account = Account.objects.create(
            company=self.company, code="1100", name="Payroll Bank", account_type=Account.AccountType.ASSET
        )
        journal = AccountJournal.objects.create(
            company=self.company, code="PAY", name="Payroll Journal",
            default_debit_account=salary_account, default_credit_account=payable_account,
        )
        PayrollAccountingSettings.objects.create(
            company=self.company, journal=journal, salary_expense_account=salary_account,
            payroll_payable_account=payable_account, bank_account=bank_account,
        )
        check_payroll_run(self.run, self.manager)
        check_payroll_run(self.run, self.manager, mark_ready=True)
        start_payroll_preview(self.run, self.manager, schedule=False)
        process_payroll_preview_batch(self.run.pk, schedule_next=False)
        start_payroll_generation(self.run, self.manager, schedule=False)
        process_payroll_generation_batch(self.run.pk, schedule_next=False)
        posted = post_generated_payroll_accrual(self.run, self.manager)
        self.assertEqual(posted.status, PayrollRun.Status.POSTED)
        event = PayrollAccountingEvent.objects.get(payroll_run=self.run, event_type="accrual")
        self.assertEqual(event.status, "posted")
        bridge = AccountingIntegrationEvent.objects.get(
            company=self.company, source_model=event._meta.label_lower, source_id=event.pk,
        )
        entry = bridge.journal_entry
        self.assertEqual((entry.total_debit, entry.total_credit), (Decimal("5000000"), Decimal("5000000")))
        self.assertEqual(
            set(entry.lines.values_list("account_id", "debit", "credit")),
            {
                (salary_account.pk, Decimal("5000000"), Decimal("0")),
                (payable_account.pk, Decimal("0"), Decimal("5000000")),
            },
        )
        self.assertEqual(Payslip.objects.get(payroll_run=self.run, employee=employee).status, Payslip.Status.CONFIRMED)
        post_generated_payroll_accrual(self.run, self.manager)
        self.assertEqual(PayrollAccountingEvent.objects.filter(payroll_run=self.run).count(), 1)

    def test_deductions_without_liability_mapping_cannot_post(self):
        employee = self._employee(61)
        snapshot = CompensationBenefit.objects.get(employee=employee)
        snapshot.deduction_amount = Decimal("100")
        snapshot.save(update_fields=["deduction_amount", "updated_at"])
        check_payroll_run(self.run, self.manager)
        check_payroll_run(self.run, self.manager, mark_ready=True)
        start_payroll_preview(self.run, self.manager, schedule=False)
        process_payroll_preview_batch(self.run.pk, schedule_next=False)
        start_payroll_generation(self.run, self.manager, schedule=False)
        process_payroll_generation_batch(self.run.pk, schedule_next=False)
        with self.assertRaises(ValidationError):
            post_generated_payroll_accrual(self.run, self.manager)
        self.assertFalse(PayrollAccountingEvent.objects.filter(payroll_run=self.run).exists())

    def test_preview_query_count_is_fixed_for_small_and_larger_cohort(self):
        for number in range(2):
            self._employee(number)
        check_payroll_run(self.run, self.manager)
        check_payroll_run(self.run, self.manager, mark_ready=True)
        start_payroll_preview(self.run, self.manager, schedule=False)
        with CaptureQueriesContext(connection) as small:
            process_payroll_preview_batch(self.run.pk, batch_size=200, schedule_next=False)
        for number in range(2, 12):
            self._employee(number)
        larger = PayrollRun.objects.create(company=self.company, period=self.period, number="RUN-CHECK-012")
        check_payroll_run(larger, self.manager)
        check_payroll_run(larger, self.manager, mark_ready=True)
        start_payroll_preview(larger, self.manager, schedule=False)
        with CaptureQueriesContext(connection) as large:
            process_payroll_preview_batch(larger.pk, batch_size=200, schedule_next=False)
        self.assertEqual(len(small), len(large))
        larger.refresh_from_db()
        self.assertEqual(larger.preview_count, 12)

    def test_generation_query_count_is_fixed_for_two_and_twelve_payslips(self):
        for number in range(14):
            self._employee(number)
        check_payroll_run(self.run, self.manager)
        check_payroll_run(self.run, self.manager, mark_ready=True)
        start_payroll_preview(self.run, self.manager, schedule=False)
        process_payroll_preview_batch(self.run.pk, schedule_next=False)
        start_payroll_generation(self.run, self.manager, schedule=False)
        with CaptureQueriesContext(connection) as small:
            process_payroll_generation_batch(self.run.pk, batch_size=2, schedule_next=False)
        with CaptureQueriesContext(connection) as large:
            process_payroll_generation_batch(self.run.pk, batch_size=200, schedule_next=False)
        self.assertEqual(len(small), len(large))
        self.run.refresh_from_db()
        self.assertEqual(self.run.generated_count, 14)

    def test_payment_preparation_is_batched_and_reconciles_every_payslip(self):
        self._post_run(employee_count=14)
        disbursement = prepare_payroll_disbursement(self.run, self.manager, schedule=False)
        self.assertEqual(disbursement.status, PayrollDisbursement.Status.PREPARING)
        with CaptureQueriesContext(connection) as small:
            self.assertTrue(prepare_payroll_disbursement_batch(disbursement.pk, batch_size=2, schedule_next=False))
        with CaptureQueriesContext(connection) as large:
            self.assertFalse(prepare_payroll_disbursement_batch(disbursement.pk, batch_size=200, schedule_next=False))
        self.assertEqual(len(small), len(large))
        disbursement.refresh_from_db()
        self.assertEqual(disbursement.status, PayrollDisbursement.Status.GENERATED)
        self.assertEqual(disbursement.prepared_count, 14)
        self.assertEqual(disbursement.lines.count(), 14)
        self.assertEqual(
            sum(disbursement.lines.values_list("amount", flat=True), Decimal("0")),
            self.run.net_amount,
        )
        self.assertEqual(
            prepare_payroll_disbursement(self.run, self.manager, schedule=False).pk,
            disbursement.pk,
        )

    def test_confirm_payment_posts_balanced_bank_journal_once(self):
        payable_account, bank_account = self._post_run(employee_count=2)
        response = self.client.post(f"/app/payroll-runs/{self.run.pk}/action/prepare-payment/")
        self.assertEqual(response.status_code, 302)
        disbursement = PayrollDisbursement.objects.get(payroll_run=self.run)
        prepare_payroll_disbursement_batch(disbursement.pk, schedule_next=False)
        response = self.client.post(
            f"/app/payroll-disbursements/{disbursement.pk}/action/confirm-payment/",
            {"payment_reference": "BANK-BATCH-001"},
        )
        self.assertEqual(response.status_code, 302)
        disbursement.refresh_from_db()
        self.assertEqual(disbursement.status, PayrollDisbursement.Status.COMPLETED)
        self.assertEqual(disbursement.payment_reference, "BANK-BATCH-001")
        self.assertEqual(disbursement.lines.filter(status=PayrollDisbursementLine.Status.PAID).count(), 2)
        self.assertEqual(Payslip.objects.filter(payroll_run=self.run, status=Payslip.Status.PAID).count(), 2)
        event = disbursement.payment_event
        self.assertEqual(event.status, "posted")
        bridge = AccountingIntegrationEvent.objects.get(
            company=self.company, source_model=event._meta.label_lower, source_id=event.pk,
        )
        entry = bridge.journal_entry
        self.assertEqual((entry.total_debit, entry.total_credit), (self.run.net_amount, self.run.net_amount))
        self.assertEqual(
            set(entry.lines.values_list("account_id", "debit", "credit")),
            {
                (payable_account.pk, self.run.net_amount, Decimal("0")),
                (bank_account.pk, Decimal("0"), self.run.net_amount),
            },
        )
        post_payroll_disbursement(disbursement, self.manager, payment_reference="BANK-BATCH-001")
        self.assertEqual(PayrollAccountingEvent.objects.filter(payroll_run=self.run, event_type="payment").count(), 1)

    def test_payment_blocks_changed_payslip_and_unposted_run(self):
        self._employee(120)
        with self.assertRaises(ValidationError):
            prepare_payroll_disbursement(self.run, self.manager, schedule=False)
        self._post_run(employee_count=1)
        disbursement = prepare_payroll_disbursement(self.run, self.manager, schedule=False)
        prepare_payroll_disbursement_batch(disbursement.pk, schedule_next=False)
        slip = Payslip.objects.get(payroll_run=self.run, employee__employee_id="PC-100")
        slip.basic_salary += Decimal("1")
        slip.save(update_fields=["basic_salary", "net_salary", "updated_at"])
        with self.assertRaises(ValidationError):
            post_payroll_disbursement(disbursement, self.manager, payment_reference="BANK-CHANGED")
        self.assertFalse(PayrollAccountingEvent.objects.filter(payroll_run=self.run, event_type="payment").exists())

    def test_payment_blocks_payable_account_change_after_accrual(self):
        self._post_run(employee_count=1)
        disbursement = prepare_payroll_disbursement(self.run, self.manager, schedule=False)
        prepare_payroll_disbursement_batch(disbursement.pk, schedule_next=False)
        other_payable = Account.objects.create(
            company=self.company, code="2120", name="Other Payroll Payable",
            account_type=Account.AccountType.LIABILITY,
        )
        settings = PayrollAccountingSettings.objects.get(company=self.company)
        settings.payroll_payable_account = other_payable
        settings.save(update_fields=["payroll_payable_account", "updated_at"])
        with self.assertRaises(ValidationError):
            post_payroll_disbursement(disbursement, self.manager, payment_reference="BANK-CHANGED")
        self.assertFalse(PayrollAccountingEvent.objects.filter(payroll_run=self.run, event_type="payment").exists())

    def test_payment_preparation_can_resume_after_a_failed_batch(self):
        self._post_run(employee_count=2)
        disbursement = prepare_payroll_disbursement(self.run, self.manager, schedule=False)
        self.assertTrue(prepare_payroll_disbursement_batch(disbursement.pk, batch_size=1, schedule_next=False))
        remaining = Payslip.objects.filter(payroll_run=self.run).order_by("pk").last()
        Payslip.objects.filter(pk=remaining.pk).update(status=Payslip.Status.CANCELLED)
        with self.assertRaises(ValidationError):
            prepare_payroll_disbursement_batch(disbursement.pk, batch_size=1, schedule_next=False)
        mark_payroll_disbursement_failed(disbursement.pk, "Payslip changed")
        disbursement.refresh_from_db()
        self.assertEqual(disbursement.status, PayrollDisbursement.Status.FAILED)
        self.assertEqual(disbursement.prepared_count, 1)
        Payslip.objects.filter(pk=remaining.pk).update(status=Payslip.Status.CONFIRMED)
        self.assertEqual(
            prepare_payroll_disbursement(self.run, self.manager, schedule=False).pk,
            disbursement.pk,
        )
        self.assertFalse(prepare_payroll_disbursement_batch(disbursement.pk, schedule_next=False))
        disbursement.refresh_from_db()
        self.assertEqual(disbursement.status, PayrollDisbursement.Status.GENERATED)
        self.assertEqual(disbursement.lines.count(), 2)

    def test_preparing_payment_can_be_queued_again_without_new_header(self):
        self._post_run(employee_count=1)
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            first = prepare_payroll_disbursement(self.run, self.manager)
            second = prepare_payroll_disbursement(self.run, self.manager)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(len(callbacks), 2)
        self.assertEqual(PayrollDisbursement.objects.filter(payroll_run=self.run).count(), 1)

    def test_payroll_payment_audit_rows_cannot_be_created_in_workspace(self):
        self._post_run(employee_count=1)
        for entity in ("payroll-disbursements", "payroll-disbursement-lines"):
            response = self.client.post(f"/app/{entity}/new/", {
                "payroll_run": self.run.pk,
                "payslip": Payslip.objects.get(payroll_run=self.run).pk,
                "amount": "5000000",
                "status": "paid",
            })
            self.assertEqual(response.status_code, 302)
        self.assertFalse(PayrollDisbursement.objects.exists())
        self.assertFalse(PayrollDisbursementLine.objects.exists())
