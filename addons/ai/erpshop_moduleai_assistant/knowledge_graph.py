"""Bounded local knowledge-graph projections for the Assistant add-on.

The graph is a read-only view of company-owned knowledge.  It intentionally
does not mirror provider credentials, document contents, or Coworker state.
"""

from collections import deque

from django.db.models import Q, TextField
from django.db.models.functions import Cast, Lower

from .models import (
    AIKnowledgeBusinessFlow,
    AIKnowledgeCategory,
    AIKnowledgeCompanyProfile,
    AIKnowledgeDocument,
    AIKnowledgeRecordModelMapping,
)


DEFAULT_GRAPH_LIMIT = 50
MAX_GRAPH_LIMIT = 100
MAX_FLOW_STEPS_PER_GRAPH = 4


def parse_graph_limit(value, *, default=DEFAULT_GRAPH_LIMIT):
    try:
        return max(1, min(MAX_GRAPH_LIMIT, int(value)))
    except (TypeError, ValueError):
        return default


def _node(node_id, label, description, node_type, source, metadata=None):
    return {
        "id": node_id,
        "label": label,
        "description": description,
        "type": node_type,
        "source": source,
        "domain": "company_knowledge",
        "metadata": metadata or {},
    }


def _edge(source, target, label):
    return {"id": f"{source}->{target}:{label}", "source": source, "target": target, "label": label}


def _graph_budgets(limit):
    """Allocate one requested graph budget among independently bounded queries."""
    category_limit = max(1, min(20, limit // 5))
    flow_limit = max(1, min(20, limit // 5))
    mapping_limit = max(1, min(20, limit // 5))
    document_limit = max(1, min(50, limit - category_limit - flow_limit - mapping_limit))
    return category_limit, document_limit, flow_limit, mapping_limit


def _branch_scope(branch_id):
    return Q(branch_id__isnull=True) | Q(branch_id=branch_id) if branch_id else Q(branch_id__isnull=True)


def _graph_meta(company_id, branch_id, limit, source="all", node_type="", *, truncated=False, targeted=False):
    return {
        "company_id": company_id,
        "branch_id": branch_id,
        "scope": "company-wide and active branch" if branch_id else "company-wide",
        "limit": parse_graph_limit(limit),
        "source": source or "all",
        "type": node_type or "",
        "truncated": bool(truncated),
        "targeted": bool(targeted),
    }


def _profile_node(company_id, profile):
    if not profile or not (profile.get("summary") or profile.get("operating_context")):
        return None
    return _node(
        f"profile:{company_id}",
        "Company profile",
        (profile.get("summary") or profile.get("operating_context") or "")[:500],
        "company",
        "profile",
        {"active": profile.get("active", True)},
    )


def _category_node(category):
    return _node(
        f"category:{category['id']}",
        category["name"],
        (category.get("description") or "")[:500],
        "policy",
        "category",
        {"active": category.get("active", True)},
    )


def _document_node(document):
    return _node(
        f"document:{document['id']}",
        document["title"],
        f"{document['status']} knowledge document",
        "document",
        "document",
        {
            "category": document.get("category") or "",
            "active": document.get("active", True),
            "status": document["status"],
            "branch_id": document.get("branch_id"),
            "branch_name": document.get("branch__name") or "company-wide",
        },
    )


def _flow_node(flow):
    steps = flow.get("steps") if isinstance(flow.get("steps"), list) else []
    return _node(
        f"flow:{flow['id']}",
        flow["name"],
        (flow.get("description") or "")[:500],
        "process",
        "flow",
        {"status": flow.get("status") or "", "trigger": flow.get("trigger") or "", "step_count": len(steps)},
    )


def _step_node(flow_id, sequence, description):
    return _node(
        f"step:{flow_id}:{sequence}",
        f"{sequence}. {description[:80]}",
        description[:500],
        "step",
        "flow_step",
        {"flow_id": flow_id, "sequence": sequence},
    )


def _mapping_node(mapping):
    tags = mapping.get("feature_tags") if isinstance(mapping.get("feature_tags"), list) else []
    return _node(
        f"record_model:{mapping['id']}",
        mapping["display_name"],
        mapping.get("module_name") or mapping["record_model"],
        "rule",
        "mapping",
        {
            "record_model": mapping["record_model"],
            "category": mapping.get("category") or "",
            "business_flow_id": mapping.get("business_flow_id"),
            "active": mapping.get("active", True),
            "feature_tags": tags[:20],
            "branch_id": mapping.get("branch_id"),
            "branch_name": mapping.get("branch__name") or "company-wide",
        },
    )


def _node_filter(source, node_type):
    source_aliases = {
        "category": "category", "document": "document", "flow": "flow", "profile": "profile",
        "step": "flow_step", "mapping": "mapping",
    }
    type_aliases = {"category": "policy", "flow": "process"}
    return source_aliases.get((source or "all").lower(), ""), type_aliases.get(
        (node_type or "").lower(), (node_type or "").lower()
    )


def _allows_node(node, source, node_type):
    requested_source, requested_type = _node_filter(source, node_type)
    return (not requested_source or node["source"] == requested_source) and (
        not requested_type or node["type"] == requested_type
    )


def _parse_node_id(node_id):
    parts = str(node_id or "").split(":")
    if len(parts) == 2 and parts[0] in {"profile", "category", "document", "flow", "record_model"}:
        try:
            return parts[0], int(parts[1]), None
        except ValueError:
            return None
    if len(parts) == 3 and parts[0] == "step":
        try:
            return "step", int(parts[1]), int(parts[2])
        except ValueError:
            return None
    return None


def _exact_graph_node(company_id, node_id, *, branch_id=None):
    parsed = _parse_node_id(node_id)
    if not parsed:
        return None
    source, record_id, sequence = parsed
    if source == "profile":
        if record_id != company_id:
            return None
        profile = AIKnowledgeCompanyProfile.objects.filter(company_id=company_id).values(
            "summary", "operating_context", "active"
        ).first()
        return _profile_node(company_id, profile)
    if source == "category":
        row = AIKnowledgeCategory.objects.filter(company_id=company_id, pk=record_id).values(
            "id", "name", "description", "active"
        ).first()
        return _category_node(row) if row else None
    if source == "document":
        row = (
            AIKnowledgeDocument.objects.filter(company_id=company_id, pk=record_id)
            .filter(_branch_scope(branch_id))
            .values("id", "title", "category", "status", "active", "branch_id", "branch__name")
            .first()
        )
        return _document_node(row) if row else None
    if source == "flow":
        row = AIKnowledgeBusinessFlow.objects.filter(company_id=company_id, pk=record_id).values(
            "id", "name", "trigger", "description", "steps", "status"
        ).first()
        return _flow_node(row) if row else None
    if source == "record_model":
        row = (
            AIKnowledgeRecordModelMapping.objects.filter(company_id=company_id, pk=record_id)
            .filter(_branch_scope(branch_id))
            .values(
                "id", "record_model", "display_name", "category", "module_name", "feature_tags",
                "business_flow_id", "active", "branch_id", "branch__name",
            )
            .first()
        )
        return _mapping_node(row) if row else None
    flow = AIKnowledgeBusinessFlow.objects.filter(company_id=company_id, pk=record_id).values("id", "steps").first()
    steps = flow.get("steps") if flow and isinstance(flow.get("steps"), list) else []
    if not 1 <= sequence <= len(steps):
        return None
    raw_step = steps[sequence - 1]
    description = str(raw_step.get("description") or "").strip() if isinstance(raw_step, dict) else ""
    return _step_node(record_id, sequence, description) if description else None


def build_knowledge_graph(company_id, *, branch_id=None, limit=DEFAULT_GRAPH_LIMIT, source="all", node_type=""):
    """Build a small projection using fixed query count and no source text."""
    limit = parse_graph_limit(limit)
    category_limit, document_limit, flow_limit, mapping_limit = _graph_budgets(limit)
    profile = (
        AIKnowledgeCompanyProfile.objects.filter(company_id=company_id)
        .only("company_id", "active", "summary", "operating_context")
        .first()
    )
    categories = list(
        AIKnowledgeCategory.objects.filter(company_id=company_id)
        .order_by("name", "id")
        .values("id", "name", "description", "active")[:category_limit]
    )
    documents = list(
        AIKnowledgeDocument.objects.filter(company_id=company_id).filter(_branch_scope(branch_id))
        .order_by("category", "title", "id")
        .values("id", "title", "category", "status", "active", "branch_id", "branch__name")[:document_limit]
    )
    flows = list(
        AIKnowledgeBusinessFlow.objects.filter(company_id=company_id)
        .order_by("name", "id")
        .values("id", "name", "trigger", "description", "steps", "edges", "status")[:flow_limit]
    )
    mappings = list(
        AIKnowledgeRecordModelMapping.objects.filter(company_id=company_id).filter(_branch_scope(branch_id))
        .order_by("display_name", "record_model", "id")
        .values(
            "id", "record_model", "display_name", "category", "module_name", "feature_tags", "business_flow_id", "active",
            "branch_id", "branch__name",
        )[:mapping_limit]
    )

    nodes, edges = [], []
    profile_id = f"profile:{company_id}"
    if profile and (profile.summary or profile.operating_context):
        nodes.append(
            _node(
                profile_id,
                "Company profile",
                (profile.summary or profile.operating_context)[:500],
                "company",
                "profile",
                {"active": profile.active},
            )
        )

    categories_by_name = {}
    for category in categories:
        node_id = f"category:{category['id']}"
        categories_by_name[category["name"].casefold()] = node_id
        nodes.append(
            _node(
                node_id,
                category["name"],
                category["description"][:500],
                "policy",
                "category",
                {"active": category["active"]},
            )
        )
        if profile and (profile.summary or profile.operating_context):
            edges.append(_edge(profile_id, node_id, "defines category"))

    for document in documents:
        node_id = f"document:{document['id']}"
        nodes.append(
            _node(
                node_id,
                document["title"],
                f"{document['status']} knowledge document",
                "document",
                "document",
                {
                    "category": document["category"],
                    "active": document["active"],
                    "status": document["status"],
                    "branch_id": document["branch_id"],
                    "branch_name": document["branch__name"] or "company-wide",
                },
            )
        )
        category_id = categories_by_name.get((document["category"] or "").casefold())
        if category_id:
            edges.append(_edge(category_id, node_id, "categorizes"))

    visible_flow_ids = set()
    for flow in flows:
        flow_id = f"flow:{flow['id']}"
        visible_flow_ids.add(flow["id"])
        steps = flow["steps"] if isinstance(flow["steps"], list) else []
        configured_edges = flow["edges"] if isinstance(flow["edges"], list) else []
        if not configured_edges:
            configured_edges = [
                {"from_sequence": index, "to_sequence": index + 1, "label": "next"}
                for index in range(1, len(steps))
            ]
        nodes.append(
            _node(
                flow_id,
                flow["name"],
                flow["description"][:500],
                "process",
                "flow",
                {"status": flow["status"], "trigger": flow["trigger"], "step_count": len(steps)},
            )
        )
        if profile and (profile.summary or profile.operating_context):
            edges.append(_edge(profile_id, flow_id, "defines flow"))
        visible_step_ids = {}
        for sequence, step in enumerate(steps[:MAX_FLOW_STEPS_PER_GRAPH], start=1):
            description = str(step.get("description", "")).strip() if isinstance(step, dict) else ""
            if not description:
                continue
            step_id = f"step:{flow['id']}:{sequence}"
            nodes.append(
                _node(
                    step_id,
                    f"{sequence}. {description[:80]}",
                    description[:500],
                    "step",
                    "flow_step",
                    {"flow_id": flow["id"], "sequence": sequence},
                )
            )
            edges.append(_edge(flow_id, step_id, "has step"))
            visible_step_ids[sequence] = step_id
        for edge in configured_edges:
            try:
                from_sequence = int(edge.get("from_sequence"))
                to_sequence = int(edge.get("to_sequence"))
            except (AttributeError, TypeError, ValueError):
                continue
            source_id, target_id = visible_step_ids.get(from_sequence), visible_step_ids.get(to_sequence)
            if source_id and target_id:
                edges.append(_edge(source_id, target_id, str(edge.get("label") or "next")[:160]))

    for mapping in mappings:
        mapping_id = f"record_model:{mapping['id']}"
        tags = mapping["feature_tags"] if isinstance(mapping["feature_tags"], list) else []
        nodes.append(
            _node(
                mapping_id,
                mapping["display_name"],
                mapping["module_name"] or mapping["record_model"],
                "rule",
                "mapping",
                {
                    "record_model": mapping["record_model"],
                    "active": mapping["active"],
                    "feature_tags": tags[:20],
                    "branch_id": mapping["branch_id"],
                    "branch_name": mapping["branch__name"] or "company-wide",
                },
            )
        )
        category_id = categories_by_name.get((mapping["category"] or "").casefold())
        if category_id:
            edges.append(_edge(category_id, mapping_id, "maps entity"))
        if mapping["business_flow_id"] in visible_flow_ids:
            edges.append(_edge(f"flow:{mapping['business_flow_id']}", mapping_id, "uses entity"))

    source_aliases = {"category": "category", "document": "document", "flow": "flow", "profile": "profile", "step": "flow_step", "mapping": "mapping"}
    requested_source = source_aliases.get((source or "all").lower(), "")
    type_aliases = {"category": "policy", "flow": "process"}
    requested_type = type_aliases.get((node_type or "").lower(), (node_type or "").lower())
    if requested_source or requested_type:
        visible_ids = {
            node["id"]
            for node in nodes
            if (not requested_source or node["source"] == requested_source)
            and (not requested_type or node["type"] == requested_type)
        }
        nodes = [node for node in nodes if node["id"] in visible_ids]
        edges = [edge for edge in edges if edge["source"] in visible_ids and edge["target"] in visible_ids]

    return {
        "graph": {"nodes": nodes, "edges": edges},
        "meta": {
            "company_id": company_id,
            "branch_id": branch_id,
            "scope": "company-wide and active branch" if branch_id else "company-wide",
            "limit": limit,
            "source": source or "all",
            "type": node_type or "",
            "truncated": len(categories) == category_limit or len(documents) == document_limit or len(flows) == flow_limit or len(mappings) == mapping_limit,
        },
    }


def graph_overview(company_id, *, branch_id=None):
    return {
        "company_id": company_id,
        "branch_id": branch_id,
        "implementation": "local_company_knowledge",
        "counts": {
            "profiles": int(AIKnowledgeCompanyProfile.objects.filter(company_id=company_id).exists()),
            "categories": AIKnowledgeCategory.objects.filter(company_id=company_id).count(),
            "documents": AIKnowledgeDocument.objects.filter(company_id=company_id).filter(_branch_scope(branch_id)).count(),
            "flows": AIKnowledgeBusinessFlow.objects.filter(company_id=company_id).count(),
            "record_model_mappings": AIKnowledgeRecordModelMapping.objects.filter(company_id=company_id).filter(_branch_scope(branch_id)).count(),
        },
        "limits": {"view_limit_max": MAX_GRAPH_LIMIT, "flow_steps_per_flow": MAX_FLOW_STEPS_PER_GRAPH},
    }


def graph_search(company_id, query, *, branch_id=None, limit=DEFAULT_GRAPH_LIMIT, source="all", node_type=""):
    term = (query or "").strip().casefold()[:120]
    limit = parse_graph_limit(limit)
    meta = _graph_meta(company_id, branch_id, limit, source, node_type, targeted=True)
    if not term:
        return {"results": [], "meta": meta}

    profile_query = AIKnowledgeCompanyProfile.objects.filter(company_id=company_id)
    if term not in "company profile":
        profile_query = profile_query.filter(Q(summary__icontains=term) | Q(operating_context__icontains=term))
    profile = profile_query.values("summary", "operating_context", "active").first()
    categories = list(
        AIKnowledgeCategory.objects.filter(company_id=company_id)
        .filter(Q(name__icontains=term) | Q(description__icontains=term))
        .order_by("name", "id")
        .values("id", "name", "description", "active")[: limit + 1]
    )
    document_query = AIKnowledgeDocument.objects.filter(company_id=company_id).filter(_branch_scope(branch_id))
    if term not in "knowledge document":
        document_query = document_query.filter(Q(title__icontains=term) | Q(status__icontains=term))
    documents = list(
        document_query
        .order_by("title", "id")
        .values("id", "title", "category", "status", "active", "branch_id", "branch__name")[: limit + 1]
    )
    flows = list(
        AIKnowledgeBusinessFlow.objects.filter(company_id=company_id)
        .alias(steps_text=Cast("steps", TextField()))
        .filter(Q(name__icontains=term) | Q(description__icontains=term) | Q(steps_text__icontains=term))
        .order_by("name", "id")
        .values("id", "name", "trigger", "description", "steps", "status")[: limit + 1]
    )
    mappings = list(
        AIKnowledgeRecordModelMapping.objects.filter(company_id=company_id)
        .filter(_branch_scope(branch_id))
        .filter(
            Q(display_name__icontains=term)
            | Q(module_name__icontains=term)
            | Q(record_model__icontains=term)
        )
        .order_by("display_name", "id")
        .values(
            "id", "record_model", "display_name", "category", "module_name", "feature_tags",
            "business_flow_id", "active", "branch_id", "branch__name",
        )[: limit + 1]
    )

    results = []
    profile_result = _profile_node(company_id, profile)
    if profile_result and _allows_node(profile_result, source, node_type):
        results.append(profile_result)
    results.extend(node for node in map(_category_node, categories[:limit]) if _allows_node(node, source, node_type))
    results.extend(node for node in map(_document_node, documents[:limit]) if _allows_node(node, source, node_type))
    for flow in flows[:limit]:
        flow_node = _flow_node(flow)
        if (term in flow_node["label"].casefold() or term in flow_node["description"].casefold()) and _allows_node(
            flow_node, source, node_type
        ):
            results.append(flow_node)
        for sequence, raw_step in enumerate((flow.get("steps") or [])[:20], start=1):
            description = str(raw_step.get("description") or "").strip() if isinstance(raw_step, dict) else ""
            if description and term in description.casefold():
                step_node = _step_node(flow["id"], sequence, description)
                if _allows_node(step_node, source, node_type):
                    results.append(step_node)
    results.extend(node for node in map(_mapping_node, mappings[:limit]) if _allows_node(node, source, node_type))
    results.sort(key=lambda node: (0 if term in node["label"].casefold() else 1, node["label"].casefold(), node["id"]))
    meta["truncated"] = any(len(rows) > limit for rows in (categories, documents, flows, mappings)) or len(results) > limit
    return {"results": results[:limit], "meta": meta}


def graph_node(company_id, node_id, *, branch_id=None, limit=MAX_GRAPH_LIMIT):
    return _exact_graph_node(company_id, node_id, branch_id=branch_id)


def _targeted_neighbors(company_id, node_id, *, branch_id=None, limit=DEFAULT_GRAPH_LIMIT, requested_depth=1):
    limit = parse_graph_limit(limit)
    target = _exact_graph_node(company_id, node_id, branch_id=branch_id)
    if target is None:
        return None
    parsed = _parse_node_id(node_id)
    source, record_id, sequence = parsed
    neighbors, edges = [], []
    truncated = requested_depth > 1

    if source == "profile":
        half = max(1, limit // 2)
        categories = list(
            AIKnowledgeCategory.objects.filter(company_id=company_id)
            .order_by("name", "id")
            .values("id", "name", "description", "active")[: half + 1]
        )
        flows = list(
            AIKnowledgeBusinessFlow.objects.filter(company_id=company_id)
            .order_by("name", "id")
            .values("id", "name", "trigger", "description", "steps", "status")[: half + 1]
        )
        truncated = truncated or len(categories) > half or len(flows) > half
        for category in categories[:half]:
            node = _category_node(category)
            neighbors.append(node)
            edges.append(_edge(target["id"], node["id"], "defines category"))
        for flow in flows[:half]:
            node = _flow_node(flow)
            neighbors.append(node)
            edges.append(_edge(target["id"], node["id"], "defines flow"))
    elif source == "category":
        category = AIKnowledgeCategory.objects.filter(company_id=company_id, pk=record_id).values("name").first()
        if category:
            profile = _exact_graph_node(company_id, f"profile:{company_id}", branch_id=branch_id)
            if profile:
                neighbors.append(profile)
                edges.append(_edge(profile["id"], target["id"], "defines category"))
            documents = list(
                AIKnowledgeDocument.objects.filter(company_id=company_id, category__iexact=category["name"])
                .filter(_branch_scope(branch_id))
                .order_by("title", "id")
                .values("id", "title", "category", "status", "active", "branch_id", "branch__name")[: limit + 1]
            )
            mappings = list(
                AIKnowledgeRecordModelMapping.objects.filter(company_id=company_id, category__iexact=category["name"])
                .filter(_branch_scope(branch_id))
                .order_by("display_name", "id")
                .values(
                    "id", "record_model", "display_name", "category", "module_name", "feature_tags",
                    "business_flow_id", "active", "branch_id", "branch__name",
                )[: limit + 1]
            )
            truncated = truncated or len(documents) + len(mappings) > limit
            for document in documents:
                node = _document_node(document)
                neighbors.append(node)
                edges.append(_edge(target["id"], node["id"], "categorizes"))
            for mapping in mappings:
                node = _mapping_node(mapping)
                neighbors.append(node)
                edges.append(_edge(target["id"], node["id"], "maps entity"))
    elif source == "document":
        category_name = target["metadata"].get("category")
        category = AIKnowledgeCategory.objects.filter(company_id=company_id, name__iexact=category_name).values(
            "id", "name", "description", "active"
        ).first()
        if category:
            node = _category_node(category)
            neighbors.append(node)
            edges.append(_edge(node["id"], target["id"], "categorizes"))
    elif source == "flow":
        flow = AIKnowledgeBusinessFlow.objects.filter(company_id=company_id, pk=record_id).values(
            "id", "steps", "edges"
        ).first()
        profile = _exact_graph_node(company_id, f"profile:{company_id}", branch_id=branch_id)
        if profile:
            neighbors.append(profile)
            edges.append(_edge(profile["id"], target["id"], "defines flow"))
        mappings = list(
            AIKnowledgeRecordModelMapping.objects.filter(company_id=company_id, business_flow_id=record_id)
            .filter(_branch_scope(branch_id))
            .order_by("display_name", "id")
            .values(
                "id", "record_model", "display_name", "category", "module_name", "feature_tags",
                "business_flow_id", "active", "branch_id", "branch__name",
            )[: limit + 1]
        )
        steps = flow.get("steps") if flow and isinstance(flow.get("steps"), list) else []
        for position, raw_step in enumerate(steps[:MAX_FLOW_STEPS_PER_GRAPH], start=1):
            description = str(raw_step.get("description") or "").strip() if isinstance(raw_step, dict) else ""
            if description:
                node = _step_node(record_id, position, description)
                neighbors.append(node)
                edges.append(_edge(target["id"], node["id"], "has step"))
        for mapping in mappings:
            node = _mapping_node(mapping)
            neighbors.append(node)
            edges.append(_edge(target["id"], node["id"], "uses entity"))
        truncated = truncated or len(steps) > MAX_FLOW_STEPS_PER_GRAPH or len(mappings) > limit
    elif source == "record_model":
        mapping = (
            AIKnowledgeRecordModelMapping.objects.filter(company_id=company_id, pk=record_id)
            .filter(_branch_scope(branch_id))
            .values("category", "business_flow_id")
            .first()
        )
        if mapping:
            category = AIKnowledgeCategory.objects.filter(
                company_id=company_id, name__iexact=mapping["category"]
            ).values("id", "name", "description", "active").first()
            flow = AIKnowledgeBusinessFlow.objects.filter(
                company_id=company_id, pk=mapping["business_flow_id"]
            ).values("id", "name", "trigger", "description", "steps", "status").first()
            if category:
                node = _category_node(category)
                neighbors.append(node)
                edges.append(_edge(node["id"], target["id"], "maps entity"))
            if flow:
                node = _flow_node(flow)
                neighbors.append(node)
                edges.append(_edge(node["id"], target["id"], "uses entity"))
    elif source == "step":
        flow = AIKnowledgeBusinessFlow.objects.filter(company_id=company_id, pk=record_id).values(
            "id", "name", "trigger", "description", "steps", "edges", "status"
        ).first()
        if flow:
            flow_node = _flow_node(flow)
            neighbors.append(flow_node)
            edges.append(_edge(flow_node["id"], target["id"], "has step"))
            configured_edges = flow.get("edges") if isinstance(flow.get("edges"), list) else []
            if not configured_edges:
                configured_edges = [
                    {"from_sequence": position, "to_sequence": position + 1, "label": "next"}
                    for position in range(1, len(flow.get("steps") or []))
                ]
            related = set()
            for edge in configured_edges:
                try:
                    source_sequence = int(edge.get("from_sequence"))
                    target_sequence = int(edge.get("to_sequence"))
                except (AttributeError, TypeError, ValueError):
                    continue
                if sequence == source_sequence:
                    related.add((target_sequence, str(edge.get("label") or "next")[:160], True))
                elif sequence == target_sequence:
                    related.add((source_sequence, str(edge.get("label") or "next")[:160], False))
            steps = flow.get("steps") or []
            for related_sequence, label, outgoing in sorted(related):
                if not 1 <= related_sequence <= len(steps):
                    continue
                raw_step = steps[related_sequence - 1]
                description = str(raw_step.get("description") or "").strip() if isinstance(raw_step, dict) else ""
                if description:
                    node = _step_node(record_id, related_sequence, description)
                    neighbors.append(node)
                    edge_source, edge_target = (target["id"], node["id"]) if outgoing else (node["id"], target["id"])
                    edges.append(_edge(edge_source, edge_target, label))

    deduplicated = {node["id"]: node for node in neighbors}
    neighbor_ids = sorted(deduplicated, key=lambda item: (deduplicated[item]["label"].casefold(), item))[:limit]
    visible_ids = set(neighbor_ids) | {target["id"]}
    truncated = truncated or len(deduplicated) > limit
    meta = _graph_meta(company_id, branch_id, limit, truncated=truncated, targeted=True)
    meta.update({"depth": 1, "requested_depth": requested_depth})
    return {
        "node": target,
        "neighbors": [deduplicated[item] for item in neighbor_ids],
        "edges": [edge for edge in edges if edge["source"] in visible_ids and edge["target"] in visible_ids],
        "meta": meta,
    }


def _frontier_neighbors(company_id, frontier, known_nodes, *, branch_id=None, limit=DEFAULT_GRAPH_LIMIT):
    """Expand one BFS level with a fixed set of bounded, type-batched queries."""
    candidates, edges = {}, []
    preferred_ids = set()
    truncated = False
    parsed = {node_id: _parse_node_id(node_id) for node_id in frontier}
    by_source = {
        source: {node_id: frontier[node_id] for node_id, parts in parsed.items() if parts and parts[0] == source}
        for source in ("profile", "category", "document", "flow", "record_model", "step")
    }

    def put(node, *, preferred=False):
        if node:
            candidates[node["id"]] = node
            if preferred:
                preferred_ids.add(node["id"])
        return node

    profile_id = f"profile:{company_id}"
    profile = known_nodes.get(profile_id)
    if (by_source["category"] or by_source["flow"]) and profile is None:
        profile = put(_exact_graph_node(company_id, profile_id, branch_id=branch_id), preferred=True)
    for node in by_source["category"].values():
        if profile:
            edges.append(_edge(profile["id"], node["id"], "defines category"))
    for node in by_source["flow"].values():
        if profile:
            edges.append(_edge(profile["id"], node["id"], "defines flow"))

    category_nodes_by_name = {
        node["label"].casefold(): node
        for node in [*known_nodes.values(), *candidates.values()]
        if node["source"] == "category"
    }
    parent_category_names = {
        str(node["metadata"].get("category") or "").strip().casefold()
        for node in [*by_source["document"].values(), *by_source["record_model"].values()]
        if str(node["metadata"].get("category") or "").strip()
    }
    missing_category_names = parent_category_names - set(category_nodes_by_name)
    if missing_category_names:
        category_rows = list(
            AIKnowledgeCategory.objects.filter(company_id=company_id)
            .annotate(name_key=Lower("name"))
            .filter(name_key__in=missing_category_names)
            .order_by("name", "id")
            .values("id", "name", "description", "active")[: limit + 1]
        )
        truncated = truncated or len(category_rows) > limit
        for row in category_rows[:limit]:
            node = put(_category_node(row), preferred=True)
            category_nodes_by_name[node["label"].casefold()] = node
    for node in by_source["document"].values():
        category = category_nodes_by_name.get(str(node["metadata"].get("category") or "").casefold())
        if category:
            edges.append(_edge(category["id"], node["id"], "categorizes"))
    for node in by_source["record_model"].values():
        category = category_nodes_by_name.get(str(node["metadata"].get("category") or "").casefold())
        if category:
            edges.append(_edge(category["id"], node["id"], "maps entity"))

    category_frontier_by_name = {
        node["label"].casefold(): node for node in by_source["category"].values()
    }
    if category_frontier_by_name:
        documents = list(
            AIKnowledgeDocument.objects.filter(company_id=company_id)
            .filter(_branch_scope(branch_id))
            .annotate(category_key=Lower("category"))
            .filter(category_key__in=category_frontier_by_name)
            .order_by("title", "id")
            .values("id", "title", "category", "status", "active", "branch_id", "branch__name")[: limit + 1]
        )
        truncated = truncated or len(documents) > limit
        for row in documents[:limit]:
            node = put(_document_node(row))
            category = category_frontier_by_name.get(str(row.get("category") or "").casefold())
            if category:
                edges.append(_edge(category["id"], node["id"], "categorizes"))

    flow_frontier_ids = {parts[1] for node_id, parts in parsed.items() if parts and parts[0] == "flow"}
    relation_filter = Q()
    if category_frontier_by_name:
        relation_filter |= Q(category_key__in=category_frontier_by_name)
    if flow_frontier_ids:
        relation_filter |= Q(business_flow_id__in=flow_frontier_ids)
    if relation_filter:
        mappings = list(
            AIKnowledgeRecordModelMapping.objects.filter(company_id=company_id)
            .filter(_branch_scope(branch_id))
            .annotate(category_key=Lower("category"))
            .filter(relation_filter)
            .order_by("display_name", "record_model", "id")
            .values(
                "id", "record_model", "display_name", "category", "module_name", "feature_tags",
                "business_flow_id", "active", "branch_id", "branch__name",
            )[: limit + 1]
        )
        truncated = truncated or len(mappings) > limit
        for row in mappings[:limit]:
            node = put(_mapping_node(row))
            category = category_frontier_by_name.get(str(row.get("category") or "").casefold())
            if category:
                edges.append(_edge(category["id"], node["id"], "maps entity"))
            if row.get("business_flow_id") in flow_frontier_ids:
                edges.append(_edge(f"flow:{row['business_flow_id']}", node["id"], "uses entity"))

    mapping_flow_ids = {
        int(node["metadata"]["business_flow_id"])
        for node in by_source["record_model"].values()
        if node["metadata"].get("business_flow_id")
    }
    step_flow_ids = {parts[1] for node_id, parts in parsed.items() if parts and parts[0] == "step"}
    required_flow_ids = flow_frontier_ids | mapping_flow_ids | step_flow_ids
    flow_rows = list(
        AIKnowledgeBusinessFlow.objects.filter(company_id=company_id, pk__in=required_flow_ids)
        .order_by("name", "id")
        .values("id", "name", "trigger", "description", "steps", "edges", "status")
    ) if required_flow_ids else []
    flows_by_id = {row["id"]: row for row in flow_rows}
    flow_nodes_by_id = {}
    for row in flow_rows:
        node = known_nodes.get(f"flow:{row['id']}") or put(
            _flow_node(row), preferred=row["id"] in mapping_flow_ids or row["id"] in step_flow_ids
        )
        flow_nodes_by_id[row["id"]] = node
    for node in by_source["record_model"].values():
        flow = flow_nodes_by_id.get(node["metadata"].get("business_flow_id"))
        if flow:
            edges.append(_edge(flow["id"], node["id"], "uses entity"))

    step_frontier_by_flow = {}
    for node_id, node in by_source["step"].items():
        parts = parsed[node_id]
        step_frontier_by_flow.setdefault(parts[1], {})[parts[2]] = node
    for flow_id, step_targets in step_frontier_by_flow.items():
        flow = flows_by_id.get(flow_id)
        flow_node = flow_nodes_by_id.get(flow_id)
        if not flow or not flow_node:
            continue
        steps = flow.get("steps") if isinstance(flow.get("steps"), list) else []
        configured_edges = flow.get("edges") if isinstance(flow.get("edges"), list) else []
        if not configured_edges:
            configured_edges = [
                {"from_sequence": position, "to_sequence": position + 1, "label": "next"}
                for position in range(1, len(steps))
            ]
        for sequence, target in step_targets.items():
            edges.append(_edge(flow_node["id"], target["id"], "has step"))
            for configured in configured_edges[:40]:
                try:
                    source_sequence = int(configured.get("from_sequence"))
                    target_sequence = int(configured.get("to_sequence"))
                except (AttributeError, TypeError, ValueError):
                    continue
                if sequence not in {source_sequence, target_sequence}:
                    continue
                related_sequence = target_sequence if sequence == source_sequence else source_sequence
                if not 1 <= related_sequence <= len(steps):
                    continue
                raw_step = steps[related_sequence - 1]
                description = str(raw_step.get("description") or "").strip() if isinstance(raw_step, dict) else ""
                if not description:
                    continue
                related = put(_step_node(flow_id, related_sequence, description), preferred=True)
                label = str(configured.get("label") or "next")[:160]
                source_id, target_id = (
                    (target["id"], related["id"])
                    if sequence == source_sequence
                    else (related["id"], target["id"])
                )
                edges.append(_edge(source_id, target_id, label))

    for flow_id in flow_frontier_ids:
        flow = flows_by_id.get(flow_id)
        flow_node = frontier.get(f"flow:{flow_id}")
        if not flow or not flow_node:
            continue
        steps = flow.get("steps") if isinstance(flow.get("steps"), list) else []
        truncated = truncated or len(steps) > MAX_FLOW_STEPS_PER_GRAPH
        for sequence, raw_step in enumerate(steps[:MAX_FLOW_STEPS_PER_GRAPH], start=1):
            description = str(raw_step.get("description") or "").strip() if isinstance(raw_step, dict) else ""
            if description:
                node = put(_step_node(flow_id, sequence, description), preferred=True)
                edges.append(_edge(flow_node["id"], node["id"], "has step"))

    if by_source["profile"]:
        half = max(1, limit // 2)
        categories = list(
            AIKnowledgeCategory.objects.filter(company_id=company_id)
            .order_by("name", "id")
            .values("id", "name", "description", "active")[: half + 1]
        )
        flows = list(
            AIKnowledgeBusinessFlow.objects.filter(company_id=company_id)
            .order_by("name", "id")
            .values("id", "name", "trigger", "description", "steps", "status")[: half + 1]
        )
        truncated = truncated or len(categories) > half or len(flows) > half
        for row in categories[:half]:
            node = put(_category_node(row))
            edges.append(_edge(profile_id, node["id"], "defines category"))
        for row in flows[:half]:
            node = put(_flow_node(row))
            edges.append(_edge(profile_id, node["id"], "defines flow"))

    return candidates, edges, truncated, preferred_ids


def _targeted_deep_neighbors(company_id, node_id, *, branch_id=None, depth=2, limit=DEFAULT_GRAPH_LIMIT):
    limit = parse_graph_limit(limit)
    target = _exact_graph_node(company_id, node_id, branch_id=branch_id)
    if target is None:
        return None
    nodes, frontier = {target["id"]: target}, {target["id"]: target}
    distances, edges_by_id = {target["id"]: 0}, {}
    truncated = False
    source_priority = {"profile": 0, "category": 1, "flow": 1, "mapping": 2, "document": 3, "flow_step": 3}
    for current_depth in range(1, depth + 1):
        candidates, edges, level_truncated, preferred_ids = _frontier_neighbors(
            company_id, frontier, nodes, branch_id=branch_id, limit=limit
        )
        truncated = truncated or level_truncated
        for edge in edges:
            edges_by_id[edge["id"]] = edge
        unseen = [node for node in candidates.values() if node["id"] not in nodes]
        unseen.sort(
            key=lambda node: (
                0 if node["id"] in preferred_ids else 1,
                source_priority.get(node["source"], 9), node["label"].casefold(), node["id"]
            )
        )
        remaining = limit - (len(nodes) - 1)
        if len(unseen) > remaining:
            truncated = True
        selected = unseen[:max(0, remaining)]
        frontier = {node["id"]: node for node in selected}
        for node in selected:
            nodes[node["id"]] = node
            distances[node["id"]] = current_depth
        if not frontier or remaining <= 0:
            break
    visible_ids = set(nodes)
    neighbors = sorted(
        (node for node_id, node in nodes.items() if node_id != target["id"]),
        key=lambda node: (distances[node["id"]], node["label"].casefold(), node["id"]),
    )
    meta = _graph_meta(company_id, branch_id, limit, truncated=truncated, targeted=True)
    meta.update({"depth": depth, "requested_depth": depth})
    return {
        "node": target,
        "neighbors": neighbors,
        "edges": [
            edge for edge in edges_by_id.values()
            if edge["source"] in visible_ids and edge["target"] in visible_ids
        ],
        "meta": meta,
    }


def graph_neighbors(company_id, node_id, *, branch_id=None, depth=1, limit=DEFAULT_GRAPH_LIMIT):
    try:
        requested_depth = max(1, min(4, int(depth)))
    except (TypeError, ValueError):
        requested_depth = 1
    if requested_depth == 1:
        return _targeted_neighbors(
            company_id, node_id, branch_id=branch_id, limit=limit, requested_depth=requested_depth
        )
    return _targeted_deep_neighbors(
        company_id, node_id, branch_id=branch_id, depth=requested_depth, limit=limit
    )


def _ancestry_graph(company_id, node, *, branch_id=None):
    """Build the bounded parent graph for one exact endpoint without scanning the canvas."""
    nodes = {node["id"]: node}
    edges = []
    parsed = _parse_node_id(node["id"])
    source, record_id, _sequence = parsed

    def add_profile(child_id, label):
        profile = _exact_graph_node(company_id, f"profile:{company_id}", branch_id=branch_id)
        if profile:
            nodes[profile["id"]] = profile
            edges.append(_edge(profile["id"], child_id, label))

    if source == "category":
        add_profile(node["id"], "defines category")
    elif source == "document":
        category_name = node["metadata"].get("category")
        category = AIKnowledgeCategory.objects.filter(company_id=company_id, name__iexact=category_name).values(
            "id", "name", "description", "active"
        ).first()
        if category:
            category_node = _category_node(category)
            nodes[category_node["id"]] = category_node
            edges.append(_edge(category_node["id"], node["id"], "categorizes"))
            add_profile(category_node["id"], "defines category")
    elif source == "flow":
        add_profile(node["id"], "defines flow")
    elif source == "record_model":
        mapping = (
            AIKnowledgeRecordModelMapping.objects.filter(company_id=company_id, pk=record_id)
            .filter(_branch_scope(branch_id))
            .values("category", "business_flow_id")
            .first()
        )
        if mapping:
            category = AIKnowledgeCategory.objects.filter(
                company_id=company_id, name__iexact=mapping["category"]
            ).values("id", "name", "description", "active").first()
            flow = AIKnowledgeBusinessFlow.objects.filter(
                company_id=company_id, pk=mapping["business_flow_id"]
            ).values("id", "name", "trigger", "description", "steps", "status").first()
            if category:
                category_node = _category_node(category)
                nodes[category_node["id"]] = category_node
                edges.append(_edge(category_node["id"], node["id"], "maps entity"))
                add_profile(category_node["id"], "defines category")
            if flow:
                flow_node = _flow_node(flow)
                nodes[flow_node["id"]] = flow_node
                edges.append(_edge(flow_node["id"], node["id"], "uses entity"))
                add_profile(flow_node["id"], "defines flow")
    elif source == "step":
        flow = AIKnowledgeBusinessFlow.objects.filter(company_id=company_id, pk=record_id).values(
            "id", "name", "trigger", "description", "steps", "edges", "status"
        ).first()
        if flow:
            flow_node = _flow_node(flow)
            nodes[flow_node["id"]] = flow_node
            add_profile(flow_node["id"], "defines flow")
            steps = flow.get("steps") if isinstance(flow.get("steps"), list) else []
            step_ids = {}
            for position, raw_step in enumerate(steps[:20], start=1):
                description = str(raw_step.get("description") or "").strip() if isinstance(raw_step, dict) else ""
                if not description:
                    continue
                step_node = _step_node(record_id, position, description)
                nodes[step_node["id"]] = step_node
                step_ids[position] = step_node["id"]
                edges.append(_edge(flow_node["id"], step_node["id"], "has step"))
            configured_edges = flow.get("edges") if isinstance(flow.get("edges"), list) else []
            if not configured_edges:
                configured_edges = [
                    {"from_sequence": position, "to_sequence": position + 1, "label": "next"}
                    for position in range(1, len(steps))
                ]
            for configured in configured_edges[:40]:
                try:
                    source_sequence = int(configured.get("from_sequence"))
                    target_sequence = int(configured.get("to_sequence"))
                except (AttributeError, TypeError, ValueError):
                    continue
                source_id, target_id = step_ids.get(source_sequence), step_ids.get(target_sequence)
                if source_id and target_id:
                    edges.append(_edge(source_id, target_id, str(configured.get("label") or "next")[:160]))
    return nodes, edges


def graph_path(company_id, from_node_id, to_node_id, *, branch_id=None, max_length=6, limit=MAX_GRAPH_LIMIT):
    from_node = _exact_graph_node(company_id, from_node_id, branch_id=branch_id)
    to_node = _exact_graph_node(company_id, to_node_id, branch_id=branch_id)
    if from_node is None or to_node is None:
        return None
    from_node_id, to_node_id = from_node["id"], to_node["id"]
    try:
        max_length = max(1, min(8, int(max_length)))
    except (TypeError, ValueError):
        max_length = 6
    from_nodes, from_edges = _ancestry_graph(company_id, from_node, branch_id=branch_id)
    to_nodes, to_edges = _ancestry_graph(company_id, to_node, branch_id=branch_id)
    nodes = {**from_nodes, **to_nodes}
    edges_by_id = {edge["id"]: edge for edge in [*from_edges, *to_edges]}
    adjacency = {node: [] for node in nodes}
    for edge in edges_by_id.values():
        adjacency[edge["source"]].append((edge["target"], edge))
        adjacency[edge["target"]].append((edge["source"], edge))
    queue, previous = deque([(from_node_id, 0)]), {from_node_id: (None, None)}
    while queue:
        current, current_length = queue.popleft()
        if current == to_node_id:
            break
        if current_length >= max_length:
            continue
        for neighbor, edge in adjacency.get(current, []):
            if neighbor not in previous:
                previous[neighbor] = (current, edge)
                queue.append((neighbor, current_length + 1))
    meta = _graph_meta(company_id, branch_id, limit, targeted=True)
    meta["max_length"] = max_length
    if to_node_id not in previous:
        return {"nodes": [], "edges": [], "meta": meta}
    node_path, edge_path, cursor = [], [], to_node_id
    while cursor is not None:
        node_path.append(nodes[cursor])
        parent, edge = previous[cursor]
        if edge:
            edge_path.append(edge)
        cursor = parent
    return {"nodes": list(reversed(node_path)), "edges": list(reversed(edge_path)), "meta": meta}


def graph_trace(company_id, target_type, target_id, *, branch_id=None, limit=DEFAULT_GRAPH_LIMIT):
    aliases = {"category": "category", "document": "document", "flow": "flow", "profile": "profile", "step": "step"}
    node_type = aliases.get((target_type or "").lower())
    if not node_type:
        return None
    node_id = f"profile:{company_id}" if node_type == "profile" else f"{node_type}:{target_id}"
    return graph_neighbors(company_id, node_id, branch_id=branch_id, depth=1, limit=limit)
