from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from core.models import Company
from modules.apphub.models import ERPApp
from modules.organization.models import UserOrganizationAccess

from .models import AssistantConfiguration, AssistantMessage, AssistantSession
from .services import execute_message


class HashyAssistantTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(code="AI", name="AI Test Company")
        self.user = get_user_model().objects.create_superuser(
            username="hashy-admin", email="hashy-admin@example.test", password="safe-password"
        )
        ERPApp.objects.create(
            slug="erpshop-moduleai-assistant",
            name="ERPShop Module AI — Hashy Assistant",
            category="AI & Data",
            summary="Test assistant",
            enabled=True,
            status=ERPApp.Status.BETA,
            route="/ai/assistant/",
        )
        self.client.force_login(self.user)

    def test_new_session_and_message_are_company_scoped_and_queued(self):
        start = self.client.post(reverse("erpshop_moduleai_assistant:new_session"), {"title": "Budget review"})
        self.assertEqual(start.status_code, 302)
        session = AssistantSession.objects.get(title="Budget review")
        self.assertEqual(session.company, self.company)
        with patch("addons.erpshop_moduleai_assistant.views.process_assistant_message.delay") as task:
            sent = self.client.post(
                reverse("erpshop_moduleai_assistant:send_message", args=[session.pk]),
                {"content": "Summarize this budget."},
            )
        self.assertEqual(sent.status_code, 302)
        message = session.messages.get(role=AssistantMessage.Role.USER)
        self.assertEqual(message.state, AssistantMessage.State.PENDING)
        task.assert_called_once_with(message.pk)

    def test_unconfigured_runtime_returns_a_safe_auditable_failure(self):
        config = AssistantConfiguration.objects.create(company=self.company)
        session = AssistantSession.objects.create(company=self.company, configuration=config, user=self.user)
        queued = AssistantMessage.objects.create(
            session=session, role=AssistantMessage.Role.USER, content="What is the status?", state=AssistantMessage.State.PENDING
        )
        result = execute_message(queued.pk)
        reply = session.messages.get(role=AssistantMessage.Role.ASSISTANT)
        self.assertEqual(result["status"], AssistantMessage.State.FAILED)
        self.assertEqual(reply.state, AssistantMessage.State.FAILED)
        self.assertNotIn("credential", reply.content.lower())

    def test_non_admin_can_only_open_their_own_session(self):
        permission = Permission.objects.get(codename="view_assistantsession")
        other_user = get_user_model().objects.create_user(username="assistant-user", password="safe-password")
        other_user.user_permissions.add(permission)
        organization_access = UserOrganizationAccess.objects.create(user=other_user, default_company=self.company)
        organization_access.companies.add(self.company)
        config = AssistantConfiguration.objects.create(company=self.company)
        foreign_session = AssistantSession.objects.create(company=self.company, configuration=config, user=self.user)
        self.client.force_login(other_user)
        response = self.client.get(reverse("erpshop_moduleai_assistant:home"), {"session": foreign_session.pk})
        self.assertEqual(response.status_code, 404)
