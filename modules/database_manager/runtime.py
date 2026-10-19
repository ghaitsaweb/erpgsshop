import contextlib
import contextvars
import hashlib
import re
from copy import deepcopy

from django.conf import settings
from django.db import connections


DATABASE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
_active_alias = contextvars.ContextVar("erp_gsshop_database_alias", default=None)
_registered_aliases = {}


def primary_database_name():
    return settings.DATABASES["default"]["NAME"]


def database_prefix():
    return getattr(settings, "ERP_DATABASE_PREFIX", "erp_gsshop")


def validate_database_name(name, *, allow_primary=True):
    name = (name or "").strip().lower()
    if not DATABASE_NAME_PATTERN.fullmatch(name):
        raise ValueError("Use 3–63 lowercase letters, numbers, or underscores; start with a letter.")
    primary = primary_database_name()
    if name == primary and allow_primary:
        return name
    prefix = f"{database_prefix()}_"
    if not name.startswith(prefix):
        raise ValueError(f"Database name must start with '{prefix}'.")
    return name


def register_database(name):
    name = validate_database_name(name)
    if name == primary_database_name():
        return "default"
    alias = _registered_aliases.get(name)
    if alias:
        return alias
    alias = f"managed_{hashlib.sha256(name.encode()).hexdigest()[:16]}"
    config = deepcopy(settings.DATABASES["default"])
    config["NAME"] = name
    config["CONN_MAX_AGE"] = 0
    settings.DATABASES[alias] = config
    connections.databases[alias] = config
    _registered_aliases[name] = alias
    return alias


def current_database_alias():
    return _active_alias.get()


@contextlib.contextmanager
def database_context(database_name):
    alias = register_database(database_name)
    token = _active_alias.set(alias)
    try:
        yield alias
    finally:
        _active_alias.reset(token)


class SelectedDatabaseRouter:
    """Route ORM work to the database selected for the current request/task."""

    @staticmethod
    def _route(hints):
        instance = hints.get("instance")
        if instance is not None and instance._state.db:
            return instance._state.db
        return current_database_alias()

    def db_for_read(self, model, **hints):
        return self._route(hints)

    def db_for_write(self, model, **hints):
        return self._route(hints)

    def allow_relation(self, obj1, obj2, **hints):
        db1 = obj1._state.db
        db2 = obj2._state.db
        if db1 and db2:
            return db1 == db2
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        alias = current_database_alias()
        if alias:
            return db == alias
        return None
