from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from .models import Company, Contact
from .smart_actions import SmartActionDefinition, _children_on
from .views import _record_smart_actions


class SmartButtonPresentationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="smart-button-tester", password="test-password-123"
        )
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        self.company = Company.objects.create(code="SMART", name="Smart Company")
        self.request = RequestFactory().get("/app/companies/1/edit/")
        self.request.user = self.user
        self.definition = SmartActionDefinition(
            key="companies.contacts",
            source_entity="companies",
            target_entity="contacts",
            label="Partners",
            icon="▤",
            filter_queryset=_children_on("company"),
            show_count=False,
        )

    def _actions(self):
        with patch(
            "core.views.smart_actions_for_source",
            return_value=(self.definition,),
        ):
            return _record_smart_actions(
                self.request, "companies", self.company
            )

    def test_empty_related_action_is_hidden_and_nonempty_action_keeps_its_url(self):
        self.assertEqual(self._actions(), [])

        Contact.objects.create(
            company=self.company,
            code="PARTNER-1",
            name="Visible Partner",
        )
        actions = self._actions()

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["key"], "companies.contacts")
        self.assertEqual(
            actions[0]["href"],
            f"/app/contacts/?smart_action=companies.contacts&source_id={self.company.pk}",
        )

    def test_empty_check_query_count_does_not_grow_with_related_records(self):
        Contact.objects.bulk_create(
            [
                Contact(company=self.company, code=f"SMALL-{index}", name=f"Small {index}")
                for index in range(2)
            ]
        )
        self._actions()
        with CaptureQueriesContext(connection) as small_context:
            small_actions = self._actions()

        Contact.objects.bulk_create(
            [
                Contact(company=self.company, code=f"LARGE-{index}", name=f"Large {index}")
                for index in range(10)
            ]
        )
        with CaptureQueriesContext(connection) as large_context:
            large_actions = self._actions()

        self.assertEqual(len(small_context), len(large_context))
        self.assertEqual(len(small_context), 1)
        self.assertEqual(len(small_actions), 1)
        self.assertEqual(len(large_actions), 1)

    def test_only_two_buttons_are_primary_and_overflow_keeps_original_links(self):
        actions = [
            {
                "key": f"companies.related-{index}",
                "label": f"Related {index}",
                "icon": "▤",
                "count": index,
                "show_count": True,
                "href": f"/app/related-{index}/?smart_action=companies.related-{index}&source_id=7",
            }
            for index in range(1, 5)
        ]

        html = render_to_string(
            "core/_smart_buttons.html", {"smart_actions": actions}
        )

        self.assertEqual(html.count('data-smart-action-slot="primary"'), 2)
        self.assertEqual(html.count('data-smart-action-slot="overflow"'), 2)
        self.assertIn("data-smart-actions-open", html)
        self.assertIn("+2", html)
        self.assertIn(actions[2]["href"].replace("&", "&amp;"), html)
        self.assertIn('class="smart-actions-dialog"', html)

    def test_entity_form_places_collapsed_drawer_below_help_copy(self):
        entity_form = Path("templates/core/entity_form.html").read_text()
        help_copy = "Fields marked with an asterisk are required."

        self.assertNotIn(
            '{% include "core/_smart_buttons.html" %}',
            entity_form.split('</header>', 1)[0],
        )
        self.assertLess(
            entity_form.index(help_copy),
            entity_form.index('{% include "core/_smart_button_drawer.html" %}'),
        )

        html = render_to_string(
            "core/_smart_button_drawer.html",
            {
                "smart_actions": [
                    {
                        "key": "companies.contacts",
                        "label": "Partners",
                        "icon": "▤",
                        "count": 1,
                        "show_count": False,
                        "href": "/app/contacts/?smart_action=companies.contacts&source_id=7",
                    }
                ]
            },
        )
        self.assertIn("data-smart-actions-drawer-open", html)
        self.assertIn("aria-expanded=\"false\"", html)
        self.assertIn("data-smart-actions-drawer-content hidden", html)
        self.assertIn("←", html)
        self.assertIn("→", html)

    def test_overflow_script_uses_native_modal_and_restores_focus(self):
        script = Path("static/js/smart-actions.js").read_text()
        styles = Path("static/css/app.css").read_text()

        self.assertIn("smart-actions-expanded", script)
        self.assertIn("content.hidden = !expanded", script)
        self.assertIn("aria-expanded", script)
        self.assertIn("dialog.showModal()", script)
        self.assertIn("dialog.close()", script)
        self.assertIn("openButton.focus()", script)
        self.assertIn(
            ".form-layout.smart-actions-expanded { grid-template-columns: minmax(0, 1fr) 420px; }",
            styles,
        )
        self.assertIn("@media (max-width: 900px)", styles)
        self.assertIn(
            ".form-layout, .form-layout.smart-actions-expanded { grid-template-columns: minmax(0, 1fr); }",
            styles,
        )
