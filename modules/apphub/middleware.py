from django.contrib.auth import get_user_model

from .developer_mode import (
    DEVELOPER_ACTOR_SESSION_KEY,
    DEVELOPER_IMPERSONATED_SESSION_KEY,
    DEVELOPER_MODE_SESSION_KEY,
    clear_developer_session,
    restore_actor_organization_context,
)


class DeveloperImpersonationMiddleware:
    """Temporarily expose a selected profile without replacing the real login session.

    The real authenticated account stays attached to ``request.developer_actor``.
    Downstream authorization and organization context intentionally see the selected
    account, which makes Login as behave like a full login for that profile.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        real_user = request.user
        request.developer_actor = real_user
        request.developer_impersonating = False

        if not real_user.is_authenticated:
            clear_developer_session(request.session)
            return self.get_response(request)

        mode_enabled = request.session.get(DEVELOPER_MODE_SESSION_KEY)
        actor_matches = str(request.session.get(DEVELOPER_ACTOR_SESSION_KEY)) == str(real_user.pk)
        if mode_enabled and (not real_user.is_superuser or not actor_matches):
            clear_developer_session(request.session)
        elif mode_enabled:
            profile_id = request.session.get(DEVELOPER_IMPERSONATED_SESSION_KEY)
            if profile_id and str(profile_id) != str(real_user.pk):
                profile = get_user_model().objects.filter(pk=profile_id, is_active=True).first()
                if profile is None:
                    restore_actor_organization_context(request.session)
                    request.session.pop(DEVELOPER_IMPERSONATED_SESSION_KEY, None)
                    request.session.modified = True
                else:
                    request.user = profile
                    request.developer_impersonating = True

        return self.get_response(request)
