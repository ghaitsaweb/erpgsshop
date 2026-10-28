"""Purpose-built, scoped workspace for an employment-contract record."""

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header

from modules.approval_workflow.models import ApprovalInstance
from modules.approval_workflow.services import can_user_act

from .contracts import add_contract_allowance_version, generate_contract_letter, refresh_contract_compensation
from .forms import (
    ContractAllowanceWorkspaceForm,
    ContractAttachmentForm,
    ContractOvertimeComponentForm,
    EmploymentContractForm,
)
from .models import (
    CompensationBenefit,
    ContractAllowance,
    ContractAttachment,
    ContractOvertimeComponent,
    HRWorkflowAudit,
    PayrollRun,
    Payslip,
    SalaryComponent,
)


CONTRACT_TABS = (
    ("overview", "Overview"),
    ("compensation", "Compensation"),
    ("allowances", "Allowances"),
    ("overtime", "Overtime"),
    ("letters", "Letters"),
    ("attachments", "Attachments"),
    ("approval", "Approval"),
    ("payroll", "Payroll impact"),
    ("audit", "Audit trail"),
)
TAB_NAMES = {key for key, _label in CONTRACT_TABS}


def _workspace_url(contract, tab):
    base = reverse("entity_edit", kwargs={"entity": "contracts", "pk": contract.pk})
    return f"{base}?{urlencode({'tab': tab})}"


def contract_create(request, config, *, scope_form):
    """Create a draft contract in the same structured workspace language as detail."""
    form = EmploymentContractForm(request.POST or None, request.FILES or None)
    scope_form(form)
    if form.is_valid():
        with transaction.atomic():
            contract = form.save()
            _audit(contract, request.user, "create", {"source": "contract_create_workspace"})
        messages.success(request, "Draft employment contract created. Add its related terms, then submit it for approval.")
        return redirect(_workspace_url(contract, "overview"))
    return render(
        request,
        "hr/contract_workspace.html",
        {
            "config": config,
            "contract_form": form,
            "tabs": CONTRACT_TABS,
            "active_tab": "overview",
            "is_new": True,
        },
    )


def _audit(contract, actor, action, details=None):
    """Retain non-workflow Contract workspace changes in the same HR audit trail."""
    HRWorkflowAudit.objects.create(
        company_id=contract.employee.company_id,
        entity="contracts",
        record_id=contract.pk,
        action=action,
        previous_status=contract.status,
        new_status=contract.status,
        actor=actor,
        details=details or {},
    )


def _form_context(contract, scope_form, bound_forms=None):
    """Build company-scoped child forms, optionally retaining validation errors."""
    bound_forms = bound_forms or {}
    effective_date = timezone.localdate() if contract.status == "active" else contract.start_date
    contract_form = bound_forms.get("contract_form") or EmploymentContractForm(instance=contract)
    allowance_form = bound_forms.get("allowance_form") or ContractAllowanceWorkspaceForm(
        instance=ContractAllowance(contract=contract),
        prefix="allowance",
        initial={"effective_date": effective_date, "active": True},
    )
    overtime_form = bound_forms.get("overtime_form") or ContractOvertimeComponentForm(
        instance=ContractOvertimeComponent(contract=contract),
        prefix="overtime",
        initial={"effective_date": effective_date, "active": True, "rate_multiplier": 1},
    )
    attachment_form = bound_forms.get("attachment_form") or ContractAttachmentForm(prefix="attachment")

    for form in (contract_form, allowance_form, overtime_form, attachment_form):
        scope_form(form)

    earning_components = SalaryComponent.objects.filter(
        company_id=contract.employee.company_id,
        component_type=SalaryComponent.ComponentType.EARNING,
        active=True,
    )
    allowance_form.fields["component"].queryset = earning_components
    overtime_form.fields["component"].queryset = earning_components
    return {
        "contract_form": contract_form,
        "allowance_form": allowance_form,
        "overtime_form": overtime_form,
        "attachment_form": attachment_form,
    }


def _approval_level_progress(approval):
    if approval is None:
        return []
    actions = list(approval.actions.all())
    user_ids = {
        user_id
        for level in approval.approval_levels_snapshot or []
        for key in ("resolved_user_ids", "resolved_delegate_user_ids")
        for user_id in level.get(key, [])
    }
    names = {
        user.pk: user.get_full_name() or user.get_username()
        for user in get_user_model().objects.filter(pk__in=user_ids).only(
            "id", "username", "first_name", "last_name"
        )
    }
    progress = []
    for index, level in enumerate(approval.approval_levels_snapshot or []):
        approved_count = len(
            {
                action.effective_approver_id or action.performed_by_id
                for action in actions
                if action.level == index and action.action in {"approve", "master_approve", "auto_approve"}
            }
        )
        rejected = any(
            action.level == index and action.action in {"reject", "master_reject", "auto_reject"}
            for action in actions
        )
        if rejected:
            state = "rejected"
        elif approval.status == ApprovalInstance.Status.APPROVED or index < approval.current_level:
            state = "approved"
        elif approval.status == ApprovalInstance.Status.PENDING and index == approval.current_level:
            state = "pending"
        elif approval.status == ApprovalInstance.Status.REJECTED and index == approval.current_level:
            state = "rejected"
        else:
            state = "waiting"
        progress.append(
            {
                "number": index + 1,
                "name": level.get("name") or level.get("layer_name") or f"Level {index + 1}",
                "state": state,
                "approved_count": approved_count,
                "minimum_approvals": int(level.get("minimum_approvals", 1)),
                "approvers": [names.get(pk, f"User #{pk}") for pk in level.get("resolved_user_ids", [])],
                "delegates": [
                    names.get(pk, f"User #{pk}") for pk in level.get("resolved_delegate_user_ids", [])
                ],
            }
        )
    return progress


def contract_workspace(request, config, scoped_queryset, pk, *, can_change, can_workflow, scope_form):
    """Render and mutate only the related records belonging to one visible Contract."""
    contract = get_object_or_404(
        scoped_queryset.select_related("employee__company", "salary_package", "letter_template"),
        pk=pk,
    )
    tab = request.GET.get("tab", "overview")
    if tab not in TAB_NAMES:
        tab = "overview"
    bound_forms = {}

    if request.method == "POST":
        if not can_change:
            messages.error(request, "You do not have permission to change this employment contract.")
            return redirect(_workspace_url(contract, tab))
        action = request.POST.get("workspace_action", "")
        if not action:
            messages.error(request, "Contract terms are locked outside the Contract workspace actions.")
            return redirect(_workspace_url(contract, tab))
        try:
            if action == "save_contract":
                if contract.status != "draft":
                    raise ValidationError("Only a draft contract can be edited. Use the contract workflow for lifecycle changes.")
                with transaction.atomic():
                    locked = get_object_or_404(
                        scoped_queryset.select_for_update().select_related("employee__company"), pk=pk
                    )
                    form = EmploymentContractForm(request.POST, request.FILES, instance=locked)
                    scope_form(form)
                    if form.is_valid():
                        updated = form.save()
                        _audit(updated, request.user, "edit", {"source": "contract_workspace"})
                        messages.success(request, "Employment contract updated.")
                        return redirect(_workspace_url(updated, "overview"))
                bound_forms["contract_form"] = form
                tab = "overview"
            elif action == "add_allowance":
                if contract.status not in {"draft", "active"}:
                    raise ValidationError("Allowances can only be changed while the contract is draft or active.")
                form = ContractAllowanceWorkspaceForm(
                    request.POST,
                    instance=ContractAllowance(contract=contract),
                    prefix="allowance",
                )
                scope_form(form)
                form.fields["component"].queryset = SalaryComponent.objects.filter(
                    company_id=contract.employee.company_id,
                    component_type=SalaryComponent.ComponentType.EARNING,
                    active=True,
                )
                if form.is_valid():
                    with transaction.atomic():
                        if contract.status == "active":
                            allowance = add_contract_allowance_version(
                                contract=contract,
                                component=form.cleaned_data["component"],
                                amount=form.cleaned_data["amount"],
                                effective_date=form.cleaned_data["effective_date"],
                                end_date=form.cleaned_data["end_date"],
                                actor=request.user,
                                reason=form.cleaned_data["change_reason"],
                            )
                        else:
                            allowance = form.save(commit=False)
                            allowance.contract = contract
                            allowance.full_clean()
                            allowance.save()
                            _audit(contract, request.user, "add_allowance", {"allowance_id": allowance.pk})
                    messages.success(request, "Allowance version added to the contract.")
                    return redirect(_workspace_url(contract, "allowances"))
                bound_forms["allowance_form"] = form
                tab = "allowances"
            elif action == "add_overtime_component":
                if contract.status not in {"draft", "active"}:
                    raise ValidationError("Overtime components can only be changed while the contract is draft or active.")
                form = ContractOvertimeComponentForm(
                    request.POST,
                    instance=ContractOvertimeComponent(contract=contract),
                    prefix="overtime",
                )
                scope_form(form)
                form.fields["component"].queryset = SalaryComponent.objects.filter(
                    company_id=contract.employee.company_id,
                    component_type=SalaryComponent.ComponentType.EARNING,
                    active=True,
                )
                if form.is_valid():
                    with transaction.atomic():
                        overtime_component = form.save(commit=False)
                        overtime_component.contract = contract
                        overtime_component.full_clean()
                        overtime_component.save()
                        _audit(
                            contract,
                            request.user,
                            "add_overtime_component",
                            {"overtime_component_id": overtime_component.pk},
                        )
                    messages.success(request, "Overtime component added to the contract.")
                    return redirect(_workspace_url(contract, "overtime"))
                bound_forms["overtime_form"] = form
                tab = "overtime"
            elif action == "add_attachment":
                form = ContractAttachmentForm(request.POST, request.FILES, prefix="attachment")
                scope_form(form)
                if form.is_valid():
                    with transaction.atomic():
                        attachment = form.save(commit=False)
                        attachment.contract = contract
                        attachment.uploaded_by = request.user
                        attachment.full_clean()
                        attachment.save()
                        _audit(contract, request.user, "add_attachment", {"attachment_id": attachment.pk})
                    messages.success(request, "Contract attachment added.")
                    return redirect(_workspace_url(contract, "attachments"))
                bound_forms["attachment_form"] = form
                tab = "attachments"
            elif action == "refresh_compensation":
                if contract.status != "active":
                    raise ValidationError("Compensation can be refreshed only after the contract is active.")
                with transaction.atomic():
                    snapshot = refresh_contract_compensation(contract, request.user, timezone.localdate())
                    _audit(
                        contract,
                        request.user,
                        "refresh_compensation",
                        {"compensation_benefit_id": snapshot.pk, "effective_date": snapshot.effective_date.isoformat()},
                    )
                messages.success(request, "Current compensation snapshot refreshed.")
                return redirect(_workspace_url(contract, "compensation"))
            elif action == "generate_letter":
                if contract.status != "active":
                    raise ValidationError("A contract letter can be generated only after activation.")
                if not contract.letter_template_id:
                    raise ValidationError("Select a contract letter template before generating a letter.")
                with transaction.atomic():
                    letter = generate_contract_letter(contract, request.user)
                    _audit(contract, request.user, "generate_letter", {"contract_letter_id": letter.pk})
                messages.success(request, "Contract letter is available in the Letters tab.")
                return redirect(_workspace_url(contract, "letters"))
            elif action == "link_payslip":
                if contract.status not in {"active", "expired", "terminated"}:
                    raise ValidationError("A payslip can be linked only to an effective or historical contract.")
                try:
                    payslip_id = int(request.POST.get("payslip_id", ""))
                except (TypeError, ValueError) as exc:
                    raise ValidationError("Choose an eligible unlinked payslip.") from exc
                with transaction.atomic():
                    payslip = get_object_or_404(
                        Payslip.objects.select_for_update()
                        .filter(
                            pk=payslip_id,
                            employee_id=contract.employee_id,
                            contract__isnull=True,
                            period_end__gte=contract.start_date,
                        )
                        .filter(
                            Q(period_start__lte=contract.end_date)
                            if contract.end_date
                            else Q()
                        ),
                        pk=payslip_id,
                    )
                    payslip.contract = contract
                    payslip.full_clean()
                    payslip.save(update_fields=["contract", "updated_at"])
                    _audit(contract, request.user, "link_payslip", {"payslip_id": payslip.pk})
                messages.success(request, f"Payslip {payslip.number} is now linked to this contract.")
                return redirect(_workspace_url(contract, "payroll"))
            else:
                raise ValidationError("Unsupported Contract workspace action.")
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))

    form_context = _form_context(contract, scope_form, bound_forms)
    compensation_snapshots = list(
        contract.compensation_snapshots.select_related("approver").prefetch_related("component_lines__component")
    )
    allowances = list(contract.allowance_lines.select_related("component"))
    overtime_components = list(contract.overtime_components.select_related("component"))
    letters = list(contract.letters.select_related("template", "generated_by"))
    attachments = list(contract.attachments.select_related("uploaded_by"))
    allowance_history = list(contract.allowance_history.select_related("allowance__component", "changed_by"))
    audits = list(
        HRWorkflowAudit.objects.filter(entity="contracts", record_id=contract.pk)
        .select_related("actor")
        .order_by("-occurred_at", "-pk")
    )
    content_type = ContentType.objects.get_for_model(contract, for_concrete_model=False)
    approvals = list(
        ApprovalInstance.objects.filter(content_type=content_type, object_id=contract.pk)
        .select_related("rule", "submitted_by")
        .prefetch_related("actions__performed_by", "actions__effective_approver")
        .order_by("-submitted_at", "-pk")
    )
    approval = approvals[0] if approvals else None
    approval_can_act = bool(approval and can_user_act(approval, request.user))
    actions = []
    for key, definition in config.get("workflow", {}).items():
        if contract.status not in definition["from"]:
            continue
        if key in {"approve", "reject"}:
            allowed = approval_can_act
        else:
            allowed = can_workflow(key, contract)
        if allowed:
            actions.append({"key": key, **definition})

    impact_end = contract.end_date or timezone.localdate()
    linked_payslips = list(
        Payslip.objects.filter(contract=contract)
        .select_related("payroll_run")
        .order_by("-period_end", "-pk")
    )
    linkable_payslips = list(
        Payslip.objects.filter(
            employee_id=contract.employee_id,
            contract__isnull=True,
            period_end__gte=contract.start_date,
        )
        .filter(period_start__lte=impact_end)
        .select_related("payroll_run")
        .order_by("-period_end", "-pk")
    )
    linked_payroll_run_ids = {payslip.payroll_run_id for payslip in linked_payslips if payslip.payroll_run_id}
    linked_payroll_runs = list(
        PayrollRun.objects.filter(pk__in=linked_payroll_run_ids)
        .select_related("period")
        .order_by("-period__date_end", "-pk")
    )
    period_matched_payroll_runs = list(
        PayrollRun.objects.filter(
            company_id=contract.employee.company_id,
            period__date_end__gte=contract.start_date,
            period__date_start__lte=impact_end,
        )
        .exclude(pk__in=linked_payroll_run_ids)
        .select_related("period")
        .order_by("-period__date_end", "-pk")
    )
    days_to_expiry = (contract.end_date - timezone.localdate()).days if contract.end_date else None
    return render(
        request,
        "hr/contract_workspace.html",
        {
            "config": config,
            "contract": contract,
            "tabs": CONTRACT_TABS,
            "active_tab": tab,
            "can_change": can_change,
            "can_manage_terms": can_change and contract.status in {"draft", "active"},
            "can_edit_contract": can_change and contract.status == "draft",
            "workflow_actions": actions,
            "compensation_snapshots": compensation_snapshots,
            "allowances": allowances,
            "allowance_history": allowance_history,
            "overtime_components": overtime_components,
            "letters": letters,
            "attachments": attachments,
            "approval": approval,
            "approval_levels": _approval_level_progress(approval),
            "approval_can_act": approval_can_act,
            "approvals": approvals,
            "payslips": linked_payslips,
            "linkable_payslips": linkable_payslips,
            "can_link_payslips": can_change and contract.status in {"active", "expired", "terminated"},
            "payroll_runs": linked_payroll_runs,
            "period_matched_payroll_runs": period_matched_payroll_runs,
            "audits": audits,
            "days_to_expiry": days_to_expiry,
            **form_context,
        },
    )


def contract_attachment_download(request, scoped_contract_queryset, contract_pk, attachment_pk):
    """Serve a stored Contract attachment only after the Contract scope is checked."""
    contract = get_object_or_404(scoped_contract_queryset, pk=contract_pk)
    attachment = get_object_or_404(
        contract.attachments.only("id", "attachment_name", "attachment_content_type", "attachment_content"),
        pk=attachment_pk,
    )
    if not attachment.attachment_name or not attachment.attachment_content:
        raise Http404("Stored attachment not found.")
    response = HttpResponse(
        bytes(attachment.attachment_content),
        content_type=attachment.attachment_content_type or "application/octet-stream",
    )
    response["Content-Disposition"] = content_disposition_header(True, attachment.attachment_name)
    response["X-Content-Type-Options"] = "nosniff"
    return response
