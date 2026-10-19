from django.apps import AppConfig


class DatabaseManagerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.database_manager"
    verbose_name = "Database Manager"
