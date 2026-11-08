from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Contact
from modules.education.models import School, Student
from modules.operations.models import Asset

from .models import (
    Account,
    AccountJournal,
    AssetDisposal,
    JournalEntry,
    JournalLine,
    RecurringJournal,
    SchoolAccountingSettings,
    SchoolFeeItem,
    SchoolFeeStructure,
    SchoolFeeType,
    StudentAccountingProfile,
    StudentFeeSchedule,
    StudentFeeScheduleLine,
)
from .school_accounting_extension import generate_due_school_invoices, provision_school_accounting
from .services import run_due_asset_disposals, run_due_recurring_journals


class SchoolAccountingProvisioningScaleTests(TestCase):
    def _company_resources(self, suffix):
        company = Company.objects.create(
            code=f"SC{suffix}",
            name=f"School Company {suffix}",
            currency="IDR",
        )
        accounts = {
            "1100": Account.objects.create(
                company=company,
                code="1100",
                name="School Receivable",
                account_type=Account.AccountType.ASSET,
            ),
            "4110": Account.objects.create(
                company=company,
                code="4110",
                name="School Fee Revenue",
                account_type=Account.AccountType.INCOME,
            ),
            "4120": Account.objects.create(
                company=company,
                code="4120",
                name="School Fine Revenue",
                account_type=Account.AccountType.INCOME,
            ),
        }
        journals = {
            "SCH": AccountJournal.objects.create(
                company=company,
                code="SCH",
                name="School Billing",
                journal_type=AccountJournal.JournalType.SALES,
                default_debit_account=accounts["1100"],
                default_credit_account=accounts["4110"],
            )
        }
        return company, accounts, journals

    def _measure_provisioning(self, school_count, suffix):
        company, accounts, journals = self._company_resources(suffix)
        School.objects.bulk_create(
            [
                School(company=company, code=f"S{index:04d}", name=f"School {index}")
                for index in range(school_count)
            ]
        )

        with CaptureQueriesContext(connection) as captured:
            result = provision_school_accounting(
                company=company,
                accounts=accounts,
                journals=journals,
            )
        self.assertEqual(result["settings_created"], school_count)
        self.assertEqual(
            SchoolAccountingSettings.objects.filter(school__company=company).count(),
            school_count,
        )
        return len(captured)

    def test_query_count_is_fixed_within_a_bounded_school_batch(self):
        small_queries = self._measure_provisioning(2, "SM")
        larger_queries = self._measure_provisioning(12, "LG")
        self.assertEqual(small_queries, larger_queries)
        print(
            "School accounting provisioning: 2 vs 12 schools; "
            f"SQL queries {small_queries} -> {larger_queries}; batch size 500."
        )

    @patch("modules.finance.school_accounting_extension.SCHOOL_INVOICE_BATCH_SIZE", 5)
    def test_due_invoice_run_defers_rows_beyond_its_bound(self):
        company, accounts, journals = self._company_resources("BI")
        school = School.objects.create(company=company, code="BILL", name="Billing School")
        SchoolAccountingSettings.objects.create(
            school=school,
            journal=journals["SCH"],
            receivable_account=accounts["1100"],
            default_revenue_account=accounts["4110"],
            late_return_revenue_account=accounts["4120"],
        )
        student = Student.objects.create(school=school, student_number="ST-1", name="Student One")
        contact = Contact.objects.create(company=company, code="GUARD-1", name="Guardian One")
        StudentAccountingProfile.objects.create(student=student, billing_contact=contact)
        fee_type = SchoolFeeType.objects.create(company=company, code="TUITION", name="Tuition")
        structure = SchoolFeeStructure.objects.create(school=school, code="BASE", name="Base Fees")
        fee_item = SchoolFeeItem.objects.create(
            structure=structure,
            code="MONTHLY",
            name="Monthly Fee",
            fee_type=fee_type,
            frequency=SchoolFeeItem.Frequency.MONTHLY,
            amount=Decimal("100"),
            revenue_account=accounts["4110"],
        )
        schedule = StudentFeeSchedule.objects.create(
            student=student,
            fee_structure=structure,
            status=StudentFeeSchedule.Status.ACTIVE,
        )
        today = timezone.localdate()
        StudentFeeScheduleLine.objects.bulk_create(
            [
                StudentFeeScheduleLine(
                    schedule=schedule,
                    fee_item=fee_item,
                    period_key=f"P-{index}",
                    invoice_date=today,
                    due_date=today,
                    amount_snapshot=Decimal("100"),
                    revenue_account_snapshot=accounts["4110"],
                )
                for index in range(6)
            ]
        )

        run = generate_due_school_invoices(schedule)

        self.assertEqual(run.requested_count, 6)
        self.assertEqual(run.created_count, 5)
        self.assertEqual(run.skipped_count, 1)
        self.assertEqual(
            StudentFeeScheduleLine.objects.filter(
                schedule=schedule,
                status=StudentFeeScheduleLine.Status.PENDING,
            ).count(),
            1,
        )

    def test_recurring_journal_run_leaves_excess_due_rows_for_next_beat(self):
        company, accounts, journals = self._company_resources("RJ")
        today = timezone.localdate()
        template = JournalEntry.objects.create(
            company=company,
            journal=journals["SCH"],
            number="TEMPLATE-RJ",
            entry_date=today,
            description="Recurring template",
        )
        JournalLine.objects.bulk_create(
            [
                JournalLine(entry=template, account=accounts["1100"], debit=Decimal("10")),
                JournalLine(entry=template, account=accounts["4110"], credit=Decimal("10")),
            ]
        )
        RecurringJournal.objects.bulk_create(
            [
                RecurringJournal(
                    company=company,
                    name=f"Recurring {index}",
                    template_entry=template,
                    next_run_date=today,
                    active=True,
                )
                for index in range(3)
            ]
        )

        entries = run_due_recurring_journals(run_date=today, batch_size=2)

        self.assertEqual(len(entries), 2)
        self.assertEqual(
            RecurringJournal.objects.filter(active=True, next_run_date__lte=today).count(),
            1,
        )

    def _measure_asset_disposal_selection(self, count, suffix):
        company = Company.objects.create(code=f"AD{suffix}", name=f"Asset Company {suffix}")
        assets = Asset.objects.bulk_create(
            [
                Asset(company=company, code=f"A{index:04d}", name=f"Asset {index}")
                for index in range(count)
            ]
        )
        AssetDisposal.objects.bulk_create(
            [
                AssetDisposal(
                    asset=asset,
                    number=f"D-{suffix}-{index}",
                    disposal_date=timezone.localdate(),
                    disposal_type=AssetDisposal.DisposalType.SCRAPPED,
                    status=AssetDisposal.Status.APPROVED,
                )
                for index, asset in enumerate(assets)
            ]
        )
        with patch("modules.finance.services.post_asset_disposal", side_effect=lambda record, actor=None: record):
            with CaptureQueriesContext(connection) as captured:
                result = run_due_asset_disposals(batch_size=100)
        self.assertEqual(result["selected"], count)
        AssetDisposal.objects.filter(asset__company=company).update(status=AssetDisposal.Status.POSTED)
        return len(captured)

    def test_asset_disposal_selection_query_count_does_not_grow(self):
        small_queries = self._measure_asset_disposal_selection(2, "SM")
        larger_queries = self._measure_asset_disposal_selection(12, "LG")
        self.assertEqual(small_queries, larger_queries)
        print(
            "Asset disposal selection: 2 vs 12 candidates; "
            f"SQL queries {small_queries} -> {larger_queries}; max batch 500."
        )
