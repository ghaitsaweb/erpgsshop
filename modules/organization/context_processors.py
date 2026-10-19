from django.contrib.auth import get_user_model

from modules.apphub.developer_mode import developer_mode_is_active


def organization_context(request):
    user = getattr(request, "user", None)
    developer_actor = getattr(request, "developer_actor", user)
    can_use_developer_mode = bool(developer_actor and developer_actor.is_authenticated and developer_actor.is_superuser)
    developer_mode = can_use_developer_mode and developer_mode_is_active(request, developer_actor)
    developer_page_meta = {}
    resolver = getattr(request, "resolver_match", None)
    entity = resolver.kwargs.get("entity") if resolver else ""
    if entity:
        try:
            from core.developer import developer_metadata
            from core.views import ENTITY_CONFIG

            config = ENTITY_CONFIG.get(entity)
            if config:
                developer_page_meta = developer_metadata(
                    config["model"],
                    entity=entity,
                    object_id=resolver.kwargs.get("pk"),
                )
        except (ImportError, AttributeError):
            # This context processor also runs during early management commands.
            developer_page_meta = {}

    return {
        "active_company": getattr(request, "active_company", None),
        "active_branch": getattr(request, "active_branch", None),
        "active_companies": getattr(request, "active_companies", []),
        "active_branches": getattr(request, "active_branches", []),
        "organization_context": getattr(request, "organization_context", None),
        "can_use_developer_mode": can_use_developer_mode,
        "developer_mode": developer_mode,
        "developer_actor": developer_actor,
        "developer_impersonating": developer_mode and bool(getattr(request, "developer_impersonating", False)),
        "developer_switchable_users": (
            get_user_model().objects.filter(is_active=True).order_by("username", "id") if developer_mode else []
        ),
        "developer_page_meta": developer_page_meta,
    }
