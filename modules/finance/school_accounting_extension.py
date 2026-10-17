"""School fee schedules, installments, billing runs, and library fines."""

from calendar import monthrange
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.models import Invoice, InvoiceLine
from modules.education.models import BookLoan, LibraryBook, School

from .invoice_posting import (
    InvoicePostingLine,
    InvoicePostingOverride,
    register_invoice_cancellation_observer,
    register_invoice_posting_contributor,
    register_invoice_posting_failure_observer,
)
from .models import (
    Account,
    AccountJournal,
    SchoolAccountingSettings,
    SchoolBillingRun,
    SchoolBillingRunLine,
    SchoolFeeItem,
    SchoolInstallmentLine,
    SchoolInstallmentPlan,
    SchoolInstallmentPolicy,
    SchoolInstallmentRequest,
    SchoolInstallmentRescheduleLog,
    SchoolInvoiceAccounting,
    SchoolInvoiceLineAccounting,
    SchoolLibraryFineAccounting,
    StudentFeeSchedule,
    StudentFeeScheduleLine,
)
from .provisioning import (
    AccountDefinition,
    JournalDefinition,
    register_accounting_blueprint,
    register_company_accounting_provisioner,
)


def _add_months(value, months):
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, monthrange(year, month)[1]))


def _student_billing_contact(student):
    try:
        return student.accounting_profile.billing_contact
    except ObjectDoesNotExist as exc:
        raise ValidationError(f"Configure a billing contact for student {student}.") from exc


def _school_invoice_posting(*, invoice, rows, partner_account, total, journal, actor=None):
    school_invoice = (
        SchoolInvoiceAccounting.objects.select_for_update(of=("self",))
        .select_related("school__accounting_settings", "student")
        .filter(invoice_id=invoice.pk)
        .first()
    )
    if school_invoice is None:
        return None
    try:
        settings = school_invoice.school.accounting_settings
    except ObjectDoesNotExist as exc:
        raise ValidationError("Configure school accounting before posting its invoices.") from exc
    metadata = {
        item.invoice_line_id: item
        for item in SchoolInvoiceLineAccounting.objects.filter(
            school_invoice=school_invoice, invoice_line_id__in=[row.pk for row in rows]
        ).select_related("revenue_account_snapshot")
    }
    if len(metadata) != len(rows):
        raise ValidationError("Every school invoice line must carry immutable accounting provenance.")
    lines = [
        InvoicePostingLine(
            account=settings.receivable_account,
            debit=total,
            partner=invoice.partner,
            description=invoice.number,
            due_date=invoice.due_date,
        )
    ]
    lines.extend(
        InvoicePostingLine(
            account=metadata[row.pk].revenue_account_snapshot,
            credit=row.subtotal,
            partner=invoice.partner,
            description=row.description,
        )
        for row in rows
    )
    return InvoicePostingOverride(
        journal=settings.journal,
        lines=tuple(lines),
        description=f"School {school_invoice.get_source_kind_display()}: {school_invoice.student}",
    )


def _school_invoice_failure(*, invoice, error):
    SchoolLibraryFineAccounting.objects.filter(invoice_id=invoice.pk).update(
        status=SchoolLibraryFineAccounting.Status.FAILED,
        error_message=str(error)[:2000],
    )


def _school_invoice_cancelled(*, invoice, actor=None):
    StudentFeeScheduleLine.objects.filter(invoice_id=invoice.pk).update(
        invoice=None,
        status=StudentFeeScheduleLine.Status.PENDING,
        failure_reason="",
    )
    SchoolLibraryFineAccounting.objects.filter(invoice_id=invoice.pk).update(
        invoice=None,
        status=SchoolLibraryFineAccounting.Status.READY,
        error_message="",
    )


@transaction.atomic
def generate_student_fee_schedule_lines(schedule, actor=None):
    locked = (
        StudentFeeSchedule.objects.select_for_update()
        .select_related("student__school__accounting_settings", "fee_structure")
        .get(pk=schedule.pk)
    )
    if locked.status not in {StudentFeeSchedule.Status.DRAFT, StudentFeeSchedule.Status.PAUSED}:
        raise ValidationError("Only draft or paused fee schedules can generate lines.")
    if not locked.effective_end_date:
        recurring = locked.fee_structure.items.filter(
            active=True,
            frequency__in=[SchoolFeeItem.Frequency.MONTHLY, SchoolFeeItem.Frequency.TERM, SchoolFeeItem.Frequency.YEARLY],
        ).exists()
        if recurring:
            raise ValidationError("Recurring fee schedules require an effective end date.")
    try:
        settings = locked.student.school.accounting_settings
    except ObjectDoesNotExist as exc:
        raise ValidationError("Configure school accounting before generating fee schedules.") from exc
    items = list(locked.fee_structure.items.select_related("revenue_account").filter(active=True))
    if not items:
        raise ValidationError("Add at least one active fee item to the fee structure.")
    generated = []
    for item in items:
        account = item.revenue_account or settings.default_revenue_account
        step_months = {
            SchoolFeeItem.Frequency.ONE_TIME: 0,
            SchoolFeeItem.Frequency.MONTHLY: 1,
            SchoolFeeItem.Frequency.TERM: 4,
            SchoolFeeItem.Frequency.YEARLY: 12,
        }[item.frequency]
        period_date = locked.effective_start_date
        while True:
            generated.append(StudentFeeScheduleLine(
                schedule=locked,
                fee_item=item,
                period_key=f"{item.pk}:{period_date.isoformat()}",
                invoice_date=period_date,
                due_date=period_date,
                amount_snapshot=item.amount,
                revenue_account_snapshot=account,
            ))
            if step_months == 0 or not locked.effective_end_date:
                break
            period_date = _add_months(period_date, step_months)
            if period_date > locked.effective_end_date:
                break
    existing_keys = set(
        StudentFeeScheduleLine.objects.filter(schedule=locked).values_list("period_key", flat=True)
    )
    StudentFeeScheduleLine.objects.bulk_create(
        [line for line in generated if line.period_key not in existing_keys]
    )
    locked.status = StudentFeeSchedule.Status.ACTIVE
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def cancel_student_fee_schedule(schedule, actor=None):
    locked = StudentFeeSchedule.objects.select_for_update().get(pk=schedule.pk)
    if locked.lines.filter(status=StudentFeeScheduleLine.Status.INVOICED).exists():
        raise ValidationError("A fee schedule with generated invoices cannot be cancelled.")
    locked.lines.filter(
        status__in=[StudentFeeScheduleLine.Status.PENDING, StudentFeeScheduleLine.Status.FAILED]
    ).update(status=StudentFeeScheduleLine.Status.CANCELLED)
    locked.status = StudentFeeSchedule.Status.CANCELLED
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def generate_due_school_invoices(schedule, actor=None, *, run_type=SchoolBillingRun.RunType.MANUAL, run_date=None):
    run_date = run_date or timezone.localdate()
    locked = StudentFeeSchedule.objects.select_for_update().select_related("student__school").get(pk=schedule.pk)
    if locked.status != StudentFeeSchedule.Status.ACTIVE:
        raise ValidationError("Only active fee schedules can generate invoices.")
    lines = list(
        StudentFeeScheduleLine.objects.select_for_update()
        .select_related("fee_item", "revenue_account_snapshot", "schedule__student__accounting_profile__billing_contact")
        .filter(schedule=locked, status=StudentFeeScheduleLine.Status.PENDING, invoice_date__lte=run_date)
        .order_by("invoice_date", "id")
    )
    billing_run = SchoolBillingRun.objects.create(
        school=locked.student.school,
        run_type=run_type,
        run_date=run_date,
        requested_count=len(lines),
        requested_by=actor,
    )
    if not lines:
        return billing_run
    contact = _student_billing_contact(locked.student)
    invoices = [
        Invoice(
            company=locked.student.school.company,
            number=f"SCH-{line.pk}",
            invoice_type=Invoice.InvoiceType.CUSTOMER,
            partner=contact,
            invoice_date=line.invoice_date,
            due_date=line.due_date,
            notes=f"School fee {line.period_key}",
        )
        for line in lines
    ]
    Invoice.objects.bulk_create(invoices)
    invoice_lines = [
        InvoiceLine(
            invoice=invoice,
            description=line.fee_item.name,
            quantity=Decimal("1"),
            unit_price=line.amount_snapshot,
        )
        for line, invoice in zip(lines, invoices)
    ]
    InvoiceLine.objects.bulk_create(invoice_lines)
    school_invoices = [
        SchoolInvoiceAccounting(
            invoice=invoice,
            school=locked.student.school,
            student=locked.student,
            source_kind=SchoolInvoiceAccounting.SourceKind.FEE_SCHEDULE,
        )
        for invoice in invoices
    ]
    SchoolInvoiceAccounting.objects.bulk_create(school_invoices)
    SchoolInvoiceLineAccounting.objects.bulk_create([
        SchoolInvoiceLineAccounting(
            invoice_line=invoice_line,
            school_invoice=school_invoice,
            schedule_line=line,
            revenue_account_snapshot=line.revenue_account_snapshot,
        )
        for line, invoice_line, school_invoice in zip(lines, invoice_lines, school_invoices)
    ])
    for line, invoice in zip(lines, invoices):
        line.invoice = invoice
        line.status = StudentFeeScheduleLine.Status.INVOICED
        line.failure_reason = ""
    StudentFeeScheduleLine.objects.bulk_update(lines, ["invoice", "status", "failure_reason", "updated_at"])
    SchoolBillingRunLine.objects.bulk_create([
        SchoolBillingRunLine(
            billing_run=billing_run,
            schedule_line=line,
            result=SchoolBillingRunLine.Result.CREATED,
            invoice=invoice,
        )
        for line, invoice in zip(lines, invoices)
    ])
    billing_run.created_count = len(lines)
    billing_run.save(update_fields=["created_count", "updated_at"])
    return billing_run


@transaction.atomic
def submit_school_installment_request(request, actor=None):
    locked = SchoolInstallmentRequest.objects.select_for_update().select_related(
        "student__school__installment_policy", "invoice"
    ).get(pk=request.pk)
    try:
        policy = locked.student.school.installment_policy
    except ObjectDoesNotExist as exc:
        raise ValidationError("Configure the school installment policy first.") from exc
    if not policy.enabled:
        raise ValidationError("Installments are disabled for this school.")
    if locked.requested_count < 1 or locked.requested_count > policy.max_installment_count:
        raise ValidationError(f"Installment count cannot exceed {policy.max_installment_count}.")
    if locked.down_payment_amount and not policy.allow_down_payment:
        raise ValidationError("This school's policy does not allow a down payment.")
    if locked.invoice.status not in {Invoice.Status.POSTED, Invoice.Status.PAID} or locked.invoice.balance <= 0:
        raise ValidationError("Installments require a posted invoice with an outstanding balance.")
    locked.status = SchoolInstallmentRequest.Status.SUBMITTED
    locked.save(update_fields=["status", "updated_at"])
    return locked


def _installment_due_date(first_due, interval_type, custom_gap_days, offset):
    if interval_type == SchoolInstallmentPolicy.Interval.WEEKLY:
        return first_due + timedelta(days=7 * offset)
    if interval_type == SchoolInstallmentPolicy.Interval.BI_WEEKLY:
        return first_due + timedelta(days=14 * offset)
    if interval_type == SchoolInstallmentPolicy.Interval.TERMLY:
        return _add_months(first_due, 4 * offset)
    if interval_type == SchoolInstallmentPolicy.Interval.CUSTOM:
        return first_due + timedelta(days=(custom_gap_days or 0) * offset)
    return _add_months(first_due, offset)


@transaction.atomic
def approve_school_installment_request(request, actor=None):
    locked = SchoolInstallmentRequest.objects.select_for_update().select_related(
        "student__school__installment_policy", "invoice"
    ).get(pk=request.pk)
    if locked.status != SchoolInstallmentRequest.Status.SUBMITTED:
        raise ValidationError("Only submitted installment requests can be approved.")
    policy = locked.student.school.installment_policy
    count = locked.approved_count or locked.requested_count
    first_due = locked.approved_first_due_date or locked.preferred_first_due_date or locked.invoice.due_date
    if count < 1 or count > policy.max_installment_count:
        raise ValidationError(f"Approved installment count cannot exceed {policy.max_installment_count}.")
    total = locked.invoice.balance - locked.down_payment_amount
    if total <= 0:
        raise ValidationError("Installment balance must be positive after down payment.")
    base = (total / count).quantize(Decimal("0.01"))
    amounts = [base for _index in range(count)]
    amounts[-1] = total - sum(amounts[:-1], Decimal("0"))
    plan = SchoolInstallmentPlan.objects.create(
        request=locked,
        invoice=locked.invoice,
        student=locked.student,
        status=SchoolInstallmentPlan.Status.ACTIVE,
        installment_count=count,
        first_due_date=first_due,
        interval_type=policy.interval_type,
        custom_gap_days=policy.custom_gap_days,
        down_payment_amount=locked.down_payment_amount,
        total_amount=total,
    )
    SchoolInstallmentLine.objects.bulk_create([
        SchoolInstallmentLine(
            plan=plan,
            sequence=index + 1,
            due_date=_installment_due_date(first_due, policy.interval_type, policy.custom_gap_days, index),
            amount=amount,
        )
        for index, amount in enumerate(amounts)
    ])
    locked.status = SchoolInstallmentRequest.Status.APPROVED
    locked.approved_by = actor
    locked.approved_count = count
    locked.approved_first_due_date = first_due
    locked.save(update_fields=[
        "status", "approved_by", "approved_count", "approved_first_due_date", "updated_at"
    ])
    return locked


@transaction.atomic
def reject_school_installment_request(request, actor=None):
    locked = SchoolInstallmentRequest.objects.select_for_update().get(pk=request.pk)
    if locked.status != SchoolInstallmentRequest.Status.SUBMITTED:
        raise ValidationError("Only submitted installment requests can be rejected.")
    if not locked.rejection_reason:
        raise ValidationError("A rejection reason is required.")
    locked.status = SchoolInstallmentRequest.Status.REJECTED
    locked.approved_by = actor
    locked.save(update_fields=["status", "approved_by", "updated_at"])
    return locked


@transaction.atomic
def start_school_installment_reschedule(plan, actor=None):
    locked = SchoolInstallmentPlan.objects.select_for_update().get(pk=plan.pk)
    if locked.status != SchoolInstallmentPlan.Status.ACTIVE:
        raise ValidationError("Only active installment plans can be rescheduled.")
    locked.status = SchoolInstallmentPlan.Status.RESCHEDULING
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def confirm_school_installment_reschedule(plan, actor=None):
    locked = SchoolInstallmentPlan.objects.select_for_update().prefetch_related("lines").get(pk=plan.pk)
    if locked.status != SchoolInstallmentPlan.Status.RESCHEDULING or not locked.reschedule_reason:
        raise ValidationError("Enter a reason before confirming an installment reschedule.")
    lines = list(locked.lines.all())
    if sum((line.amount for line in lines), Decimal("0")) != locked.total_amount:
        raise ValidationError("Rescheduled installment lines must still equal the plan total.")
    SchoolInstallmentRescheduleLog.objects.create(
        plan=locked,
        reason=locked.reschedule_reason,
        schedule_snapshot=[
            {"sequence": line.sequence, "due_date": line.due_date.isoformat(), "amount": str(line.amount), "status": line.status}
            for line in lines
        ],
        changed_by=actor,
    )
    locked.status = SchoolInstallmentPlan.Status.ACTIVE
    locked.reschedule_reason = ""
    locked.save(update_fields=["status", "reschedule_reason", "updated_at"])
    return locked


@transaction.atomic
def process_school_library_return(fine_record, actor=None):
    record = SchoolLibraryFineAccounting.objects.select_for_update().select_related(
        "book_loan__book__school__accounting_settings",
        "book_loan__student__accounting_profile__billing_contact",
    ).get(pk=fine_record.pk)
    loan = BookLoan.objects.select_for_update().get(pk=record.book_loan_id)
    if record.invoice_id:
        raise ValidationError("This book loan already has a late-return invoice.")
    today = timezone.localdate()
    if loan.returned_date:
        return_date = loan.returned_date
    else:
        return_date = today
        loan.returned_date = return_date
        loan.status = BookLoan.Status.RETURNED
        loan.save(update_fields=["returned_date", "status", "updated_at"])
        LibraryBook.objects.filter(pk=loan.book_id, copies_available__lt=F("copies_total")).update(
            copies_available=F("copies_available") + 1
        )
    late_days = max((return_date - loan.due_date).days, 0)
    settings = loan.book.school.accounting_settings
    amount = settings.late_return_fine_per_day * late_days
    record.late_days = late_days
    record.fine_amount = amount
    if amount <= 0:
        record.status = SchoolLibraryFineAccounting.Status.NO_FINE
        record.error_message = ""
        record.save(update_fields=["late_days", "fine_amount", "status", "error_message", "updated_at"])
        return record
    contact = _student_billing_contact(loan.student)
    invoice = Invoice.objects.create(
        company=loan.book.school.company,
        number=f"LIB-{loan.pk}",
        invoice_type=Invoice.InvoiceType.CUSTOMER,
        partner=contact,
        invoice_date=return_date,
        due_date=return_date,
        notes=f"Late return fine: {loan.book.title}",
    )
    invoice_line = InvoiceLine.objects.create(
        invoice=invoice,
        description=f"Late return fine for book: {loan.book.title}",
        quantity=late_days,
        unit_price=settings.late_return_fine_per_day,
    )
    school_invoice = SchoolInvoiceAccounting.objects.create(
        invoice=invoice,
        school=loan.book.school,
        student=loan.student,
        source_kind=SchoolInvoiceAccounting.SourceKind.LIBRARY_FINE,
    )
    SchoolInvoiceLineAccounting.objects.create(
        invoice_line=invoice_line,
        school_invoice=school_invoice,
        book_loan=loan,
        revenue_account_snapshot=settings.late_return_revenue_account,
    )
    record.invoice = invoice
    record.status = SchoolLibraryFineAccounting.Status.INVOICED
    record.error_message = ""
    record.save(update_fields=["invoice", "late_days", "fine_amount", "status", "error_message", "updated_at"])
    from .services import post_invoice

    post_invoice(invoice, actor)
    return record


def process_school_library_return_with_failure_state(fine_record, actor=None):
    try:
        return process_school_library_return(fine_record, actor)
    except Exception as exc:
        SchoolLibraryFineAccounting.objects.filter(pk=fine_record.pk).update(
            status=SchoolLibraryFineAccounting.Status.FAILED,
            error_message=str(exc)[:2000],
        )
        raise


def provision_school_accounting(*, company, accounts, journals, actor=None):
    schools = list(School.objects.filter(company=company).only("id"))
    existing_school_ids = set(
        SchoolAccountingSettings.objects.filter(school_id__in=[school.pk for school in schools]).values_list(
            "school_id", flat=True
        )
    )
    created = SchoolAccountingSettings.objects.bulk_create([
        SchoolAccountingSettings(
            school=school,
            journal=journals["SCH"],
            receivable_account=accounts["1100"],
            default_revenue_account=accounts["4110"],
            late_return_revenue_account=accounts["4120"],
        )
        for school in schools
        if school.pk not in existing_school_ids
    ])
    return {"extension": "school_accounting", "settings_created": len(created)}


def register_school_accounting_extension():
    register_accounting_blueprint(
        accounts=(
            AccountDefinition("4110", "School Fee Revenue", Account.AccountType.INCOME),
            AccountDefinition("4120", "School Library Fine Revenue", Account.AccountType.INCOME),
        ),
        journals=(
            JournalDefinition("SCH", "School Billing", AccountJournal.JournalType.SALES, "1100", "4110"),
        ),
    )
    register_company_accounting_provisioner(provision_school_accounting)
    register_invoice_posting_contributor(_school_invoice_posting)
    register_invoice_posting_failure_observer(_school_invoice_failure)
    register_invoice_cancellation_observer(_school_invoice_cancelled)
