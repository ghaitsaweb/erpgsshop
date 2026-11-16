from django import forms
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from .document_registry import apply_document_extensions
from .views import _build_document_tabs


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

    def test_purchase_tabs_are_applied_in_hmx_order(self):
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
            [tab["key"] for tab in applied["purchase-tenders"]["tabs"]],
            [
                "order-lines",
                "participating-vendors",
                "document-completion",
                "vendor-scoring",
                "purchase-team",
                "approval-history",
                "other-information",
            ],
        )
        self.assertEqual(
            [tab["key"] for tab in applied["purchases"]["tabs"]],
            [
                "order-lines",
                "milestone-contract-terms",
                "service-work-order",
                "billing-policy",
                "purchase-team",
                "communication-history",
                "attachment-file",
                "other-information",
                "approval-history",
            ],
        )

    def test_tab_builder_routes_fields_and_line_sections_once(self):
        class ExampleForm(forms.Form):
            company = forms.CharField()
            team = forms.CharField()

        line_section = {"line_relation": "lines", "index": 0}
        result = _build_document_tabs(
            {
                "tabs": [
                    {
                        "key": "order-lines",
                        "label": "Order Lines",
                        "template": "core/document_tabs/lines.html",
                        "line_relation": "lines",
                    },
                    {
                        "key": "purchase-team",
                        "label": "Purchase Teams",
                        "template": "core/document_tabs/fields.html",
                        "field_names": ["team"],
                        "captures": ["summary"],
                    },
                ]
            },
            ExampleForm(),
            [line_section],
            instance=None,
            user=AnonymousUser(),
        )

        self.assertEqual([field.name for field in result["document_header_fields"]], ["company"])
        self.assertEqual(
            [field.name for field in result["document_tabs"][1]["fields"]],
            ["team"],
        )
        self.assertIs(result["document_tabs"][0]["line_section"], line_section)
        self.assertEqual(result["untabbed_line_sections"], [])
        self.assertEqual(result["document_tab_captures"], {"summary"})

    def test_tab_builder_rejects_duplicate_field_ownership(self):
        class ExampleForm(forms.Form):
            team = forms.CharField()

        with self.assertRaisesMessage(ImproperlyConfigured, "repeats form fields: team"):
            _build_document_tabs(
                {
                    "tabs": [
                        {
                            "key": "first",
                            "label": "First",
                            "template": "core/document_tabs/fields.html",
                            "field_names": ["team"],
                        },
                        {
                            "key": "second",
                            "label": "Second",
                            "template": "core/document_tabs/fields.html",
                            "field_names": ["team"],
                        },
                    ]
                },
                ExampleForm(),
                [],
                instance=None,
                user=AnonymousUser(),
            )
