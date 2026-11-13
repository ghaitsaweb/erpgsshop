from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from .document_registry import apply_document_extensions


class DocumentRegistryTests(SimpleTestCase):
    def test_installed_purchase_revision_overrides_legacy_action_and_option(self):
        config = {
            "purchase-requests": {},
            "purchase-quotations": {},
            "purchase-tenders": {},
            "blanket-orders": {},
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

    def test_purchase_line_field_extension_is_applied_in_place(self):
        config = {
            "purchase-requests": {},
            "purchase-quotations": {},
            "purchase-tenders": {},
            "purchases": {"editable_statuses": [], "actions": {"revise": {}}},
            "blanket-orders": {
                "line_fields": ["product", "unit_price", "minimum_release_quantity"],
            },
        }

        applied = apply_document_extensions(config)

        self.assertEqual(
            applied["blanket-orders"]["line_fields"],
            [
                "product",
                "unit_price",
                "tax_percent",
                "cost_center_mode",
                "cost_center_distribution_ids",
                "cost_center_distribution_data",
                "cost_center_analytic_tag_ids",
                "cost_center",
                "minimum_release_quantity",
            ],
        )
