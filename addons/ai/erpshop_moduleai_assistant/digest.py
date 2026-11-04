"""Bounded, local-first AI digest runtime.

Digest rules consume the privacy-filtered work-signal outbox rather than raw
business records.  A provider can later enrich a record through an explicit
governed adapter; this baseline always produces a useful auditable digest
without making an external request.
"""

from calendar import monthrange
from datetime import datetime, timedelta
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from .models import AIDigestRecord, AIDigestRule, AIWorkSignalOutbox


MAX_DIGEST_EVENTS = 100


def next_digest_run(rule, *, after=None):
    """Return the first scheduled run after ``after`` in the ERP time zone."""
    after = after or timezone.now()
    if rule.trigger_type == rule.Trigger.EVENT or not rule.schedule_frequency:
        return None
    local_after = timezone.localtime(after)
    run_at = rule.run_at or local_after.time().replace(second=0, microsecond=0)

    def candidate(day):
        return timezone.make_aware(datetime.combine(day, run_at), timezone.get_current_timezone())

    if rule.schedule_frequency == rule.Frequency.HOURLY:
        value = local_after.replace(minute=run_at.minute, second=run_at.second, microsecond=0)
        return value + timedelta(hours=1) if value <= local_after else value
    if rule.schedule_frequency == rule.Frequency.DAILY:
        value = candidate(local_after.date())
        return value + timedelta(days=1) if value <= local_after else value
    if rule.schedule_frequency == rule.Frequency.WEEKLY:
        value = candidate(local_after.date())
        return value + timedelta(days=7) if value <= local_after else value
    # Monthly runs keep the configured day where possible and clamp short months.
    desired_day = local_after.day
    value = candidate(local_after.date())
    if value > local_after:
        return value
    month = local_after.month + 1
    year = local_after.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    return candidate(local_after.replace(year=year, month=month, day=min(desired_day, monthrange(year, month)[1])).date())


def _allowed_action_keys(rule):
    return set(
        rule.sources.filter(active=True, actions__validity_state__in=["unknown", "valid"])
        .values_list("actions__canonical_action_key", flat=True)
    )


def _urgency(events):
    failures = sum(event.event_type.endswith("failed") for event in events)
    if failures >= 5:
        return AIDigestRecord.Urgency.CRITICAL, 90
    if failures >= 2:
        return AIDigestRecord.Urgency.HIGH, 70
    if failures:
        return AIDigestRecord.Urgency.MEDIUM, 45
    return AIDigestRecord.Urgency.LOW, 15


def run_digest_rule(*, rule_id, now=None):
    """Create exactly one digest record from a bounded safe event window."""
    now = now or timezone.now()
    with transaction.atomic():
        rule = AIDigestRule.objects.select_for_update().get(pk=rule_id, active=True)
        since = rule.last_run_at or now - timedelta(days=1)
        events = list(
            AIWorkSignalOutbox.objects.filter(company_id=rule.company_id, occurred_at__gt=since, occurred_at__lte=now)
            .order_by("occurred_at", "id")[:MAX_DIGEST_EVENTS]
        )
        allowed_actions = _allowed_action_keys(rule)
        if allowed_actions:
            events = [event for event in events if event.canonical_action in allowed_actions or event.event_type in allowed_actions]
        urgency, urgency_score = _urgency(events)
        evidence = [
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "canonical_action": event.canonical_action,
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in events
        ]
        failed = sum(event.event_type.endswith("failed") for event in events)
        summary = f"{len(events)} governed AI work signal(s) observed"
        if failed:
            summary += f"; {failed} require follow-up"
        record = AIDigestRecord.objects.create(
            company=rule.company,
            rule=rule,
            action="digest_run",
            summary=summary,
            narrative="This digest is generated from bounded, privacy-filtered AI work signals. It does not include raw ERP records or message content.",
            diagnosis=("Provider or worker failures occurred in the window." if failed else "No failed work signal was observed in the window."),
            evidence=evidence,
            timeline=evidence,
            urgency=urgency,
            urgency_score=urgency_score,
            confidence_score=1 if events else 0,
            recommended_actions=(
                ["Review failed AI tasks and provider configuration before retrying."] if failed else ["Review the next scheduled digest window."]
            ),
            trace_id=f"digest:{rule.pk}:{uuid4()}",
            job_id=f"digest-rule:{rule.pk}",
        )
        rule.last_run_at = now
        rule.save(update_fields=["last_run_at", "updated_at"])
    return record


def queue_due_digests(*, now=None, batch_size=25):
    now = now or timezone.now()
    rules = list(AIDigestRule.objects.filter(active=True).order_by("last_run_at", "id")[:batch_size])
    records = []
    for rule in rules:
        if rule.trigger_type == rule.Trigger.EVENT:
            continue
        due_at = next_digest_run(rule, after=rule.last_run_at) if rule.last_run_at else now
        if due_at <= now:
            records.append(run_digest_rule(rule_id=rule.pk, now=now))
    return records
