from celery import shared_task

from .services import run_due_asset_disposals, run_due_recurring_journals


@shared_task(name="finance.finalize_due_asset_disposals")
def finalize_due_asset_disposals():
    return run_due_asset_disposals()


@shared_task(name="finance.run_due_recurring_journals")
def finalize_due_recurring_journals():
    entries = run_due_recurring_journals()
    return {"created_entries": [entry.pk for entry in entries], "count": len(entries)}
