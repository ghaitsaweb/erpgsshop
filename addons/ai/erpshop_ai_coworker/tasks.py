from celery import shared_task

from .services import execute_coworker_task, queue_due_crons, queue_due_heartbeats, queue_due_schedules


@shared_task
def worker_healthcheck():
    return {"service": "erp-gsshop-worker", "status": "ok"}


@shared_task(name="ai_coworker.execute_task")
def process_coworker_task(task_id):
    return execute_coworker_task(task_id)


@shared_task(name="ai_coworker.queue_due_schedules")
def queue_due_coworker_schedules():
    task_ids = queue_due_schedules()
    for task_id in task_ids:
        process_coworker_task.delay(task_id)
    return {"queued": len(task_ids)}


@shared_task(name="ai_coworker.queue_due_crons")
def queue_due_coworker_crons():
    task_ids = queue_due_crons()
    for task_id in task_ids:
        process_coworker_task.delay(task_id)
    return {"queued": len(task_ids)}


@shared_task(name="ai_coworker.queue_due_heartbeats")
def queue_due_coworker_heartbeats():
    task_ids = queue_due_heartbeats()
    for task_id in task_ids:
        process_coworker_task.delay(task_id)
    return {"queued": len(task_ids)}
