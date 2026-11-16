from functools import lru_cache
from functools import wraps

from django.contrib import messages
from django.db.utils import OperationalError, ProgrammingError
from django.shortcuts import redirect

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
    if app_slug == "approval-workflow":
        # The dedicated inbox applies company, active-level, delegation, and
        # submitter checks per transaction. Any role-bearing ERP user may open
        # the module even when they do not administer its configuration.
        return True
    if app_slug == "erpshop-moduleai-assistant":
        return user.has_perm("erpshop_moduleai_assistant.view_assistantsession")

    from core.views import ENTITY_CONFIG, _can

    return any(
        entity in ENTITY_CONFIG
        and _can(user, "view", ENTITY_CONFIG[entity]["model"], entity)
        for entity in app_entities(app_slug)
    )


def is_app_enabled(app_slug):
    """Return the current database's runtime installation state for one app."""
    from .models import ERPApp

    try:
        return ERPApp.objects.filter(slug=app_slug, enabled=True).exists()
    except (OperationalError, ProgrammingError):
        return False


def enabled_app_required(app_slug):
    """Fail closed when a deployed add-on is not installed in this database."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if is_app_enabled(app_slug):
                return view(request, *args, **kwargs)
            messages.info(request, f"Install {app_slug} from Apps before opening this feature.")
            return redirect("app_workspace", slug=app_slug)

        return wrapped

    return decorator
