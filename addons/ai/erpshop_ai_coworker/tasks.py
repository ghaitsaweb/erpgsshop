from celery import shared_task

from .services import (
    expire_due_tool_approvals,
    execute_coworker_task,
    queue_due_crons,
    queue_due_heartbeats,
    queue_due_schedules,
    run_connection_check,
)


@shared_task
def worker_healthcheck():
    return {"service": "erp-gsshop-worker", "status": "ok"}


@shared_task(name="ai_coworker.execute_task", bind=True, max_retries=None)
def process_coworker_task(self, task_id):
    result = execute_coworker_task(task_id)
    if result.get("status") == "deferred":
        raise self.retry(countdown=result.get("retry_after", 5))
    return result


@shared_task(name="ai_coworker.test_connection")
def process_connection_check(check_id):
    return run_connection_check(check_id)


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


@shared_task(name="ai_coworker.expire_due_tool_approvals")
def expire_due_coworker_tool_approvals():
    expired_ids = expire_due_tool_approvals()
    return {"expired": len(expired_ids)}
