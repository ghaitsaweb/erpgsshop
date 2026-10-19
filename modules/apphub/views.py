from collections import OrderedDict
import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.management import CommandError, call_command
from django.db import DatabaseError, transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .discovery import AddonManifestError
from .models import ERPApp
from .upgrade import django_labels_for, execute_upgrade, pending_migration_plan, upgrade_target_apps
from .workspaces import (
    APP_WORKSPACES,
    app_menu_groups,
    filter_menu_items,
    find_menu_feature,
    hr_feature_definition,
)
from modules.construction.dashboard import build_construction_analytics
from modules.finance.dashboard import build_accounting_analytics
from modules.finance.report_exports import render_analytics_excel, render_analytics_pdf
from modules.hr.dashboard import build_hr_analytics
from modules.pos.dashboard import build_fnb_analytics, build_pos_analytics
from modules.purchase.dashboard import build_purchase_analytics


def _workspace_url(app):
    return reverse("app_workspace", args=[app.slug])


def _return_url(request, fallback="app_manager"):
    candidate = request.POST.get("next", "")
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return candidate
    return reverse(fallback)


@require_POST
@login_required
def set_developer_mode(request):
    if not request.user.is_staff or not request.user.has_perm("auth.view_permission"):
        messages.error(request, "Developer Mode requires technical administration permission.")
        return redirect(_return_url(request))
    enabled = request.POST.get("enabled") == "1"
    request.session["developer_mode"] = enabled
    request.session.modified = True
    messages.success(request, f"Developer Mode {'enabled' if enabled else 'disabled'}.")
    return redirect(_return_url(request))


@login_required
def app_manager(request):
    query = request.GET.get("q", "").strip()
    queryset = ERPApp.objects.all()
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(category__icontains=query)
            | Q(summary__icontains=query)
            | Q(slug__icontains=query)
        )
    apps = list(queryset)
    groups = OrderedDict()
    for app in apps:
        app.launch_url = _workspace_url(app)
        groups.setdefault(app.category, []).append(app)
    summary = ERPApp.objects.aggregate(
        total=Count("id"),
        stable=Count("id", filter=Q(status=ERPApp.Status.STABLE)),
        beta=Count("id", filter=Q(status=ERPApp.Status.BETA)),
        planned=Count("id", filter=Q(status=ERPApp.Status.PLANNED)),
        enabled=Count("id", filter=Q(enabled=True)),
    )
    return render(
        request,
        "apphub/manager.html",
        {"app_groups": groups.items(), "summary": summary, "query": query, "result_count": len(apps)},
    )


@require_POST
@login_required
def refresh_app_catalog(request):
    if not request.user.is_superuser:
        messages.error(request, "Only an administrator can refresh the application catalog.")
        return redirect("app_manager")
    try:
        call_command("sync_apps", verbosity=0)
    except (AddonManifestError, ValueError) as exc:
        messages.error(request, f"Application catalog could not be refreshed: {exc}")
    else:
        messages.success(request, "Application catalog refreshed. New manifest-based modules are ready to install.")
    return redirect("app_manager")


@require_POST
@login_required
def upgrade_application(request, slug):
    destination = _return_url(request)
    if not request.user.is_superuser:
        messages.error(request, "Only an administrator can upgrade applications.")
        return redirect(destination)

    app = get_object_or_404(ERPApp, slug=slug)
    if not app.enabled:
        messages.error(request, f"Install {app.name} before upgrading it.")
        return redirect(destination)
    if app.status == ERPApp.Status.PLANNED:
        messages.error(request, f"{app.name} is still planned and cannot be upgraded.")
        return redirect(destination)

    mode = request.POST.get("mode", "upgrade")
    if mode not in {"upgrade", "upgrade-no-deps"}:
        messages.error(request, "Unknown application upgrade mode.")
        return redirect(destination)
    include_dependants = mode == "upgrade"
    mode_label = "Upgrade" if include_dependants else "Upgrade No-Deps"
    confirmation_phrase = f"{mode_label.upper()} {app.slug}"

    try:
        installed_apps = list(ERPApp.objects.filter(enabled=True))
        targets = upgrade_target_apps(app, installed_apps, include_dependants=include_dependants)
        django_labels = django_labels_for(targets)
        migration_plan = pending_migration_plan(django_labels)
    except (DatabaseError, ValueError) as exc:
        messages.error(request, f"Upgrade plan could not be prepared: {exc}")
        return redirect(destination)

    if request.POST.get("confirmation", "").strip() != confirmation_phrase:
        if request.POST.get("confirmation"):
            messages.error(request, f"Confirmation did not match {confirmation_phrase}.")
        return render(
            request,
            "apphub/upgrade_confirm.html",
            {
                "app": app,
                "mode": mode,
                "mode_label": mode_label,
                "mode_description": (
                    "Upgrade this application and every installed downstream application that depends on it."
                    if include_dependants
                    else "Upgrade only this logical application; installed downstream applications are not included."
                ),
                "targets": targets,
                "django_labels": django_labels,
                "migration_plan": migration_plan,
                "confirmation_phrase": confirmation_phrase,
                "destination": destination,
            },
        )

    try:
        execute_upgrade(django_labels)
    except (AddonManifestError, CommandError, DatabaseError, RuntimeError, ValueError) as exc:
        messages.error(request, f"{mode_label} failed: {exc}")
    else:
        target_names = ", ".join(target.name for target in targets)
        messages.success(
            request,
            f"{mode_label} completed for {target_names}. Deployed-code migrations and the app catalog are current.",
        )
    return redirect(destination)


@login_required
def app_workspace(request, slug):
    app = get_object_or_404(ERPApp, slug=slug)
    request.session["active_erp_app"] = slug
    recent_apps = [item for item in request.session.get("recent_erp_apps", []) if item != slug]
    request.session["recent_erp_apps"] = [slug, *recent_apps][:6]
    workspace = APP_WORKSPACES.get(slug, {"tagline": app.summary, "entities": [], "workflow": []})
    entity_cards = []
    recent_items = []
    workspace_menu_groups = app_menu_groups(slug)

    if app.enabled:
        from core.views import ENTITY_CONFIG, _can, _scope_queryset

        def can_open_menu_item(item):
            return not item.get("entity") or (
                item["entity"] in ENTITY_CONFIG
                and _can(request.user, "view", ENTITY_CONFIG[item["entity"]]["model"], item["entity"])
            )

        workspace_menu_groups = [
            {**group, "items": permitted_items}
            for group in workspace_menu_groups
            if (permitted_items := filter_menu_items(group["items"], can_open_menu_item))
        ]

        for entity in workspace.get("entities", []):
            config = ENTITY_CONFIG.get(entity)
            if not config:
                continue
            model = config["model"]
            if not request.user.has_perm(f"{model._meta.app_label}.view_{model._meta.model_name}"):
                continue
            entity_cards.append(
                {
                    "slug": entity,
                    "title": config["title"],
                    "singular": config["singular"],
                    "count": _scope_queryset(request.user, entity, model.objects.all()).count(),
                    "list_url": reverse("entity_list", args=[entity]),
                    "create_url": reverse("entity_create", args=[entity]),
                    "can_add": config.get("creatable", True)
                    and request.user.has_perm(f"{model._meta.app_label}.add_{model._meta.model_name}"),
                }
            )
        if entity_cards:
            primary_config = ENTITY_CONFIG[entity_cards[0]["slug"]]
            primary_slug = entity_cards[0]["slug"]
            recent_items = list(
                _scope_queryset(
                    request.user,
                    primary_slug,
                    primary_config["model"].objects.all(),
                )[:5]
            )

    related_apps = ERPApp.objects.filter(category=app.category).exclude(pk=app.pk)[:5]
    dependencies = ERPApp.objects.filter(slug__in=app.dependencies).order_by("name")
    return render(
        request,
        "apphub/workspace.html",
        {
            "app": app,
            "workspace": workspace,
            "entity_cards": entity_cards,
            "recent_items": recent_items,
            "related_apps": related_apps,
            "dependencies": dependencies,
            "custom_launch_url": app.route if app.route.startswith("/") else "",
            "workspace_menu_groups": workspace_menu_groups,
        },
    )


@login_required
def hr_feature(request, feature):
    definition = hr_feature_definition(feature)
    if definition is None:
        raise Http404("Human Resources feature not found.")

    app = get_object_or_404(ERPApp, slug="hr")
    request.session["active_erp_app"] = "hr"
    recent_apps = [item for item in request.session.get("recent_erp_apps", []) if item != "hr"]
    request.session["recent_erp_apps"] = ["hr", *recent_apps][:6]
    if not app.enabled:
        messages.info(request, "Install Human Resources to open this feature.")
        return redirect("app_workspace", slug="hr")

    analytics = build_hr_analytics(feature, request.user)
    template = "apphub/hr_analytics.html" if analytics is not None else "apphub/hr_feature.html"
    return render(request, template, {"app": app, "feature": definition, "analytics": analytics})


@login_required
def accounting_feature(request, feature):
    group, item = find_menu_feature("accounting", feature)
    definition = None if item is None else {
        "slug": feature,
        "title": item["label"],
        "group": group["label"],
        "description": f"Review {item['label'].lower()} across the accounting records in your company scope.",
    }
    if definition is None:
        raise Http404("Accounting feature not found.")
    app = get_object_or_404(ERPApp, slug="accounting")
    request.session["active_erp_app"] = "accounting"
    if not app.enabled:
        messages.info(request, "Install Accounting to open this feature.")
        return redirect("app_workspace", slug="accounting")
    analytics = build_accounting_analytics(feature, request.user)
    if analytics is None:
        raise Http404("Accounting analytics not found.")
    return render(request, "apphub/hr_analytics.html", {
        "app": app,
        "feature": definition,
        "analytics": analytics,
        "export_url": reverse("accounting_feature_export", args=[feature]),
        "export_pdf_url": reverse("accounting_feature_export_pdf", args=[feature]),
        "export_excel_url": reverse("accounting_feature_export_excel", args=[feature]),
    })


@login_required
def accounting_feature_export(request, feature):
    group, item = find_menu_feature("accounting", feature)
    if item is None:
        raise Http404("Accounting feature not found.")
    app = get_object_or_404(ERPApp, slug="accounting", enabled=True)
    analytics = build_accounting_analytics(feature, request.user)
    if analytics is None:
        raise Http404("Accounting analytics not found.")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="accounting-{feature}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow([app.name, item["label"]])
    writer.writerow([])
    writer.writerow(["Metric", "Value", "Description"])
    for metric in analytics["metrics"]:
        writer.writerow([metric["label"], metric["value"], metric["detail"]])
    writer.writerow([])
    writer.writerow([analytics["breakdown_title"], "Total"])
    for row in analytics["breakdown"]:
        writer.writerow([row["label"], row["total"]])
    return response


def _accounting_export_data(request, feature):
    _group, item = find_menu_feature("accounting", feature)
    if item is None:
        raise Http404("Accounting feature not found.")
    get_object_or_404(ERPApp, slug="accounting", enabled=True)
    analytics = build_accounting_analytics(feature, request.user)
    if analytics is None:
        raise Http404("Accounting analytics not found.")
    return item["label"], analytics


@login_required
def accounting_feature_export_pdf(request, feature):
    title, analytics = _accounting_export_data(request, feature)
    response = HttpResponse(render_analytics_pdf(title, analytics), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="accounting-{feature}.pdf"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def accounting_feature_export_excel(request, feature):
    title, analytics = _accounting_export_data(request, feature)
    response = HttpResponse(
        render_analytics_excel(title, analytics),
        content_type="application/vnd.ms-excel; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="accounting-{feature}.xls"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def purchase_feature(request, feature):
    definition = None
    for group in app_menu_groups("purchase"):
        for item in group["items"]:
            if item.get("feature") == feature:
                definition = {
                    "slug": feature,
                    "title": item["label"],
                    "group": group["label"],
                    "description": f"Review {item['label'].lower()} within the Purchase workspace.",
                }
                break
        if definition:
            break
    if definition is None:
        raise Http404("Purchase feature not found.")

    app = get_object_or_404(ERPApp, slug="purchase")
    request.session["active_erp_app"] = "purchase"
    if not app.enabled:
        messages.info(request, "Install Purchase to open this feature.")
        return redirect("app_workspace", slug="purchase")
    analytics = build_purchase_analytics(feature, request.user)
    if analytics is None:
        raise Http404("Purchase analytics not found.")
    return render(request, "apphub/hr_analytics.html", {"app": app, "feature": definition, "analytics": analytics})


@login_required
def construction_feature(request, feature):
    group, item = find_menu_feature("construction", feature)
    definition = None if item is None else {
        "slug": feature,
        "title": item["label"],
        "group": group["label"],
        "description": f"Review {item['label'].lower()} within the Construction workspace.",
    }
    if definition is None:
        raise Http404("Construction feature not found.")

    app = get_object_or_404(ERPApp, slug="construction")
    request.session["active_erp_app"] = "construction"
    if not app.enabled:
        messages.info(request, "Install Construction to open this feature.")
        return redirect("app_workspace", slug="construction")
    analytics = build_construction_analytics(feature, request.user)
    if analytics is None:
        raise Http404("Construction analytics not found.")
    return render(request, "apphub/hr_analytics.html", {"app": app, "feature": definition, "analytics": analytics})


def _pos_feature(request, feature, app_slug, analytics_builder):
    group, item = find_menu_feature(app_slug, feature)
    definition = None if item is None else {
        "slug": feature,
        "title": item["label"],
        "group": group["label"],
        "description": f"Review {item['label'].lower()} within the {app_slug.upper()} workspace.",
    }
    if definition is None:
        raise Http404("Retail/F&B feature not found.")

    app = get_object_or_404(ERPApp, slug=app_slug)
    request.session["active_erp_app"] = app_slug
    if not app.enabled:
        messages.info(request, f"Install {app.name} to open this feature.")
        return redirect("app_workspace", slug=app_slug)
    analytics = analytics_builder(feature, request.user)
    if analytics is None:
        raise Http404("Retail/F&B analytics not found.")
    return render(request, "apphub/hr_analytics.html", {"app": app, "feature": definition, "analytics": analytics})


@login_required
def pos_feature(request, feature):
    return _pos_feature(request, feature, "pos", build_pos_analytics)


@login_required
def pos_fnb_feature(request, feature):
    return _pos_feature(request, feature, "pos-fnb", build_fnb_analytics)


def _required_installations(target, apps_by_slug):
    required = set()
    visiting = set()

    def visit(app):
        if app.slug in required:
            return
        if app.slug in visiting:
            raise ValueError(f"Dependency cycle detected at {app.name}.")
        if app.status == ERPApp.Status.PLANNED:
            raise ValueError(f"{app.name} is still planned and cannot be installed.")
        visiting.add(app.slug)
        for dependency_slug in app.dependencies:
            dependency = apps_by_slug.get(dependency_slug)
            if dependency is None:
                raise ValueError(f"Required application '{dependency_slug}' is not available.")
            visit(dependency)
        visiting.remove(app.slug)
        required.add(app.slug)

    visit(target)
    return required


@require_POST
@login_required
@transaction.atomic
def change_installation(request, slug):
    destination = _return_url(request)
    if not request.user.is_superuser:
        messages.error(request, "Only an administrator can install or uninstall applications.")
        return redirect(destination)

    apps = list(ERPApp.objects.select_for_update().all())
    apps_by_slug = {item.slug: item for item in apps}
    app = get_object_or_404(ERPApp, slug=slug)
    action = request.POST.get("action", "install")

    if action == "install":
        try:
            required = _required_installations(app, apps_by_slug)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(destination)
        installable = [apps_by_slug[item] for item in required if not apps_by_slug[item].enabled]
        if installable:
            now = timezone.now()
            for item in installable:
                item.enabled = True
                item.enabled_at = now
                item.enabled_by = request.user
            ERPApp.objects.bulk_update(installable, ["enabled", "enabled_at", "enabled_by"])
            names = ", ".join(sorted(item.name for item in installable))
            messages.success(request, f"Installed: {names}.")
        else:
            messages.info(request, f"{app.name} is already installed.")
        return redirect(destination)

    if action != "uninstall":
        messages.error(request, "Unknown application action.")
        return redirect(destination)
    if app.is_core:
        messages.error(request, f"{app.name} is required by the ERP foundation and cannot be uninstalled.")
        return redirect(destination)

    dependants = [item for item in apps if item.enabled and app.slug in item.dependencies]
    if dependants:
        names = ", ".join(sorted(item.name for item in dependants))
        messages.error(request, f"Uninstall {names} first because they depend on {app.name}.")
        return redirect(destination)

    app.enabled = False
    app.enabled_at = None
    app.enabled_by = None
    app.save(update_fields=["enabled", "enabled_at", "enabled_by"])
    messages.success(request, f"{app.name} uninstalled. Existing business data was preserved.")
    return redirect(destination)
