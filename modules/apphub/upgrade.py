"""Application upgrade planning and execution for the App Manager."""

from contextlib import contextmanager
from threading import Lock

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


# Logical App Manager entries can share one Django schema application.
APP_DJANGO_LABELS = {
    "foundation": ("core",),
    "app-manager": ("gsshop_apphub",),
    "crm": ("gsshop_crm",),
    "sales": ("core",),
    "invoicing": ("core",),
    "product": ("core",),
    "purchase": ("gsshop_purchase",),
    "inventory": ("core",),
    "quality": ("gsshop_operations",),
    "manufacturing": ("gsshop_operations",),
    "projects": ("gsshop_operations",),
    "maintenance": ("gsshop_operations",),
    "accounting": ("gsshop_finance",),
    "budget": ("gsshop_finance",),
    "expense": ("gsshop_finance", "gsshop_hr"),
    "loan": ("gsshop_finance", "gsshop_hr"),
    "hr": ("gsshop_hr",),
    "payroll": ("gsshop_hr",),
    "recruitment": ("gsshop_hr",),
    "talent": ("gsshop_hr",),
    "assets": ("core",),
    "construction": ("gsshop_construction",),
    "pos": ("gsshop_pos",),
    "pos-fnb": ("gsshop_pos",),
    "school": ("gsshop_education",),
    "lms": ("gsshop_education",),
    "library": ("gsshop_education",),
    "ai-coworker": ("gsshop_ai",),
    "localization-id": ("gsshop_localization",),
    "localization-sg": ("gsshop_localization",),
    "localization-my": ("gsshop_localization",),
    "localization-ph": ("gsshop_localization",),
    "localization-th": ("gsshop_localization",),
    "localization-au": ("gsshop_localization",),
}

_process_upgrade_lock = Lock()
_POSTGRES_LOCK_ID = 80100187


def upgrade_target_apps(selected, installed_apps, *, include_dependants):
    """Return the selected app followed by installed transitive dependants."""
    apps_by_slug = {app.slug: app for app in installed_apps}
    selected_slugs = {selected.slug}
    if include_dependants:
        changed = True
        while changed:
            changed = False
            for app in installed_apps:
                if app.slug not in selected_slugs and any(
                    dependency in selected_slugs for dependency in app.dependencies
                ):
                    selected_slugs.add(app.slug)
                    changed = True

    ordered = []
    remaining = set(selected_slugs)
    while remaining:
        ready = sorted(
            (
                apps_by_slug[slug]
                for slug in remaining
                if not (set(apps_by_slug[slug].dependencies) & remaining)
            ),
            key=lambda app: (app.sequence, app.name),
        )
        if not ready:
            raise ValueError("Application dependency cycle prevents a safe upgrade plan.")
        ordered.extend(ready)
        remaining.difference_update(app.slug for app in ready)
    return ordered


def django_labels_for(apps):
    labels = []
    for app in apps:
        for label in APP_DJANGO_LABELS.get(app.slug, ()):
            if label not in labels:
                labels.append(label)
    return labels


def pending_migration_plan(labels):
    executor = MigrationExecutor(connection)
    targets = []
    for label in labels:
        targets.extend(executor.loader.graph.leaf_nodes(label))
    return [
        {
            "label": migration.app_label,
            "name": migration.name,
            "backwards": backwards,
        }
        for migration, backwards in executor.migration_plan(targets)
    ]


@contextmanager
def application_upgrade_lock():
    """Prevent two web workers from running App Manager upgrades together."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [_POSTGRES_LOCK_ID])
            acquired = cursor.fetchone()[0]
        if not acquired:
            raise RuntimeError("Another application upgrade is currently running.")
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [_POSTGRES_LOCK_ID])
        return

    acquired = _process_upgrade_lock.acquire(blocking=False)
    if not acquired:
        raise RuntimeError("Another application upgrade is currently running.")
    try:
        yield
    finally:
        _process_upgrade_lock.release()


def execute_upgrade(labels):
    """Apply migrations from already-deployed code and refresh the catalog."""
    with application_upgrade_lock():
        for label in labels:
            call_command("migrate", label, interactive=False, verbosity=0)
        call_command("sync_apps", verbosity=0)
