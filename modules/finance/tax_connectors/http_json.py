"""Provider-neutral HTTPS JSON transport.

Country packs can register another backend when signing, envelopes, or response
semantics differ. Credentials stay in Django settings and are referenced by
alias from database records.
"""

import json
import ssl
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ValidationError

from . import register_connector_backend


@dataclass(frozen=True)
class ConnectorResult:
    status: str
    external_reference: str
    payload: dict


class JsonHttpConnectorBackend:
    def __init__(self, connector):
        self.connector = connector
        self.configuration = dict(connector.configuration) if isinstance(connector.configuration, dict) else {}

    def validate_configuration(self):
        parsed = urlparse(self.connector.endpoint_url)
        allowed_schemes = {"https"}
        if settings.DEBUG and self.configuration.get("allow_http_in_debug"):
            allowed_schemes.add("http")
        if parsed.scheme not in allowed_schemes or not parsed.hostname:
            raise ValidationError("Connector endpoint must be an absolute HTTPS URL.")
        allowed_hosts = set(getattr(settings, "ACCOUNTING_CONNECTOR_ALLOWED_HOSTS", ()))
        allowed_hosts.update(self.configuration.get("allowed_hosts", []))
        if allowed_hosts and parsed.hostname not in allowed_hosts:
            raise ValidationError("Connector endpoint host is not in ACCOUNTING_CONNECTOR_ALLOWED_HOSTS.")
        credentials = getattr(settings, "ACCOUNTING_CONNECTOR_CREDENTIALS", {})
        secret = credentials.get(self.connector.credential_alias)
        if not secret:
            raise ValidationError("The connector credential alias is not configured in server settings.")
        if not isinstance(secret, (str, dict)):
            raise ValidationError("Connector credentials must be a token string or header dictionary.")
        return True

    def _headers(self):
        credentials = getattr(settings, "ACCOUNTING_CONNECTOR_CREDENTIALS", {})
        secret = credentials[self.connector.credential_alias]
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        headers.update(self.configuration.get("headers", {}))
        if isinstance(secret, str):
            header = self.configuration.get("auth_header", "Authorization")
            scheme = self.configuration.get("auth_scheme", "Bearer")
            headers[header] = f"{scheme} {secret}".strip()
        else:
            headers.update({str(key): str(value) for key, value in secret.items()})
        return headers

    def _request(self, path_key, payload, method="POST"):
        self.validate_configuration()
        path = self.configuration.get(path_key)
        if not path:
            raise ValidationError(f"Connector configuration requires {path_key}.")
        url = urljoin(self.connector.endpoint_url.rstrip("/") + "/", str(path).lstrip("/"))
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8") if method != "GET" else None,
            headers=self._headers(),
            method=method,
        )
        context = None if self.connector.verify_tls else ssl._create_unverified_context()
        try:
            with urlopen(request, timeout=self.connector.timeout_seconds, context=context) as response:
                body = response.read().decode("utf-8")
                decoded = json.loads(body) if body else {}
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ValidationError(f"Tax authority returned HTTP {exc.code}: {body[:500]}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ValidationError(f"Tax connector request failed: {exc}") from exc
        if not isinstance(decoded, dict):
            raise ValidationError("Tax connector response must be a JSON object.")
        status = str(decoded.get("status", "submitted")).lower()
        reference = str(
            decoded.get("external_reference")
            or decoded.get("reference")
            or decoded.get("id")
            or ""
        )
        return ConnectorResult(status=status, external_reference=reference, payload=decoded)

    def send_document(self, document):
        return self._request("document_path", document.payload)

    def lookup_identifier(self, lookup):
        return self._request(
            "lookup_path",
            {"identifier": lookup.identifier, "identifier_type": lookup.identifier_type},
        )

    def submit_report(self, submission):
        return self._request("report_path", submission.request_payload)

    def fetch_received_documents(self, cursor):
        return self._request(
            "received_path",
            {"cursor": cursor.cursor, "resource_type": cursor.resource_type},
            method=str(self.configuration.get("received_method", "POST")).upper(),
        )


register_connector_backend("json_http", JsonHttpConnectorBackend)
