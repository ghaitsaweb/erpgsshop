"""Authenticated HTTPS OCR adapter for scanned bank statements."""

import base64
import json
import ssl
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ValidationError

from . import BankParseResult, register_bank_parser


class HttpOCRBankParser:
    def __init__(self, job):
        self.job = job
        self.options = job.provider_options if isinstance(job.provider_options, dict) else {}

    def _configuration(self):
        endpoint = str(self.options.get("endpoint_url") or "").strip()
        parsed = urlparse(endpoint)
        allowed_schemes = {"https"}
        if settings.DEBUG and self.options.get("allow_http_in_debug"):
            allowed_schemes.add("http")
        if parsed.scheme not in allowed_schemes or not parsed.hostname:
            raise ValidationError("OCR provider endpoint must be an absolute HTTPS URL.")
        allowed_hosts = set(getattr(settings, "ACCOUNTING_CONNECTOR_ALLOWED_HOSTS", ()))
        allowed_hosts.update(self.options.get("allowed_hosts", []))
        if allowed_hosts and parsed.hostname not in allowed_hosts:
            raise ValidationError("OCR provider host is not allowed by server configuration.")
        alias = str(self.options.get("credential_alias") or "").strip()
        secret = getattr(settings, "ACCOUNTING_CONNECTOR_CREDENTIALS", {}).get(alias)
        if not alias or not secret:
            raise ValidationError("OCR provider credential alias is not configured in server settings.")
        return endpoint, secret

    def parse(self):
        if not self.job.source_file:
            raise ValidationError("The HTTP OCR parser requires an uploaded bank statement file.")
        endpoint, secret = self._configuration()
        maximum = int(self.options.get("max_file_bytes", 10 * 1024 * 1024))
        if self.job.source_file.size > maximum:
            raise ValidationError(f"Bank statement file exceeds the configured {maximum}-byte limit.")
        self.job.source_file.open("rb")
        try:
            content = self.job.source_file.read()
        finally:
            self.job.source_file.close()
        payload = {
            "filename": self.job.filename, "document": base64.b64encode(content).decode("ascii"),
            "mapping": self.job.column_mapping, "options": self.options.get("request_options", {}),
        }
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if isinstance(secret, str):
            headers[str(self.options.get("auth_header", "Authorization"))] = f"{self.options.get('auth_scheme', 'Bearer')} {secret}".strip()
        elif isinstance(secret, dict):
            headers.update({str(key): str(value) for key, value in secret.items()})
        request = Request(endpoint, data=json.dumps(payload, separators=(",", ":")).encode("utf-8"), headers=headers, method="POST")
        context = None if self.options.get("verify_tls", True) else ssl._create_unverified_context()
        try:
            with urlopen(request, timeout=int(self.options.get("timeout_seconds", 60)), context=context) as response:
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ValidationError(f"OCR provider returned HTTP {exc.code}: {body[:500]}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ValidationError(f"OCR provider request failed: {exc}") from exc
        rows = decoded.get("transactions") or decoded.get("rows")
        if not isinstance(rows, list):
            raise ValidationError("OCR provider response requires a transactions array.")
        return BankParseResult(
            rows=rows, confidence=Decimal(str(decoded.get("confidence", 0))), raw_result=decoded,
        )


register_bank_parser("http_ocr", HttpOCRBankParser)
