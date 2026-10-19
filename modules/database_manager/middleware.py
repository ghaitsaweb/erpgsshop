from django.conf import settings
from django.db import DatabaseError

from .runtime import _active_alias, primary_database_name, register_database, validate_database_name


MANAGER_PREFIX = "/database-manager/"
DEFAULT_ONLY_PATHS = ("/health/", "/static/", "/media/")


class DatabaseSelectionMiddleware:
    """Select one physical ERP database before sessions/auth access the ORM."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        selected = primary_database_name()
        if path != "/" and not path.startswith(MANAGER_PREFIX) and not path.startswith(DEFAULT_ONLY_PATHS):
            candidate = request.COOKIES.get(settings.ERP_DATABASE_COOKIE, "")
            try:
                if candidate:
                    candidate = validate_database_name(candidate)
                    if candidate == primary_database_name() or self._is_registered(candidate):
                        selected = candidate
            except (ValueError, DatabaseError):
                selected = primary_database_name()

        alias = register_database(selected)
        token = _active_alias.set(alias)
        request.database_name = selected
        request.database_alias = alias
        try:
            return self.get_response(request)
        finally:
            _active_alias.reset(token)

    @staticmethod
    def _is_registered(database_name):
        from .models import ManagedDatabase

        return ManagedDatabase.objects.using("default").filter(name=database_name).exists()
