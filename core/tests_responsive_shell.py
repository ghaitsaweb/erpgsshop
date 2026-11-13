from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Company


class ResponsiveShellContractTests(TestCase):
    def setUp(self):
        Company.objects.create(code="RESP", name="Responsive Company")
        self.user = get_user_model().objects.create_superuser(
            username="responsive-admin",
            email="responsive@example.test",
            password="safe-password",
        )
        self.client.force_login(self.user)

    def test_authenticated_shell_has_compact_drawer_controls(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="shell-sidebar"')
        self.assertContains(response, "data-shell-sidebar")
        self.assertContains(response, "data-sidebar-open")
        self.assertContains(response, "data-sidebar-close", count=2)
        self.assertContains(response, "shell-sidebar-backdrop")
        self.assertContains(response, 'aria-controls="shell-sidebar"')
        self.assertContains(response, "•••")
        self.assertContains(response, 'aria-label="Change active organization"')
        self.assertContains(response, "js/responsive-shell.js")

    def test_compact_css_keeps_sidebar_out_of_list_and_form_flow(self):
        stylesheet = (Path(__file__).parents[1] / "static" / "css" / "app.css").read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 1024px)", stylesheet)
        self.assertIn("width: 290px", stylesheet)
        self.assertIn("transform: translateX(-105%)", stylesheet)
        self.assertIn(".sidebar.is-open { transform: translateX(0); }", stylesheet)
        self.assertIn("body.sidebar-open .shell-sidebar-backdrop", stylesheet)
        self.assertIn(".topbar { min-height: 64px", stylesheet)
        self.assertIn(".document-paper { padding: 18px 14px; }", stylesheet)
        self.assertNotIn(".sidebar { position: static; height: auto; }", stylesheet)

    def test_drawer_script_supports_close_paths_and_accessibility_state(self):
        javascript = (Path(__file__).parents[1] / "static" / "js" / "responsive-shell.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("matchMedia('(max-width: 1024px)')", javascript)
        self.assertIn("aria-expanded", javascript)
        self.assertIn("event.key === 'Escape'", javascript)
        self.assertIn("sidebar.addEventListener('click'", javascript)
        self.assertIn("restoreFocus", javascript)
