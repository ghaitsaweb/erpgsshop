from celery import shared_task

from .services import execute_message
from .work_intelligence import refresh_due_work_profiles
from .digest import queue_due_digests


@shared_task(bind=True, autoretry_for=(OSError,), retry_backoff=True, retry_kwargs={"max_retries": 2})
def process_assistant_message(self, message_id):
    return execute_message(message_id)


@shared_task(name="erpshop_ai_assistant.refresh_work_profiles")
def refresh_assistant_work_profiles():
    results = refresh_due_work_profiles()
    return {"refreshed": len(results), "runs": results}


@shared_task(name="erpshop_ai_assistant.queue_due_digests")
def queue_assistant_digests():
    records = queue_due_digests()
    return {"created": len(records), "record_ids": [record.pk for record in records]}
