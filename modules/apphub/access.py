from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect

from .models import ERPApp


def enabled_app_required(app_slug):
    """Protect an add-on view with authentication and installation state."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not ERPApp.objects.filter(slug=app_slug, enabled=True).exists():
                return redirect("app_workspace", slug=app_slug)
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
