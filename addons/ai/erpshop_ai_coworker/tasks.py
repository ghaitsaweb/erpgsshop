from celery import shared_task

from .services import execute_coworker_task


@shared_task
def worker_healthcheck():
    return {"service": "erp-gsshop-worker", "status": "ok"}


@shared_task(name="ai_coworker.execute_task")
def process_coworker_task(task_id):
    return execute_coworker_task(task_id)
