from django.apps import AppConfig


class ERPShopModuleAIAssistantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "addons.ai.erpshop_moduleai_assistant"
    label = "erpshop_moduleai_assistant"
    verbose_name = "ERPShop Module AI — GashyAI"

    def ready(self):
        from modules.apphub.global_ui import register_global_ui_extension

        from .launcher import launcher_is_visible

        register_global_ui_extension(
            "erpshop-moduleai-assistant.launcher",
            "erpshop_moduleai_assistant/gashyai_launcher.html",
            predicate=launcher_is_visible,
            priority=900,
        )
