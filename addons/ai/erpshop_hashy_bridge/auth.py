"""Strict HS256 bridge authentication with company and branch isolation."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db.models import Q

from core.models import Company, Employee
from modules.organization.context import OrganizationContext
from modules.organization.models import Branch, UserOrganizationAccess

from .errors import disabled, forbidden, unauthorized


ISSUER = "hashy-os"
LEEWAY_SECONDS = 30
MAX_TOKEN_BYTES = 8192


@dataclass(frozen=True)
class BridgePrincipal:
    user: object
    company: Company
    branch: Branch
    context: OrganizationContext
    claims: dict


def bridge_enabled():
    return os.getenv("ERPGSSHOP_HASHY_BRIDGE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _secret():
    value = os.getenv("ERPGSSHOP_HASHY_BRIDGE_SECRET", "")
    if len(value.encode("utf-8")) < 32:
        raise disabled("Hashy Bridge requires a secret of at least 32 bytes.")
    return value.encode("utf-8")


def _instance_code():
    value = os.getenv("ERPGSSHOP_HASHY_BRIDGE_INSTANCE_CODE", "").strip()
    if not value:
        raise disabled("Hashy Bridge instance code is not configured.")
    return value


def extract_token(request):
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise unauthorized("Missing bearer token.")
    token = authorization[7:].strip()
    if not token or len(token.encode("utf-8")) > MAX_TOKEN_BYTES:
        raise unauthorized("Malformed bearer token.")
    return token


def _b64decode(value):
    try:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise unauthorized("Malformed bearer token.") from exc


def _b64encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_token(token, *, now=None):
    try:
        header_part, payload_part, signature_part = token.split(".")
        header = json.loads(_b64decode(header_part))
        claims = json.loads(_b64decode(payload_part))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise unauthorized("Malformed bearer token.") from exc
    if not isinstance(header, dict) or header.get("alg") != "HS256" or header.get("typ") not in (None, "JWT"):
        raise unauthorized("Unsupported bearer token algorithm.")
    if not isinstance(claims, dict):
        raise unauthorized("Malformed bearer token claims.")
    try:
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
    except UnicodeEncodeError as exc:
        raise unauthorized("Malformed bearer token.") from exc
    expected = hmac.new(_secret(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64decode(signature_part)):
        raise unauthorized("Invalid bearer token signature.")

    current = int(time.time() if now is None else now)
    try:
        expires_at = int(claims["exp"])
        issued_at = int(claims["iat"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise unauthorized("Bearer token requires valid exp and iat claims.") from exc
    if expires_at < current - LEEWAY_SECONDS:
        raise unauthorized("Bearer token expired.")
    if expires_at <= issued_at:
        raise unauthorized("Bearer token expiry must follow its issue time.")
    if issued_at > current + LEEWAY_SECONDS:
        raise unauthorized("Bearer token was issued in the future.")
    if expires_at - issued_at > 900:
        raise unauthorized("Bearer token lifetime exceeds 15 minutes.")
    if claims.get("iss") != ISSUER:
        raise unauthorized("Bearer token issuer mismatch.")
    audience = claims.get("aud")
    if audience != _instance_code():
        raise unauthorized("Bearer token audience mismatch.")
    subject = str(claims.get("sub") or "").strip()
    if not subject or len(subject) > 256:
        raise unauthorized("Bearer token subject is missing.")
    return claims


def encode_token(claims):
    """Create a compatible token for provisioning tools and tests."""
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64encode(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = _b64encode(hmac.new(_secret(), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def _resolve_user(subject):
    user_model = get_user_model()
    exact = list(user_model.objects.filter(is_active=True, username=subject).order_by("pk")[:2])
    candidates = exact
    if len(candidates) != 1:
        candidates = list(
            user_model.objects.filter(is_active=True)
            .filter(Q(username__iexact=subject) | Q(email__iexact=subject))
            .order_by("pk")[:2]
        )
    if len(candidates) != 1:
        raise forbidden("Token subject does not resolve to one active user.")
    return candidates[0]


def _organization_access(user, company, branch):
    if user.is_superuser:
        return
    try:
        profile = UserOrganizationAccess.objects.get(user=user)
    except UserOrganizationAccess.DoesNotExist:
        profile = None
    company_allowed = bool(
        profile
        and (profile.all_companies or profile.companies.filter(pk=company.pk).exists())
    )
    branch_allowed = bool(
        profile
        and company_allowed
        and (profile.all_branches or profile.branches.filter(pk=branch.pk).exists())
    )
    if not company_allowed or not branch_allowed:
        employee_allowed = Employee.objects.filter(
            user=user,
            company=company,
            branch=branch,
            status=Employee.Status.ACTIVE,
        ).exists()
        if not employee_allowed:
            raise forbidden("Token user cannot access the requested company and branch.")


def ensure_organization_access(user, company, branch):
    """Public fail-closed organization gate for trusted in-process consumers."""
    if user is None or not user.is_authenticated or not user.is_active:
        raise forbidden("User cannot access the requested company and branch.")
    _organization_access(user, company, branch)


def resolve_principal(token):
    claims = decode_token(token)
    user = _resolve_user(str(claims["sub"]).strip())
    try:
        company_id = int(claims["company_id"])
        branch_id = int(claims["branch_id"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise unauthorized("Bearer token requires integer company_id and branch_id claims.") from exc
    company = Company.objects.filter(pk=company_id, active=True).first()
    branch = Branch.objects.filter(pk=branch_id, company_id=company_id, active=True).first()
    if company is None or branch is None:
        raise forbidden("Token organization is unavailable.")
    _organization_access(user, company, branch)
    context = OrganizationContext(
        company_id=company.pk,
        branch_id=branch.pk,
        allowed_company_ids=(company.pk,),
        allowed_branch_ids=(branch.pk,),
        selected_company_ids=(company.pk,),
        selected_branch_ids=(branch.pk,),
    )
    return BridgePrincipal(user=user, company=company, branch=branch, context=context, claims=claims)
