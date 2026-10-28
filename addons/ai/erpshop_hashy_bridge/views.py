import hashlib
import json
import logging
import time
from functools import wraps

from django.core.exceptions import RequestDataTooBig
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from modules.organization.context import reset_current_context, set_current_context

from . import api
from .auth import bridge_enabled, extract_token, resolve_principal
from .errors import BridgeError, disabled, invalid_request
from .models import BridgeAuditLog


logger = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def _response(data=None, *, error=None, status=200):
    if error:
        payload = {"ok": False, "error": {"code": error.code, "message": error.message}}
    else:
        payload = {"ok": True, "data": data}
    content = json.dumps(payload, cls=DjangoJSONEncoder, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if not error and len(content) > MAX_RESPONSE_BYTES:
        status = 422
        content = json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "response_too_large",
                    "message": "Reduce page_size or request fewer fields.",
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
    return HttpResponse(content, status=status, content_type="application/json")


def _payload(request):
    if request.content_type != "application/json":
        raise invalid_request("Content-Type must be application/json.")
    try:
        declared_size = int(request.headers.get("Content-Length") or 0)
    except (TypeError, ValueError, OverflowError):
        declared_size = 0
    if declared_size > MAX_REQUEST_BYTES:
        raise BridgeError("payload_too_large", "Request body exceeds 256 KB.", 413)
    try:
        raw = request.body
    except RequestDataTooBig as exc:
        raise BridgeError("payload_too_large", "Request body exceeds 256 KB.", 413) from exc
    if len(raw) > MAX_REQUEST_BYTES:
        raise BridgeError("payload_too_large", "Request body exceeds 256 KB.", 413)
    try:
        value = json.loads(raw or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise invalid_request("Request body must be a JSON object.") from exc
    if not isinstance(value, dict):
        raise invalid_request("Request body must be a JSON object.")
    return value, hashlib.sha256(raw).hexdigest()


def _result_count(data):
    for key in ("records", "groups", "results", "entities"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, list):
            return len(value)
    return 1 if data else 0


def _audit(principal, endpoint, started, status, ok, digest="", data=None, error=None):
    if principal is None:
        return
    try:
        BridgeAuditLog.objects.create(
            company=principal.company,
            user=principal.user,
            endpoint=endpoint,
            run_id=str(principal.claims.get("run") or "")[:128],
            ok=ok,
            http_status=status,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            result_count=_result_count(data),
            payload_digest=digest,
            error_code=error.code if error else "",
            error_message=error.message[:300] if error else "",
        )
    except Exception:
        logger.exception("Could not persist Hashy Bridge audit metadata")


def bridge_endpoint(endpoint, handler):
    def decorator(view):
        @csrf_exempt
        @wraps(view)
        def wrapped(request):
            if request.method != "POST":
                error = BridgeError("method_not_allowed", "Use POST for this endpoint.", 405)
                response = _response(error=error, status=error.status)
                response["Allow"] = "POST"
                return response
            started = time.monotonic()
            principal = None
            digest = ""
            context_token = None
            try:
                if not bridge_enabled():
                    raise disabled()
                principal = resolve_principal(extract_token(request))
                payload, digest = _payload(request)
                context_token = set_current_context(principal.context)
                data = handler(principal, payload)
            except BridgeError as error:
                _audit(principal, endpoint, started, error.status, False, digest, error=error)
                return _response(error=error, status=error.status)
            except Exception:
                logger.exception("Unhandled Hashy Bridge endpoint failure: %s", endpoint)
                error = BridgeError("internal_error", "The bridge could not process this request.", 500)
                _audit(principal, endpoint, started, 500, False, digest, error=error)
                return _response(error=error, status=500)
            finally:
                if context_token is not None:
                    reset_current_context(context_token)
            response = _response(data)
            if response.status_code == 200:
                _audit(principal, endpoint, started, 200, True, digest, data=data)
            else:
                error = BridgeError("response_too_large", "Reduce page_size or request fewer fields.", 422)
                _audit(principal, endpoint, started, 422, False, digest, error=error)
            return response

        return wrapped

    return decorator


def ping(request):
    if request.method != "GET":
        response = _response(error=BridgeError("method_not_allowed", "Use GET for this endpoint.", 405), status=405)
        response["Allow"] = "GET"
        return response
    return _response({"module": "erpshop-hashy-bridge", "version": "1.0.0"})


@bridge_endpoint("health", api.health)
def health(request):
    pass


@bridge_endpoint("instance_info", api.instance_info)
def instance_info(request):
    pass


@bridge_endpoint("schema", api.schema)
def schema(request):
    pass


@bridge_endpoint("search_read", api.search_read)
def search_read(request):
    pass


@bridge_endpoint("read_group", api.read_group)
def read_group(request):
    pass


@bridge_endpoint("batch", api.batch)
def batch(request):
    pass
