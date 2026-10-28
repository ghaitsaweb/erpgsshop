from collections import OrderedDict

from django.db.utils import OperationalError, ProgrammingError
from django.urls import reverse

from modules.apphub.access import can_access_app
from modules.apphub.models import ERPApp
from modules.apphub.workspaces import (
    ENTITY_APP_MAP,
    app_menu_groups,
    app_navigation_contains,
    filter_menu_items,
    find_menu_feature,
    hr_feature_definition,
    iter_menu_items,
)


def navigation(request):
    resolver = request.resolver_match
    url_name = resolver.url_name if resolver else ""
    kwargs = resolver.kwargs if resolver else {}
    entity = kwargs.get("entity")
    feature = kwargs.get("feature") if url_name in {"hr_feature", "purchase_feature", "construction_feature", "pos_feature", "pos_fnb_feature"} else None
    feature_app_slug = {
        "hr_feature": "hr",
        "purchase_feature": "purchase",
        "construction_feature": "construction",
        "pos_feature": "pos",
        "pos_fnb_feature": "pos-fnb",
    }.get(url_name)
    route_app_slug = kwargs.get("slug") if url_name == "app_workspace" else None
    session_app_slug = request.session.get("active_erp_app") if hasattr(request, "session") else None
    contextual_app_slug = None
    if entity and session_app_slug and app_navigation_contains(session_app_slug, entity=entity):
        contextual_app_slug = session_app_slug
    active_app_slug = route_app_slug or feature_app_slug or contextual_app_slug or ENTITY_APP_MAP.get(entity)

    sections = []
    apps_by_slug = {}
    installed_apps = []
    try:
        installed_apps = [
            app
            for app in ERPApp.objects.filter(enabled=True).order_by("category", "sequence", "name")
            if can_access_app(request.user, app.slug)
        ]
        apps_by_slug = {app.slug: app for app in installed_apps}
    except (OperationalError, ProgrammingError):
        pass

    # Add-ons own their direct routes (rather than a generic entity URL).  Map
    # those routes back to the installed application so the normal ERP shell,
    # switcher, and active-app navigation remain intact.
    if not active_app_slug:
        active_app_slug = next(
            (
                app.slug
                for app in installed_apps
                if app.route.startswith("/") and request.path.startswith(app.route)
            ),
            None,
        )
    current_nav_app = apps_by_slug.get(active_app_slug)
    if current_nav_app:
        menu_groups = app_menu_groups(current_nav_app.slug)
        from core.views import ENTITY_CONFIG, _can

        def can_open_menu_item(item):
            return not item.get("entity") or (
                item["entity"] in ENTITY_CONFIG
                and _can(request.user, "view", ENTITY_CONFIG[item["entity"]]["model"], item["entity"])
            )

        menu_groups = [
            {**group, "items": permitted_items}
            for group in menu_groups
            if (permitted_items := filter_menu_items(group["items"], can_open_menu_item))
        ]
        if not any(group["label"] == "Dashboard" for group in menu_groups):
            overview_items = [
                {
                    "label": f"{current_nav_app.name} Overview",
                    "app_slug": current_nav_app.slug,
                    "icon": current_nav_app.icon,
                    "category": current_nav_app.category,
                }
            ]
            if current_nav_app.route.startswith("/"):
                overview_items.append(
                    {
                        "label": f"Open {current_nav_app.name}",
                        "url": current_nav_app.route,
                        "icon": "↗",
                    }
                )
            sections.append(
                {
                    "label": "Dashboard",
                    "icon": "D",
                    "collapsible": True,
                    "open": True,
                    "items": overview_items,
                }
            )
        for index, group in enumerate(menu_groups):
            contains_active_item = any(
                (entity and item.get("entity") == entity)
                or (feature and item.get("feature") == feature)
                or (route_app_slug and item.get("app_slug") == route_app_slug)
                for item in iter_menu_items(group["items"])
            )
            sections.append(
                {
                    "label": group["label"],
                    "icon": group["icon"],
                    "collapsible": True,
                    "open": contains_active_item or (url_name == "app_workspace" and index == 0),
                    "items": group["items"],
                }
            )
    else:
        sections.append(
            {
                "label": "Workspace",
                "items": [
                    {"label": "Dashboard", "name": "dashboard", "icon": "D"},
                    {"label": "Apps", "name": "app_manager", "icon": "A"},
                ],
            }
        )

    switcher_groups = OrderedDict()
    for app in installed_apps:
        switcher_groups.setdefault(app.category, []).append(app)
    recent_slugs = request.session.get("recent_erp_apps", []) if hasattr(request, "session") else []
    recent_apps = [apps_by_slug[slug] for slug in recent_slugs if slug in apps_by_slug][:6]
    if not recent_apps:
        recent_apps = installed_apps[:6]

    global_menu_items = []
    if request.user.is_authenticated:
        from core.views import ENTITY_CONFIG, _can

        seen_targets = set()
        for app in installed_apps:
            app_url = (
                reverse("app_manager")
                if app.slug == "app-manager"
                else reverse("app_workspace", args=[app.slug])
            )
            app_search_aliases = (
                "apps applications modules install uninstall upgrade update manage manager"
                if app.slug == "app-manager"
                else ""
            )
            global_menu_items.append(
                {
                    "label": app.name,
                    "group": "Application",
                    "app": app.category,
                    "app_slug": app.slug,
                    "kind": "application",
                    "url": app_url,
                    "search": (
                        f"{app.name} {app.category} {app.slug} {app.summary} "
                        f"installed application app module {app_search_aliases}"
                    ).lower(),
                    "installed": True,
                }
            )
            for group in app_menu_groups(app.slug):
                for item in iter_menu_items(group["items"]):
                    target_url = None
                    if item.get("entity"):
                        item_entity = item["entity"]
                        config = ENTITY_CONFIG.get(item_entity)
                        if config and _can(request.user, "view", config["model"], item_entity):
                            target_url = reverse("entity_list", args=[item_entity])
                    elif item.get("feature"):
                        target_url = reverse(item["feature_url_name"], args=[item["feature"]])
                    elif item.get("app_slug"):
                        target_url = reverse("app_workspace", args=[item["app_slug"]])
                    elif item.get("url"):
                        target_url = item["url"]
                    if not target_url or target_url in seen_targets:
                        continue
                    seen_targets.add(target_url)
                    global_menu_items.append(
                        {
                            "label": item["label"],
                            "group": group["label"],
                            "app": app.name,
                            "app_slug": app.slug,
                            "kind": "menu",
                            "url": target_url,
                            "search": (
                                f"{item['label']} {group['label']} {app.name} {app.category} "
                                "installed menu"
                            ).lower(),
                            "installed": True,
                        }
                    )

        administration_items = []
        if request.user.is_staff:
            administration_items.append(
                {
                    "label": "Administration",
                    "url": reverse("admin:index"),
                    "search": "administration admin settings configuration installed menu",
                }
            )
        if request.user.has_perm("auth.view_user"):
            administration_items.append(
                {
                    "label": "Users",
                    "url": reverse("admin:auth_user_changelist"),
                    "search": "users user accounts permissions administration installed menu",
                }
            )
        if request.user.has_perm("auth.view_group"):
            administration_items.append(
                {
                    "label": "Groups & Permissions",
                    "url": reverse("admin:auth_group_changelist"),
                    "search": "groups roles permissions access administration installed menu",
                }
            )
        for item in administration_items:
            global_menu_items.append(
                {
                    **item,
                    "group": "Administration",
                    "app": "ERP Foundation",
                    "app_slug": "foundation",
                    "kind": "menu",
                    "installed": True,
                }
            )

    breadcrumbs = [{"label": "Home", "url": reverse("dashboard")}]
    if url_name == "app_manager":
        breadcrumbs.append({"label": "Apps"})
    elif url_name == "app_workspace" and route_app_slug:
        app = apps_by_slug.get(route_app_slug)
        if app is None:
            try:
                app = ERPApp.objects.filter(slug=route_app_slug).first()
            except (OperationalError, ProgrammingError):
                app = None
        breadcrumbs.append({"label": "Apps", "url": reverse("app_manager")})
        breadcrumbs.append({"label": app.name if app else route_app_slug.replace("-", " ").title()})
    elif url_name == "hr_feature" and feature:
        definition = hr_feature_definition(feature)
        breadcrumbs.append({"label": "Human Resources", "url": reverse("app_workspace", args=["hr"])})
        if definition:
            breadcrumbs.append({"label": definition["group"]})
            breadcrumbs.append({"label": definition["title"]})
    elif url_name == "accounting_feature" and feature:
        breadcrumbs.append({"label": "Accounting", "url": reverse("app_workspace", args=["accounting"])})
        group, item = find_menu_feature("accounting", feature)
        if item:
            breadcrumbs.append({"label": group["label"]})
            breadcrumbs.append({"label": item["label"]})
    elif url_name == "purchase_feature" and feature:
        breadcrumbs.append({"label": "Purchase", "url": reverse("app_workspace", args=["purchase"])})
        for group in app_menu_groups("purchase"):
            item = next((item for item in group["items"] if item.get("feature") == feature), None)
            if item:
                breadcrumbs.append({"label": group["label"]})
                breadcrumbs.append({"label": item["label"]})
                break
    elif url_name == "construction_feature" and feature:
        breadcrumbs.append({"label": "Construction", "url": reverse("app_workspace", args=["construction"])})
        group, item = find_menu_feature("construction", feature)
        if item:
            breadcrumbs.append({"label": group["label"]})
            breadcrumbs.append({"label": item["label"]})
    elif url_name in {"pos_feature", "pos_fnb_feature"} and feature:
        app_slug = "pos" if url_name == "pos_feature" else "pos-fnb"
        app_label = "Point of Sale" if app_slug == "pos" else "POS F&B"
        breadcrumbs.append({"label": app_label, "url": reverse("app_workspace", args=[app_slug])})
        group, item = find_menu_feature(app_slug, feature)
        if item:
            breadcrumbs.append({"label": group["label"]})
            breadcrumbs.append({"label": item["label"]})
    elif entity:
        app_slug = active_app_slug or ENTITY_APP_MAP.get(entity)
        app = apps_by_slug.get(app_slug)
        if app_slug:
            breadcrumbs.append(
                {
                    "label": app.name if app else app_slug.replace("-", " ").title(),
                    "url": reverse("app_workspace", args=[app_slug]),
                }
            )
        entity_label = entity.replace("-", " ").title()
        if url_name == "entity_list":
            breadcrumbs.append({"label": entity_label})
        else:
            breadcrumbs.append({"label": entity_label, "url": reverse("entity_list", args=[entity])})
            if url_name == "entity_create":
                breadcrumbs.append({"label": "New"})
            elif url_name == "entity_edit":
                breadcrumbs.append({"label": "Edit"})
            elif url_name == "entity_delete":
                breadcrumbs.append({"label": "Delete"})

    return {
        "nav_sections": sections,
        "active_app_slug": active_app_slug,
        "current_nav_app": current_nav_app,
        "switcher_app_groups": switcher_groups.items(),
        "switcher_recent_apps": recent_apps,
        "global_menu_items": global_menu_items,
        "breadcrumbs": breadcrumbs,
    }
