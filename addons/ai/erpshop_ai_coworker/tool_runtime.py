"""Approval gate for stateful AI tools; tool adapters must call this before execution."""

import hashlib
import json
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import AITaskEvent, AIToolApproval


WRITE_TOOLS = {"erp_write", "message", "sandbox", "mcp", "filesystem", "fetch", "web_search"}


TOOL_DEFINITIONS = {
    "erp_read": {
        "description": "Read a bounded, permission-checked ERP record snapshot. This never changes ERP data.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "ERP entity slug to read."},
                "fields": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                "filters": {"type": "array", "items": {"type": "object"}},
            },
            "additionalProperties": False,
        },
    },
    "web_search": {
        "description": "Request an external web search. A human must approve it before any network request.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "maxLength": 500}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "fetch": {
        "description": "Request retrieval of an external URL. A human must approve it before any network request.",
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "maxLength": 2000}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    "filesystem": {
        "description": "Request a governed workspace file operation. A human must approve it before execution.",
        "parameters": {
            "type": "object",
            "properties": {"operation": {"type": "string"}, "path": {"type": "string", "maxLength": 1000}},
            "required": ["operation", "path"],
            "additionalProperties": False,
        },
    },
    "mcp": {
        "description": "Request a configured MCP tool operation. A human must approve it before execution.",
        "parameters": {
            "type": "object",
            "properties": {"tool": {"type": "string"}, "arguments": {"type": "object"}},
            "required": ["tool"],
            "additionalProperties": False,
        },
    },
    "sandbox": {
        "description": "Request a sandboxed computation. A human must approve it before execution.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "maxLength": 4000}},
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    "erp_write": {
        "description": "Request a governed ERP write. A human must approve it before execution.",
        "parameters": {
            "type": "object",
            "properties": {"entity": {"type": "string"}, "operation": {"type": "string"}, "data": {"type": "object"}},
            "required": ["entity", "operation", "data"],
            "additionalProperties": False,
        },
    },
    "message": {
        "description": "Request an outbound message. A human must approve it before delivery.",
        "parameters": {
            "type": "object",
            "properties": {"channel": {"type": "string"}, "recipient": {"type": "string"}, "content": {"type": "string", "maxLength": 8000}},
            "required": ["channel", "recipient", "content"],
            "additionalProperties": False,
        },
    },
}


def tool_definitions(agent):
    """Return only the explicit allow-listed tools in the provider function format."""
    enabled = set(agent.allowed_tools or [])
    definitions = []
    for name in sorted(enabled):
        definition = TOOL_DEFINITIONS.get(name)
        if definition is None:
            continue
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": definition["description"],
                    "parameters": definition["parameters"],
                },
            }
        )
    return definitions


def normalize_tool_call(call):
    """Validate one provider-neutral tool call before it reaches any adapter."""
    if not isinstance(call, dict):
        raise ValidationError("Tool calls must be JSON objects.")
    call_id = str(call.get("id") or "").strip()
    name = str(call.get("name") or "").strip()
    arguments = call.get("arguments")
    if not call_id or len(call_id) > 180 or name not in TOOL_DEFINITIONS or not isinstance(arguments, dict):
        raise ValidationError("The provider requested an invalid tool call.")
    return {"id": call_id, "name": name, "arguments": arguments}


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
        AITaskEvent.objects.create(
            task=approval.task,
            actor=actor if approval.state != AIToolApproval.State.EXPIRED else None,
            event=f"tool_{approval.state}",
            details={"approval_id": approval.pk, "tool": approval.tool_name},
        )
    return approval


def consume_approval(approval):
    with transaction.atomic():
        approval = AIToolApproval.objects.select_for_update().get(pk=approval.pk)
        if approval.state != AIToolApproval.State.APPROVED or approval.expires_at <= timezone.now():
            raise ValidationError("The tool approval is not executable.")
        approval.state = AIToolApproval.State.EXECUTED
        approval.save(update_fields=["state", "updated_at"])
    return approval
