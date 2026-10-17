"""HM3 multi-database/OAuth1 invoice connector backend."""

import base64
from dataclasses import dataclass
import hashlib
import hmac
import http.cookiejar
import json
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError

from . import register_external_connector_backend


@dataclass(frozen=True)
class HM3Result:
    status: str
    external_reference: str
    request_payload: dict
    response_payload: dict


class HM3ConnectorBackend:
    def __init__(self, connector):
        self.connector = connector
        self.configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
        self.credentials = getattr(settings, "ACCOUNTING_CONNECTOR_CREDENTIALS", {}).get(
            connector.credential_alias
        )
        context = None if connector.verify_tls else ssl._create_unverified_context()
        handlers = [HTTPCookieProcessor(http.cookiejar.CookieJar())]
        if context is not None:
            handlers.append(HTTPSHandler(context=context))
        self.opener = build_opener(*handlers)

    def validate_configuration(self):
        parsed = urlparse(self.connector.endpoint_url)
        allowed_schemes = {"https"}
        if settings.DEBUG and self.configuration.get("allow_http_in_debug"):
            allowed_schemes.add("http")
        if parsed.scheme not in allowed_schemes or not parsed.hostname:
            raise ValidationError("External accounting connector endpoint must be an absolute HTTPS URL.")
        allowed_hosts = set(getattr(settings, "ACCOUNTING_CONNECTOR_ALLOWED_HOSTS", ()))
        allowed_hosts.update(self.configuration.get("allowed_hosts", []))
        if allowed_hosts and parsed.hostname not in allowed_hosts:
            raise ValidationError("External connector endpoint host is not allow-listed.")
        if not isinstance(self.credentials, dict):
            raise ValidationError("HM3 credentials must be a server-side credential dictionary.")
        if not self.credentials.get("username") or not self.credentials.get("password"):
            raise ValidationError("HM3 credentials require username and password.")
        return True

    def _url(self, key, default):
        return urljoin(
            self.connector.endpoint_url.rstrip("/") + "/",
            str(self.configuration.get(key, default)).lstrip("/"),
        )

    def _request(self, method, url, *, payload=None, params=None, headers=None):
        if params:
            url = f"{url}?{urlencode(params)}"
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(url, data=data, method=method, headers={
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {}),
            **(headers or {}),
        })
        try:
            with self.opener.open(request, timeout=self.connector.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ValidationError(f"HM3 returned HTTP {exc.code}: {body[:500]}") from exc
        except (URLError, TimeoutError) as exc:
            raise ValidationError(f"HM3 request failed: {exc}") from exc
        try:
            decoded = json.loads(body) if body else {}
        except json.JSONDecodeError as exc:
            raise ValidationError("HM3 response is not valid JSON.") from exc
        if not isinstance(decoded, dict):
            raise ValidationError("HM3 response must be a JSON object.")
        return decoded

    @staticmethod
    def _encode(value):
        return quote(str(value), safe="~")

    @classmethod
    def _signature(cls, method, url, params, consumer_secret, token_secret):
        encoded = "&".join(
            f"{cls._encode(key)}={cls._encode(value)}" for key, value in sorted(params.items())
        )
        base = "&".join((method.upper(), cls._encode(url), cls._encode(encoded)))
        key = f"{cls._encode(consumer_secret)}&{cls._encode(token_secret)}"
        return base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()

    def _access_token(self):
        self.validate_configuration()
        database = self.credentials.get("database") or self.configuration.get("database")
        if database:
            self._request("POST", self._url("multi_db_login_path", "/multi_database/api/auth/login"), payload={
                "login": self.credentials["username"],
                "password": self.credentials["password"],
                "database": database,
            })
        login = self._request("POST", self._url("login_path", "/api/v1/auth/login"), payload={
            "login": self.credentials["username"],
            "password": self.credentials["password"],
        })
        token = login.get("token") or {}
        required = ("oauth_token", "oauth_token_secret", "consumer_key", "consumer_secret")
        if not all(token.get(key) for key in required):
            raise ValidationError("HM3 login response is missing OAuth1 token fields.")
        verifier = self._request(
            "GET",
            self._url("authorize_path", "/restapi/1.0/common/oauth1/authorize"),
            params={
                "oauth_consumer_key": token["consumer_key"],
                "oauth_token": token["oauth_token"],
                "oauth_token_secret": token["oauth_token_secret"],
            },
        ).get("oauth_verifier")
        if not verifier:
            raise ValidationError("HM3 did not return an OAuth verifier.")
        access_url = self._url("access_token_path", "/restapi/1.0/common/oauth1/access_token")
        params = {
            "oauth_consumer_key": token["consumer_key"],
            "oauth_token": token["oauth_token"],
            "oauth_verifier": verifier,
            "oauth_nonce": uuid.uuid4().hex,
            "oauth_timestamp": str(int(time.time())),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_version": "1.0",
        }
        params["oauth_signature"] = self._signature(
            "POST", access_url, params, token["consumer_secret"], token["oauth_token_secret"]
        )
        access = self._request("POST", access_url, params=params)
        access_token = access.get("oauth_token")
        if not access_token:
            raise ValidationError("HM3 did not return an access token.")
        return access_token

    def send_invoice(self, sync):
        token = self._access_token()
        invoice = sync.invoice
        rows = list(invoice.lines.all())
        auth = {"Authorization": f"Bearer {token}"}
        company_ref = self.configuration.get("remote_company_id", invoice.company_id)
        customers = self._request(
            "GET",
            self._url("customer_lookup_path", "/api/accounting/get/customer"),
            params={"company_id": company_ref, "search": invoice.partner.name},
            headers=auth,
        ).get("data", [])
        customer = customers[0] if customers else self._request(
            "POST",
            self._url("customer_create_path", "/api/accounting/create/partner"),
            payload={
                "name": invoice.partner.name,
                "company_id": company_ref,
                "email": invoice.partner.email,
                "phone": invoice.partner.phone,
                "mobile": invoice.partner.phone,
                "address": invoice.partner.address,
                "is_customer": True,
                "is_vendor": False,
            },
            headers=auth,
        ).get("data", {})
        currencies = self._request(
            "GET",
            self._url("currency_lookup_path", "/api/accounting/get/currency"),
            params={"search": invoice.company.currency},
            headers=auth,
        ).get("data", [])
        payload = {
            "invoice_origin": invoice.number,
            "origin": invoice.number,
            "partner_id": customer.get("id"),
            "invoice_date": invoice.invoice_date.isoformat(),
            "invoice_date_due": invoice.due_date.isoformat(),
            "currency_id": currencies[0].get("id") if currencies else None,
            "company_id": company_ref,
            "branch_id": self.configuration.get("remote_branch_id"),
            "invoice_line_ids": [
                {
                    "product_id": False,
                    "name": row.description,
                    "serial_number_id": False,
                    "quantity": float(row.quantity),
                    "price_unit": float(row.unit_price),
                    "discount_amount": 0,
                    "tax_ids": [],
                }
                for row in rows
            ],
        }
        response = self._request(
            "POST",
            self._url("invoice_create_path", "/api/accounting/create/invoice"),
            payload=payload,
            headers=auth,
        )
        return HM3Result(
            status="sent",
            external_reference=str(response.get("move_name") or response.get("reference_invoice") or ""),
            request_payload=payload,
            response_payload=response,
        )

    def connect(self):
        self._access_token()
        return True


register_external_connector_backend("hm3_oauth1", HM3ConnectorBackend)
