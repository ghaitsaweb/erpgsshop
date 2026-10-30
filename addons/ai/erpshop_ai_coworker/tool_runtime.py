"""Approval gate for stateful AI tools; tool adapters must call this before execution."""

import hashlib
import json
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import AIToolApproval


WRITE_TOOLS = {"erp_write", "message", "sandbox", "mcp", "filesystem", "fetch", "web_search"}


def request_approval(*, task, tool_name, request_preview, ttl_minutes=15):
    if tool_name not in task.agent.SUPPORTED_TOOLS or tool_name not in (task.agent.allowed_tools or []):
        raise ValidationError("This coworker is not permitted to use the requested tool.")
    if not isinstance(request_preview, dict):
        raise ValidationError("Tool requests must use a JSON object preview.")
    encoded = json.dumps(request_preview, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    with transaction.atomic():
        approval, created = AIToolApproval.objects.get_or_create(
            task=task,
            tool_name=tool_name,
            request_digest=digest,
            defaults={"agent": task.agent, "request_preview": request_preview, "expires_at": timezone.now() + timedelta(minutes=ttl_minutes)},
        )
    return approval, created


def decide_approval(*, approval, actor, approve):
    with transaction.atomic():
        approval = AIToolApproval.objects.select_for_update().get(pk=approval.pk)
        if approval.state != AIToolApproval.State.PENDING:
            return approval
        if approval.expires_at <= timezone.now():
            approval.state = AIToolApproval.State.EXPIRED
        else:
            approval.state = AIToolApproval.State.APPROVED if approve else AIToolApproval.State.REJECTED
            approval.decided_by, approval.decided_at = actor, timezone.now()
        approval.save(update_fields=["state", "decided_by", "decided_at", "updated_at"])
    return approval


def consume_approval(approval):
    with transaction.atomic():
        approval = AIToolApproval.objects.select_for_update().get(pk=approval.pk)
        if approval.state != AIToolApproval.State.APPROVED or approval.expires_at <= timezone.now():
            raise ValidationError("The tool approval is not executable.")
        approval.state = AIToolApproval.State.EXECUTED
        approval.save(update_fields=["state", "updated_at"])
    return approval
