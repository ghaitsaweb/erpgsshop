DEVELOPER_MODE_SESSION_KEY = "developer_mode"
DEVELOPER_ACTOR_SESSION_KEY = "developer_actor_user_id"
DEVELOPER_IMPERSONATED_SESSION_KEY = "developer_impersonated_user_id"
DEVELOPER_ACTOR_ORGANIZATION_CONTEXT_SESSION_KEY = "developer_actor_organization_context"

ORGANIZATION_CONTEXT_SESSION_KEYS = (
    "active_company_id",
    "active_branch_id",
    "selected_company_ids",
    "selected_branch_ids",
)


def clear_developer_session(session):
    """Remove all session state belonging to the Super Admin developer tools."""
    changed = False
    for key in (
        DEVELOPER_MODE_SESSION_KEY,
        DEVELOPER_ACTOR_SESSION_KEY,
        DEVELOPER_IMPERSONATED_SESSION_KEY,
        DEVELOPER_ACTOR_ORGANIZATION_CONTEXT_SESSION_KEY,
    ):
        if key in session:
            session.pop(key, None)
            changed = True
    if changed:
        session.modified = True


def begin_profile_switch(session):
    """Keep the Super Admin context, then let the selected profile start fresh."""
    if DEVELOPER_ACTOR_ORGANIZATION_CONTEXT_SESSION_KEY not in session:
        session[DEVELOPER_ACTOR_ORGANIZATION_CONTEXT_SESSION_KEY] = {
            key: session[key]
            for key in ORGANIZATION_CONTEXT_SESSION_KEYS
            if key in session
        }
    for key in ORGANIZATION_CONTEXT_SESSION_KEYS:
        session.pop(key, None)
    session.modified = True


def restore_actor_organization_context(session):
    """Return the session to the Super Admin's company/branch selection."""
    if DEVELOPER_ACTOR_ORGANIZATION_CONTEXT_SESSION_KEY not in session:
        return
    saved_context = session.pop(DEVELOPER_ACTOR_ORGANIZATION_CONTEXT_SESSION_KEY) or {}
    for key in ORGANIZATION_CONTEXT_SESSION_KEYS:
        session.pop(key, None)
    for key, value in saved_context.items():
        if key in ORGANIZATION_CONTEXT_SESSION_KEYS:
            session[key] = value
    session.modified = True


def developer_mode_is_active(request, actor=None):
    """Return whether developer mode belongs to the authenticated Super Admin."""
    actor = actor or getattr(request, "developer_actor", request.user)
    return bool(
        actor
        and actor.is_authenticated
        and actor.is_superuser
        and request.session.get(DEVELOPER_MODE_SESSION_KEY)
        and str(request.session.get(DEVELOPER_ACTOR_SESSION_KEY)) == str(actor.pk)
    )
