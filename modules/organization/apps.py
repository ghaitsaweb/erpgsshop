from django.apps import AppConfig


class OrganizationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.organization"
    label = "organization"
    verbose_name = "Organization Context"

    def ready(self):
        from . import signals  # noqa: F401
