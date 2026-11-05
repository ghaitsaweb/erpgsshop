"""Bounded local knowledge-graph projections for the Assistant add-on.

The graph is a read-only view of company-owned knowledge.  It intentionally
does not mirror provider credentials, document contents, or Coworker state.
"""

from collections import deque

from django.db.models import Q

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
    graph = build_knowledge_graph(company_id, branch_id=branch_id, limit=limit, source=source, node_type=node_type)
    term = (query or "").strip().casefold()[:120]
    if not term:
        return {"results": [], "meta": graph["meta"]}
    results = [
        node
        for node in graph["graph"]["nodes"]
        if term in node["label"].casefold() or term in node["description"].casefold()
    ]
    return {"results": results[:parse_graph_limit(limit)], "meta": graph["meta"]}


def graph_node(company_id, node_id, *, branch_id=None, limit=MAX_GRAPH_LIMIT):
    graph = build_knowledge_graph(company_id, branch_id=branch_id, limit=limit)
    return next((node for node in graph["graph"]["nodes"] if node["id"] == str(node_id)), None)


def graph_neighbors(company_id, node_id, *, branch_id=None, depth=1, limit=DEFAULT_GRAPH_LIMIT):
    graph = build_knowledge_graph(company_id, branch_id=branch_id, limit=limit)
    nodes = {node["id"]: node for node in graph["graph"]["nodes"]}
    if node_id not in nodes:
        return None
    try:
        depth = max(1, min(4, int(depth)))
    except (TypeError, ValueError):
        depth = 1
    adjacency = {node: set() for node in nodes}
    for edge in graph["graph"]["edges"]:
        adjacency[edge["source"]].add(edge["target"])
        adjacency[edge["target"]].add(edge["source"])
    seen, queue = {node_id}, deque([(node_id, 0)])
    while queue:
        current, current_depth = queue.popleft()
        if current_depth >= depth:
            continue
        for neighbor in adjacency.get(current, set()):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, current_depth + 1))
    edges = [edge for edge in graph["graph"]["edges"] if edge["source"] in seen and edge["target"] in seen]
    return {
        "node": nodes[node_id],
        "neighbors": [nodes[item] for item in sorted(seen) if item != node_id],
        "edges": edges,
        "meta": graph["meta"],
    }


def graph_path(company_id, from_node_id, to_node_id, *, branch_id=None, max_length=6, limit=MAX_GRAPH_LIMIT):
    graph = build_knowledge_graph(company_id, branch_id=branch_id, limit=limit)
    nodes = {node["id"]: node for node in graph["graph"]["nodes"]}
    if from_node_id not in nodes or to_node_id not in nodes:
        return None
    try:
        max_length = max(1, min(8, int(max_length)))
    except (TypeError, ValueError):
        max_length = 6
    adjacency = {node: [] for node in nodes}
    for edge in graph["graph"]["edges"]:
        adjacency[edge["source"]].append((edge["target"], edge))
        adjacency[edge["target"]].append((edge["source"], edge))
    queue, previous = deque([from_node_id]), {from_node_id: (None, None)}
    while queue:
        current = queue.popleft()
        if current == to_node_id:
            break
        current_length = 0
        cursor = current
        while previous[cursor][0] is not None:
            current_length += 1
            cursor = previous[cursor][0]
        if current_length >= max_length:
            continue
        for neighbor, edge in adjacency.get(current, []):
            if neighbor not in previous:
                previous[neighbor] = (current, edge)
                queue.append(neighbor)
    if to_node_id not in previous:
        return {"nodes": [], "edges": [], "meta": graph["meta"]}
    node_path, edge_path, cursor = [], [], to_node_id
    while cursor is not None:
        node_path.append(nodes[cursor])
        parent, edge = previous[cursor]
        if edge:
            edge_path.append(edge)
        cursor = parent
    return {"nodes": list(reversed(node_path)), "edges": list(reversed(edge_path)), "meta": graph["meta"]}


def graph_trace(company_id, target_type, target_id, *, branch_id=None, limit=DEFAULT_GRAPH_LIMIT):
    aliases = {"category": "category", "document": "document", "flow": "flow", "profile": "profile", "step": "step"}
    node_type = aliases.get((target_type or "").lower())
    if not node_type:
        return None
    node_id = f"profile:{company_id}" if node_type == "profile" else f"{node_type}:{target_id}"
    return graph_neighbors(company_id, node_id, branch_id=branch_id, depth=1, limit=limit)
