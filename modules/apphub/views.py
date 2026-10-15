from collections import OrderedDict

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .discovery import AddonManifestError
from .models import ERPApp
from .workspaces import APP_WORKSPACES, app_menu_groups, hr_feature_definition
from modules.hr.dashboard import build_hr_analytics


def _workspace_url(app):
    return reverse("app_workspace", args=[app.slug])


def _return_url(request, fallback="app_manager"):
    candidate = request.POST.get("next", "")
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return candidate
    return reverse(fallback)


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
        from core.views import ENTITY_CONFIG, _can
        from modules.hr.access import scope_hr_queryset

        workspace_menu_groups = [
            {**group, "items": permitted_items}
            for group in workspace_menu_groups
            if (
                permitted_items := [
                    item
                    for item in group["items"]
                    if not item.get("entity")
                    or (
                        item["entity"] in ENTITY_CONFIG
                        and _can(request.user, "view", ENTITY_CONFIG[item["entity"]]["model"], item["entity"])
                    )
                ]
            )
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
                    "count": scope_hr_queryset(request.user, entity, model.objects.all()).count(),
                    "list_url": reverse("entity_list", args=[entity]),
                    "create_url": reverse("entity_create", args=[entity]),
                    "can_add": request.user.has_perm(f"{model._meta.app_label}.add_{model._meta.model_name}"),
                }
            )
        if entity_cards:
            primary_config = ENTITY_CONFIG[entity_cards[0]["slug"]]
            primary_slug = entity_cards[0]["slug"]
            recent_items = list(
                scope_hr_queryset(
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
