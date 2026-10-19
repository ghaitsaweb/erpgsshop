from functools import lru_cache

from .workspaces import APP_WORKSPACES, ENTITY_APP_MAP


@lru_cache(maxsize=None)
def app_entities(app_slug):
    """Return every generic ERP entity that belongs to an application."""
    workspace_entities = APP_WORKSPACES.get(app_slug, {}).get("entities", [])
    mapped_entities = [entity for entity, owner in ENTITY_APP_MAP.items() if owner == app_slug]
    return tuple(dict.fromkeys([*workspace_entities, *mapped_entities]))


def can_access_app(user, app_slug):
    """Whether a user may open an ERP application workspace.

    Application access is derived from Django Groups and their model view
    permissions. This keeps menu visibility and direct URLs aligned with the
    existing CRUD authorization rules instead of maintaining a second list of
    applications per user.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if not user.groups.exists():
        return False

    from core.views import ENTITY_CONFIG, _can

    return any(
        entity in ENTITY_CONFIG
        and _can(user, "view", ENTITY_CONFIG[entity]["model"], entity)
        for entity in app_entities(app_slug)
    )
