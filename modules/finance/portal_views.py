"""Read-only customer accounting portal views."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import render

from .portal_registry import (
    _portal_customer,
    get_portal_document,
    list_portal_documents,
    portal_document_definitions,
)


def _base_context(request, *, title, breadcrumbs):
    customer = _portal_customer(request.user)
    return {
        "portal_customer": customer,
        "portal_admin": customer is None,
        "portal_title": title,
        "breadcrumbs": breadcrumbs,
    }


@login_required
def customer_portal_dashboard(request):
    context = _base_context(
        request,
        title="Customer Documents",
        breadcrumbs=[{"label": "Customer Documents"}],
    )
    context["definitions"] = portal_document_definitions()
    context["portal_mode"] = "dashboard"
    return render(request, "finance/customer_portal.html", context)


@login_required
def customer_portal_document_list(request, document_type):
    try:
        result = list_portal_documents(
            request.user,
            document_type,
            search=request.GET.get("q", "").strip(),
            page=request.GET.get("page", 1),
        )
    except (ValidationError, ValueError) as exc:
        raise Http404("Document type not found.") from exc
    definition = result["definition"]
    context = _base_context(
        request,
        title=definition.label,
        breadcrumbs=[
            {"label": "Customer Documents", "url": "/customer-portal/"},
            {"label": definition.label},
        ],
    )
    context.update(result)
    context.update({"portal_mode": "list", "search": request.GET.get("q", "").strip()})
    return render(request, "finance/customer_portal.html", context)


@login_required
def customer_portal_document_detail(request, document_type, document_uuid):
    try:
        result = get_portal_document(request.user, document_type, document_uuid)
    except ValidationError as exc:
        raise Http404("Document type not found.") from exc
    if result is None:
        raise Http404("Document not found.")
    definition = result["definition"]
    context = _base_context(
        request,
        title=f"{definition.singular} {result['document']['number']}",
        breadcrumbs=[
            {"label": "Customer Documents", "url": "/customer-portal/"},
            {"label": definition.label, "url": f"/customer-portal/documents/{definition.key}/"},
            {"label": result["document"]["number"]},
        ],
    )
    context.update(result)
    context["portal_mode"] = "detail"
    return render(request, "finance/customer_portal.html", context)
