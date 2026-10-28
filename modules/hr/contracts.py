"""Guard the local employment-contract and compensation lifecycle."""

from datetime import timedelta
from decimal import Decimal
import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import Employee

from .models import (
    ApprovalStatus,
    CompensationBenefit,
    CompensationBenefitLine,
    ContractAllowance,
    ContractAllowanceHistory,
    ContractLetter,
    EmploymentContract,
    SalaryComponent,
)


CONTRACT_TRANSITIONS = {
    "submit": ({EmploymentContract.Status.DRAFT}, EmploymentContract.Status.SUBMITTED),
    "activate": ({EmploymentContract.Status.SUBMITTED}, EmploymentContract.Status.ACTIVE),
    "reject": ({EmploymentContract.Status.SUBMITTED}, EmploymentContract.Status.REJECTED),
    "cancel": ({EmploymentContract.Status.DRAFT, EmploymentContract.Status.SUBMITTED}, EmploymentContract.Status.CANCELLED),
    "reset": ({EmploymentContract.Status.REJECTED, EmploymentContract.Status.CANCELLED}, EmploymentContract.Status.DRAFT),
    "expire": (EmploymentContract.Status.ACTIVE, EmploymentContract.Status.EXPIRED),
    "terminate": (EmploymentContract.Status.ACTIVE, EmploymentContract.Status.TERMINATED),
}


def _allowance_total(contract, effective_date):
    """Aggregate effective earning allowances in one bounded SQL query."""
    return (
        ContractAllowance.objects.filter(
            contract=contract,
            component__component_type=SalaryComponent.ComponentType.EARNING,
            active=True,
            effective_date__lte=effective_date,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=effective_date))
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )


def _basic_salary(contract):
    """Resolve the contract salary without another query for selected packages."""
    package_salary = contract.salary_package.base_salary if contract.salary_package_id else Decimal("0.00")
    return contract.base_salary or package_salary


def refresh_contract_compensation(contract, actor, effective_date=None):
    """Create a traceable compensation snapshot from the effective contract terms."""
    effective_date = effective_date or contract.start_date
    allowances = list(
        ContractAllowance.objects.filter(
            contract=contract,
            component__component_type=SalaryComponent.ComponentType.EARNING,
            active=True,
            effective_date__lte=effective_date,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=effective_date))
        .select_related("component")
        .order_by("component__name", "pk")
    )
    basic_salary = _basic_salary(contract)
    allowance_total = sum((line.amount for line in allowances), Decimal("0.00"))
    snapshot, _created = CompensationBenefit.objects.update_or_create(
        contract=contract,
        effective_date=effective_date,
        defaults={
            "employee_id": contract.employee_id,
            "basic_salary": basic_salary,
            "allowance_amount": allowance_total,
            "deduction_amount": Decimal("0.00"),
            "status": ApprovalStatus.APPROVED,
            "approver": actor,
            "notes": f"Generated from active contract {contract.number}.",
        },
    )
    # A snapshot has its own frozen rows, so later allowance revisions do not
    # rewrite the component trail for a previous effective date.
    snapshot.component_lines.all().delete()
    component_lines = [
        CompensationBenefitLine(
            benefit=snapshot,
            category=CompensationBenefitLine.Category.BASIC,
            label="Basic salary",
            amount=basic_salary,
            sequence=10,
        )
    ]
    component_lines.extend(
        CompensationBenefitLine(
            benefit=snapshot,
            component=line.component,
            category=CompensationBenefitLine.Category.ALLOWANCE,
            label=line.component.name,
            amount=line.amount,
            sequence=20 + index,
        )
        for index, line in enumerate(allowances, start=1)
    )
    CompensationBenefitLine.objects.bulk_create(component_lines)
    return snapshot


def _render_contract_letter(contract):
    template = contract.letter_template
    if template is None:
        return ""
    values = {
        "company_name": contract.employee.company.name,
        "contract_number": contract.number,
        "employee_id": contract.employee.employee_id,
        "employee_name": contract.employee.name,
        "start_date": contract.start_date.isoformat(),
        "end_date": contract.end_date.isoformat() if contract.end_date else "Open ended",
        "base_salary": f"{_basic_salary(contract):,.2f}",
    }

    def replace(match):
        return values[match.group(1)]

    return re.sub(r"{{\s*([a-z_][a-z0-9_]*)\s*}}", replace, template.content)


def generate_contract_letter(contract, actor):
    """Persist an immutable rendered letter snapshot for an active contract."""
    if not contract.letter_template_id:
        return None
    letter, _created = ContractLetter.objects.get_or_create(
        contract=contract,
        letter_number=f"{contract.number}-LETTER",
        defaults={
            "template": contract.letter_template,
            "letter_type": contract.letter_template.letter_type,
            "issued_date": timezone.localdate(),
            "content": _render_contract_letter(contract),
            "generated_by": actor,
            "notes": "Generated when the employment contract was activated.",
        },
    )
    return letter


@transaction.atomic
def add_contract_allowance_version(*, contract, component, amount, effective_date, end_date, actor, reason):
    """Append an audited allowance version without rewriting active history."""
    contract = EmploymentContract.objects.select_for_update(of=("self",)).select_related(
        "employee", "salary_package"
    ).get(pk=contract.pk)
    if contract.status != EmploymentContract.Status.ACTIVE:
        raise ValidationError("Allowance versioning is available only for active contracts.")
    if effective_date < timezone.localdate():
        raise ValidationError("An active-contract allowance change cannot be backdated.")

    versions = ContractAllowance.objects.select_for_update().filter(
        contract=contract,
        component=component,
    )
    if versions.filter(effective_date__gte=effective_date).exists():
        raise ValidationError("A current or future allowance version already exists for this component.")
    previous = (
        versions.filter(active=True, effective_date__lt=effective_date)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=effective_date))
        .order_by("-effective_date", "-pk")
        .first()
    )
    previous_amount = previous.amount if previous else Decimal("0.00")
    if previous:
        previous.end_date = effective_date - timedelta(days=1)
        previous.full_clean()
        previous.save(update_fields=["end_date", "updated_at"])

    allowance = ContractAllowance(
        contract=contract,
        component=component,
        amount=amount,
        effective_date=effective_date,
        end_date=end_date,
        active=True,
    )
    allowance.full_clean()
    allowance.save()
    ContractAllowanceHistory.objects.create(
        contract=contract,
        allowance=allowance,
        previous_amount=previous_amount,
        new_amount=amount,
        effective_date=effective_date,
        changed_by=actor,
        reason=reason,
    )
    refresh_contract_compensation(contract, actor, effective_date=effective_date)
    return allowance


@transaction.atomic
def transition_employment_contract(contract, action, actor=None):
    """Change one locked contract after checking dates and employee overlap."""
    transition = CONTRACT_TRANSITIONS.get(action)
    if transition is None or contract.status not in transition[0]:
        raise ValidationError("That contract action is not available for the current status.")

    if action in {"submit", "activate"}:
        employee = Employee.objects.select_for_update().get(pk=contract.employee_id)
        contract.full_clean()
        if employee.status != Employee.Status.ACTIVE:
            raise ValidationError("An inactive employee cannot have a new active contract.")
        if contract.end_date and contract.end_date < timezone.localdate():
            raise ValidationError("A contract that has already ended cannot be activated.")
        overlaps = EmploymentContract.objects.filter(
            employee_id=contract.employee_id,
            status=EmploymentContract.Status.ACTIVE,
        ).exclude(pk=contract.pk).filter(Q(end_date__isnull=True) | Q(end_date__gte=contract.start_date))
        if contract.end_date:
            overlaps = overlaps.filter(start_date__lte=contract.end_date)
        if overlaps.exists():
            raise ValidationError("This employee already has an active contract for the same date range.")
    elif action == "expire":
        if not contract.end_date or contract.end_date > timezone.localdate():
            raise ValidationError("A contract can expire only on or after its end date.")

    contract.status = transition[1]
    contract.save(update_fields=["status", "updated_at"])
    if action == "activate":
        compensation = refresh_contract_compensation(contract, actor)
        letter = generate_contract_letter(contract, actor) if actor is not None else None
        contract._workflow_audit_details = {
            "compensation_benefit_id": compensation.pk,
            "contract_letter_id": letter.pk if letter else None,
        }
    return contract
