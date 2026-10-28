"""Snapshot employee lifecycle checklists and record verified task completion."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from modules.organization.context import access_scope

from .access import FULL_HR_GROUPS
from .models import (
    ApprovalStatus,
    EmployeeChecklist,
    EmployeeDocument,
    EmployeeLifecycleTask,
    EmployeeOffboarding,
    EmployeeOnboarding,
    HRWorkflowAudit,
)


def _process_spec(process):
    if isinstance(process, EmployeeOnboarding):
        return "onboarding", EmployeeChecklist.Lifecycle.ONBOARDING
    if isinstance(process, EmployeeOffboarding):
        return "offboarding", EmployeeChecklist.Lifecycle.OFFBOARDING
    raise ValidationError("The record is not an employee lifecycle process.")


def _authorize(process, actor):
    if actor.is_superuser:
        return
    if not actor.groups.filter(name__in=FULL_HR_GROUPS).exists():
        raise ValidationError("Only an HR manager can operate employee lifecycle checklists.")
    if process.employee.company_id not in access_scope(actor)[0]:
        raise ValidationError("The employee is outside your company scope.")


def _task_totals(process, relation_field):
    return EmployeeLifecycleTask.objects.filter(**{relation_field: process}).order_by().aggregate(
        total=Count("pk"), completed=Count("pk", filter=Q(status=EmployeeLifecycleTask.Status.COMPLETED))
    )


@transaction.atomic
def transition_lifecycle_process(record, action, actor):
    relation_field, lifecycle = _process_spec(record)
    process = record.__class__.objects.select_for_update().select_related("employee").get(pk=record.pk)
    _authorize(process, actor)
    previous_status = process.status
    if action == "submit":
        if process.status not in {ApprovalStatus.DRAFT, ApprovalStatus.SUBMITTED}:
            raise ValidationError("Only a draft or uninitialized process can start a checklist.")
        existing = _task_totals(process, relation_field)
        if existing["total"] == 0:
            templates = EmployeeChecklist.objects.filter(
                company_id=process.employee.company_id, lifecycle=lifecycle, active=True
            ).order_by("sequence", "pk")
            batch = []
            created = 0
            for template in templates.iterator(chunk_size=200):
                batch.append(EmployeeLifecycleTask(
                    **{relation_field: process},
                    template=template,
                    name=template.name,
                    sequence=template.sequence,
                    requires_evidence=template.requires_evidence,
                ))
                if len(batch) == 200:
                    EmployeeLifecycleTask.objects.bulk_create(batch, batch_size=200)
                    created += len(batch)
                    batch = []
            if batch:
                EmployeeLifecycleTask.objects.bulk_create(batch, batch_size=200)
                created += len(batch)
            if created == 0:
                raise ValidationError("Configure an active company checklist before starting this process.")
        process.status = ApprovalStatus.SUBMITTED
        totals = _task_totals(process, relation_field)
        process.progress = totals["completed"] * 100 // totals["total"]
        process.save(update_fields=["status", "progress", "updated_at"])
    elif action == "complete":
        if process.status != ApprovalStatus.SUBMITTED:
            raise ValidationError("Start the checklist before completing this process.")
        totals = _task_totals(process, relation_field)
        if not totals["total"] or totals["completed"] != totals["total"]:
            raise ValidationError("Complete every checklist task before finalizing this process.")
        process.status = ApprovalStatus.APPROVED
        process.progress = 100
        process.save(update_fields=["status", "progress", "updated_at"])
    elif action == "cancel":
        if process.status not in {ApprovalStatus.DRAFT, ApprovalStatus.SUBMITTED}:
            raise ValidationError("Only an unfinished process can be cancelled.")
        process.status = ApprovalStatus.CANCELLED
        process.save(update_fields=["status", "updated_at"])
    else:
        raise ValidationError("Unsupported lifecycle checklist action.")
    process._workflow_audit_details = {
        "task_total": _task_totals(process, relation_field)["total"],
        "previous_status": previous_status,
    }
    return process


@transaction.atomic
def complete_lifecycle_task(record, actor, *, evidence_document_id=None):
    parent_ids = EmployeeLifecycleTask.objects.filter(pk=record.pk).values(
        "onboarding_id", "offboarding_id"
    ).get()
    relation_field = "onboarding" if parent_ids["onboarding_id"] else "offboarding"
    model = EmployeeOnboarding if relation_field == "onboarding" else EmployeeOffboarding
    process = model.objects.select_for_update().select_related("employee").get(pk=parent_ids[f"{relation_field}_id"])
    task = EmployeeLifecycleTask.objects.select_for_update().select_related("template").get(pk=record.pk)
    if getattr(task, f"{relation_field}_id") != process.pk:
        raise ValidationError("The checklist task changed while completing it.")
    _authorize(process, actor)
    if process.status != ApprovalStatus.SUBMITTED:
        raise ValidationError("The employee lifecycle process is not active.")
    if task.status == EmployeeLifecycleTask.Status.COMPLETED:
        return task
    if task.template.company_id != process.employee.company_id:
        raise ValidationError("The employee company changed after the checklist was started.")
    document = None
    if evidence_document_id:
        if not str(evidence_document_id).isdecimal():
            raise ValidationError("Select a valid employee evidence document.")
        document = EmployeeDocument.objects.select_for_update().defer("attachment_content").filter(
            pk=int(evidence_document_id), employee_id=process.employee_id,
            status=EmployeeDocument.Status.ACTIVE, verified=True,
        ).filter(Q(expiry_date__isnull=True) | Q(expiry_date__gt=timezone.localdate())).first()
        if document is None or not (document.attachment_url or document.attachment_name):
            raise ValidationError("Evidence must be an active, verified, unexpired document with an attachment.")
    if task.requires_evidence and document is None:
        raise ValidationError("This checklist task requires a verified employee document.")
    task.evidence_document = document
    task.evidence_document_number = document.document_number if document else ""
    task.evidence_attachment_url = (
        (reverse("employee_document_attachment", args=[document.pk]) if document.attachment_name
         else document.attachment_url) if document else ""
    )
    task.status = EmployeeLifecycleTask.Status.COMPLETED
    task.completed_by = actor
    task.completed_at = timezone.now()
    task.save(update_fields=[
        "evidence_document", "evidence_document_number", "evidence_attachment_url",
        "status", "completed_by", "completed_at", "updated_at",
    ])
    totals = _task_totals(process, relation_field)
    if not totals["total"]:
        raise ValidationError("The checklist has no tasks.")
    process.progress = totals["completed"] * 100 // totals["total"]
    if totals["completed"] == totals["total"]:
        process.status = ApprovalStatus.APPROVED
        process.save(update_fields=["progress", "status", "updated_at"])
        HRWorkflowAudit.objects.create(
            company_id=process.employee.company_id,
            entity="employee-onboarding" if relation_field == "onboarding" else "employee-offboarding",
            record_id=process.pk,
            action="auto-complete",
            previous_status=ApprovalStatus.SUBMITTED,
            new_status=ApprovalStatus.APPROVED,
            actor=actor,
            details={"task_id": task.pk, "task_total": totals["total"]},
        )
    else:
        process.save(update_fields=["progress", "updated_at"])
    task._workflow_audit_details = {
        "evidence_document_id": document.pk if document else None,
        "process_id": process.pk,
        "progress": process.progress,
    }
    return task
