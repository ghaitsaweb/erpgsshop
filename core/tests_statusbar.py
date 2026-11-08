from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase


class DocumentStatusbarTemplateTests(SimpleTestCase):
    def test_current_status_is_the_collapsed_summary_and_flow_keeps_all_steps(self):
        html = render_to_string(
            "core/_document_statusbar.html",
            {
                "current_status": "in_progress",
                "status_choices": (
                    ("draft", "Draft"),
                    ("in_progress", "In progress"),
                    ("approved", "Approved"),
                    ("done", "Done"),
                ),
                "status_aria_label": "Test status",
            },
        )

        self.assertIn('<div class="document-statusbar" aria-label="Test status">', html)
        self.assertIn('class="document-statusbar__summary" type="button" aria-haspopup="true"', html)
        self.assertIn("Step 2 of 4", html)
        self.assertIn("status-in_progress current", html)
        self.assertEqual(html.count('aria-current="step"'), 1)
        self.assertEqual(html.count('class="status-glyph"'), 5)
        for label in ("Draft", "In progress", "Approved", "Done"):
            self.assertIn(label, html)

    def test_statusbar_is_omitted_until_a_record_has_a_current_status(self):
        html = render_to_string(
            "core/_document_statusbar.html",
            {"current_status": None, "status_choices": (("draft", "Draft"),)},
        )

        self.assertEqual(html.strip(), "")

    def test_current_status_indicators_do_not_use_loading_animations(self):
        css = (Path(settings.BASE_DIR) / "static/css/app.css").read_text()

        self.assertNotIn("status-current-spin", css)
        self.assertNotIn("status-current-pulse", css)
