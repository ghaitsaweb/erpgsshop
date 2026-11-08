from celery import shared_task
from django.utils import timezone

from .models import SaleOrder
from .sales import MAX_EXPIRED_SALES_PER_RUN, expire_due_sales_quotations


MAX_EXPIRY_BATCHES_PER_TASK = 20


@shared_task(name="sales.expire_quotations", ignore_result=True)
def expire_sales_quotations_task():
    """Expire a bounded workload and continue asynchronously when backlog remains."""
    total = 0
    for _batch in range(MAX_EXPIRY_BATCHES_PER_TASK):
        touched = expire_due_sales_quotations(batch_size=MAX_EXPIRED_SALES_PER_RUN)
        total += touched
        if touched < MAX_EXPIRED_SALES_PER_RUN:
            break

    if total == MAX_EXPIRED_SALES_PER_RUN * MAX_EXPIRY_BATCHES_PER_TASK and (
        SaleOrder.objects.filter(
            validity_date__lte=timezone.localdate(),
            status__in=[
                SaleOrder.Status.DRAFT,
                SaleOrder.Status.SENT,
                SaleOrder.Status.TO_APPROVE,
            ],
        ).exists()
    ):
        expire_sales_quotations_task.apply_async(countdown=10)
    return total
