from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from core.models import Company, Contact
from core.smart_actions import (
    EXPLICIT_SMART_ACTIONS,
    SmartActionDefinition,
    _children_on,
    build_standard_smart_actions,
    get_smart_action,
)
from core.views import ENTITY_CONFIG  # noqa: F401 - configures installed entity actions


class StandardSmartActionDiscoveryTests(SimpleTestCase):
    def setUp(self):
        self.entity_config = {
            "companies": {
                "model": Company,
                "title": "Companies",
                "singular": "Company",
            },
            "contacts": {
                "model": Contact,
                "title": "Partners",
                "singular": "Partner",
            },
        }

    def test_foreign_key_generates_both_directions_without_manual_registration(self):
        actions = build_standard_smart_actions(self.entity_config)
        by_key = {action.key: action for action in actions}

        self.assertEqual(by_key["contacts.company"].target_entity, "companies")
        self.assertEqual(by_key["companies.contacts"].target_entity, "contacts")
        self.assertFalse(by_key["companies.contacts"].show_count)
        self.assertFalse(by_key["companies.contacts"].hide_when_empty)

    def test_explicit_exception_wins_for_its_direction(self):
        explicit = SmartActionDefinition(
            key="companies.preferred-partners",
            source_entity="companies",
            target_entity="contacts",
            label="Preferred Partners",
            icon="★",
            filter_queryset=_children_on("company"),
        )

        actions = build_standard_smart_actions(self.entity_config, (explicit,))
        pairs = {(action.source_entity, action.target_entity) for action in actions}

        self.assertNotIn(("companies", "contacts"), pairs)
        self.assertIn(("contacts", "companies"), pairs)

    def test_ambiguous_model_mapping_fails_fast(self):
        ambiguous = {
            **self.entity_config,
            "contact-aliases": {
                "model": Contact,
                "title": "Contact Aliases",
                "singular": "Contact Alias",
            },
        }

        with self.assertRaisesMessage(ImproperlyConfigured, "Smart action discovery is ambiguous"):
            build_standard_smart_actions(ambiguous)

    def test_purchase_relations_are_discovered_not_explicitly_declared(self):
        explicit_keys = {action.key for action in EXPLICIT_SMART_ACTIONS}
        generated_keys = {
            "purchase-tenders.document-completion-matrix",
            "purchase-tenders.document-completion-lines",
            "tender-document-completion-lines.vendor",
            "purchase-legal-documents.attachments",
            "purchase-legal-documents.tender-requirements",
        }

        for key in generated_keys:
            self.assertNotIn(key, explicit_keys)
            self.assertIsNotNone(get_smart_action(key))
