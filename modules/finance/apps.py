from django.apps import AppConfig


class FinanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.finance"
    label = "gsshop_finance"
    verbose_name = "GSSHOP Finance"

    def ready(self):
        from . import signals  # noqa: F401

