from django.apps import AppConfig
from django.db.models.signals import post_migrate


class HRConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.hr"
    label = "gsshop_hr"
    verbose_name = "GSSHOP Human Resources"

    def ready(self):
        from .security import ensure_hr_roles

        post_migrate.connect(ensure_hr_roles, sender=self, dispatch_uid="gsshop_hr.ensure_roles")
