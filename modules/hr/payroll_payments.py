"""Prepare and post a full-run payroll payment with payslip-level audit rows."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Sum
from django.utils import timezone

from .models import (
    PayrollAccountingEvent,
    PayrollDisbursement,
    PayrollDisbursementLine,
    PayrollPeriod,
    PayrollRun,
    Payslip,
)
from .payroll_runs import _authorized_run


def _require_posted_accrual_payable(run):
    """Keep payment on the liability account credited by the posted accrual."""
    from modules.finance.models import AccountingIntegrationEvent, JournalEntry, PayrollAccountingSettings

    accrual = PayrollAccountingEvent.objects.filter(
        payroll_run=run,
        event_type=PayrollAccountingEvent.EventType.ACCRUAL,
        status="posted",
    ).order_by("pk").first()
    if accrual is None or accrual.amount != run.net_amount:
        raise ValidationError("A posted payroll accrual matching the net payable is required.")
    bridge = AccountingIntegrationEvent.objects.select_related("journal_entry").filter(
        company_id=run.company_id,
        source_model=accrual._meta.label_lower,
        source_id=accrual.pk,
        event_type=PayrollAccountingEvent.EventType.ACCRUAL,
        status=AccountingIntegrationEvent.Status.POSTED,
    ).first()
    if bridge is None or bridge.journal_entry_id is None:
        raise ValidationError("The payroll accrual has no posted accounting journal.")
    entry = bridge.journal_entry
    if entry.status != JournalEntry.Status.POSTED or entry.company_id != run.company_id or (
        entry.total_debit != run.net_amount or entry.total_credit != run.net_amount
    ):
        raise ValidationError("The posted payroll accrual journal no longer matches the run.")
    try:
        settings = PayrollAccountingSettings.objects.select_for_update().get(company_id=run.company_id)
    except PayrollAccountingSettings.DoesNotExist as exc:
        raise ValidationError("Configure payroll accounting before payment.") from exc
    if not entry.lines.filter(
        account_id=settings.payroll_payable_account_id,
        debit=0,
        credit=run.net_amount,
    ).exists():
        raise ValidationError("The configured salary payable account differs from the posted accrual.")


@transaction.atomic
def prepare_payroll_disbursement(record, actor, *, schedule=True):
    run = PayrollRun.objects.select_for_update().select_related("period").get(pk=record.pk)
    _authorized_run(run, actor)
    if run.status != PayrollRun.Status.POSTED:
        raise ValidationError("Post the payroll accrual before preparing payment.")
    if run.generated_count == 0 or run.generated_count != run.employee_count or run.net_amount <= 0:
        raise ValidationError("The posted payroll run has no complete payable amount.")
    _require_posted_accrual_payable(run)
    reference = f"PAY-{run.pk}-FULL"
    prior = PayrollDisbursement.objects.select_for_update().filter(payroll_run=run).order_by("pk").first()
    if prior:
        if prior.reference != reference or prior.amount != run.net_amount:
            raise ValidationError("Another payroll disbursement already exists for this run.")
        if prior.status == PayrollDisbursement.Status.FAILED:
            prior.status = PayrollDisbursement.Status.PREPARING
            prior.response_message = ""
            prior.save(update_fields=["status", "response_message", "updated_at"])
        if prior.status == PayrollDisbursement.Status.PREPARING and schedule:
            from .tasks import prepare_payroll_disbursement_task
            transaction.on_commit(lambda: prepare_payroll_disbursement_task.delay(prior.pk))
        run._workflow_audit_details = {"disbursement_id": prior.pk, "amount": str(prior.amount)}
        return prior
    totals = Payslip.objects.filter(payroll_run=run, status=Payslip.Status.CONFIRMED).order_by().aggregate(
        count=Count("pk"), amount=Sum("net_salary")
    )
    if totals["count"] != run.generated_count or totals["amount"] != run.net_amount:
        raise ValidationError("Confirmed payslips do not match the posted payroll run.")
    disbursement = PayrollDisbursement.objects.create(
        payroll_run=run,
        reference=reference,
        disbursement_date=timezone.localdate(),
        amount=run.net_amount,
        status=PayrollDisbursement.Status.PREPARING,
    )
    run._workflow_audit_details = {"disbursement_id": disbursement.pk, "amount": str(disbursement.amount)}
    if schedule:
        from .tasks import prepare_payroll_disbursement_task
        transaction.on_commit(lambda: prepare_payroll_disbursement_task.delay(disbursement.pk))
    return disbursement


def mark_payroll_disbursement_failed(disbursement_id, error):
    PayrollDisbursement.objects.filter(
        pk=disbursement_id, status=PayrollDisbursement.Status.PREPARING
    ).update(status=PayrollDisbursement.Status.FAILED, response_message=str(error)[:1000])


@transaction.atomic
def prepare_payroll_disbursement_batch(disbursement_id, *, batch_size=200, schedule_next=True):
    if not 1 <= batch_size <= 500:
        raise ValueError("Payroll payment batch size must be between 1 and 500.")
    disbursement = (
        PayrollDisbursement.objects.select_for_update()
        .select_related("payroll_run__period")
        .get(pk=disbursement_id)
    )
    if disbursement.status != PayrollDisbursement.Status.PREPARING:
        return False
    run = disbursement.payroll_run
    PayrollRun.objects.select_for_update().get(pk=run.pk)
    PayrollPeriod.objects.select_for_update().get(pk=run.period_id)
    if run.status != PayrollRun.Status.POSTED or disbursement.amount != run.net_amount:
        raise ValidationError("The posted payroll run changed during payment preparation.")
    slips = list(
        Payslip.objects.filter(
            payroll_run=run, pk__gt=disbursement.preparation_cursor,
        ).order_by("pk")[:batch_size]
    )
    if not slips:
        if disbursement.prepared_count != run.generated_count:
            raise ValidationError("The prepared payment does not cover every payslip.")
        disbursement.status = PayrollDisbursement.Status.GENERATED
        disbursement.prepared_at = timezone.now()
        disbursement.save(update_fields=["status", "prepared_at", "updated_at"])
        return False
    if any(slip.status != Payslip.Status.CONFIRMED or slip.net_salary < 0 for slip in slips):
        raise ValidationError("Only confirmed payslips with nonnegative net pay can be prepared.")
    slip_ids = [slip.pk for slip in slips]
    if PayrollDisbursementLine.objects.filter(payslip_id__in=slip_ids).exclude(
        disbursement=disbursement
    ).exists():
        raise ValidationError("A payslip is already assigned to another payroll payment.")
    PayrollDisbursementLine.objects.bulk_create([
        PayrollDisbursementLine(disbursement=disbursement, payslip=slip, amount=slip.net_salary)
        for slip in slips
    ])
    disbursement.preparation_cursor = slips[-1].pk
    disbursement.prepared_count += len(slips)
    update_fields = ["preparation_cursor", "prepared_count", "updated_at"]
    more = len(slips) == batch_size
    if not more:
        if disbursement.prepared_count != run.generated_count:
            raise ValidationError("The prepared payment does not cover every payslip.")
        disbursement.status = PayrollDisbursement.Status.GENERATED
        disbursement.prepared_at = timezone.now()
        update_fields += ["status", "prepared_at"]
    disbursement.save(update_fields=update_fields)
    if more and schedule_next:
        from .tasks import prepare_payroll_disbursement_task
        transaction.on_commit(lambda: prepare_payroll_disbursement_task.apply_async(args=[disbursement.pk], countdown=1))
    return more


@transaction.atomic
def post_payroll_disbursement(record, actor, *, payment_reference):
    disbursement = (
        PayrollDisbursement.objects.select_for_update()
        .select_related("payroll_run__period")
        .get(pk=record.pk)
    )
    run = PayrollRun.objects.select_for_update().get(pk=disbursement.payroll_run_id)
    _authorized_run(run, actor)
    if disbursement.status == PayrollDisbursement.Status.COMPLETED:
        return disbursement
    if disbursement.status != PayrollDisbursement.Status.GENERATED or run.status != PayrollRun.Status.POSTED:
        raise ValidationError("Prepare a posted payroll run before confirming payment.")
    payment_reference = (payment_reference or "").strip()
    if not payment_reference or len(payment_reference) > 160:
        raise ValidationError("Enter a payment reference of at most 160 characters.")
    if disbursement.payment_reference and disbursement.payment_reference != payment_reference:
        raise ValidationError("The payment reference cannot change after a posting attempt.")
    if disbursement.prepared_count != run.generated_count or run.generated_count == 0:
        raise ValidationError("The payroll payment lines are incomplete.")
    totals = PayrollDisbursementLine.objects.filter(disbursement=disbursement).order_by().aggregate(
        count=Count("pk"), amount=Sum("amount")
    )
    if totals["count"] != run.generated_count or totals["amount"] != disbursement.amount or (
        disbursement.amount != run.net_amount
    ):
        raise ValidationError("Payroll payment lines do not reconcile to the posted run.")
    if PayrollDisbursementLine.objects.filter(disbursement=disbursement).exclude(
        status=PayrollDisbursementLine.Status.PREPARED
    ).exists() or PayrollDisbursementLine.objects.filter(disbursement=disbursement).exclude(
        payslip__payroll_run=run,
        payslip__status=Payslip.Status.CONFIRMED,
        amount=F("payslip__net_salary"),
    ).exists():
        raise ValidationError("A payment line or payslip changed after preparation.")
    _require_posted_accrual_payable(run)
    event_reference = f"PAYROLL-{run.pk}-PAYMENT"
    if PayrollAccountingEvent.objects.filter(
        payroll_run=run,
        event_type=PayrollAccountingEvent.EventType.PAYMENT,
    ).exclude(reference=event_reference).exists():
        raise ValidationError("Another payroll payment event already exists for this run.")
    event, _created = PayrollAccountingEvent.objects.get_or_create(
        payroll_run=run,
        event_type=PayrollAccountingEvent.EventType.PAYMENT,
        reference=event_reference,
        defaults={"event_date": disbursement.disbursement_date, "amount": disbursement.amount},
    )
    if event.amount != disbursement.amount:
        raise ValidationError("The existing payroll payment event amount has changed.")
    from modules.finance.services import post_payroll_accounting_event

    event = post_payroll_accounting_event(event, actor)
    disbursement.payment_reference = payment_reference
    disbursement.payment_event = event
    update_fields = ["payment_reference", "payment_event", "updated_at"]
    if event.status == "posted":
        now = timezone.now()
        PayrollDisbursementLine.objects.filter(disbursement=disbursement).update(
            status=PayrollDisbursementLine.Status.PAID, paid_at=now, updated_at=now
        )
        Payslip.objects.filter(payroll_run=run, status=Payslip.Status.CONFIRMED).update(
            status=Payslip.Status.PAID, updated_at=now
        )
        disbursement.status = PayrollDisbursement.Status.COMPLETED
        update_fields.append("status")
    disbursement.save(update_fields=update_fields)
    disbursement._workflow_audit_details = {
        "payment_event_id": event.pk,
        "journal_reference": event.journal_reference,
        "payment_reference": payment_reference,
        "amount": str(disbursement.amount),
    }
    return disbursement
