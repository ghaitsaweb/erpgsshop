"""Privacy-bounded work intelligence derived from local assistant activity.

The HMX AI module records episodes, workflow aggregates, profile signals, and
outboxes.  This Django adaptation keeps the same useful lifecycle while
remaining local by default: it stores no prompt text, ERP values, or secrets,
and its outbox supports only an explicit mock delivery mode.
"""

from datetime import timedelta
from decimal import Decimal

from django.db import models, transaction
from django.db.models import Prefetch
from django.utils import timezone

from .models import (
    AIHashyInteractionSignalOutbox,
    AIWorkEpisode,
    AIWorkEpisodeEvent,
    AIWorkProfileRefreshRun,
    AIWorkProfileRefreshSetting,
    AIWorkProfileSignal,
    AIWorkSignalOutbox,
    AIWorkflowInteractionAggregate,
    AIWorkflowScopeAggregate,
)


MAX_EVIDENCE_IDS = 100
MAX_PROFILE_HINTS = 5


def _strength(confidence):
    if confidence >= Decimal("0.7500"):
        return "high"
    if confidence >= Decimal("0.5500"):
        return "medium"
    return "low"


def record_assistant_turn(*, session, request_message, response_message):
    """Persist an auditable episode without copying either message's content."""
    completed = response_message.state == response_message.State.COMPLETED
    state = AIWorkEpisode.State.COMPLETED if completed else AIWorkEpisode.State.FAILED
    now = timezone.now()
    episode = AIWorkEpisode.objects.create(
        company=session.company,
        actor=session.user,
        session=session,
        workflow_scope="assistant_chat",
        semantic_bucket="assistant_chat",
        source_ref=f"assistant-message:{request_message.pk}",
        state=state,
        summary="Hashy assistant turn",
        context={
            "request_message_id": request_message.pk,
            "has_page_context": bool(session.page_context),
            "attachment_count": request_message.attachments.count(),
        },
        outcome={
            "response_message_id": response_message.pk,
            "state": response_message.state,
            "input_tokens": response_message.input_tokens,
            "output_tokens": response_message.output_tokens,
            "latency_ms": response_message.latency_ms,
        },
        completed_at=now,
    )
    AIWorkEpisodeEvent.objects.bulk_create(
        [
            AIWorkEpisodeEvent(
                episode=episode,
                event_type=AIWorkEpisodeEvent.Type.INPUT,
                semantic_bucket="assistant_chat",
                payload={"message_id": request_message.pk, "has_attachment": request_message.attachments.exists()},
                occurred_at=request_message.created_at,
            ),
            AIWorkEpisodeEvent(
                episode=episode,
                event_type=AIWorkEpisodeEvent.Type.OUTCOME,
                semantic_bucket="assistant_chat",
                selected=completed,
                confidence=Decimal("1.0000") if completed else Decimal("0"),
                payload={
                    "response_message_id": response_message.pk,
                    "state": response_message.state,
                    "input_tokens": response_message.input_tokens,
                    "output_tokens": response_message.output_tokens,
                },
                occurred_at=now,
            ),
        ]
    )
    event_type = "assistant_turn_completed" if completed else "assistant_turn_failed"
    safe_payload = {
        "schema_version": "erpgsshop_work_signal_v1",
        "event_type": event_type,
        "workflow_scope": "assistant_chat",
        "state": response_message.state,
        "input_tokens": response_message.input_tokens,
        "output_tokens": response_message.output_tokens,
        "latency_ms": response_message.latency_ms,
    }
    AIWorkSignalOutbox.objects.create(
        company=session.company,
        actor=session.user,
        episode=episode,
        event_type=event_type,
        canonical_action="assistant_reply",
        workflow_scope="assistant_chat",
        source_ref=f"assistant-message:{request_message.pk}",
        payload=safe_payload,
        occurred_at=now,
    )
    AIHashyInteractionSignalOutbox.objects.create(
        company=session.company,
        actor=session.user,
        session=session,
        event_type=event_type,
        payload=safe_payload,
    )
    return episode


def _append_evidence(existing, episode_id):
    values = [int(value) for value in (existing or []) if str(value).isdigit()]
    if episode_id not in values:
        values.append(episode_id)
    return values[-MAX_EVIDENCE_IDS:]


def _refresh_episode(episode, *, touched_scopes, touched_interactions):
    events = list(episode.events.all())
    if not events:
        return 0, 0
    scope, _created = AIWorkflowScopeAggregate.objects.select_for_update().get_or_create(
        company=episode.company,
        actor=episode.actor,
        workflow_scope=episode.workflow_scope,
    )
    scope.episode_count += 1
    scope.completed_episode_count += int(episode.state == AIWorkEpisode.State.COMPLETED)
    scope.total_event_count += len(events)
    scope.decision_completed_count += sum(event.event_type == AIWorkEpisodeEvent.Type.OUTCOME for event in events)
    scope.approved_outcome_count += sum(
        event.event_type == AIWorkEpisodeEvent.Type.OUTCOME and event.selected for event in events
    )
    scope.rejected_outcome_count += sum(
        event.event_type == AIWorkEpisodeEvent.Type.OUTCOME and not event.selected for event in events
    )
    scope.last_episode_at = max(event.occurred_at for event in events)
    scope.save()
    touched_scopes.add((episode.company_id, episode.actor_id, episode.workflow_scope))

    interaction_updates = 0
    for event in events:
        if not (event.selected and event.semantic_bucket):
            continue
        aggregate, _created = AIWorkflowInteractionAggregate.objects.select_for_update().get_or_create(
            company=episode.company,
            actor=episode.actor,
            workflow_scope=episode.workflow_scope,
            semantic_bucket=event.semantic_bucket,
        )
        old_count = aggregate.selection_count
        old_total = aggregate.average_confidence * old_count
        aggregate.selection_count = old_count + 1
        aggregate.episode_count = max(aggregate.episode_count, 0) + 1
        aggregate.average_confidence = (old_total + event.confidence) / aggregate.selection_count
        aggregate.evidence_episode_ids = _append_evidence(aggregate.evidence_episode_ids, episode.pk)
        aggregate.last_event_at = event.occurred_at
        aggregate.save()
        touched_interactions.add((episode.company_id, episode.actor_id, episode.workflow_scope, event.semantic_bucket))
        interaction_updates += 1
    return 1, interaction_updates


def _upsert_scope_signal(*, company_id, actor_id, workflow_scope):
    scope = AIWorkflowScopeAggregate.objects.get(
        company_id=company_id,
        actor_id=actor_id,
        workflow_scope=workflow_scope,
    )
    if scope.episode_count < 3 or scope.total_event_count < 3:
        return False
    completion_ratio = Decimal(scope.completed_episode_count) / Decimal(max(scope.episode_count, 1))
    confidence = min(Decimal("1"), Decimal(scope.episode_count) / Decimal("5")) * Decimal("0.50")
    confidence += min(Decimal("1"), Decimal(scope.total_event_count) / Decimal("10")) * Decimal("0.30")
    confidence += completion_ratio * Decimal("0.20")
    confidence = confidence.quantize(Decimal("0.0001"))
    defaults = {
        "signal_type": "erp_workflow_behavior_pattern",
        "semantic_bucket": "workflow_activity",
        "confidence": confidence,
        "evidence_strength": _strength(confidence),
        "evidence_episode_count": scope.episode_count,
        "evidence_selection_count": scope.approved_outcome_count,
        "coverage_ratio": completion_ratio.quantize(Decimal("0.0001")),
        "summary": f"This user has repeated ERP activity in {workflow_scope}; use it only as workflow familiarity context.",
        "profile_payload": {
            "schema_version": "erpgsshop_work_profile_v1",
            "source": "ai_workflow_scope_aggregate",
            "runtime_instruction": "Use as a soft contextual hint only. Keep normal confirmations and permission checks.",
        },
        "active": True,
        "last_derived_at": timezone.now(),
    }
    AIWorkProfileSignal.objects.update_or_create(
        company_id=company_id,
        actor_id=actor_id,
        workflow_scope=workflow_scope,
        signal_key="frequent_erp_workflow_activity",
        defaults=defaults,
    )
    return True


def refresh_work_profiles(*, setting_id):
    """Process a bounded, cursor-free batch of newly updated local episodes."""
    with transaction.atomic():
        setting = AIWorkProfileRefreshSetting.objects.select_for_update().get(pk=setting_id)
        run = AIWorkProfileRefreshRun.objects.create(setting=setting)
        since = setting.last_run_at
        episodes_query = AIWorkEpisode.objects.filter(company_id=setting.company_id)
        if since:
            episodes_query = episodes_query.filter(updated_at__gt=since)
        episodes = list(
            episodes_query
            .select_related("company", "actor")
            .prefetch_related(Prefetch("events", queryset=AIWorkEpisodeEvent.objects.order_by("occurred_at", "id")))
            .order_by("updated_at", "id")[: setting.batch_size]
        )
        touched_scopes, touched_interactions = set(), set()
        updated_scopes = updated_interactions = 0
        for episode in episodes:
            scope_count, interaction_count = _refresh_episode(
                episode,
                touched_scopes=touched_scopes,
                touched_interactions=touched_interactions,
            )
            updated_scopes += scope_count
            updated_interactions += interaction_count
        signals = sum(
            _upsert_scope_signal(company_id=company_id, actor_id=actor_id, workflow_scope=workflow_scope)
            for company_id, actor_id, workflow_scope in touched_scopes
            if actor_id
        )
        setting.last_run_at = timezone.now()
        setting.save(update_fields=["last_run_at", "updated_at"])
        run.status = AIWorkProfileRefreshRun.Status.COMPLETED
        run.scanned_episodes = len(episodes)
        run.updated_aggregates = updated_scopes + updated_interactions
        run.upserted_signals = signals
        run.details = {"scope_groups": len(touched_scopes), "interaction_groups": len(touched_interactions)}
        run.completed_at = timezone.now()
        run.save()
    return {"run_id": run.pk, "scanned": len(episodes), "signals": signals}


def refresh_due_work_profiles(*, now=None, batch_size=25):
    now = now or timezone.now()
    settings = list(
        AIWorkProfileRefreshSetting.objects.filter(enabled=True)
        .order_by("id")[:batch_size]
    )
    due = [
        setting
        for setting in settings
        if setting.last_run_at is None or setting.last_run_at <= now - timedelta(minutes=setting.interval_minutes)
    ]
    return [refresh_work_profiles(setting_id=setting.pk) for setting in due]


def profile_context_for(*, company_id, actor_id):
    """Return a small, non-decisive provider context with no ERP record values."""
    return list(
        AIWorkProfileSignal.objects.filter(company_id=company_id, actor_id=actor_id, active=True)
        .order_by("-confidence", "-last_derived_at", "id")
        .values("workflow_scope", "summary", "confidence", "evidence_strength")[:MAX_PROFILE_HINTS]
    )


def export_pending_work_signals(*, company_id, batch_size=100, dry_run=True):
    """Build a privacy-filtered payload; real external delivery stays disabled."""
    rows = list(
        AIWorkSignalOutbox.objects.filter(company_id=company_id, status=AIWorkSignalOutbox.Status.PENDING)
        .order_by("id")[: max(0, min(int(batch_size), 500))]
    )
    payload = {
        "schema_version": "erpgsshop_work_signal_v1",
        "source": "erp_gsshop",
        "event_count": len(rows),
        "events": [dict(row.payload, event_id=row.event_id) for row in rows],
    }
    if dry_run or not rows:
        return {"dry_run": True, "selected": len(rows), "batch_payload": payload}
    now = timezone.now()
    AIWorkSignalOutbox.objects.filter(pk__in=[row.pk for row in rows]).update(
        status=AIWorkSignalOutbox.Status.SENT_MOCK,
        attempt_count=models.F("attempt_count") + 1,
        sent_at=now,
        updated_at=now,
    )
    return {"dry_run": False, "transport": "mock", "selected": len(rows), "sent": len(rows), "batch_payload": payload}
