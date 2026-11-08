"""HMX-compatible, company-scoped Base Knowledge API.

The HMX module proxies these paths to Hashy. ERP GS Shop keeps the equivalent
knowledge locally, so this adapter preserves the route and response contract
without weakening the local company, branch, permission, or size boundaries.
"""

from __future__ import annotations

import hashlib
import json
from functools import wraps
from pathlib import Path

from django.contrib.postgres.search import SearchQuery, SearchVector
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Subquery
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from modules.apphub.models import ERPApp
from modules.organization.models import Branch

from .flow_generation import FlowGenerationError, flow_generation_prerequisites, reason_about_business_flows
from .knowledge import query_terms, rebuild_document_chunks
from .knowledge_graph import (
    build_knowledge_graph,
    graph_neighbors,
    graph_node,
    graph_overview,
    graph_path,
    graph_search,
    graph_trace,
)
from .models import (
    AIKnowledgeBusinessFlow,
    AIKnowledgeBusinessFlowGenerationJob,
    AIKnowledgeCategory,
    AIKnowledgeChunk,
    AIKnowledgeCompanyProfile,
    AIKnowledgeDocument,
    AIKnowledgeRecordModelMapping,
    AssistantConfiguration,
)
from .tasks import generate_business_flow as queue_business_flow_generation
from .views import APP_SLUG, EXTRACTABLE_CONTENT_TYPES, MAX_KNOWLEDGE_TEXT_BYTES, _active_company, _can_configure


MAX_JSON_BYTES = 64 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
MAX_PAGE = 100


def _response(data=None, *, status=200, message="", **extra):
    payload = {"status": status < 400, "data": data}
    if message:
        payload["message"] = message
    payload.update(extra)
    return JsonResponse(payload, status=status)


def _error(message, *, status=400, code="BASE_KNOWLEDGE_ERROR", details=None):
    payload = {"status": False, "data": None, "message": str(message), "code": code}
    if details:
        payload["errors"] = details
    return JsonResponse(payload, status=status)


def _validation_error(exc):
    if hasattr(exc, "message_dict"):
        return _error("Validation failed.", details=exc.message_dict)
    return _error("Validation failed.", details={"non_field_errors": exc.messages})


def _knowledge_api(view):
    """Return JSON-only access failures and require knowledge-admin rights."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _error("Authentication required.", status=401, code="AUTHENTICATION_REQUIRED")
        if not ERPApp.objects.filter(slug=APP_SLUG, enabled=True).exists():
            return _error("Hashy Assistant is not enabled.", status=404, code="APP_NOT_ENABLED")
        if not _can_configure(request.user):
            return _error("Assistant administrator access is required.", status=403, code="FORBIDDEN")
        try:
            request.knowledge_company_id = _active_company(request)
        except PermissionError as exc:
            return _error(str(exc), status=400, code="COMPANY_REQUIRED")
        return view(request, *args, **kwargs)

    return wrapped


def _json_body(request):
    raw = request.body or b""
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("Request data must be at most 64 KB.")
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Request data must be a JSON object.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request data must be a JSON object.")
    payload.pop("odoo_service_id", None)
    payload.pop("service_id", None)
    return payload


def _text(payload, *keys, default="", maximum=None):
    value = next((payload[key] for key in keys if key in payload), default)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{keys[0]} must be text.")
    value = value.strip()
    if maximum is not None and len(value) > maximum:
        raise ValueError(f"{keys[0]} must be at most {maximum} characters.")
    return value


def _boolean(payload, *keys, default=False):
    value = next((payload[key] for key in keys if key in payload), default)
    if not isinstance(value, bool):
        raise ValueError(f"{keys[0]} must be a boolean.")
    return value


def _positive_int(value, name, *, default=None, maximum=None):
    if value in (None, ""):
        return default
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if result < 1 or (maximum is not None and result > maximum):
        suffix = f" no greater than {maximum}" if maximum is not None else ""
        raise ValueError(f"{name} must be a positive integer{suffix}.")
    return result


def _pagination(request):
    page = _positive_int(request.GET.get("page"), "page", default=1, maximum=MAX_PAGE)
    limit = _positive_int(request.GET.get("limit"), "limit", default=DEFAULT_PAGE_SIZE, maximum=MAX_PAGE_SIZE)
    return page, limit, (page - 1) * limit


def _candidate_branch_ids(request):
    context = getattr(request, "organization_context", None)
    return tuple(getattr(context, "active_branch_ids", ()) or ())


def _allowed_branches(request):
    return Branch.objects.filter(
        company_id=request.knowledge_company_id,
        pk__in=_candidate_branch_ids(request),
        active=True,
    )


def _branch_scope(request):
    allowed_branch_ids = _allowed_branches(request).values_list("pk", flat=True)
    return Q(branch_id__isnull=True) | Q(branch_id__in=allowed_branch_ids)


def _requested_branch(request, raw_value, *, allow_company_wide=True):
    if raw_value in (None, ""):
        if allow_company_wide:
            return None
        raise ValueError("division_id is required.")
    branch_id = _positive_int(raw_value, "division_id")
    branch = Branch.objects.filter(company_id=request.knowledge_company_id, pk=branch_id).first()
    if branch is None:
        raise LookupError("Division not found in the active company.")
    if not request.user.is_superuser and not _allowed_branches(request).filter(pk=branch_id).exists():
        raise PermissionError("The selected division is outside the active organization scope.")
    return branch


def _timestamps(instance):
    return {
        "created_at": instance.created_at.isoformat() if instance.created_at else None,
        "updated_at": instance.updated_at.isoformat() if instance.updated_at else None,
    }


def _profile_payload(profile):
    if profile is None:
        return None
    return {
        "id": profile.pk,
        "company_id": profile.company_id,
        "content": profile.summary,
        "summary": profile.summary,
        "operating_context": profile.operating_context,
        "status": "active" if profile.active else "archived",
        "active": profile.active,
        **_timestamps(profile),
    }


def _division_payload(branch):
    return {
        "id": branch.pk,
        "company_id": branch.company_id,
        "code": branch.code,
        "name": branch.name,
        "parent_id": branch.parent_id,
        "email": branch.email,
        "phone": branch.phone,
        "address": branch.address,
        "sequence": branch.sequence,
        "is_main": branch.is_main,
        "active": branch.active,
        "status": "active" if branch.active else "archived",
        **_timestamps(branch),
    }


def _category_payload(category):
    return {
        "id": category.pk,
        "category": category.name,
        "name": category.name,
        "description": category.description,
        "active": category.active,
        "status": "active" if category.active else "archived",
        **_timestamps(category),
    }


def _document_payload(document):
    metadata = document.metadata if isinstance(document.metadata, dict) else {}
    return {
        "id": document.pk,
        "document_title": document.title,
        "title": document.title,
        "division_id": document.branch_id,
        "division": _division_payload(document.branch) if document.branch_id else None,
        "category": document.category,
        "document_type": metadata.get("document_type", "file" if document.file else "manual"),
        "lifecycle_status": "active" if document.active else "archived",
        "status": document.status,
        "active": document.active,
        "content": document.extracted_text,
        "extracted_text": document.extracted_text,
        "original_name": document.original_name,
        "content_type": document.content_type,
        "byte_size": document.byte_size,
        "content_sha256": document.content_sha256,
        "metadata": metadata,
        "error_message": document.error_message,
        **_timestamps(document),
    }


def _flow_payload(flow):
    return {
        "id": flow.pk,
        "name": flow.name,
        "trigger": flow.trigger,
        "description": flow.description,
        "steps": flow.steps,
        "edges": flow.edges,
        "status": flow.status,
        **_timestamps(flow),
    }


def _mapping_payload(mapping):
    return {
        "id": mapping.pk,
        "record_model": mapping.record_model,
        "display_name": mapping.display_name,
        "division_id": mapping.branch_id,
        "category": mapping.category,
        "module_name": mapping.module_name,
        "feature_tags": mapping.feature_tags,
        "business_flow_id": mapping.business_flow_id,
        "active": mapping.active,
        "status": "active" if mapping.active else "archived",
        **_timestamps(mapping),
    }


def _job_payload(job):
    return {
        "job_id": str(job.job_id),
        "status": job.state,
        "error_reason": job.error_reason,
        "business_flow_map_id": job.result_flow_id,
        "flow_id": job.result_flow_id,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _flow_map_payload(flow):
    nodes = {}
    for position, raw_step in enumerate(flow.steps if isinstance(flow.steps, list) else [], start=1):
        source = raw_step if isinstance(raw_step, dict) else {"description": raw_step}
        try:
            sequence = int(source.get("sequence", position))
        except (TypeError, ValueError):
            sequence = position
        description = str(source.get("description") or "").strip()
        if description:
            nodes[sequence] = {
                "label": description,
                "description": description,
                "sort_order": sequence,
                "metadata": {"sequence": sequence},
            }
    configured_edges = flow.edges if isinstance(flow.edges, list) else []
    if not configured_edges:
        sequences = sorted(nodes)
        configured_edges = [
            {"from_sequence": source, "to_sequence": target, "label": "next"}
            for source, target in zip(sequences, sequences[1:])
        ]
    edges = []
    for raw_edge in configured_edges[:40]:
        try:
            source_sequence = int(raw_edge.get("from_sequence"))
            target_sequence = int(raw_edge.get("to_sequence"))
        except (AttributeError, TypeError, ValueError):
            continue
        source, target = nodes.get(source_sequence), nodes.get(target_sequence)
        if source and target:
            edges.append(
                {
                    "source_node_label": source["label"],
                    "target_node_label": target["label"],
                    "label": str(raw_edge.get("label") or "next")[:160],
                    "metadata": {"source_sequence": source_sequence, "target_sequence": target_sequence},
                }
            )
    return {
        "id": flow.pk,
        "name": flow.name,
        "description": flow.description,
        "nodes": [nodes[sequence] for sequence in sorted(nodes)],
        "edges": edges,
    }


@require_http_methods(["GET", "POST", "PUT"])
@_knowledge_api
def company_profile(request):
    company_id = request.knowledge_company_id
    if request.method == "GET":
        profile = AIKnowledgeCompanyProfile.objects.filter(company_id=company_id).first()
        if profile is None:
            return _error("Company profile not found.", status=404, code="NOT_FOUND")
        return _response(_profile_payload(profile))
    try:
        payload = _json_body(request)
        profile, _ = AIKnowledgeCompanyProfile.objects.get_or_create(company_id=company_id)
        profile.summary = _text(payload, "summary", "content", default=profile.summary, maximum=12_000)
        profile.operating_context = _text(
            payload, "operating_context", "context", default=profile.operating_context, maximum=12_000
        )
        status = payload.get("status")
        if status is not None and status not in {"active", "archived"}:
            raise ValueError("status must be active or archived.")
        profile.active = status != "archived" if status is not None else _boolean(
            payload, "active", default=profile.active
        )
        profile.full_clean()
        profile.save()
        return _response(_profile_payload(profile), message="Company profile saved.")
    except (ValueError, ValidationError) as exc:
        return _validation_error(exc) if isinstance(exc, ValidationError) else _error(str(exc))


@require_http_methods(["POST", "PUT"])
@_knowledge_api
def company_profile_update(request):
    return company_profile.__wrapped__(request)


@require_http_methods(["GET", "POST"])
@_knowledge_api
def divisions(request):
    company_id = request.knowledge_company_id
    if request.method == "GET":
        rows = list(Branch.objects.filter(company_id=company_id).order_by("sequence", "name", "id")[:500])
        return _response({"items": [_division_payload(row) for row in rows], "count": len(rows), "limit": 500})
    if not request.user.has_perm("organization.add_branch") and not request.user.is_superuser:
        return _error("Organization branch creation permission is required.", status=403, code="FORBIDDEN")
    try:
        payload = _json_body(request)
        branch = Branch(
            company_id=company_id,
            code=_text(payload, "code", maximum=30),
            name=_text(payload, "name", maximum=160),
            email=_text(payload, "email", maximum=254),
            phone=_text(payload, "phone", maximum=40),
            address=_text(payload, "address", maximum=10_000),
            sequence=_positive_int(payload.get("sequence"), "sequence", default=10, maximum=1_000_000),
            is_main=_boolean(payload, "is_main", default=False),
            active=_boolean(payload, "active", default=True),
        )
        parent_id = payload.get("parent_id")
        if parent_id not in (None, ""):
            branch.parent = _requested_branch(request, parent_id, allow_company_wide=False)
        branch.full_clean()
        branch.save()
        return _response(_division_payload(branch), status=201, message="Division created.")
    except (ValueError, ValidationError) as exc:
        return _validation_error(exc) if isinstance(exc, ValidationError) else _error(str(exc))
    except (LookupError, PermissionError) as exc:
        return _error(str(exc), status=404 if isinstance(exc, LookupError) else 403)
    except IntegrityError:
        return _error("A division with this code already exists.", status=409, code="CONFLICT")


@require_http_methods(["POST", "PUT", "DELETE"])
@_knowledge_api
def division_detail(request, division_id, delete_alias=False):
    if not request.user.has_perm("organization.change_branch") and not request.user.is_superuser:
        return _error("Organization branch management permission is required.", status=403, code="FORBIDDEN")
    branch = Branch.objects.filter(company_id=request.knowledge_company_id, pk=division_id).first()
    if branch is None:
        return _error("Division not found.", status=404, code="NOT_FOUND")
    if request.method == "DELETE" or delete_alias:
        if branch.is_main:
            return _error("The main division cannot be archived.", status=409, code="CONFLICT")
        branch.active = False
        branch.save(update_fields=["active", "updated_at"])
        return _response(_division_payload(branch), message="Division archived; no organization data was deleted.")
    try:
        payload = _json_body(request)
        for key, field, maximum in (
            ("code", "code", 30), ("name", "name", 160), ("email", "email", 254),
            ("phone", "phone", 40), ("address", "address", 10_000),
        ):
            if key in payload:
                setattr(branch, field, _text(payload, key, maximum=maximum))
        if "sequence" in payload:
            branch.sequence = _positive_int(payload["sequence"], "sequence", maximum=1_000_000)
        if "active" in payload:
            branch.active = _boolean(payload, "active")
        if "parent_id" in payload:
            branch.parent = _requested_branch(request, payload["parent_id"]) if payload["parent_id"] else None
        branch.full_clean()
        branch.save()
        return _response(_division_payload(branch), message="Division updated.")
    except (ValueError, ValidationError) as exc:
        return _validation_error(exc) if isinstance(exc, ValidationError) else _error(str(exc))
    except (LookupError, PermissionError) as exc:
        return _error(str(exc), status=404 if isinstance(exc, LookupError) else 403)
    except IntegrityError:
        return _error("The division update conflicts with an existing record.", status=409, code="CONFLICT")


@require_http_methods(["POST", "DELETE"])
@_knowledge_api
def division_delete_alias(request, division_id):
    return division_detail.__wrapped__(request, division_id, delete_alias=True)


@require_http_methods(["GET", "POST"])
@_knowledge_api
def categories(request):
    company_id = request.knowledge_company_id
    if request.method == "GET":
        search = request.GET.get("search", "").strip()[:100]
        queryset = AIKnowledgeCategory.objects.filter(company_id=company_id)
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(description__icontains=search))
        rows = list(queryset.order_by("name", "id")[:500])
        return _response({"items": [_category_payload(row) for row in rows], "count": len(rows), "limit": 500})
    try:
        payload = _json_body(request)
        category = AIKnowledgeCategory(
            company_id=company_id,
            name=_text(payload, "category", "name", maximum=100),
            description=_text(payload, "description", maximum=500),
            active=_boolean(payload, "active", default=True),
        )
        category.full_clean()
        category.save()
        return _response(_category_payload(category), status=201, message="Category created.")
    except (ValueError, ValidationError) as exc:
        return _validation_error(exc) if isinstance(exc, ValidationError) else _error(str(exc))
    except IntegrityError:
        return _error("A category with this name already exists.", status=409, code="CONFLICT")


def _category_for_token(company_id, token):
    queryset = AIKnowledgeCategory.objects.filter(company_id=company_id)
    try:
        category_id = int(token)
    except (TypeError, ValueError):
        return queryset.filter(name=token).first()
    return queryset.filter(Q(pk=category_id) | Q(name=token)).first()


@require_http_methods(["POST", "PUT", "DELETE"])
@_knowledge_api
def category_detail(request, category, delete_alias=False):
    instance = _category_for_token(request.knowledge_company_id, category)
    if instance is None:
        return _error("Category not found.", status=404, code="NOT_FOUND")
    if request.method == "DELETE" or delete_alias:
        instance.active = False
        instance.save(update_fields=["active", "updated_at"])
        return _response(_category_payload(instance), message="Category archived; document labels were retained.")
    try:
        payload = _json_body(request)
        if "category" in payload or "name" in payload:
            instance.name = _text(payload, "category", "name", maximum=100)
        if "description" in payload:
            instance.description = _text(payload, "description", maximum=500)
        if "active" in payload:
            instance.active = _boolean(payload, "active")
        instance.full_clean()
        instance.save()
        return _response(_category_payload(instance), message="Category updated.")
    except (ValueError, ValidationError) as exc:
        return _validation_error(exc) if isinstance(exc, ValidationError) else _error(str(exc))
    except IntegrityError:
        return _error("The category name already exists.", status=409, code="CONFLICT")


@require_http_methods(["POST", "DELETE"])
@_knowledge_api
def category_delete_alias(request, category):
    return category_detail.__wrapped__(request, category, delete_alias=True)


def _document_queryset(request):
    return (
        AIKnowledgeDocument.objects.filter(company_id=request.knowledge_company_id)
        .filter(_branch_scope(request))
        .select_related("branch")
    )


def _document_status(payload, *, has_content, current=None):
    lifecycle = payload.get("lifecycle_status")
    status = payload.get("status")
    if lifecycle not in (None, "", "active", "archived"):
        raise ValueError("lifecycle_status must be active or archived.")
    if status in (None, ""):
        if lifecycle == "archived":
            return AIKnowledgeDocument.Status.ARCHIVED, False
        if lifecycle == "active":
            return AIKnowledgeDocument.Status.READY if has_content else AIKnowledgeDocument.Status.PENDING, True
        resolved = current or (AIKnowledgeDocument.Status.READY if has_content else AIKnowledgeDocument.Status.PENDING)
        return resolved, resolved != AIKnowledgeDocument.Status.ARCHIVED
    if status not in AIKnowledgeDocument.Status.values:
        raise ValueError("status is not a supported document state.")
    return status, status != AIKnowledgeDocument.Status.ARCHIVED and lifecycle != "archived"


def _apply_document_payload(request, document, payload, *, creating=False):
    if creating or "document_title" in payload or "title" in payload:
        document.title = _text(payload, "document_title", "title", maximum=255)
    if "division_id" in payload or "division" in payload:
        document.branch = _requested_branch(request, payload.get("division_id", payload.get("division")))
    if creating or "category" in payload:
        document.category = _text(payload, "category", maximum=100)
    if "content" in payload or "extracted_text" in payload:
        document.extracted_text = _text(
            payload, "content", "extracted_text", maximum=MAX_KNOWLEDGE_TEXT_BYTES
        )
    if "metadata" in payload:
        if not isinstance(payload["metadata"], dict):
            raise ValueError("metadata must be a JSON object.")
        document.metadata = payload["metadata"]
    metadata = dict(document.metadata or {})
    if "document_type" in payload:
        metadata["document_type"] = _text(payload, "document_type", maximum=40)
    metadata.setdefault("ingestion", "hmx_compat_api")
    metadata.setdefault("created_by_user_id", request.user.pk)
    document.metadata = metadata
    document.content_sha256 = (
        hashlib.sha256(document.extracted_text.encode("utf-8")).hexdigest() if document.extracted_text else ""
    )
    document.status, document.active = _document_status(
        payload,
        has_content=bool(document.extracted_text or document.file),
        current=None if creating else document.status,
    )
    document.error_message = "" if document.status != AIKnowledgeDocument.Status.FAILED else document.error_message
    document.full_clean()


@require_http_methods(["GET", "POST"])
@_knowledge_api
def documents(request):
    if request.method == "GET":
        try:
            page, limit, offset = _pagination(request)
            queryset = _document_queryset(request)
            division_id = request.GET.get("division_id")
            if division_id not in (None, ""):
                branch = _requested_branch(request, division_id, allow_company_wide=False)
                queryset = queryset.filter(branch_id=branch.pk)
            search = request.GET.get("search", "").strip()[:120]
            if search:
                terms = query_terms(search)
                if terms:
                    search_query = SearchQuery(" | ".join(terms), config="simple", search_type="raw")
                    matching_documents = (
                        AIKnowledgeChunk.objects.filter(company_id=request.knowledge_company_id)
                        .annotate(search_vector=SearchVector("content", config="simple"))
                        .filter(search_vector=search_query)
                        .values("document_id")
                    )
                    queryset = queryset.filter(
                        Q(title__iexact=search) | Q(pk__in=Subquery(matching_documents))
                    )
                else:
                    queryset = queryset.filter(title__iexact=search)
            category = request.GET.get("category", "").strip()[:100]
            if category:
                queryset = queryset.filter(category__iexact=category)
            status = request.GET.get("status", "").strip()
            if status:
                if status not in AIKnowledgeDocument.Status.values:
                    raise ValueError("status is not a supported document state.")
                queryset = queryset.filter(status=status)
            lifecycle = request.GET.get("lifecycle_status", "").strip()
            if lifecycle:
                if lifecycle not in {"active", "archived"}:
                    raise ValueError("lifecycle_status must be active or archived.")
                queryset = queryset.filter(active=lifecycle == "active")
            rows = list(queryset.order_by("id")[offset : offset + limit + 1])
            has_next = len(rows) > limit
            items = [_document_payload(row) for row in rows[:limit]]
            return _response(
                {
                    "items": items,
                    "page": page,
                    "limit": limit,
                    "has_next": has_next,
                    "next_page": page + 1 if has_next else None,
                }
            )
        except (ValueError, LookupError, PermissionError) as exc:
            status = 404 if isinstance(exc, LookupError) else 403 if isinstance(exc, PermissionError) else 400
            return _error(str(exc), status=status)
    try:
        payload = _json_body(request)
        document = AIKnowledgeDocument(company_id=request.knowledge_company_id)
        _apply_document_payload(request, document, payload, creating=True)
        with transaction.atomic():
            document.save()
            rebuild_document_chunks(document)
        return _response(_document_payload(document), status=201, message="Knowledge document created.")
    except (ValueError, ValidationError) as exc:
        return _validation_error(exc) if isinstance(exc, ValidationError) else _error(str(exc))
    except (LookupError, PermissionError) as exc:
        return _error(str(exc), status=404 if isinstance(exc, LookupError) else 403)
    except IntegrityError:
        return _error("A document with this title already exists.", status=409, code="CONFLICT")


@require_http_methods(["GET"])
@_knowledge_api
def base_documents(request):
    return documents.__wrapped__(request)


@require_http_methods(["POST"])
@_knowledge_api
def upload_document(request):
    upload = request.FILES.get("file")
    if upload is None:
        return _error("File is required.", code="MISSING_FILE")
    if upload.size > MAX_UPLOAD_BYTES:
        return _error("Knowledge files must be at most 10 MB.", code="FILE_TOO_LARGE")
    try:
        raw_metadata = request.POST.get("metadata", "")
        metadata = json.loads(raw_metadata) if raw_metadata else {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a JSON object.")
        branch = _requested_branch(request, request.POST.get("division_id") or request.POST.get("division"))
        content_type = (getattr(upload, "content_type", "") or "")[:120].lower()
        extracted_text = ""
        digest = ""
        if content_type in EXTRACTABLE_CONTENT_TYPES:
            raw = upload.read(MAX_KNOWLEDGE_TEXT_BYTES + 1)
            upload.seek(0)
            if len(raw) > MAX_KNOWLEDGE_TEXT_BYTES:
                raise ValueError("Plain-text knowledge files must be at most 200 KB after extraction.")
            extracted_text = raw.decode("utf-8", errors="replace").strip()
            digest = hashlib.sha256(raw).hexdigest()
        metadata.setdefault("ingestion", "hmx_compat_api_upload")
        metadata.setdefault("created_by_user_id", request.user.pk)
        if request.POST.get("document_type"):
            metadata["document_type"] = request.POST["document_type"][:40]
        document = AIKnowledgeDocument(
            company_id=request.knowledge_company_id,
            branch=branch,
            title=(request.POST.get("document_title") or request.POST.get("title") or Path(upload.name).name)[:255],
            category=request.POST.get("category", "").strip()[:100],
            file=upload,
            original_name=Path(upload.name).name[:255],
            content_type=content_type,
            byte_size=upload.size,
            extracted_text=extracted_text,
            content_sha256=digest,
            metadata=metadata,
            status=AIKnowledgeDocument.Status.READY if extracted_text else AIKnowledgeDocument.Status.PENDING,
        )
        lifecycle = request.POST.get("lifecycle_status")
        if lifecycle not in (None, "", "active", "archived"):
            raise ValueError("lifecycle_status must be active or archived.")
        if lifecycle == "archived":
            document.active = False
            document.status = AIKnowledgeDocument.Status.ARCHIVED
        document.full_clean()
        with transaction.atomic():
            document.save()
            rebuild_document_chunks(document)
        result = _document_payload(document)
        result["job_id"] = f"local-{document.pk}"
        return _response(result, status=202, message="Knowledge upload accepted.")
    except (ValueError, ValidationError, json.JSONDecodeError) as exc:
        return _validation_error(exc) if isinstance(exc, ValidationError) else _error(str(exc))
    except (LookupError, PermissionError) as exc:
        return _error(str(exc), status=404 if isinstance(exc, LookupError) else 403)
    except IntegrityError:
        return _error("A document with this title already exists.", status=409, code="CONFLICT")


@require_http_methods(["POST", "PUT", "DELETE"])
@_knowledge_api
def document_detail(request, document_id):
    document = _document_queryset(request).filter(pk=document_id).first()
    if document is None:
        return _error("Knowledge document not found.", status=404, code="NOT_FOUND")
    if request.method == "DELETE":
        document.active = False
        document.status = AIKnowledgeDocument.Status.ARCHIVED
        document.save(update_fields=["active", "status", "updated_at"])
        return _response(_document_payload(document), message="Knowledge document archived; content was retained.")
    try:
        payload = _json_body(request)
        previous_text = document.extracted_text
        _apply_document_payload(request, document, payload)
        with transaction.atomic():
            document.save()
            if previous_text != document.extracted_text:
                rebuild_document_chunks(document)
        return _response(_document_payload(document), message="Knowledge document updated.")
    except (ValueError, ValidationError) as exc:
        return _validation_error(exc) if isinstance(exc, ValidationError) else _error(str(exc))
    except (LookupError, PermissionError) as exc:
        return _error(str(exc), status=404 if isinstance(exc, LookupError) else 403)
    except IntegrityError:
        return _error("The document update conflicts with an existing record.", status=409, code="CONFLICT")


@require_http_methods(["POST"])
@_knowledge_api
def document_retry(request, document_id):
    document = _document_queryset(request).filter(pk=document_id).first()
    if document is None:
        return _error("Knowledge document not found.", status=404, code="NOT_FOUND")
    document.active = True
    document.status = (
        AIKnowledgeDocument.Status.READY if document.extracted_text else AIKnowledgeDocument.Status.PENDING
    )
    document.error_message = ""
    with transaction.atomic():
        document.save(update_fields=["active", "status", "error_message", "updated_at"])
        if document.extracted_text:
            rebuild_document_chunks(document)
    return _response(_document_payload(document), message="Knowledge document retry accepted.")


@require_http_methods(["GET"])
@_knowledge_api
def document_upload_job(request, job_id):
    document = None
    if job_id.startswith("local-"):
        try:
            document_id = int(job_id.removeprefix("local-"))
        except ValueError:
            document_id = None
        if document_id is not None:
            document = _document_queryset(request).filter(pk=document_id).first()
    if document is None:
        return _error("Knowledge upload job not found.", status=404, code="UPLOAD_JOB_NOT_FOUND")
    return _response(
        {
            "job_id": job_id,
            "status": "completed" if document.status != AIKnowledgeDocument.Status.FAILED else "failed",
            "document": _document_payload(document),
        }
    )


@require_http_methods(["POST", "DELETE"])
@_knowledge_api
def document_delete(request, document_id):
    document = _document_queryset(request).filter(pk=document_id).first()
    if document is None:
        return _error("Knowledge document not found.", status=404, code="NOT_FOUND")
    document.active = False
    document.status = AIKnowledgeDocument.Status.ARCHIVED
    document.save(update_fields=["active", "status", "updated_at"])
    return _response(_document_payload(document), message="Knowledge document archived; content was retained.")


def _normalize_steps(raw_steps):
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 20:
        raise ValueError("steps must contain 1 to 20 entries.")
    steps = []
    for sequence, raw in enumerate(raw_steps, start=1):
        source = raw.get("description") if isinstance(raw, dict) else raw
        description = str(source or "").strip()
        if not description or len(description) > 500:
            raise ValueError(f"Step {sequence} must contain up to 500 characters.")
        steps.append({"sequence": sequence, "description": description})
    return steps


def _normalize_edges(raw_edges, step_count):
    if raw_edges in (None, []):
        return [
            {"from_sequence": sequence, "to_sequence": sequence + 1, "label": "next"}
            for sequence in range(1, step_count)
        ]
    if not isinstance(raw_edges, list) or len(raw_edges) > 40:
        raise ValueError("edges must contain no more than 40 entries.")
    edges = []
    for raw in raw_edges:
        try:
            source, target = int(raw.get("from_sequence")), int(raw.get("to_sequence"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("Every edge requires valid from_sequence and to_sequence values.") from exc
        if not 1 <= source <= step_count or not 1 <= target <= step_count:
            raise ValueError("An edge references a step outside this flow.")
        edges.append({"from_sequence": source, "to_sequence": target, "label": str(raw.get("label") or "")[:160]})
    return edges


def _apply_flow_payload(flow, payload, *, creating=False):
    if creating or "name" in payload:
        flow.name = _text(payload, "name", maximum=180)
    if "trigger" in payload:
        flow.trigger = _text(payload, "trigger", maximum=255)
    if "description" in payload:
        flow.description = _text(payload, "description", maximum=6_000)
    if creating or "steps" in payload:
        flow.steps = _normalize_steps(payload.get("steps"))
    if "edges" in payload or creating:
        flow.edges = _normalize_edges(payload.get("edges"), len(flow.steps))
    if "status" in payload:
        if payload["status"] not in AIKnowledgeBusinessFlow.Status.values:
            raise ValueError("status is not a supported business-flow state.")
        flow.status = payload["status"]
    flow.full_clean()


@require_http_methods(["GET", "POST"])
@_knowledge_api
def business_flows(request):
    company_id = request.knowledge_company_id
    if request.method == "GET":
        rows = list(AIKnowledgeBusinessFlow.objects.filter(company_id=company_id).order_by("name", "id")[:500])
        return _response({"items": [_flow_payload(row) for row in rows], "count": len(rows), "limit": 500})
    try:
        payload = _json_body(request)
        flow = AIKnowledgeBusinessFlow(company_id=company_id)
        _apply_flow_payload(flow, payload, creating=True)
        flow.save()
        return _response(_flow_payload(flow), status=201, message="Business flow created.")
    except (ValueError, ValidationError) as exc:
        return _validation_error(exc) if isinstance(exc, ValidationError) else _error(str(exc))
    except IntegrityError:
        return _error("A business flow with this name already exists.", status=409, code="CONFLICT")


@require_http_methods(["GET", "POST", "PUT", "DELETE"])
@_knowledge_api
def business_flow_detail(request, flow_id, delete_alias=False):
    flow = AIKnowledgeBusinessFlow.objects.filter(company_id=request.knowledge_company_id, pk=flow_id).first()
    if flow is None:
        return _error("Business flow not found.", status=404, code="NOT_FOUND")
    if request.method == "GET":
        return _response(_flow_payload(flow))
    if request.method == "DELETE" or delete_alias:
        flow.status = AIKnowledgeBusinessFlow.Status.ARCHIVED
        flow.save(update_fields=["status", "updated_at"])
        return _response(_flow_payload(flow), message="Business flow archived; its definition was retained.")
    try:
        payload = _json_body(request)
        _apply_flow_payload(flow, payload)
        flow.save()
        return _response(_flow_payload(flow), message="Business flow updated.")
    except (ValueError, ValidationError) as exc:
        return _validation_error(exc) if isinstance(exc, ValidationError) else _error(str(exc))
    except IntegrityError:
        return _error("The business-flow update conflicts with an existing record.", status=409, code="CONFLICT")


@require_http_methods(["POST", "DELETE"])
@_knowledge_api
def business_flow_delete(request, flow_id):
    return business_flow_detail.__wrapped__(request, flow_id, delete_alias=True)


@require_http_methods(["GET"])
@_knowledge_api
def business_flow_prerequisites(request):
    company_id = request.knowledge_company_id
    configuration, _ = AssistantConfiguration.objects.get_or_create(company_id=company_id)
    return _response(flow_generation_prerequisites(company_id, configuration))


@require_http_methods(["POST"])
@_knowledge_api
def business_flow_generate(request):
    try:
        payload = _json_body(request)
        overwrite = _boolean(payload, "overwriteManual", "overwrite_manual", default=False)
        company_id = request.knowledge_company_id
        configuration, _ = AssistantConfiguration.objects.get_or_create(company_id=company_id)
        prerequisites = flow_generation_prerequisites(company_id, configuration)
        if not prerequisites["can_generate"]:
            return _error(
                "Business-flow generation is not ready.",
                status=409,
                code="GENERATION_NOT_READY",
                details={"prerequisites": prerequisites},
            )
        job = AIKnowledgeBusinessFlowGenerationJob(
            company_id=company_id,
            configuration=configuration,
            requested_by=request.user,
            overwrite_manual=overwrite,
            input_snapshot={"requested_via": "hmx_compat_api", "references": prerequisites["references"]},
        )
        job.full_clean()
        job.save()
        try:
            queue_business_flow_generation.delay(str(job.job_id))
        except Exception:
            job.state = AIKnowledgeBusinessFlowGenerationJob.State.FAILED
            job.error_reason = "The background worker could not be reached. No provider request was sent."
            job.completed_at = timezone.now()
            job.save(update_fields=["state", "error_reason", "completed_at", "updated_at"])
            return _response(_job_payload(job), status=503, message=job.error_reason)
        return _response(_job_payload(job), status=202, message="Business-flow generation queued.")
    except (ValueError, ValidationError) as exc:
        return _validation_error(exc) if isinstance(exc, ValidationError) else _error(str(exc))


@require_http_methods(["GET"])
@_knowledge_api
def business_flow_job(request, job_id):
    try:
        job = (
            AIKnowledgeBusinessFlowGenerationJob.objects.select_related("result_flow")
            .filter(company_id=request.knowledge_company_id, job_id=job_id)
            .first()
        )
    except (TypeError, ValueError, ValidationError):
        job = None
    if job is None:
        return _error("Business-flow generation job not found.", status=404, code="NOT_FOUND")
    return _response(_job_payload(job))


@require_http_methods(["GET"])
@_knowledge_api
def business_flow_map(request, flow_id):
    flow = AIKnowledgeBusinessFlow.objects.filter(company_id=request.knowledge_company_id, pk=flow_id).first()
    if flow is None:
        return _error("Business-flow map not found.", status=404, code="NOT_FOUND")
    return _response(_flow_map_payload(flow))


@require_http_methods(["POST"])
@_knowledge_api
def business_flow_reason(request):
    try:
        payload = _json_body(request)
        query = _text(payload, "query", maximum=500)
        return _response(reason_about_business_flows(request.knowledge_company_id, query))
    except (ValueError, FlowGenerationError) as exc:
        return _error(str(exc))


@require_http_methods(["GET", "POST", "PUT"])
@_knowledge_api
def record_model_mappings(request):
    company_id = request.knowledge_company_id
    if request.method == "GET":
        rows = list(
            AIKnowledgeRecordModelMapping.objects.filter(company_id=company_id)
            .filter(_branch_scope(request))
            .order_by("display_name", "record_model", "id")[:500]
        )
        return _response({"items": [_mapping_payload(row) for row in rows], "count": len(rows), "limit": 500})
    try:
        payload = _json_body(request)
        record_model = _text(payload, "record_model", maximum=100)
        branch = _requested_branch(request, payload.get("division_id", payload.get("branch_id")))
        mapping = AIKnowledgeRecordModelMapping.objects.filter(
            company_id=company_id, branch=branch, record_model=record_model
        ).first() or AIKnowledgeRecordModelMapping(company_id=company_id, branch=branch, record_model=record_model)
        mapping.display_name = _text(payload, "display_name", default=mapping.display_name or record_model, maximum=180)
        mapping.category = _text(payload, "category", default=mapping.category, maximum=100)
        mapping.module_name = _text(payload, "module_name", default=mapping.module_name, maximum=128)
        if "feature_tags" in payload:
            if not isinstance(payload["feature_tags"], list):
                raise ValueError("feature_tags must be an array.")
            mapping.feature_tags = payload["feature_tags"]
        if "business_flow_id" in payload:
            flow_id = payload["business_flow_id"]
            mapping.business_flow = (
                AIKnowledgeBusinessFlow.objects.filter(company_id=company_id, pk=flow_id).first() if flow_id else None
            )
            if flow_id and mapping.business_flow is None:
                raise LookupError("Business flow not found in the active company.")
        if "active" in payload:
            mapping.active = _boolean(payload, "active")
        mapping.full_clean()
        created = mapping.pk is None
        mapping.save()
        return _response(
            _mapping_payload(mapping), status=201 if created else 200, message="Record-model mapping saved."
        )
    except (ValueError, ValidationError) as exc:
        return _validation_error(exc) if isinstance(exc, ValidationError) else _error(str(exc))
    except (LookupError, PermissionError) as exc:
        return _error(str(exc), status=404 if isinstance(exc, LookupError) else 403)
    except IntegrityError:
        return _error("The record-model mapping conflicts with an existing record.", status=409, code="CONFLICT")


@require_http_methods(["POST", "DELETE"])
@_knowledge_api
def record_model_mapping_delete(request, record_model):
    queryset = AIKnowledgeRecordModelMapping.objects.filter(
        company_id=request.knowledge_company_id, record_model=record_model
    ).filter(_branch_scope(request))
    archived_count = queryset.update(active=False, updated_at=timezone.now())
    if not archived_count:
        return _error("Record-model mapping not found.", status=404, code="NOT_FOUND")
    return _response(
        {"record_model": record_model, "archived_count": archived_count, "status": "archived"},
        message="Visible record-model mappings archived.",
    )


@require_http_methods(["GET"])
@_knowledge_api
def graph_status(request):
    context = getattr(request, "organization_context", None)
    overview = graph_overview(request.knowledge_company_id, branch_id=getattr(context, "branch_id", None))
    return _response({"status": "ready", "overview": overview})


@require_http_methods(["GET"])
@_knowledge_api
def graph_overview_api(request):
    context = getattr(request, "organization_context", None)
    return _response(
        graph_overview(request.knowledge_company_id, branch_id=getattr(context, "branch_id", None))
    )


@require_http_methods(["GET"])
@_knowledge_api
def graph_view(request):
    try:
        context = getattr(request, "organization_context", None)
        return _response(
            build_knowledge_graph(
                request.knowledge_company_id,
                branch_id=getattr(context, "branch_id", None),
                limit=request.GET.get("limit"),
                source=request.GET.get("source", "all"),
                node_type=request.GET.get("type", ""),
            )
        )
    except ValueError as exc:
        return _error(str(exc))


@require_http_methods(["GET"])
@_knowledge_api
def graph_search_api(request):
    try:
        context = getattr(request, "organization_context", None)
        return _response(
            graph_search(
                request.knowledge_company_id,
                request.GET.get("q", "")[:120],
                branch_id=getattr(context, "branch_id", None),
                limit=request.GET.get("limit"),
                source=request.GET.get("source", "all"),
                node_type=request.GET.get("type", ""),
            )
        )
    except ValueError as exc:
        return _error(str(exc))


@require_http_methods(["GET"])
@_knowledge_api
def graph_node_api(request, node_id):
    context = getattr(request, "organization_context", None)
    result = graph_node(
        request.knowledge_company_id,
        node_id[:180],
        branch_id=getattr(context, "branch_id", None),
    )
    return _response({"node": result}) if result is not None else _error(
        "Knowledge graph node not found.", status=404, code="NOT_FOUND"
    )


@require_http_methods(["GET"])
@_knowledge_api
def graph_neighbors_api(request):
    try:
        context = getattr(request, "organization_context", None)
        result = graph_neighbors(
            request.knowledge_company_id,
            request.GET.get("node_id", "")[:180],
            branch_id=getattr(context, "branch_id", None),
            depth=request.GET.get("depth", 1),
            limit=request.GET.get("limit"),
        )
    except ValueError as exc:
        return _error(str(exc))
    return _response(result) if result is not None else _error(
        "Knowledge graph node not found.", status=404, code="NOT_FOUND"
    )


@require_http_methods(["GET"])
@_knowledge_api
def graph_path_api(request):
    try:
        context = getattr(request, "organization_context", None)
        result = graph_path(
            request.knowledge_company_id,
            request.GET.get("from_node_id", "")[:180],
            request.GET.get("to_node_id", "")[:180],
            branch_id=getattr(context, "branch_id", None),
            max_length=request.GET.get("max_length", 6),
        )
    except ValueError as exc:
        return _error(str(exc))
    return _response(result) if result is not None else _error(
        "One or more knowledge graph nodes were not found.", status=404, code="NOT_FOUND"
    )


@require_http_methods(["GET"])
@_knowledge_api
def graph_trace_api(request):
    try:
        context = getattr(request, "organization_context", None)
        result = graph_trace(
            request.knowledge_company_id,
            request.GET.get("target_type", ""),
            request.GET.get("target_id", "")[:120],
            branch_id=getattr(context, "branch_id", None),
            limit=request.GET.get("limit"),
        )
    except ValueError as exc:
        return _error(str(exc))
    return _response(result) if result is not None else _error(
        "Knowledge graph target was not found.", status=404, code="NOT_FOUND"
    )
