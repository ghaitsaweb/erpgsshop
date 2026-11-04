"""Permission-aware proactive greeting suggestions for ERP pages.

The client supplies only a page *hint*.  Every label and prompt is rebuilt
from the local entity allow-list, while record visibility is checked through
the same ERP scope helpers used by the main list views and Hashy Bridge.
"""

from __future__ import annotations

from core.views import ENTITY_CONFIG, _can, _scope_queryset


MAX_INTENTS = 4
VIEW_TYPES = {"form", "list", "kanban", "calendar", "pivot", "graph"}


def _hidden(reason):
    return {"display": False, "reason_code": reason}


def _view_type(value):
    candidate = str(value or "list").strip().lower()
    return candidate if candidate in VIEW_TYPES else "list"


def _intents(*, title, singular, view_type, language):
    indonesian = str(language or "").lower().startswith("id")
    if indonesian:
        intents = [
            {"key": "page_summary", "label": f"Ringkas {title}", "prompt": f"Ringkas {title} yang terlihat pada halaman ini dan soroti prioritas."},
            {"key": "page_attention", "label": "Cek hal penting", "prompt": f"Tinjau {title} pada halaman ini dan jelaskan hal yang perlu diperhatikan berikutnya."},
        ]
        if view_type == "form":
            intents.extend(
                [
                    {"key": "record_status", "label": "Jelaskan status", "prompt": f"Jelaskan status dan konteks penting untuk {singular} ini."},
                    {"key": "record_completeness", "label": "Cek kelengkapan", "prompt": f"Periksa informasi yang belum lengkap pada {singular} ini."},
                ]
            )
    else:
        intents = [
            {"key": "page_summary", "label": f"Summarize {title}", "prompt": f"Summarize the {title} visible on this page and highlight priorities."},
            {"key": "page_attention", "label": "Review what matters", "prompt": f"Review the {title} on this page and explain what deserves attention next."},
        ]
        if view_type == "form":
            intents.extend(
                [
                    {"key": "record_status", "label": "Explain status", "prompt": f"Explain the status and important context for this {singular}."},
                    {"key": "record_completeness", "label": "Check completeness", "prompt": f"Check visible information for this {singular} that may be incomplete."},
                ]
            )
    for intent in intents:
        intent["assistance_mode"] = "guide"
        intent["required_access"] = ["read"]
    return intents[:MAX_INTENTS]


def resolve_proactive_greeting(*, user, page_context, company_id=None, branch_id=None, language="id"):
    """Build a deterministic greeting without sending ERP data to a provider."""
    page_context = page_context if isinstance(page_context, dict) else {}
    entity = str(page_context.get("entity") or page_context.get("model") or "").strip()
    config = ENTITY_CONFIG.get(entity)
    if config is None:
        return _hidden("UNTRUSTED_PAGE_ENTITY")
    if not _can(user, "view", config["model"], entity):
        return _hidden("NO_READ_ACCESS")
    try:
        if not _scope_queryset(user, entity, config["model"].objects.all()).exists():
            return _hidden("NO_VISIBLE_RECORDS")
    except Exception:
        # Scope resolution must fail closed; do not expose metadata if context
        # middleware is unavailable or the entity scope cannot be evaluated.
        return _hidden("UNAVAILABLE_SCOPE")

    title = str(config.get("title") or entity.replace("-", " ").title())[:160]
    singular = str(config.get("singular") or title.rstrip("s"))[:160]
    view_type = _view_type(page_context.get("view_type") or page_context.get("active_mode"))
    indonesian = str(language or "").lower().startswith("id")
    greeting = (
        f"Saya Hashy. Saya bisa membantu meninjau {title} yang dapat Anda akses di halaman ini."
        if indonesian
        else f"I'm Hashy. I can help review the {title} available to you on this page."
    )
    return {
        "display": True,
        "surface": "embedded_chatbox",
        "mode": "initial",
        "page": {"entity": entity, "view_type": view_type},
        "greeting": {"kind": "proactive_greeting", "title": title, "text": greeting},
        "intents": _intents(title=title, singular=singular, view_type=view_type, language=language),
        "context_envelope": {
            "permission_scope": "current_user",
            "company_scope": "active_company",
            "branch_scope": "active_branch",
            "company_id": company_id,
            "branch_id": branch_id,
            "erp_record_data_included": False,
        },
    }
