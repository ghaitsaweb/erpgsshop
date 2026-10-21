from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .adapters import apply_approval_result
from .models import ApprovalInstance
from .runtime import activate_due_delegation, complete_due_timeout, drain_approval_outbox, due_instance_ids
from .services import perform_approval_action


@shared_task(name="approval_workflow.scan_due_runtime")
def scan_due_runtime():
    instance_ids = due_instance_ids()
    for instance_id in instance_ids:
        process_instance_runtime.delay(instance_id)
    return {"queued": len(instance_ids)}


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    name="approval_workflow.process_instance_runtime",
)
def process_instance_runtime(self, instance_id):
    delegated = activate_due_delegation(instance_id)
    timed_out = complete_due_timeout(instance_id)
    return {"instance_id": instance_id, "delegated": delegated, "timed_out": timed_out}


@shared_task(name="approval_workflow.drain_outbox")
def process_approval_outbox():
    return {"processed": drain_approval_outbox()}


@shared_task(name="approval_workflow.process_mass_approval_item")
def process_mass_approval_item(instance_id, actor_id, action, reason=""):
    try:
        instance = ApprovalInstance.objects.select_related("workflow").get(pk=instance_id)
        actor = get_user_model().objects.get(pk=actor_id, is_active=True)
        if not instance.workflow.enable_mass_approval:
            raise ValidationError("Mass approval is disabled for this workflow.")
        outcome = perform_approval_action(
            instance,
            actor,
            action,
            reason=reason,
            apply_result=lambda target, result_action: apply_approval_result(target, result_action, actor),
        )
    except (ApprovalInstance.DoesNotExist, get_user_model().DoesNotExist, ValidationError) as exc:
        messages = getattr(exc, "messages", None)
        return {
            "instance_id": instance_id,
            "success": False,
            "error": "; ".join(messages) if messages else str(exc),
        }
    return {
        "instance_id": instance_id,
        "success": True,
        "completed": outcome.completed,
        "status": outcome.instance.status,
    }
