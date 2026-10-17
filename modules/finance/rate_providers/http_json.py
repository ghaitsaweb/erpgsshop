import json
import ssl
from datetime import datetime
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ValidationError

from . import RegulatoryRateResult, register_rate_provider


class HttpJsonRateProvider:
    def __init__(self, run):
        self.run = run
        profile_config = run.profile.configuration if isinstance(run.profile.configuration, dict) else {}
        self.config = profile_config.get("rate_provider", {})
        if not isinstance(self.config, dict):
            self.config = {}

    def fetch(self):
        endpoint = str(self.config.get("endpoint_url") or "").strip()
        parsed = urlparse(endpoint)
        allowed_schemes = {"https"}
        if settings.DEBUG and self.config.get("allow_http_in_debug"):
            allowed_schemes.add("http")
        if parsed.scheme not in allowed_schemes or not parsed.hostname:
            raise ValidationError("Rate provider endpoint must be an absolute HTTPS URL.")
        allowed_hosts = set(getattr(settings, "ACCOUNTING_CONNECTOR_ALLOWED_HOSTS", ()))
        allowed_hosts.update(self.config.get("allowed_hosts", []))
        if allowed_hosts and parsed.hostname not in allowed_hosts:
            raise ValidationError("Rate provider host is not allowed by server configuration.")
        params = {
            str(self.config.get("date_from_parameter", "date_from")): self.run.date_from.isoformat(),
            str(self.config.get("date_to_parameter", "date_to")): self.run.date_to.isoformat(),
            str(self.config.get("currencies_parameter", "currencies")): ",".join(self.run.currencies),
        }
        url = f"{endpoint}{'&' if '?' in endpoint else '?'}{urlencode(params)}"
        headers = {"Accept": "application/json"}
        alias = str(self.config.get("credential_alias") or "").strip()
        if alias:
            secret = getattr(settings, "ACCOUNTING_CONNECTOR_CREDENTIALS", {}).get(alias)
            if not secret:
                raise ValidationError("Rate provider credential alias is not configured.")
            if isinstance(secret, str):
                headers[str(self.config.get("auth_header", "Authorization"))] = f"{self.config.get('auth_scheme', 'Bearer')} {secret}".strip()
            elif isinstance(secret, dict):
                headers.update({str(key): str(value) for key, value in secret.items()})
        context = None if self.config.get("verify_tls", True) else ssl._create_unverified_context()
        try:
            with urlopen(Request(url, headers=headers, method="GET"), timeout=int(self.config.get("timeout_seconds", 30)), context=context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ValidationError(f"Rate provider returned HTTP {exc.code}: {body[:500]}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ValidationError(f"Rate provider request failed: {exc}") from exc
        rows = payload.get(str(self.config.get("rows_key", "rates")))
        if not isinstance(rows, list):
            raise ValidationError("Rate provider response requires a rates array.")
        maximum_rows = int(self.config.get("max_rows", 5000))
        if len(rows) > maximum_rows:
            raise ValidationError(f"Rate provider response exceeds the configured {maximum_rows}-row limit.")
        date_key = str(self.config.get("date_key", "date"))
        currency_key = str(self.config.get("currency_key", "currency"))
        rate_key = str(self.config.get("rate_key", "rate"))
        normalized = []
        seen = set()
        requested_currencies = {str(value).upper() for value in self.run.currencies}
        for row in rows:
            if not isinstance(row, dict):
                raise ValidationError("Every rate provider row must be an object.")
            rate_date = datetime.strptime(
                str(row[date_key]), self.config.get("date_format", "%Y-%m-%d")
            ).date()
            currency = str(row[currency_key]).upper()
            rate = Decimal(str(row[rate_key]))
            key = (rate_date, currency)
            if key in seen:
                raise ValidationError(f"Rate provider returned duplicate {currency} data for {rate_date}.")
            if not self.run.date_from <= rate_date <= self.run.date_to:
                raise ValidationError(f"Rate provider returned {rate_date} outside the requested period.")
            if requested_currencies and currency not in requested_currencies:
                raise ValidationError(f"Rate provider returned unrequested currency {currency}.")
            if rate <= 0:
                raise ValidationError(f"Rate provider returned a non-positive rate for {currency} on {rate_date}.")
            seen.add(key)
            normalized.append({
                "rate_date": rate_date,
                "currency": currency, "rate": rate,
                "reference": str(row.get(self.config.get("reference_key", "reference"), "")), "payload": row,
            })
        return RegulatoryRateResult(rates=normalized, payload=payload)


register_rate_provider("http_json", HttpJsonRateProvider)
register_rate_provider("bi", HttpJsonRateProvider)
