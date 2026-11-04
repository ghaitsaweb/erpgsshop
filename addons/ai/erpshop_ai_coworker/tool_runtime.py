"""Approval gate for stateful AI tools; tool adapters must call this before execution."""

import hashlib
import ipaddress
import json
import socket
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import AITaskEvent, AIToolApproval


WRITE_TOOLS = {"erp_write", "message", "sandbox", "mcp", "filesystem", "fetch", "web_search"}
MAX_TOOL_FETCH_BYTES = 256 * 1024
MAX_WORKSPACE_FILE_BYTES = 64 * 1024


class ToolExecutionError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = build_opener(_NoRedirect)


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
            "properties": {
                "operation": {"type": "string", "enum": ["list", "read", "write"]},
                "path": {"type": "string", "maxLength": 1000},
                "content": {"type": "string", "maxLength": MAX_WORKSPACE_FILE_BYTES},
            },
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


def _workspace_path(agent, raw_path):
    relative = str(raw_path or "").strip().replace("\\", "/")
    if not relative or relative.startswith("/") or "\x00" in relative:
        raise ToolExecutionError("Workspace paths must be non-empty relative paths.")
    root = (Path(settings.MEDIA_ROOT) / "ai_coworker_workspaces" / (agent.slug or str(agent.pk))).resolve()
    target = (root / relative).resolve()
    if root != target and root not in target.parents:
        raise ToolExecutionError("Workspace path escapes the coworker workspace.")
    return root, target


def _execute_filesystem(agent, arguments):
    root, target = _workspace_path(agent, arguments.get("path"))
    operation = str(arguments.get("operation") or "").strip()
    root.mkdir(parents=True, exist_ok=True)
    if operation == "list":
        if not target.exists() or not target.is_dir():
            raise ToolExecutionError("The requested workspace directory does not exist.")
        children = sorted(item.name for item in target.iterdir())[:200]
        return {"status": "completed", "operation": operation, "path": str(target.relative_to(root)), "entries": children}
    if operation == "read":
        if not target.is_file():
            raise ToolExecutionError("The requested workspace file does not exist.")
        if target.stat().st_size > MAX_WORKSPACE_FILE_BYTES:
            raise ToolExecutionError("The requested workspace file exceeds the safe read limit.")
        return {
            "status": "completed",
            "operation": operation,
            "path": str(target.relative_to(root)),
            "content": target.read_text(encoding="utf-8", errors="replace"),
        }
    if operation == "write":
        content = arguments.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_WORKSPACE_FILE_BYTES:
            raise ToolExecutionError("Workspace writes must be UTF-8 text within the safe size limit.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"status": "completed", "operation": operation, "path": str(target.relative_to(root)), "bytes_written": len(content.encode("utf-8"))}
    raise ToolExecutionError("Unsupported workspace operation.")


def _execute_fetch(arguments):
    url = str(arguments.get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ToolExecutionError("Fetch requires a credential-free HTTPS URL.")
    try:
        addresses = {value[4][0] for value in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ToolExecutionError("Fetch host cannot be resolved.") from exc
    for address in addresses:
        candidate = ipaddress.ip_address(address)
        if candidate.is_private or candidate.is_loopback or candidate.is_link_local or candidate.is_reserved or candidate.is_multicast:
            raise ToolExecutionError("Fetch host resolves to a non-public address.")
    request = Request(url, headers={"Accept": "text/plain,text/html,application/json;q=0.9"})
    with _NO_REDIRECT_OPENER.open(request, timeout=20) as response:
        body = response.read(MAX_TOOL_FETCH_BYTES + 1)
        content_type = response.headers.get("Content-Type", "")[:120]
        final_url = response.geturl()
        status = response.status
    if len(body) > MAX_TOOL_FETCH_BYTES:
        raise ToolExecutionError("Fetch response exceeds the safe size limit.")
    return {
        "status": "completed",
        "url": final_url,
        "http_status": status,
        "content_type": content_type,
        "content": body.decode("utf-8", errors="replace")[:MAX_TOOL_FETCH_BYTES],
    }


def execute_approved_tool(approval):
    """Execute a supported adapter after an explicit, unexpired human approval."""
    approval = consume_approval(approval)
    preview = approval.request_preview if isinstance(approval.request_preview, dict) else {}
    arguments = preview.get("arguments") if isinstance(preview.get("arguments"), dict) else {}
    try:
        if approval.tool_name == "filesystem":
            result = _execute_filesystem(approval.agent, arguments)
        elif approval.tool_name == "fetch":
            result = _execute_fetch(arguments)
        else:
            result = {"status": "not_configured", "message": "No governed adapter is configured for this approved tool."}
    except (OSError, ToolExecutionError) as exc:
        result = {"status": "error", "message": str(exc)[:1000]}
    approval.result = result
    approval.save(update_fields=["result", "updated_at"])
    AITaskEvent.objects.create(
        task=approval.task,
        event="tool_executed",
        details={"approval_id": approval.pk, "tool": approval.tool_name, "status": result.get("status")},
    )
    return approval
