from django.apps import AppConfig


class GSPurchasePhilippinesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "addons.purchase.gs_purchase_ph"
    label = "gs_purchase_ph"
    verbose_name = "GS Purchase Philippines"

    def ready(self):
        from .workflow_extensions import register_purchase_ph_extensions

        register_purchase_ph_extensions()
