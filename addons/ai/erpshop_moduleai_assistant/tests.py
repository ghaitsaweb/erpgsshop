import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Company
from modules.apphub.models import ERPApp
from modules.organization.models import UserOrganizationAccess

from .forms import HashyOSConnectionForm
from .models import AssistantConfiguration, AssistantMessage, AssistantSession, HashyOSConnection
from .services import (
    MAX_PROVIDER_RESPONSE_BYTES,
    AssistantProviderError,
    _provider_endpoint,
    _request_messages,
    _response_text,
    _token_count,
    execute_message,
)


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
        with patch("addons.ai.erpshop_moduleai_assistant.views.process_assistant_message.delay") as task:
            sent = self.client.post(
                reverse("erpshop_moduleai_assistant:send_message", args=[session.pk]),
                {"content": "Summarize this budget."},
            )
        self.assertEqual(sent.status_code, 302)
        message = session.messages.get(role=AssistantMessage.Role.USER)
        self.assertEqual(message.state, AssistantMessage.State.PENDING)
        task.assert_called_once_with(message.pk)

    def test_login_defaults_to_the_assistant_but_honors_next(self):
        self.client.logout()
        credentials = {"username": self.user.username, "password": "safe-password"}

        default_login = self.client.post(reverse("login"), credentials)
        self.assertRedirects(
            default_login,
            reverse("erpshop_moduleai_assistant:home"),
            fetch_redirect_response=False,
        )

        self.client.logout()
        explicit_next = self.client.post(reverse("login"), {**credentials, "next": reverse("dashboard")})
        self.assertRedirects(explicit_next, reverse("dashboard"), fetch_redirect_response=False)

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

    def test_message_history_is_paged_without_losing_older_messages(self):
        config = AssistantConfiguration.objects.create(company=self.company)
        session = AssistantSession.objects.create(company=self.company, configuration=config, user=self.user)
        AssistantMessage.objects.bulk_create(
            [AssistantMessage(session=session, role=AssistantMessage.Role.USER, content=f"Message {i}") for i in range(125)]
        )
        first = self.client.get(reverse("erpshop_moduleai_assistant:home"), {"session": session.pk})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.context["messages"]), 100)
        self.assertTrue(first.context["has_older_messages"])
        older = self.client.get(
            reverse("erpshop_moduleai_assistant:home"),
            {"session": session.pk, "before": first.context["oldest_message_id"]},
        )
        self.assertEqual(older.status_code, 200)
        self.assertEqual(len(older.context["messages"]), 25)
        self.assertFalse(older.context["has_older_messages"])

    def test_provider_history_query_count_does_not_grow_with_messages(self):
        config = AssistantConfiguration.objects.create(company=self.company, allow_file_content=True)
        session = AssistantSession.objects.create(company=self.company, configuration=config, user=self.user)
        session.configuration  # Exclude the one-time configuration lookup from both measurements.
        AssistantMessage.objects.bulk_create(
            [AssistantMessage(session=session, role=AssistantMessage.Role.USER, content=f"Message {i}") for i in range(2)]
        )
        with CaptureQueriesContext(connection) as small_queries:
            _request_messages(session)
        AssistantMessage.objects.bulk_create(
            [AssistantMessage(session=session, role=AssistantMessage.Role.USER, content=f"Message {i}") for i in range(2, 12)]
        )
        with CaptureQueriesContext(connection) as large_queries:
            _request_messages(session)
        self.assertEqual(len(small_queries), len(large_queries))

    def test_provider_response_size_is_bounded(self):
        config = AssistantConfiguration.objects.create(
            company=self.company,
            provider=AssistantConfiguration.Provider.OPENAI_COMPATIBLE,
            api_base_url="https://provider.example/v1",
            credential_env_var="AI_TEST_KEY",
            model_name="test-model",
            active=True,
        )
        session = AssistantSession.objects.create(company=self.company, configuration=config, user=self.user)
        queued = AssistantMessage.objects.create(
            session=session, role=AssistantMessage.Role.USER, content="Hello", state=AssistantMessage.State.PENDING
        )
        with patch.dict("os.environ", {"AI_TEST_KEY": "test-only"}), patch(
            "addons.ai.erpshop_moduleai_assistant.services.build_opener"
        ) as opener:
            provider_response = opener.return_value.open.return_value.__enter__.return_value
            provider_response.read.return_value = b"x" * (MAX_PROVIDER_RESPONSE_BYTES + 1)
            result = execute_message(queued.pk)
        self.assertEqual(result["status"], AssistantMessage.State.FAILED)
        self.assertEqual(session.messages.get(role=AssistantMessage.Role.ASSISTANT).state, AssistantMessage.State.FAILED)
        provider_response.read.assert_called_once_with(MAX_PROVIDER_RESPONSE_BYTES + 1)

    def test_provider_reply_is_saved_with_usage(self):
        config = AssistantConfiguration.objects.create(
            company=self.company,
            provider=AssistantConfiguration.Provider.OPENAI_COMPATIBLE,
            api_base_url="https://provider.example/v1",
            credential_env_var="AI_TEST_KEY",
            model_name="test-model",
            active=True,
        )
        session = AssistantSession.objects.create(company=self.company, configuration=config, user=self.user)
        queued = AssistantMessage.objects.create(
            session=session, role=AssistantMessage.Role.USER, content="Hello", state=AssistantMessage.State.PENDING
        )
        payload = {
            "id": "reply-1",
            "choices": [{"message": {"content": "Hello from the provider"}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 6},
        }
        with patch.dict("os.environ", {"AI_TEST_KEY": "test-only"}), patch(
            "addons.ai.erpshop_moduleai_assistant.services.build_opener"
        ) as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            result = execute_message(queued.pk)
        reply = session.messages.get(role=AssistantMessage.Role.ASSISTANT)
        self.assertEqual(result["status"], AssistantMessage.State.COMPLETED)
        self.assertEqual(reply.content, "Hello from the provider")
        self.assertEqual((reply.input_tokens, reply.output_tokens), (4, 6))
        self.assertEqual(reply.provider_message_id, "reply-1")

    def test_provider_url_and_account_status_cannot_be_faked(self):
        with self.assertRaises(AssistantProviderError):
            _provider_endpoint("https://secret@provider.example/v1")
        with self.assertRaises(AssistantProviderError):
            _provider_endpoint("https://provider.example/v1?token=secret")
        self.assertNotIn("state", HashyOSConnectionForm().fields)
        self.assertNotIn("status_detail", HashyOSConnectionForm().fields)

    def test_malformed_provider_usage_and_reply_are_handled(self):
        with self.assertRaises(AssistantProviderError):
            _response_text({"choices": [{"message": 42}]})
        self.assertEqual(_token_count(["bad"]), 0)
        self.assertEqual(_token_count(10**20), 2_147_483_647)

    def test_page_context_has_a_size_limit(self):
        response = self.client.post(
            reverse("erpshop_moduleai_assistant:new_session"),
            {"page_context": '{"text":"' + ("a" * 12000) + '"}'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AssistantSession.objects.exists())

    def test_home_does_not_claim_an_unverified_hashy_connection(self):
        response = self.client.get(reverse("erpshop_moduleai_assistant:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provider configuration needed")
        self.assertFalse(HashyOSConnection.objects.exists())
