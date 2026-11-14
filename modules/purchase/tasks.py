from celery import shared_task
from django.utils import timezone

from .models import PurchaseSetting, VendorPricelist, VendorPricelistImportLog, VendorProfile
from .services import (
    MAX_VENDOR_PRICELIST_EXPIRY_BATCH,
    expire_due_vendor_pricelists,
    process_vendor_pricelist_import,
    recalculate_vendor_performance,
    refresh_vendor_legal_document_expiry_statuses,
)
from .vendor_transaction_history import sync_vendor_transaction_history_page


MAX_EXPIRY_BATCHES_PER_TASK = 20


@shared_task(name="purchase.expire_vendor_pricelists", ignore_result=True)
def expire_vendor_pricelists_task():
    total = 0
    for _batch in range(MAX_EXPIRY_BATCHES_PER_TASK):
        touched = expire_due_vendor_pricelists()
        total += touched
        if touched < MAX_VENDOR_PRICELIST_EXPIRY_BATCH:
            break
    if total == MAX_VENDOR_PRICELIST_EXPIRY_BATCH * MAX_EXPIRY_BATCHES_PER_TASK and (
        VendorPricelist.objects.filter(date_end__lt=timezone.localdate())
        .exclude(status__in=[VendorPricelist.Status.EXPIRED, VendorPricelist.Status.INACTIVE])
        .exists()
    ):
        expire_vendor_pricelists_task.apply_async(countdown=10)
    return total


@shared_task(name="purchase.recalculate_vendor_performance", ignore_result=True)
def recalculate_vendor_performance_task(*, after_id=0):
    enabled_company_ids = PurchaseSetting.objects.filter(
        vendor_performance_scoring_enabled=True,
        vendor_performance_recalculation_enabled=True,
        vendor_performance_default_template__isnull=False,
    ).values("company_id")
    vendor_ids = list(
        VendorProfile.objects.filter(
            pk__gt=after_id,
            company_id__in=enabled_company_ids,
            status=VendorProfile.Status.ACTIVE,
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:101]
    )
    for vendor_id in vendor_ids[:100]:
        recalculate_single_vendor_performance_task.apply_async(kwargs={"vendor_id": vendor_id})
    if len(vendor_ids) > 100:
        recalculate_vendor_performance_task.apply_async(
            kwargs={"after_id": vendor_ids[99]},
            countdown=5,
        )
    return {"scheduled": min(len(vendor_ids), 100)}


@shared_task(name="purchase.recalculate_single_vendor_performance", ignore_result=True)
def recalculate_single_vendor_performance_task(*, vendor_id):
    recalculate_vendor_performance(vendor_id)
    return vendor_id


@shared_task(name="purchase.sync_vendor_transaction_history", ignore_result=True)
def sync_vendor_transaction_history_task(*, vendor_id, source, before_id=None):
    result = sync_vendor_transaction_history_page(
        vendor_id,
        source,
        before_id=before_id,
    )
    if result["has_more"]:
        sync_vendor_transaction_history_task.apply_async(
            kwargs={
                "vendor_id": vendor_id,
                "source": source,
                "before_id": result["next_before_id"],
            },
            countdown=2,
        )
    return result


@shared_task(name="purchase.refresh_vendor_legal_document_expiry", ignore_result=True)
def refresh_vendor_legal_document_expiry_task(*, after_id=0):
    result = refresh_vendor_legal_document_expiry_statuses(after_id=after_id)
    if result["has_more"]:
        refresh_vendor_legal_document_expiry_task.apply_async(
            kwargs={"after_id": result["last_id"]},
            countdown=5,
        )
    return result


@shared_task(name="purchase.process_vendor_pricelist_import", ignore_result=True)
def process_vendor_pricelist_import_task(*, import_log_id):
    try:
        process_vendor_pricelist_import(import_log_id)
    except Exception as exc:
        import_log = VendorPricelistImportLog.objects.filter(pk=import_log_id).first()
        if import_log:
            errors = list(import_log.errors or [])
            errors.append({"row": 0, "data": {}, "error": str(exc)[:1000]})
            import_log.status = VendorPricelistImportLog.Status.FAILED
            import_log.errors = errors[-500:]
            import_log.error_rows = len(import_log.errors)
            import_log.completed_at = timezone.now()
            import_log.save(
                update_fields=["status", "errors", "error_rows", "completed_at", "updated_at"]
            )
        raise
    return import_log_id
