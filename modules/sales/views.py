from dataclasses import replace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from core.models import SaleOrder
from modules.apphub.access import can_access_app
from modules.apphub.models import ERPApp
from modules.apphub.workspaces import find_menu_feature
from modules.organization.models import Branch

from .dashboard import build_sales_dashboard


def _all_company_branches(context):
    """Use every permitted branch in the selected companies for this dashboard."""
    if context is None:
        return None
    branch_ids = tuple(
        Branch.objects.filter(
            pk__in=context.allowed_branch_ids,
            company_id__in=context.active_company_ids,
            active=True,
        )
        .order_by("company__name", "-is_main", "sequence", "name", "pk")
        .values_list("pk", flat=True)
    )
    if not branch_ids:
        return context
    primary_branch_id = context.branch_id if context.branch_id in branch_ids else branch_ids[0]
    return replace(
        context,
        branch_id=primary_branch_id,
        selected_branch_ids=branch_ids,
    )


def _access_denied(request):
    messages.error(
        request,
        "You do not have access to Sales analytics. Ask an administrator to assign Sales Order access.",
    )
    return redirect("dashboard")


@login_required
def dashboard(request, feature):
    group, item = find_menu_feature("sales", feature)
    if item is None or feature != "dashboard":
        raise Http404("Sales feature not found.")

    app = get_object_or_404(ERPApp, slug="sales")
    if not can_access_app(request.user, app.slug):
        return _access_denied(request)

    from core.views import _can

    if not _can(request.user, "view", SaleOrder, "sales"):
        return _access_denied(request)
    request.session["active_erp_app"] = "sales"
    if not app.enabled:
        messages.info(request, "Install Sales to open this feature.")
        return redirect("app_workspace", slug="sales")

    analytics = build_sales_dashboard(
        request.user,
        request.GET,
        context=_all_company_branches(getattr(request, "organization_context", None)),
    )
    return render(
        request,
        "sales/dashboard.html",
        {
            "app": app,
            "feature": {"slug": feature, "title": item["label"], "group": group["label"]},
            "analytics": analytics,
            "can_add_sale": _can(request.user, "add", SaleOrder, "sales"),
        },
    )
