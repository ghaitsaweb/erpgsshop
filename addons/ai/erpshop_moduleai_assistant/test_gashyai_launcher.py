from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Company
from modules.apphub.models import ERPApp
from modules.organization.models import UserOrganizationAccess

from .models import AssistantConfiguration, AssistantMessage, AssistantSession


class GashyAILauncherTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(code="GASHY", name="GashyAI Company")
        self.other_company = Company.objects.create(code="OTHER-AI", name="Other AI Company")
        self.user = get_user_model().objects.create_user(username="gashyai-user", password="safe-password")
        permission = Permission.objects.get(codename="view_assistantsession")
        group = Group.objects.create(name="GashyAI users")
        group.permissions.add(permission)
        self.user.groups.add(group)
        access = UserOrganizationAccess.objects.create(user=self.user, default_company=self.company)
        access.companies.add(self.company)
        ERPApp.objects.create(
            slug="erpshop-moduleai-assistant",
            name="ERPShop Module AI — GashyAI",
            category="AI & Data",
            summary="Test assistant",
            enabled=True,
            status=ERPApp.Status.BETA,
            route="/ai/assistant/",
        )
        self.configuration = AssistantConfiguration.objects.create(company=self.company)
        self.client.force_login(self.user)

    def test_launcher_is_module_owned_and_follows_apphub_visibility(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-gashyai-shell")
        self.assertContains(response, "GashyAI")
        self.assertContains(response, "gashyai-hat.svg")

        ERPApp.objects.filter(slug="erpshop-moduleai-assistant").update(enabled=False)
        hidden = self.client.get(reverse("dashboard"))
        self.assertNotContains(hidden, "data-gashyai-shell")

    def test_launcher_state_is_bounded_to_active_company_and_session_owner(self):
        own_session = AssistantSession.objects.create(
            company=self.company,
            configuration=self.configuration,
            user=self.user,
            title="My scoped chat",
        )
        AssistantMessage.objects.create(
            session=own_session,
            role=AssistantMessage.Role.ASSISTANT,
            content="Scoped answer",
        )
        another_user = get_user_model().objects.create_user(username="another-gashyai-user")
        AssistantSession.objects.create(
            company=self.company,
            configuration=self.configuration,
            user=another_user,
            title="Another user's chat",
        )
        foreign_configuration = AssistantConfiguration.objects.create(company=self.other_company)
        AssistantSession.objects.create(
            company=self.other_company,
            configuration=foreign_configuration,
            user=self.user,
            title="Foreign company chat",
        )

        response = self.client.get(reverse("erpshop_moduleai_assistant:launcher_state"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selected_session_id"], own_session.pk)
        self.assertEqual([item["title"] for item in payload["sessions"]], ["My scoped chat"])
        self.assertEqual(payload["messages"][0]["content"], "Scoped answer")
        self.assertNotContains(response, "Foreign company chat")
        self.assertNotContains(response, "Another user's chat")

    def test_launcher_can_create_and_send_through_existing_governed_flow(self):
        created = self.client.post(
            reverse("erpshop_moduleai_assistant:new_session"),
            {"title": "New conversation"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(created.status_code, 201)
        session = AssistantSession.objects.get(pk=created.json()["session_id"])
        self.assertEqual(session.company, self.company)
        self.assertEqual(session.user, self.user)

        with patch("addons.ai.erpshop_moduleai_assistant.views.process_assistant_message.delay") as task:
            sent = self.client.post(
                reverse("erpshop_moduleai_assistant:send_message", args=[session.pk]),
                {"content": "Ringkas aktivitas hari ini."},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(sent.status_code, 202)
        message = session.messages.get(role=AssistantMessage.Role.USER)
        task.assert_called_once_with(message.pk)

    def test_launcher_query_count_does_not_grow_with_session_count(self):
        AssistantSession.objects.bulk_create(
            [
                AssistantSession(
                    company=self.company,
                    configuration=self.configuration,
                    user=self.user,
                    title=f"Chat {index}",
                )
                for index in range(2)
            ]
        )
        state_url = reverse("erpshop_moduleai_assistant:launcher_state")
        self.client.get(state_url)
        with CaptureQueriesContext(connection) as two_session_queries:
            self.client.get(state_url)

        AssistantSession.objects.bulk_create(
            [
                AssistantSession(
                    company=self.company,
                    configuration=self.configuration,
                    user=self.user,
                    title=f"Chat {index}",
                )
                for index in range(2, 12)
            ]
        )
        with CaptureQueriesContext(connection) as twelve_session_queries:
            self.client.get(state_url)
        self.assertEqual(len(two_session_queries), len(twelve_session_queries))

    def test_launcher_assets_keep_hmx_interaction_contract_with_gsshop_identity(self):
        static_root = Path(__file__).parent / "static" / "erpshop_moduleai_assistant"
        javascript = (static_root / "gashyai-launcher.js").read_text(encoding="utf-8")
        stylesheet = (static_root / "gashyai-launcher.css").read_text(encoding="utf-8")
        icon = (static_root / "gashyai-hat.svg").read_text(encoding="utf-8")

        self.assertIn("gashyai_launcher_position_ratio", javascript)
        self.assertIn("visualViewport", javascript)
        self.assertIn("setPointerCapture", javascript)
        self.assertIn("--gashyai-size: 68px", stylesheet)
        self.assertIn("touch-action: none", stylesheet)
        self.assertIn("Topi GashyAI ERP GSSHOP", icon)
        self.assertNotIn("logo-hashy", icon)
