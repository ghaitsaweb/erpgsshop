from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from .document_registry import apply_document_extensions


class DocumentRegistryTests(SimpleTestCase):
    def test_installed_purchase_revision_overrides_legacy_action_and_option(self):
        config = {
            "purchases": {
                "editable_statuses": ["draft", "revised"],
                "actions": {"revise": {"label": "Legacy"}},
            }
        }

        applied = apply_document_extensions(config)

        self.assertEqual(applied["purchases"]["editable_statuses"], ["draft"])
        self.assertTrue(applied["purchases"]["actions"]["revise"]["service_controls_state"])

    def test_unknown_entity_is_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            apply_document_extensions({})
