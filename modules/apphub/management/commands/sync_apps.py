from django.core.management.base import BaseCommand

from modules.apphub.discovery import discover_app_catalog
from modules.apphub.models import ERPApp
from modules.apphub.workspaces import APP_DEPENDENCIES


class Command(BaseCommand):
    help = "Synchronize the versioned GSSHOP application catalog."

    def handle(self, *args, **options):
        catalog = discover_app_catalog()
        existing = {app.slug: app for app in ERPApp.objects.all()}
        new_apps = []
        changed_apps = []
        fields = ["name", "category", "summary", "icon", "version", "status", "is_core", "sequence", "dependencies", "route"]

        for definition in catalog:
            defaults = {
                "name": definition["name"],
                "category": definition["category"],
                "summary": definition["summary"],
                "icon": definition.get("icon", "◆"),
                "version": definition.get("version", "0.1.0"),
                "status": definition.get("status", ERPApp.Status.PLANNED),
                "enabled": definition.get("enabled", False),
                "is_core": definition.get("is_core", False),
                "sequence": definition.get("sequence", 100),
                "dependencies": definition.get("dependencies", APP_DEPENDENCIES.get(definition["slug"], [])),
                "route": definition.get("route", ""),
            }
            app = existing.get(definition["slug"])
            if app is None:
                new_apps.append(ERPApp(slug=definition["slug"], **defaults))
                continue
            for field in fields:
                setattr(app, field, defaults[field])
            if app.is_core:
                app.enabled = True
            changed_apps.append(app)

        ERPApp.objects.bulk_create(new_apps)
        if changed_apps:
            ERPApp.objects.bulk_update(changed_apps, fields + ["enabled"])
        self.stdout.write(self.style.SUCCESS(f"App catalog ready: {len(catalog)} applications."))
