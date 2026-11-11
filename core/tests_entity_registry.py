from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from core.entity_registry import discover_installed_entity_configs, merge_entity_configs
from core.models import Company


class InstalledEntityRegistryTests(SimpleTestCase):
    def test_installed_modules_publish_standard_entity_config(self):
        discovered = discover_installed_entity_configs()

        self.assertIn("purchase-tenders", discovered)
        self.assertIn("employee-change-relation-history", discovered)
        self.assertIn("pos-orders", discovered)
        self.assertIn("account-journals", discovered)

    def test_core_and_module_entity_collisions_fail_fast(self):
        base = {
            "companies": {
                "model": Company,
                "title": "Companies",
                "singular": "Company",
            }
        }

        with self.assertRaisesMessage(ImproperlyConfigured, "collides with core entities"):
            merge_entity_configs(base, {"companies": base["companies"]})
