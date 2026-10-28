from celery import shared_task
from django.utils import timezone

from .leave_buckets import expire_leave_buckets
from .document_lifecycle import expire_employee_documents
from .models import EmployeeDocument, LeaveBucket
from .payroll_runs import (
    mark_payroll_processing_failed,
    process_payroll_generation_batch,
    process_payroll_preview_batch,
)
from .payroll_payments import mark_payroll_disbursement_failed, prepare_payroll_disbursement_batch


EXPIRY_BATCH_SIZE = 500
EXPIRY_MAX_BATCHES = 20


@shared_task(name="hr.expire_leave_buckets", ignore_result=True)
def expire_leave_buckets_task():
    touched = expire_leave_buckets(batch_size=EXPIRY_BATCH_SIZE, max_batches=EXPIRY_MAX_BATCHES)
    if touched == EXPIRY_BATCH_SIZE * EXPIRY_MAX_BATCHES and LeaveBucket.objects.filter(
        status=LeaveBucket.Status.ACTIVE, expiry_date__lt=timezone.localdate()
    ).exists():
        expire_leave_buckets_task.apply_async(countdown=10)
    return touched


@shared_task(name="hr.expire_employee_documents", ignore_result=True)
def expire_employee_documents_task():
    touched = expire_employee_documents(batch_size=EXPIRY_BATCH_SIZE, max_batches=EXPIRY_MAX_BATCHES)
    if touched == EXPIRY_BATCH_SIZE * EXPIRY_MAX_BATCHES and EmployeeDocument.objects.filter(
        status=EmployeeDocument.Status.ACTIVE, expiry_date__lte=timezone.localdate()
    ).exists():
        expire_employee_documents_task.apply_async(countdown=10)
    return touched


@shared_task(name="hr.process_payroll_preview", ignore_result=True)
def process_payroll_preview_task(run_id):
    try:
        process_payroll_preview_batch(run_id)
    except Exception as exc:
        mark_payroll_processing_failed(run_id, exc)
        raise


@shared_task(name="hr.process_payroll_generation", ignore_result=True)
def process_payroll_generation_task(run_id):
    try:
        process_payroll_generation_batch(run_id)
    except Exception as exc:
        mark_payroll_processing_failed(run_id, exc)
        raise


@shared_task(name="hr.prepare_payroll_disbursement", ignore_result=True)
def prepare_payroll_disbursement_task(disbursement_id):
    try:
        prepare_payroll_disbursement_batch(disbursement_id)
    except Exception as exc:
        mark_payroll_disbursement_failed(disbursement_id, exc)
        raise
