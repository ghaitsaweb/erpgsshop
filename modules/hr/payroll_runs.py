"""Company and period scoped payroll readiness checks.

Each check is an aggregate or EXISTS query. The employee cohort is never
materialized in a request, so query count and memory do not grow per employee.
"""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q, Subquery, Sum
from django.utils import timezone

from core.models import Attendance, Employee
from modules.organization.context import access_scope

from .access import FULL_HR_GROUPS, PAYROLL_ADMIN
from .models import (
    ApprovalStatus,
    AttendanceChangeRequest,
    CompensationBenefit,
    EmploymentContract,
    LeaveRequest,
    LeaveEncashment,
    OvertimeRequest,
    PayrollPeriod,
    PayrollAccountingEvent,
    PayrollRun,
    PayrollRunChecklistItem,
    PayrollRunPreviewLine,
    Payslip,
)


def _authorized_run(run, actor):
    if actor.is_superuser:
        return
    if not actor.groups.filter(name__in=FULL_HR_GROUPS | {PAYROLL_ADMIN}).exists():
        raise ValidationError("Only HR or payroll managers can check a payroll run.")
    if run.company_id not in access_scope(actor)[0]:
        raise ValidationError("The payroll run is outside your company scope.")


def _cohort(run):
    period = run.period
    return Employee.objects.filter(
        company_id=run.company_id,
        join_date__lte=period.date_end,
    ).filter(Q(status=Employee.Status.ACTIVE) | Q(inactive_date__gte=period.date_start)).order_by()


def _check_counts(run):
    """Return fixed-size checklist facts for one company and payroll period."""
    period = run.period
    if period.company_id != run.company_id:
        raise ValidationError("Payroll period and run must belong to the same company.")
    if period.date_end < period.date_start:
        raise ValidationError("Payroll period dates are invalid.")
    if not period.date_start <= period.cutoff_date <= period.date_end:
        raise ValidationError("Payroll cut-off date must be inside the payroll period.")
    cohort = _cohort(run)
    cohort_ids = cohort.values("pk")
    employee_count = cohort.count()
    partial_employment = cohort.filter(
        Q(join_date__gt=period.date_start)
        | Q(inactive_date__isnull=False, inactive_date__lt=period.date_end)
    ).count()
    contract_scope = EmploymentContract.objects.filter(
        employee_id=OuterRef("pk"),
        status=EmploymentContract.Status.ACTIVE,
        start_date__lte=period.date_start,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=period.date_end)).order_by()
    missing_contract = cohort.annotate(eligible=Exists(contract_scope)).filter(eligible=False).count()
    snapshot_scope = CompensationBenefit.objects.filter(
        employee_id=OuterRef("pk"),
        contract__status=EmploymentContract.Status.ACTIVE,
        contract__start_date__lte=period.date_start,
        effective_date__lte=period.date_start,
        status=ApprovalStatus.APPROVED,
    ).filter(Q(contract__end_date__isnull=True) | Q(contract__end_date__gte=period.date_end)).order_by()
    missing_snapshot = cohort.annotate(eligible=Exists(snapshot_scope)).filter(eligible=False).count()
    midperiod_compensation = CompensationBenefit.objects.filter(
        employee_id__in=cohort_ids,
        status=ApprovalStatus.APPROVED,
        effective_date__gt=period.date_start,
        effective_date__lte=period.date_end,
    ).order_by().count()
    pending_attendance = AttendanceChangeRequest.objects.filter(
        employee_id__in=cohort_ids,
        attendance_date__range=(period.date_start, period.date_end),
        status=ApprovalStatus.SUBMITTED,
    ).order_by().count()
    pending_overtime = OvertimeRequest.objects.filter(
        employee_id__in=cohort_ids,
        overtime_date__range=(period.date_start, period.date_end),
        status=LeaveRequest.Status.SUBMITTED,
    ).order_by().count()
    pending_leave = LeaveRequest.objects.filter(
        employee_id__in=cohort_ids,
        start_date__lte=period.date_end,
        end_date__gte=period.date_start,
        status=LeaveRequest.Status.SUBMITTED,
    ).order_by().count()
    approved_unpaid_or_unknown_leave = LeaveRequest.objects.filter(
        employee_id__in=cohort_ids,
        start_date__lte=period.date_end,
        end_date__gte=period.date_start,
        status=LeaveRequest.Status.APPROVED,
    ).filter(Q(leave_type_record__isnull=True) | Q(leave_type_record__paid=False)).order_by().count()
    approved_overtime = OvertimeRequest.objects.filter(
        employee_id__in=cohort_ids,
        overtime_date__range=(period.date_start, period.date_end),
        status=LeaveRequest.Status.APPROVED,
    ).order_by().count()
    attendance_exceptions = Attendance.objects.filter(
        employee_id__in=cohort_ids,
        attendance_date__range=(period.date_start, period.date_end),
        status__in=[Attendance.Status.ABSENT, Attendance.Status.SICK, Attendance.Status.LEAVE],
    ).order_by().count()
    unassigned_encashments = LeaveEncashment.objects.filter(
        employee_id__in=cohort_ids,
        request_date__range=(period.date_start, period.date_end),
        status=LeaveEncashment.Status.APPROVED,
    ).order_by().count()
    duplicate_payslips = (
        Payslip.objects.filter(
            employee_id__in=cohort_ids,
            period_start=period.date_start,
            period_end=period.date_end,
        )
        .exclude(status=Payslip.Status.CANCELLED)
        .order_by()
        .values("employee_id")
        .distinct()
        .count()
    )
    return employee_count, [
        ("cohort", "Employees in Scope", "blocking", 0 if employee_count else 1, f"{employee_count} employee(s) in scope."),
        ("period", "Open Payroll Period", "blocking", 0 if period.status == PayrollPeriod.Status.OPEN else 1,
         "Period is open." if period.status == PayrollPeriod.Status.OPEN else "Open the payroll period before calculation."),
        ("partial_employment", "Full-Period Employment", "blocking", partial_employment,
         f"{partial_employment} employee(s) need partial-period payroll, which is not calculated yet."),
        ("contract", "Active Contract", "blocking", missing_contract,
         f"{missing_contract} employee(s) have no active contract covering the full period."),
        ("compensation", "Approved Compensation Snapshot", "blocking", missing_snapshot,
         f"{missing_snapshot} employee(s) have no approved compensation snapshot effective from period start."),
        ("midperiod_compensation", "Mid-Period Compensation Changes", "blocking", midperiod_compensation,
         f"{midperiod_compensation} compensation change(s) need prorated payroll."),
        ("attendance", "Pending Attendance Changes", "blocking", pending_attendance,
         f"{pending_attendance} attendance change request(s) are awaiting approval."),
        ("overtime", "Pending Overtime", "blocking", pending_overtime,
         f"{pending_overtime} overtime request(s) are awaiting approval."),
        ("leave", "Pending Leave", "blocking", pending_leave,
         f"{pending_leave} leave request(s) are awaiting approval."),
        ("unpaid_leave", "Unpaid or Unclassified Leave", "blocking", approved_unpaid_or_unknown_leave,
         f"{approved_unpaid_or_unknown_leave} approved leave request(s) need a payroll deduction or classification."),
        ("approved_overtime", "Approved Overtime", "blocking", approved_overtime,
         f"{approved_overtime} approved overtime request(s) need payroll valuation."),
        ("absence", "Attendance Exceptions", "blocking", attendance_exceptions,
         f"{attendance_exceptions} non-present attendance day(s) need payroll valuation."),
        ("encashment_assignment", "Unassigned Encashments", "blocking", unassigned_encashments,
         f"{unassigned_encashments} approved encashment(s) are not assigned to payroll."),
        ("duplicate_payslip", "Duplicate Payslips", "blocking", duplicate_payslips,
         f"{duplicate_payslips} employee(s) already have a payslip for this period."),
    ]


def _save_checks(run, checks, now):
    items = [
        PayrollRunChecklistItem(
            run=run,
            code=code,
            name=name,
            severity=severity,
            state=PayrollRunChecklistItem.State.FAILED if affected else PayrollRunChecklistItem.State.PASSED,
            affected_count=affected,
            message=message,
            checked_at=now,
            updated_at=now,
        )
        for code, name, severity, affected, message in checks
    ]
    PayrollRunChecklistItem.objects.bulk_create(
        items,
        update_conflicts=True,
        unique_fields=["run", "code"],
        update_fields=["name", "severity", "state", "affected_count", "message", "checked_at", "updated_at"],
    )
    return sum(1 for _code, _name, severity, affected, _message in checks if severity == "blocking" and affected)


@transaction.atomic
def check_payroll_run(record, actor, *, mark_ready=False):
    run = PayrollRun.objects.select_for_update().select_related("period").get(pk=record.pk)
    _authorized_run(run, actor)
    allowed = {PayrollRun.Status.DRAFT, PayrollRun.Status.CALCULATING}
    if run.status not in allowed:
        raise ValidationError("This payroll run cannot be checked in its current status.")
    if mark_ready and run.checklist_checked_at is None:
        raise ValidationError("Run the payroll checklist before marking the run ready.")
    employee_count, checks = _check_counts(run)
    now = timezone.now()
    blocking_count = _save_checks(run, checks, now)
    run.checklist_checked_at = now
    run.checklist_blocking_count = blocking_count
    run.employee_count = employee_count
    run.status = PayrollRun.Status.READY if mark_ready and not blocking_count else PayrollRun.Status.CALCULATING
    run.save(update_fields=["checklist_checked_at", "checklist_blocking_count", "employee_count", "status", "updated_at"])
    run._workflow_audit_details = {
        "checklist_blocking_count": blocking_count,
        "checklist_checked_at": now.isoformat(),
    }
    return run


def _effective_compensations(employee_ids, period):
    latest = (
        CompensationBenefit.objects.filter(
            employee_id=OuterRef("pk"),
            status=ApprovalStatus.APPROVED,
            effective_date__lte=period.date_start,
            contract__status=EmploymentContract.Status.ACTIVE,
            contract__start_date__lte=period.date_start,
        )
        .filter(Q(contract__end_date__isnull=True) | Q(contract__end_date__gte=period.date_end))
        .order_by("-effective_date", "-pk")
        .values("pk")[:1]
    )
    selected = dict(
        Employee.objects.filter(pk__in=employee_ids)
        .annotate(compensation_pk=Subquery(latest))
        .values_list("pk", "compensation_pk")
    )
    snapshots = CompensationBenefit.objects.filter(
        pk__in=[pk for pk in selected.values() if pk is not None]
    ).in_bulk()
    return {employee_id: snapshots.get(snapshot_pk) for employee_id, snapshot_pk in selected.items()}


def _waiting_encashments(employee_ids, period):
    return {
        row["employee_id"]: row["total"]
        for row in LeaveEncashment.objects.filter(
            employee_id__in=employee_ids,
            request_date__range=(period.date_start, period.date_end),
            status=LeaveEncashment.Status.WAITING_FOR_PAYROLL,
        ).order_by().values("employee_id").annotate(total=Sum("amount"))
    }


@transaction.atomic
def start_payroll_preview(record, actor, *, schedule=True):
    run = PayrollRun.objects.select_for_update().select_related("period").get(pk=record.pk)
    _authorized_run(run, actor)
    if run.status != PayrollRun.Status.READY:
        raise ValidationError("Only a ready payroll run can build a preview.")
    employee_count, checks = _check_counts(run)
    if employee_count != run.employee_count or any(severity == "blocking" and affected for _, _, severity, affected, _ in checks):
        raise ValidationError("Payroll inputs changed or checks are blocking; create a new checked run.")
    if run.preview_lines.exists():
        raise ValidationError("This run already contains preview rows.")
    run.status = PayrollRun.Status.PREVIEWING
    run.processing_error = ""
    run.preview_period_start = run.period.date_start
    run.preview_period_end = run.period.date_end
    run.save(update_fields=["status", "processing_error", "preview_period_start", "preview_period_end", "updated_at"])
    if schedule:
        from .tasks import process_payroll_preview_task
        transaction.on_commit(lambda: process_payroll_preview_task.delay(run.pk))
    return run


@transaction.atomic
def process_payroll_preview_batch(run_id, *, batch_size=200, schedule_next=True):
    if not 1 <= batch_size <= 500:
        raise ValueError("Payroll preview batch size must be between 1 and 500.")
    run = PayrollRun.objects.select_for_update().select_related("period").get(pk=run_id)
    if run.status != PayrollRun.Status.PREVIEWING:
        return False
    if (run.period.date_start, run.period.date_end) != (run.preview_period_start, run.preview_period_end):
        raise ValidationError("The payroll period changed after preview started.")
    employee_ids = list(
        _cohort(run).filter(pk__gt=run.preview_cursor)
        .order_by("pk").values_list("pk", flat=True)[:batch_size]
    )
    if not employee_ids:
        if run.preview_count != run.employee_count:
            raise ValidationError("The payroll employee scope changed while the preview was running.")
        run.status = PayrollRun.Status.PREVIEWED
        run.previewed_at = timezone.now()
        run.save(update_fields=["status", "previewed_at", "updated_at"])
        return False
    snapshots = _effective_compensations(employee_ids, run.period)
    encashments = _waiting_encashments(employee_ids, run.period)
    lines = []
    gross_total = deduction_total = net_total = Decimal("0.00")
    blocked = 0
    for employee_id in employee_ids:
        snapshot = snapshots.get(employee_id)
        basic = snapshot.basic_salary if snapshot else Decimal("0.00")
        allowance = snapshot.allowance_amount if snapshot else Decimal("0.00")
        deduction = snapshot.deduction_amount if snapshot else Decimal("0.00")
        encashment = encashments.get(employee_id, Decimal("0.00"))
        gross = basic + allowance + encashment
        net = gross - deduction
        issue = "" if snapshot and net >= 0 else (
            "No approved effective compensation snapshot." if snapshot is None else "Net pay cannot be negative."
        )
        if issue:
            blocked += 1
        else:
            gross_total += gross
            deduction_total += deduction
            net_total += net
        lines.append(PayrollRunPreviewLine(
            run=run,
            employee_id=employee_id,
            compensation=snapshot,
            basic_salary=basic,
            allowance_amount=allowance,
            encashment_amount=encashment,
            deduction_amount=deduction,
            gross_amount=gross,
            net_amount=net,
            state=PayrollRunPreviewLine.State.BLOCKED if issue else PayrollRunPreviewLine.State.READY,
            issue=issue,
        ))
    PayrollRunPreviewLine.objects.bulk_create(lines)
    run.preview_cursor = employee_ids[-1]
    run.preview_count += len(lines)
    run.preview_blocking_count += blocked
    run.gross_amount += gross_total
    run.deduction_amount += deduction_total
    run.net_amount += net_total
    update_fields = [
        "preview_cursor", "preview_count", "preview_blocking_count", "gross_amount",
        "deduction_amount", "net_amount", "updated_at",
    ]
    more = len(employee_ids) == batch_size
    if not more:
        if run.preview_count != run.employee_count:
            raise ValidationError("The payroll employee scope changed while the preview was running.")
        run.status = PayrollRun.Status.PREVIEWED
        run.previewed_at = timezone.now()
        update_fields += ["status", "previewed_at"]
    run.save(update_fields=update_fields)
    if more and schedule_next:
        from .tasks import process_payroll_preview_task
        transaction.on_commit(lambda: process_payroll_preview_task.apply_async(args=[run.pk], countdown=1))
    return more


@transaction.atomic
def start_payroll_generation(record, actor, *, schedule=True):
    run = PayrollRun.objects.select_for_update().select_related("period").get(pk=record.pk)
    _authorized_run(run, actor)
    if run.status != PayrollRun.Status.PREVIEWED or run.preview_blocking_count:
        raise ValidationError("A complete preview without blocked employees is required.")
    if run.preview_count != run.employee_count or run.preview_count == 0:
        raise ValidationError("The payroll preview is incomplete.")
    if (run.period.date_start, run.period.date_end) != (run.preview_period_start, run.preview_period_end):
        raise ValidationError("The payroll period changed after preview.")
    employee_count, checks = _check_counts(run)
    if employee_count != run.preview_count or any(severity == "blocking" and affected for _, _, severity, affected, _ in checks):
        raise ValidationError("Payroll inputs changed or checks are blocking; generation cannot start.")
    run.status = PayrollRun.Status.GENERATING
    run.processing_error = ""
    run.save(update_fields=["status", "processing_error", "updated_at"])
    if schedule:
        from .tasks import process_payroll_generation_task
        transaction.on_commit(lambda: process_payroll_generation_task.delay(run.pk))
    return run


@transaction.atomic
def process_payroll_generation_batch(run_id, *, batch_size=200, schedule_next=True):
    if not 1 <= batch_size <= 500:
        raise ValueError("Payroll generation batch size must be between 1 and 500.")
    run = PayrollRun.objects.select_for_update().select_related("period").get(pk=run_id)
    if run.status != PayrollRun.Status.GENERATING:
        return False
    # All runs for one period serialize here, including duplicate checks.
    PayrollPeriod.objects.select_for_update().get(pk=run.period_id)
    if run.period.status != PayrollPeriod.Status.OPEN or (
        run.period.date_start, run.period.date_end
    ) != (run.preview_period_start, run.preview_period_end):
        raise ValidationError("The payroll period changed after preview.")
    lines = list(
        PayrollRunPreviewLine.objects.filter(
            run=run, pk__gt=run.generation_cursor,
        ).order_by("pk")[:batch_size]
    )
    if not lines:
        if run.generated_count != run.preview_count:
            raise ValidationError("The generated payslip count does not match the preview.")
        run.status = PayrollRun.Status.GENERATED
        run.generated_at = timezone.now()
        run.save(update_fields=["status", "generated_at", "updated_at"])
        return False
    if any(line.state != PayrollRunPreviewLine.State.READY for line in lines):
        raise ValidationError("A blocked payroll preview line cannot generate a payslip.")
    employee_ids = [line.employee_id for line in lines]
    current_scope = set(_cohort(run).filter(pk__in=employee_ids).values_list("pk", flat=True))
    if current_scope != set(employee_ids):
        raise ValidationError("An employee's payroll scope changed after preview.")
    partial_scope = Employee.objects.filter(pk__in=employee_ids).filter(
        Q(join_date__gt=run.period.date_start)
        | Q(inactive_date__isnull=False, inactive_date__lt=run.period.date_end)
    ).exists()
    if partial_scope:
        raise ValidationError("Partial-period employment needs payroll proration.")
    current_snapshots = _effective_compensations(employee_ids, run.period)
    if any(
        (snapshot := current_snapshots.get(line.employee_id)) is None
        or snapshot.pk != line.compensation_id
        or (snapshot.basic_salary, snapshot.allowance_amount, snapshot.deduction_amount)
        != (line.basic_salary, line.allowance_amount, line.deduction_amount)
        for line in lines
    ):
        raise ValidationError("A compensation snapshot changed after payroll preview.")
    if CompensationBenefit.objects.filter(
        employee_id__in=employee_ids,
        status=ApprovalStatus.APPROVED,
        effective_date__gt=run.period.date_start,
        effective_date__lte=run.period.date_end,
    ).exists():
        raise ValidationError("A mid-period compensation change needs payroll proration.")
    if AttendanceChangeRequest.objects.filter(
        employee_id__in=employee_ids,
        attendance_date__range=(run.period.date_start, run.period.date_end),
        status=ApprovalStatus.SUBMITTED,
    ).exists() or OvertimeRequest.objects.filter(
        employee_id__in=employee_ids,
        overtime_date__range=(run.period.date_start, run.period.date_end),
        status__in=[LeaveRequest.Status.SUBMITTED, LeaveRequest.Status.APPROVED],
    ).exists() or LeaveRequest.objects.filter(
        employee_id__in=employee_ids,
        start_date__lte=run.period.date_end,
        end_date__gte=run.period.date_start,
    ).filter(
        Q(status=LeaveRequest.Status.SUBMITTED)
        | Q(status=LeaveRequest.Status.APPROVED, leave_type_record__isnull=True)
        | Q(status=LeaveRequest.Status.APPROVED, leave_type_record__paid=False)
    ).exists() or Attendance.objects.filter(
        employee_id__in=employee_ids,
        attendance_date__range=(run.period.date_start, run.period.date_end),
        status__in=[Attendance.Status.ABSENT, Attendance.Status.SICK, Attendance.Status.LEAVE],
    ).exists() or LeaveEncashment.objects.filter(
        employee_id__in=employee_ids,
        request_date__range=(run.period.date_start, run.period.date_end),
        status=LeaveEncashment.Status.APPROVED,
    ).exists():
        raise ValidationError("A payroll-affecting HR record changed after preview.")
    existing = Payslip.objects.filter(
        employee_id__in=employee_ids,
        period_start=run.period.date_start,
        period_end=run.period.date_end,
    ).exclude(status=Payslip.Status.CANCELLED).exists()
    if existing:
        raise ValidationError("A payslip already exists for an employee in this payroll period.")
    waiting = list(
        LeaveEncashment.objects.select_for_update().filter(
            employee_id__in=employee_ids,
            request_date__range=(run.period.date_start, run.period.date_end),
            status=LeaveEncashment.Status.WAITING_FOR_PAYROLL,
        ).order_by("employee_id", "pk")
    )
    by_employee = defaultdict(lambda: Decimal("0.00"))
    for encashment in waiting:
        by_employee[encashment.employee_id] += encashment.amount
    if any(by_employee[line.employee_id] != line.encashment_amount for line in lines):
        raise ValidationError("Leave encashments changed after the payroll preview.")
    Payslip.objects.bulk_create([
        Payslip(
            employee_id=line.employee_id,
            payroll_run=run,
            number=f"PS-{run.pk}-{line.employee_id}",
            period_start=run.period.date_start,
            period_end=run.period.date_end,
            basic_salary=line.basic_salary,
            allowances=line.allowance_amount + line.encashment_amount,
            deductions=line.deduction_amount,
            net_salary=line.net_amount,
            status=Payslip.Status.DRAFT,
        )
        for line in lines
    ])
    payslip_ids = dict(Payslip.objects.filter(
        payroll_run=run, employee_id__in=employee_ids,
    ).values_list("employee_id", "pk"))
    now = timezone.now()
    for encashment in waiting:
        encashment.payslip_id = payslip_ids[encashment.employee_id]
        encashment.status = LeaveEncashment.Status.PAYROLL_GENERATED
        encashment.updated_at = now
    if waiting:
        LeaveEncashment.objects.bulk_update(waiting, ["payslip", "status", "updated_at"])
    run.generation_cursor = lines[-1].pk
    run.generated_count += len(lines)
    update_fields = ["generation_cursor", "generated_count", "updated_at"]
    more = len(lines) == batch_size
    if not more:
        if run.generated_count != run.preview_count:
            raise ValidationError("The generated payslip count does not match the preview.")
        run.status = PayrollRun.Status.GENERATED
        run.generated_at = now
        update_fields += ["status", "generated_at"]
    run.save(update_fields=update_fields)
    if more and schedule_next:
        from .tasks import process_payroll_generation_task
        transaction.on_commit(lambda: process_payroll_generation_task.apply_async(args=[run.pk], countdown=1))
    return more


@transaction.atomic
def mark_payroll_processing_failed(run_id, message):
    run = PayrollRun.objects.select_for_update().get(pk=run_id)
    if run.status in {PayrollRun.Status.PREVIEWING, PayrollRun.Status.GENERATING}:
        run.status = PayrollRun.Status.FAILED
        run.processing_error = str(message)[:255]
        run.save(update_fields=["status", "processing_error", "updated_at"])


@transaction.atomic
def post_generated_payroll_accrual(record, actor):
    """Post a balanced accrual for the currently supported zero-deduction run."""
    run = PayrollRun.objects.select_for_update().select_related("period").get(pk=record.pk)
    _authorized_run(run, actor)
    if run.status == PayrollRun.Status.POSTED:
        return run
    if run.status != PayrollRun.Status.GENERATED:
        raise ValidationError("Generate all payroll payslips before posting an accrual.")
    if run.generated_count == 0 or run.generated_count != run.preview_count or run.generated_count != run.employee_count:
        raise ValidationError("The generated payroll employee count does not match its preview.")
    if run.period.company_id != run.company_id or (
        run.period.date_start, run.period.date_end
    ) != (run.preview_period_start, run.preview_period_end):
        raise ValidationError("The payroll period changed after preview.")
    totals = Payslip.objects.filter(payroll_run=run).order_by().aggregate(
        count=Count("pk"), basic=Sum("basic_salary"), allowances=Sum("allowances"),
        deductions=Sum("deductions"), net=Sum("net_salary"),
    )
    gross = (totals["basic"] or Decimal("0.00")) + (totals["allowances"] or Decimal("0.00"))
    deductions = totals["deductions"] or Decimal("0.00")
    net = totals["net"] or Decimal("0.00")
    if totals["count"] != run.generated_count or (
        gross, deductions, net
    ) != (run.gross_amount, run.deduction_amount, run.net_amount):
        raise ValidationError("Payslip totals no longer match the generated payroll run.")
    if deductions or gross <= 0 or net != gross:
        raise ValidationError("Configure deduction liability accounts before posting this payroll run.")
    if Payslip.objects.filter(payroll_run=run).exclude(status=Payslip.Status.DRAFT).exists():
        raise ValidationError("Generated payslips must remain draft until payroll accrual posting.")
    reference = f"PAYROLL-{run.pk}-ACCRUAL"
    if PayrollAccountingEvent.objects.filter(
        payroll_run=run, event_type=PayrollAccountingEvent.EventType.ACCRUAL,
    ).exclude(reference=reference).exists():
        raise ValidationError("Another payroll accrual already exists for this run.")
    event, _created = PayrollAccountingEvent.objects.get_or_create(
        payroll_run=run,
        event_type=PayrollAccountingEvent.EventType.ACCRUAL,
        reference=reference,
        defaults={"event_date": run.period.date_end, "amount": gross},
    )
    if event.amount != gross:
        raise ValidationError("The existing payroll accrual amount differs from the payslip total.")
    from modules.finance.services import post_payroll_accounting_event

    event = post_payroll_accounting_event(event, actor)
    if event.status == "posted":
        now = timezone.now()
        Payslip.objects.filter(payroll_run=run, status=Payslip.Status.DRAFT).update(
            status=Payslip.Status.CONFIRMED, updated_at=now
        )
        run.status = PayrollRun.Status.POSTED
        run.save(update_fields=["status", "updated_at"])
    run._workflow_audit_details = {
        "accounting_event_id": event.pk,
        "journal_reference": event.journal_reference,
        "accrual_status": event.status,
        "gross": str(gross),
    }
    return run
