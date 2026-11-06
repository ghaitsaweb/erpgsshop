"""Safe local counterparts for the HMX business-flow generation endpoints."""

import json
import logging
import re

from django.db import transaction
from django.utils import timezone

from .business_flows import search_business_flows
from .knowledge import query_terms
from .models import (
    AIKnowledgeBusinessFlow,
    AIKnowledgeBusinessFlowGenerationJob,
    AIKnowledgeCompanyProfile,
    AIKnowledgeDocument,
)
from .services import request_completion


logger = logging.getLogger(__name__)

MAX_GENERATION_DOCUMENTS = 6
MAX_REASON_RESULTS = 5


class FlowGenerationError(ValueError):
    pass


def flow_generation_prerequisites(company_id, configuration):
    """Return bounded readiness facts without exposing provider credentials."""
    profile_present = AIKnowledgeCompanyProfile.objects.filter(company_id=company_id, active=True).exclude(
        summary="", operating_context=""
    ).exists()
    document_count = AIKnowledgeDocument.objects.filter(
        company_id=company_id,
        branch_id__isnull=True,
        active=True,
        status=AIKnowledgeDocument.Status.READY,
    ).count()
    flow_count = AIKnowledgeBusinessFlow.objects.filter(company_id=company_id).count()
    blockers = []
    if not configuration.is_ready:
        blockers.append("An active AI provider, HTTPS endpoint, model, and server credential are required.")
    if not profile_present and not document_count:
        blockers.append("Add a company profile or an approved company-wide knowledge document first.")
    return {
        "can_generate": not blockers,
        "blockers": blockers,
        "provider": {
            "configured": configuration.is_ready,
            "provider": configuration.provider,
            "model": configuration.model_name if configuration.is_ready else "",
        },
        "references": {
            "company_profile": profile_present,
            "company_wide_documents": document_count,
            "existing_flows": flow_count,
        },
    }


def _generation_context(company_id):
    profile = (
        AIKnowledgeCompanyProfile.objects.filter(company_id=company_id, active=True)
        .only("summary", "operating_context")
        .first()
    )
    documents = list(
        AIKnowledgeDocument.objects.filter(
            company_id=company_id,
            branch_id__isnull=True,
            active=True,
            status=AIKnowledgeDocument.Status.READY,
        )
        .only("title", "category", "extracted_text")
        .order_by("-updated_at", "-id")[:MAX_GENERATION_DOCUMENTS]
    )
    flows = list(
        AIKnowledgeBusinessFlow.objects.filter(company_id=company_id)
        .only("name", "status")
        .order_by("name", "id")[:50]
    )
    profile_text = ""
    if profile and (profile.summary or profile.operating_context):
        profile_text = f"Summary: {profile.summary[:2400]}\nOperating context: {profile.operating_context[:2400]}"
    document_text = "\n\n".join(
        f"[{document.category or 'General'}] {document.title}\n{document.extracted_text[:900]}"
        for document in documents
        if document.extracted_text
    )
    existing_names = [flow.name for flow in flows]
    snapshot = {
        "company_profile_present": bool(profile_text),
        "company_wide_document_count": len(documents),
        "existing_flow_count": len(flows),
    }
    return profile_text, document_text, existing_names, snapshot


def _generation_messages(company_id):
    profile_text, document_text, existing_names, snapshot = _generation_context(company_id)
    system = (
        "You draft one business-flow reference for ERP GSSHOP. It is documentation only and must never claim to "
        "execute ERP actions. Use only the supplied company reference material. Return only a JSON object with "
        "keys name, trigger, description, steps, and edges. steps must be an array of 1 to 20 objects with a "
        "description string. edges must be an array of objects with from_sequence, to_sequence, and optional label. "
        "Do not include markdown fences, credentials, personal data, or policy text that is absent from the reference."
    )
    reference = (
        "Company profile:\n"
        + (profile_text or "No company profile is available.")
        + "\n\nApproved company-wide documents:\n"
        + (document_text or "No approved company-wide documents are available.")
        + "\n\nExisting flow names (avoid duplicates unless explicitly overwriting):\n"
        + (", ".join(existing_names) or "None")
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": reference[:16000]}], snapshot


def _json_from_provider(content):
    candidate = (content or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE).strip()
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end < start:
        raise FlowGenerationError("The provider did not return a JSON business-flow proposal.")
    try:
        result = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise FlowGenerationError("The provider returned an invalid business-flow proposal.") from exc
    if not isinstance(result, dict):
        raise FlowGenerationError("The provider returned an invalid business-flow proposal.")
    return result.get("flow") if isinstance(result.get("flow"), dict) else result


def _normalized_flow_payload(content):
    raw = _json_from_provider(content)
    name = str(raw.get("name") or "").strip()[:180]
    trigger = str(raw.get("trigger") or "").strip()[:255]
    description = str(raw.get("description") or "").strip()[:6000]
    raw_steps = raw.get("steps")
    if not name or not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 20:
        raise FlowGenerationError("The provider proposal must contain a name and 1 to 20 steps.")
    steps = []
    for sequence, raw_step in enumerate(raw_steps, start=1):
        source = raw_step.get("description") if isinstance(raw_step, dict) else raw_step
        step = str(source or "").strip()[:500]
        if not step:
            raise FlowGenerationError("Every generated flow step must contain a description.")
        steps.append({"sequence": sequence, "description": step})
    edges = []
    raw_edges = raw.get("edges") or []
    if not isinstance(raw_edges, list) or len(raw_edges) > 40:
        raise FlowGenerationError("The provider proposal contains an invalid edge list.")
    for raw_edge in raw_edges:
        try:
            source = int(raw_edge.get("from_sequence"))
            target = int(raw_edge.get("to_sequence"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise FlowGenerationError("Each generated edge requires source and target steps.") from exc
        if not 1 <= source <= len(steps) or not 1 <= target <= len(steps):
            raise FlowGenerationError("A generated edge refers to a missing step.")
        edges.append({"from_sequence": source, "to_sequence": target, "label": str(raw_edge.get("label") or "")[:160]})
    if not edges:
        edges = [
            {"from_sequence": sequence, "to_sequence": sequence + 1, "label": "next"}
            for sequence in range(1, len(steps))
        ]
    return {"name": name, "trigger": trigger, "description": description, "steps": steps, "edges": edges}


def _failure_reason(exc):
    text = str(exc).strip()
    if not text or re.search(r"credential|api[ _-]?key|bearer", text, re.IGNORECASE):
        return "Business-flow generation could not be completed. Check the provider configuration and retry."
    return text[:500]


def execute_flow_generation(job_id):
    """Run exactly one bounded provider request and persist a reviewed-flow draft."""
    with transaction.atomic():
        job = (
            AIKnowledgeBusinessFlowGenerationJob.objects.select_for_update()
            .select_related("configuration")
            .filter(job_id=job_id)
            .first()
        )
        if job is None:
            return {"status": "ignored"}
        if job.state != AIKnowledgeBusinessFlowGenerationJob.State.QUEUED:
            return {"status": job.state, "job_id": str(job.job_id)}
        job.state = AIKnowledgeBusinessFlowGenerationJob.State.RUNNING
        job.started_at = timezone.now()
        job.error_reason = ""
        job.save(update_fields=["state", "started_at", "error_reason", "updated_at"])

    try:
        if job.configuration.company_id != job.company_id:
            raise FlowGenerationError("The generation configuration must belong to the job company.")
        prerequisites = flow_generation_prerequisites(job.company_id, job.configuration)
        if not prerequisites["can_generate"]:
            raise FlowGenerationError("Business-flow generation is no longer ready. Check the provider and approved references.")
        messages, snapshot = _generation_messages(job.company_id)
        result = request_completion(
            job.configuration,
            messages,
            user_agent="ERP-GSSHOP-Hashy-Flow-Generation/1.0",
        )
        candidate = _normalized_flow_payload(result["content"])
        with transaction.atomic():
            current_job = (
                AIKnowledgeBusinessFlowGenerationJob.objects.select_for_update()
                .select_related("company")
                .get(pk=job.pk)
            )
            existing_flow = AIKnowledgeBusinessFlow.objects.filter(
                company_id=current_job.company_id,
                name=candidate["name"],
            ).first()
            if existing_flow and not current_job.overwrite_manual:
                raise FlowGenerationError("A company business flow already uses the generated name.")
            flow = existing_flow or AIKnowledgeBusinessFlow(company_id=current_job.company_id)
            flow.name = candidate["name"]
            flow.trigger = candidate["trigger"]
            flow.description = candidate["description"]
            flow.steps = candidate["steps"]
            flow.edges = candidate["edges"]
            flow.status = AIKnowledgeBusinessFlow.Status.DRAFT
            flow.full_clean()
            flow.save()
            current_job.input_snapshot = snapshot
            current_job.result_flow = flow
            current_job.state = AIKnowledgeBusinessFlowGenerationJob.State.COMPLETED
            current_job.error_reason = ""
            current_job.input_tokens = result["input_tokens"]
            current_job.output_tokens = result["output_tokens"]
            current_job.latency_ms = result["latency_ms"]
            current_job.provider_message_id = result["provider_message_id"]
            current_job.completed_at = timezone.now()
            current_job.save()
            return {
                "status": current_job.state,
                "job_id": str(current_job.job_id),
                "business_flow_map_id": flow.pk,
            }
    except Exception as exc:
        logger.exception("Business-flow generation job %s failed", job_id)
        error_reason = _failure_reason(exc)
        AIKnowledgeBusinessFlowGenerationJob.objects.filter(
            pk=job.pk,
            state__in=[
                AIKnowledgeBusinessFlowGenerationJob.State.QUEUED,
                AIKnowledgeBusinessFlowGenerationJob.State.RUNNING,
            ],
        ).update(
            state=AIKnowledgeBusinessFlowGenerationJob.State.FAILED,
            error_reason=error_reason,
            completed_at=timezone.now(),
        )
        return {"status": AIKnowledgeBusinessFlowGenerationJob.State.FAILED, "job_id": str(job_id)}


def reason_about_business_flows(company_id, query):
    """Return evidence-based flow matches, never hidden provider reasoning."""
    terms = query_terms(query, min_length=2)
    if not terms:
        raise FlowGenerationError("Provide a business-flow question with at least one searchable word of two characters.")
    matches = []
    for match in search_business_flows(company_id, query, limit=MAX_REASON_RESULTS):
        flow = match["flow"]
        matches.append(
            {
                "id": flow.pk,
                "name": flow.name,
                "trigger": flow.trigger,
                "matched_terms": match["matched_terms"],
                "score": match["score"],
                "supporting_steps": match["supporting_steps"],
            }
        )
    confidence = min(100, round((matches[0]["score"] / max(1, len(terms) * 4)) * 100)) if matches else 0
    return {
        "query": query.strip()[:500],
        "matches": matches,
        "confidence": confidence,
        "reasoning_summary": {
            "kind": "decision_summary_not_raw_chain_of_thought",
            "decisions": [f"Matched {len(matches)} active reviewed flow(s) using their names, triggers, and steps."],
            "remaining_blockers": [] if matches else ["No active reviewed business flow matched this query."],
        },
    }
