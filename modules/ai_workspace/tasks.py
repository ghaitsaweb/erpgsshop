from celery import shared_task


@shared_task
def worker_healthcheck():
    return {"service": "erp-gsshop-worker", "status": "ok"}

