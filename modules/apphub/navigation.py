from django.urls import NoReverseMatch, reverse

from .workspaces import app_menu_groups, iter_menu_items


def can_open_menu_item(user, item):
    """Keep launch targets aligned with the generic entity permission boundary."""
    permission_entity = item.get("permission_entity") or item.get("entity")
    if not permission_entity:
        return True
    from core.views import ENTITY_CONFIG, _can

    config = ENTITY_CONFIG.get(permission_entity)
    return bool(
        config
        and _can(user, "view", config["model"], permission_entity)
    )


def menu_item_url(item):
    if item.get("entity"):
        return reverse("entity_list", args=[item["entity"]])
    if item.get("feature"):
        return reverse(item["feature_url_name"], args=[item["feature"]])
    if item.get("app_slug"):
        return reverse("app_workspace", args=[item["app_slug"]])
    return item.get("url") or ""


def application_launch_url(user, app_slug):
    """Resolve the first permitted dashboard leaf for an installed application."""
    if app_slug == "app-manager":
        return reverse("app_manager") if user.is_superuser else reverse("dashboard")

    groups = app_menu_groups(app_slug)
    dashboard_groups = [group for group in groups if group["label"] == "Dashboard"]
    for group in dashboard_groups:
        for item in iter_menu_items(group["items"]):
            if not can_open_menu_item(user, item):
                continue
            try:
                target = menu_item_url(item)
            except NoReverseMatch:
                continue
            if target:
                return target
    return reverse("app_workspace", args=[app_slug])
