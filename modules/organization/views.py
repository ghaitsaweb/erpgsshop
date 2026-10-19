import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from core.models import Company

from .context import access_scope, switch_context
from .models import Branch


def _payload(request):
    if request.content_type == "application/json":
        try:
            return json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return {}
    return request.POST


def _multi_value(data, key):
    if hasattr(data, "getlist"):
        values = data.getlist(key)
        return values if values else None
    return data.get(key)


@require_GET
@login_required
def options(request):
    company_ids, branch_ids, _, _ = access_scope(request.user)
    branches_by_company = {}
    for branch in Branch.objects.filter(pk__in=branch_ids, active=True).order_by("sequence", "name", "pk"):
        branches_by_company.setdefault(branch.company_id, []).append(
            {
                "id": branch.pk,
                "code": branch.code,
                "name": branch.name,
                "is_main": branch.is_main,
            }
        )
    companies = [
        {
            "id": company.pk,
            "code": company.code,
            "name": company.name,
            "currency": company.currency,
            "branches": branches_by_company.get(company.pk, []),
        }
        for company in Company.objects.filter(pk__in=company_ids, active=True).order_by("name", "pk")
    ]
    return JsonResponse(
        {
            "companies": companies,
            "active_company_id": getattr(request.active_company, "pk", None),
            "active_branch_id": getattr(request.active_branch, "pk", None),
            "selected_company_ids": list(request.organization_context.active_company_ids),
            "selected_branch_ids": list(request.organization_context.active_branch_ids),
        }
    )


@require_POST
@login_required
def select_context(request):
    data = _payload(request)
    company_ids = _multi_value(data, "company_ids")
    branch_ids = _multi_value(data, "branch_ids")
    if company_ids is None:
        company_ids = data.get("company_id")
    if branch_ids is None and "branch_ids" not in data:
        branch_ids = data.get("branch_id") or None
    try:
        context = switch_context(
            request.user,
            request.session,
            company_ids,
            branch_ids,
            primary_company_id=data.get("active_company_id") or data.get("company_id"),
            primary_branch_id=data.get("active_branch_id") or data.get("branch_id"),
        )
    except ValueError as exc:
        return JsonResponse({"success": False, "detail": str(exc)}, status=403)
    return JsonResponse(
        {
            "success": True,
            "active_company_id": context.company_id,
            "active_branch_id": context.branch_id,
            "selected_company_ids": list(context.active_company_ids),
            "selected_branch_ids": list(context.active_branch_ids),
        }
    )
