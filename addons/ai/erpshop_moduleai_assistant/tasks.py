from celery import shared_task

from .services import execute_message


@shared_task(bind=True, autoretry_for=(OSError,), retry_backoff=True, retry_kwargs={"max_retries": 2})
def process_assistant_message(self, message_id):
    return execute_message(message_id)
