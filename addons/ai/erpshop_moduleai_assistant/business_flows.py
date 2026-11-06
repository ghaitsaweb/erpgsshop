"""Question-relevant, bounded retrieval of reviewed company business flows."""

from django.db.models import Case, IntegerField, Q, TextField, Value, When
from django.db.models.functions import Cast

from .knowledge import query_terms
from .models import AIKnowledgeBusinessFlow


MAX_FLOW_CANDIDATES = 100


def _flow_candidates(company_id, terms):
    # Rank before LIMIT so unrelated recent flows cannot hide older evidence.
    relevance = Value(0, output_field=IntegerField())
    for term in terms:
        relevance += Case(
            When(name__icontains=term, then=Value(4)),
            When(trigger__icontains=term, then=Value(2)),
            When(Q(description__icontains=term) | Q(steps_text__icontains=term), then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
    return (
        AIKnowledgeBusinessFlow.objects.filter(
            company_id=company_id, status=AIKnowledgeBusinessFlow.Status.ACTIVE
        )
        .alias(steps_text=Cast("steps", TextField()), relevance=relevance)
        .filter(relevance__gt=0)
        .only("id", "name", "trigger", "description", "steps")
        .order_by("-relevance", "name", "id")[:MAX_FLOW_CANDIDATES]
    )


def search_business_flows(company_id, query, *, limit=3):
    """Use one query, at most 10 terms and 100 candidates; exclude drafts."""
    terms = query_terms(query, min_length=2)
    if not terms:
        return []
    matches = []
    for flow in _flow_candidates(company_id, terms):
        steps = [
            str(step.get("description") or "")[:500]
            for step in (flow.steps or [])[:20]
            if isinstance(step, dict)
        ]
        fields = {
            "name": flow.name.casefold(),
            "trigger": flow.trigger.casefold(),
            "description": flow.description.casefold(),
            "steps": " ".join(steps).casefold(),
        }
        # JSON keys are not evidence: confirm matches against step text only.
        matched_terms = [term for term in terms if any(term in value for value in fields.values())]
        if not matched_terms:
            continue
        matches.append({
            "flow": flow,
            "matched_terms": matched_terms,
            "score": sum(4 if term in fields["name"] else 2 if term in fields["trigger"] else 1 for term in matched_terms),
            "supporting_steps": [step for step in steps if any(term in step.casefold() for term in matched_terms)][:3],
        })
    matches.sort(key=lambda item: (-item["score"], item["flow"].name, item["flow"].pk))
    return matches[:max(1, min(limit, MAX_FLOW_CANDIDATES))]
