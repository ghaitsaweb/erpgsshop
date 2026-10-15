from django.apps import AppConfig


class POSConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.pos"
    label = "gsshop_pos"
    verbose_name = "GSSHOP Point of Sale"

    def ready(self):
        from . import signals  # noqa: F401

