"""Inbound tax-authority callbacks with replay and signature protection."""

import json

from django.core.exceptions import ValidationError
from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import CountryTaxProfile, TaxAuthorityCallbackEvent
from .services import apply_tax_authority_callback


@csrf_exempt
def tax_authority_callback(request, profile_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if len(request.body) > 1_048_576:
        return JsonResponse({"error": "Callback payload exceeds 1 MiB."}, status=413)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "Callback body must be a JSON object."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "Callback body must be a JSON object."}, status=400)
    profile = CountryTaxProfile.objects.filter(pk=profile_id, active=True).only("pk").first()
    if profile is None:
        return JsonResponse({"error": "Tax profile was not found."}, status=404)
    event_id = str(
        request.headers.get("X-Event-ID") or payload.get("event_id") or payload.get("id") or ""
    ).strip()
    if not event_id:
        return JsonResponse({"error": "A stable event id is required."}, status=400)
    signature = str(request.headers.get("X-Signature") or request.headers.get("X-Hub-Signature-256") or "")
    external_reference = str(
        payload.get("external_reference") or payload.get("reference") or payload.get("uuid") or ""
    ).strip()
    event, created = TaxAuthorityCallbackEvent.objects.get_or_create(
        profile=profile,
        event_id=event_id[:160],
        defaults={
            "event_type": str(payload.get("event_type") or payload.get("type") or "status")[:80],
            "external_reference": external_reference[:160],
            "signature": signature[:255],
            "payload": payload,
        },
    )
    if not created and (event.payload != payload or event.signature != signature[:255]):
        if event.status in {
            TaxAuthorityCallbackEvent.Status.APPLIED,
            TaxAuthorityCallbackEvent.Status.IGNORED,
        }:
            return JsonResponse({"error": "Event id was already used with a different payload."}, status=409)
        event.signature = signature[:255]
        event.payload = payload
        event.external_reference = external_reference[:160]
        event.save(update_fields=["signature", "payload", "external_reference", "updated_at"])
    try:
        event = apply_tax_authority_callback(event)
    except ValidationError as exc:
        message = "; ".join(exc.messages)
        return JsonResponse({"event_id": event.event_id, "status": "failed", "error": message}, status=400)
    return JsonResponse({"event_id": event.event_id, "status": event.status}, status=200)
