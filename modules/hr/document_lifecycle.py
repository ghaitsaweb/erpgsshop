"""Guard employee-document transitions and retain an explicit status history."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Employee
from modules.organization.context import access_scope

from .access import FULL_HR_GROUPS
from .models import EmployeeDocument, EmployeeDocumentHistory


def _authorize(document, actor):
    if actor.is_superuser:
        return
    if not actor.groups.filter(name__in=FULL_HR_GROUPS).exists():
        raise ValidationError("Only an HR manager can verify employee documents.")
    if document.employee.company_id not in access_scope(actor)[0]:
        raise ValidationError("The document is outside your company scope.")


@transaction.atomic
def transition_employee_document(record, action, actor, *, replacement_document_id=None, note=""):
    employee_id = EmployeeDocument.objects.filter(pk=record.pk).values_list("employee_id", flat=True).get()
    Employee.objects.select_for_update().get(pk=employee_id)
    document = EmployeeDocument.objects.select_for_update(of=("self",)).defer("attachment_content").select_related(
        "employee", "document_type"
    ).get(pk=record.pk)
    _authorize(document, actor)
    if document.document_type.company_id != document.employee.company_id:
        raise ValidationError("The document type belongs to another company.")
    if not document.document_type.active:
        raise ValidationError("The document type is inactive.")
    if document.issued_date and document.issued_date > timezone.localdate():
        raise ValidationError("The document issue date cannot be in the future.")
    if document.issued_date and document.expiry_date and document.expiry_date <= document.issued_date:
        raise ValidationError("The document expiry date must be after its issue date.")
    if document.document_type.has_expiry and not document.expiry_date:
        raise ValidationError("This document type requires an expiry date.")
    previous_status = document.status
    if action == "verify":
        if document.status != EmployeeDocument.Status.DRAFT:
            raise ValidationError("Only a draft document can be verified.")
        if not (document.attachment_url or document.attachment_name):
            raise ValidationError("Attach the document before HR verification.")
        if document.expiry_date and document.expiry_date <= timezone.localdate():
            raise ValidationError("An expired document cannot be verified.")
        if not document.document_type.allow_multiple_versions and EmployeeDocument.objects.filter(
            employee_id=document.employee_id,
            document_type_id=document.document_type_id,
            status=EmployeeDocument.Status.ACTIVE,
            verified=True,
        ).exclude(pk=document.pk).exists():
            raise ValidationError("Replace the existing active document instead of verifying another version.")
        document.status = EmployeeDocument.Status.ACTIVE
        document.verified = True
        update_fields = ["status", "verified", "updated_at"]
    elif action == "expire":
        if document.status != EmployeeDocument.Status.ACTIVE:
            raise ValidationError("Only an active document can expire.")
        if not document.expiry_date or document.expiry_date > timezone.localdate():
            raise ValidationError("The document expiry date has not arrived.")
        document.status = EmployeeDocument.Status.EXPIRED
        update_fields = ["status", "updated_at"]
    elif action == "replace":
        if document.status != EmployeeDocument.Status.ACTIVE or document.replaced_by_id:
            raise ValidationError("Only an active latest document can be replaced.")
        if document.document_type.allow_multiple_versions:
            raise ValidationError("This document type allows independent versions; verify the new draft directly.")
        if not replacement_document_id or not str(replacement_document_id).isdecimal():
            raise ValidationError("Select a draft replacement document.")
        if EmployeeDocument.objects.filter(
            employee_id=document.employee_id,
            document_type_id=document.document_type_id,
            status=EmployeeDocument.Status.ACTIVE,
            verified=True,
        ).exclude(pk=document.pk).exists():
            raise ValidationError("Another active version makes this replacement ambiguous.")
        replacement = EmployeeDocument.objects.select_for_update(of=("self",)).defer("attachment_content").filter(
            pk=int(replacement_document_id), employee_id=document.employee_id,
            document_type_id=document.document_type_id,
            status=EmployeeDocument.Status.DRAFT, verified=False,
            parent_document__isnull=True,
        ).exclude(pk=document.pk).first()
        if replacement is None or not (replacement.attachment_url or replacement.attachment_name) or (
            replacement.expiry_date and replacement.expiry_date <= timezone.localdate()
        ):
            raise ValidationError("The replacement must be an attached, unexpired draft for this employee and type.")
        if replacement.issued_date and replacement.issued_date > timezone.localdate():
            raise ValidationError("The replacement issue date cannot be in the future.")
        if replacement.issued_date and replacement.expiry_date and replacement.expiry_date <= replacement.issued_date:
            raise ValidationError("The replacement expiry date must be after its issue date.")
        if document.document_type.has_expiry and not replacement.expiry_date:
            raise ValidationError("The replacement requires an expiry date.")
        replacement.status = EmployeeDocument.Status.ACTIVE
        replacement.verified = True
        replacement.parent_document = document
        replacement.version = document.version + 1
        replacement.save(update_fields=["status", "verified", "parent_document", "version", "updated_at"])
        EmployeeDocumentHistory.objects.create(
            document=replacement,
            action="replacement_verified",
            previous_status=EmployeeDocument.Status.DRAFT,
            new_status=EmployeeDocument.Status.ACTIVE,
            actor=actor,
            note=f"Replaces document {document.pk}",
        )
        document.status = EmployeeDocument.Status.REPLACED
        document.replaced_by = replacement
        update_fields = ["status", "replaced_by", "updated_at"]
    elif action == "archive":
        if document.status == EmployeeDocument.Status.ARCHIVED:
            raise ValidationError("The document is already archived.")
        document.status = EmployeeDocument.Status.ARCHIVED
        update_fields = ["status", "updated_at"]
    elif action == "unarchive":
        if document.status != EmployeeDocument.Status.ARCHIVED:
            raise ValidationError("Only an archived document can be restored.")
        archived_from = document.history.filter(action="archive").order_by("-pk").values_list(
            "previous_status", flat=True
        ).first()
        if archived_from not in {
            EmployeeDocument.Status.DRAFT, EmployeeDocument.Status.ACTIVE,
            EmployeeDocument.Status.EXPIRED, EmployeeDocument.Status.REPLACED,
        }:
            raise ValidationError("The archived document has no valid previous state.")
        restored_status = archived_from
        if archived_from == EmployeeDocument.Status.ACTIVE and (
            document.expiry_date and document.expiry_date <= timezone.localdate()
        ):
            restored_status = EmployeeDocument.Status.EXPIRED
        if restored_status == EmployeeDocument.Status.ACTIVE and not document.document_type.allow_multiple_versions:
            if EmployeeDocument.objects.filter(
                employee_id=document.employee_id,
                document_type_id=document.document_type_id,
                status=EmployeeDocument.Status.ACTIVE,
                verified=True,
            ).exclude(pk=document.pk).exists():
                raise ValidationError("Another active version must be archived before restoring this document.")
        document.status = restored_status
        update_fields = ["status", "updated_at"]
    else:
        raise ValidationError("Unsupported document lifecycle action.")
    document.save(update_fields=update_fields)
    EmployeeDocumentHistory.objects.create(
        document=document,
        action=action,
        previous_status=previous_status,
        new_status=document.status,
        actor=actor,
        note=(note or "")[:2000],
    )
    document._workflow_audit_details = {
        "history_action": action,
        "replacement_document_id": document.replaced_by_id,
    }
    return document


def _expire_employee_document_batch(*, today, batch_size):
    """Expire one locked page and return its size."""
    with transaction.atomic():
        document_ids = list(
            EmployeeDocument.objects.select_for_update(skip_locked=True)
            .filter(status=EmployeeDocument.Status.ACTIVE, expiry_date__lte=today)
            .order_by("expiry_date", "pk")
            .values_list("pk", flat=True)[:batch_size]
        )
        if not document_ids:
            return 0
        now = timezone.now()
        changed = EmployeeDocument.objects.filter(
            pk__in=document_ids, status=EmployeeDocument.Status.ACTIVE
        ).update(status=EmployeeDocument.Status.EXPIRED, updated_at=now)
        if changed != len(document_ids):
            raise ValidationError("A document changed while the expiry batch was running.")
        EmployeeDocumentHistory.objects.bulk_create([
            EmployeeDocumentHistory(
                document_id=document_id,
                action="auto_expire",
                previous_status=EmployeeDocument.Status.ACTIVE,
                new_status=EmployeeDocument.Status.EXPIRED,
                actor=None,
                note=f"Automatic expiry on {today.isoformat()}",
            )
            for document_id in document_ids
        ], batch_size=batch_size)
        return changed


def expire_employee_documents(*, today=None, batch_size=500, max_batches=20):
    """Expire every due active document in bounded, retry-safe transactions."""
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    if not 1 <= max_batches <= 100:
        raise ValueError("max_batches must be between 1 and 100")
    today = today or timezone.localdate()
    touched = 0
    for _batch in range(max_batches):
        changed = _expire_employee_document_batch(today=today, batch_size=batch_size)
        touched += changed
        if not changed:
            break
    return touched
