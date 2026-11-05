from unittest.mock import patch
from tempfile import TemporaryDirectory
from decimal import Decimal
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from addons.ai.erpshop_moduleai_assistant.models import AssistantConfiguration
from core.models import Company, Product
from modules.apphub.models import ERPApp
from modules.organization.models import UserOrganizationAccess

from .models import (
    AIAgent,
    AIChannelConfiguration,
    AIChannelInboundEvent,
    AIChannelMessage,
    AIChannelSession,
    AICompanyCredit,
    AICreditLedger,
    AIConversation,
    AICoworkerCron,
    AICoworkerConnectionCheck,
    AICoworkerHeartbeat,
    AICoworkerChannel,
    AICoworkerMemory,
    AICoworkerSecret,
    AICoworkerSubagent,
    AIMessage,
    AIModelCatalogAuditEvent,
    AIModelPricing,
    AIModelTemplate,
    AIProvider,
    AIUsageEvent,
    AIToolApproval,
    AITask,
    AITaskEvent,
    AIWorkspace,
    AIWebhookEndpoint,
)
from .channel_runtime import deliver_task_output, queue_inbound
from .services import (
    _messages,
    execute_coworker_task,
    expire_due_tool_approvals,
    queue_due_crons,
    queue_due_heartbeats,
    queue_subagent,
    run_connection_check,
)
from .tool_runtime import decide_approval, execute_approved_tool, request_approval


PROVIDER_RESULT = {
    "content": "Bounded advisory result.",
    "input_tokens": 20,
    "output_tokens": 8,
    "provider_message_id": "coworker-test",
    "latency_ms": 12,
}


AGENT_TOOL_RESULT = {
    "content": "",
    "tool_calls": [
        {"id": "call-search", "name": "web_search", "arguments": {"query": "ERP inventory trends"}}
    ],
    "input_tokens": 12,
    "output_tokens": 4,
    "provider_message_id": "coworker-tool-1",
    "finish_reason": "tool_calls",
    "latency_ms": 10,
}


AGENT_FINAL_RESULT = {
    "content": "The web search request is awaiting human approval.",
    "tool_calls": [],
    "input_tokens": 9,
    "output_tokens": 7,
    "provider_message_id": "coworker-tool-2",
    "finish_reason": "stop",
    "latency_ms": 11,
}


class AICoworkerTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(code="COW", name="Coworker Company")
        self.branch = self.company.branches.get(is_main=True)
        self.user = get_user_model().objects.create_superuser(
            username="coworker-admin", email="coworker-admin@example.test", password="safe-password"
        )
        self.workspace = AIWorkspace.objects.create(company=self.company, name="ERP Coworkers")
        self.agent = AIAgent.objects.create(
            workspace=self.workspace,
            name="Operations Analyst",
            role="Operational analysis",
            system_prompt="Analyze carefully and state limits.",
            allowed_tools=["erp_read"],
            max_context_records=50,
        )
        AssistantConfiguration.objects.create(company=self.company)
        ERPApp.objects.create(
            slug="ai-coworker",
            name="ERPShop AI Coworker",
            category="AI & Data",
            summary="Test coworker",
            enabled=True,
            status=ERPApp.Status.BETA,
            route="/ai/coworker/",
        )
        self.client.force_login(self.user)

    def _task(self, *, task_type=AITask.TaskType.SUMMARIZE, requested_by=None, **overrides):
        user = requested_by or self.user
        conversation = AIConversation.objects.create(agent=self.agent, user=user, title="Review products")
        AIMessage.objects.create(conversation=conversation, role=AIMessage.Role.USER, content="Review these records")
        values = {
            "agent": self.agent,
            "company": self.company,
            "branch": self.branch,
            "requested_by": user,
            "conversation": conversation,
            "title": "Review products",
            "task_type": task_type,
            "instruction": "Review these records and explain the limits.",
            "entity": "products" if task_type != AITask.TaskType.PLAN else "",
            "selected_fields": ["id", "sku", "name"] if task_type != AITask.TaskType.PLAN else [],
            "filters": [],
        }
        values.update(overrides)
        return AITask.objects.create(**values)

    def _telegram_channel(self, *, callback_secret="", enabled=True):
        self.agent.owner_user = self.user
        self.agent.save(update_fields=["owner_user", "updated_at"])
        channel = AIChannelConfiguration.objects.create(
            company=self.company,
            owner_user=self.user,
            name="Operations Telegram",
            channel_type=AIChannelConfiguration.ChannelType.TELEGRAM,
            credential_env_var="TEST_TELEGRAM_TOKEN",
            app_secret_env_var="TEST_TELEGRAM_CALLBACK_SECRET" if callback_secret else "",
            enabled=enabled,
        )
        AICoworkerChannel.objects.create(agent=self.agent, channel=channel)
        return channel

    def test_profile_and_task_are_created_through_governed_workspace(self):
        with patch("addons.ai.erpshop_ai_coworker.views.process_coworker_task.delay") as queued:
            response = self.client.post(
                reverse("erpshop_ai_coworker:create_task"),
                {
                    "agent": self.agent.pk,
                    "task_type": AITask.TaskType.ANALYZE,
                    "title": "Analyze products",
                    "instruction": "Find notable patterns.",
                    "entity": "products",
                    "field_names": "id,sku,name",
                    "filters_json": "[]",
                },
            )
        self.assertEqual(response.status_code, 302)
        task = AITask.objects.get(title="Analyze products")
        self.assertEqual((task.company_id, task.branch_id, task.requested_by_id), (self.company.pk, self.branch.pk, self.user.pk))
        self.assertEqual(task.events.get().event, "queued")
        queued.assert_called_once_with(task.pk)

    def test_connection_check_is_queued_for_only_the_active_company_coworker(self):
        with patch("addons.ai.erpshop_ai_coworker.views.process_connection_check.delay") as queued:
            response = self.client.post(reverse("erpshop_ai_coworker:connection_test", args=[self.agent.pk]))

        self.assertEqual(response.status_code, 302)
        check = AICoworkerConnectionCheck.objects.get()
        self.assertEqual((check.agent_id, check.requested_by_id, check.state), (self.agent.pk, self.user.pk, check.State.QUEUED))
        queued.assert_called_once_with(check.pk)

    def test_connection_check_uses_one_bounded_probe_and_stores_no_provider_response(self):
        self.agent.model_name = "readiness-test-model"
        self.agent.save(update_fields=["model_name", "updated_at"])
        check = AICoworkerConnectionCheck.objects.create(agent=self.agent, requested_by=self.user)
        with patch("addons.ai.erpshop_ai_coworker.services.request_completion", return_value=PROVIDER_RESULT) as provider:
            result = run_connection_check(check.pk)

        check.refresh_from_db()
        self.assertEqual(result["status"], AICoworkerConnectionCheck.State.COMPLETED)
        self.assertEqual(check.state, AICoworkerConnectionCheck.State.COMPLETED)
        self.assertEqual(check.provider_name, "company-default")
        self.assertEqual(check.model_name, "readiness-test-model")
        self.assertEqual(check.latency_ms, PROVIDER_RESULT["latency_ms"])
        self.assertFalse(check.safe_error)
        self.assertEqual(provider.call_args.args[1], [{"role": "user", "content": "Connection verification. Reply exactly: ready."}])
        self.assertEqual(provider.call_args.args[0].max_output_tokens, 16)
        self.assertEqual(provider.call_args.kwargs["model_name"], "readiness-test-model")
        self.assertNotIn(PROVIDER_RESULT["content"], str(check.__dict__))

    def test_connection_check_redacts_provider_error_from_the_audit_record(self):
        check = AICoworkerConnectionCheck.objects.create(agent=self.agent, requested_by=self.user)
        with patch(
            "addons.ai.erpshop_ai_coworker.services.request_completion",
            side_effect=Exception("authorization failed for secret never-store-this"),
        ):
            result = run_connection_check(check.pk)

        check.refresh_from_db()
        self.assertEqual(result["status"], AICoworkerConnectionCheck.State.FAILED)
        self.assertEqual(check.state, AICoworkerConnectionCheck.State.FAILED)
        self.assertNotIn("never-store-this", check.safe_error)
        self.assertIn("did not complete", check.safe_error)

    def test_worker_uses_read_only_snapshot_and_persists_audited_result(self):
        Product.objects.bulk_create(
            [Product(company=self.company, sku=f"COW-{index}", name=f"Product {index}") for index in range(4)]
        )
        task = self._task()
        with patch("addons.ai.erpshop_ai_coworker.services.request_completion", return_value=PROVIDER_RESULT) as provider:
            result = execute_coworker_task(task.pk)
        task.refresh_from_db()
        self.agent.refresh_from_db()
        self.assertEqual(result["status"], AITask.Status.COMPLETED)
        self.assertEqual(task.context_record_count, 4)
        self.assertEqual(task.result_text, PROVIDER_RESULT["content"])
        self.assertEqual(self.agent.total_requests, 1)
        self.assertEqual(list(task.events.values_list("event", flat=True)), ["started", "completed"])
        prompt = provider.call_args.args[1][1]["content"]
        self.assertIn("Governed ERP snapshot", prompt)
        self.assertIn('"sku":"COW-0"', prompt)

    def test_worker_passes_a_coworker_model_override_to_the_provider(self):
        self.agent.model_name = "coworker-analysis-model"
        self.agent.save(update_fields=["model_name", "updated_at"])
        task = self._task(task_type=AITask.TaskType.PLAN)

        with patch("addons.ai.erpshop_ai_coworker.services.request_completion", return_value=PROVIDER_RESULT) as provider:
            execute_coworker_task(task.pk)

        self.assertEqual(provider.call_args.kwargs["model_name"], "coworker-analysis-model")

    def test_tool_loop_creates_an_approval_without_executing_external_work(self):
        self.agent.allowed_tools = ["erp_read", "web_search"]
        self.agent.save(update_fields=["allowed_tools", "updated_at"])
        task = self._task(task_type=AITask.TaskType.PLAN)

        with patch(
            "addons.ai.erpshop_ai_coworker.services.request_agent_completion",
            side_effect=[AGENT_TOOL_RESULT, AGENT_FINAL_RESULT],
        ) as provider:
            result = execute_coworker_task(task.pk)

        task.refresh_from_db()
        approval = AIToolApproval.objects.get(task=task)
        self.assertEqual(result["status"], AITask.Status.COMPLETED)
        self.assertEqual(approval.tool_name, "web_search")
        self.assertEqual(approval.state, AIToolApproval.State.PENDING)
        self.assertEqual(task.result_data["iterations"], 2)
        self.assertEqual(task.result_text, AGENT_FINAL_RESULT["content"])
        self.assertEqual(provider.call_count, 2)
        self.assertTrue(task.conversation.messages.filter(role=AIMessage.Role.TOOL).exists())
        self.assertTrue(task.events.filter(event="tool_approval_requested").exists())

    def test_tool_loop_can_read_a_second_bounded_erp_snapshot(self):
        Product.objects.create(company=self.company, sku="TOOL-1", name="Tool snapshot product")
        self.agent.allowed_tools = ["erp_read", "web_search"]
        self.agent.save(update_fields=["allowed_tools", "updated_at"])
        task = self._task()
        tool_read = {
            **AGENT_TOOL_RESULT,
            "tool_calls": [
                {
                    "id": "call-read",
                    "name": "erp_read",
                    "arguments": {"entity": "products", "fields": ["id", "sku", "name"], "filters": []},
                }
            ],
        }

        with patch(
            "addons.ai.erpshop_ai_coworker.services.request_agent_completion",
            side_effect=[tool_read, AGENT_FINAL_RESULT],
        ):
            execute_coworker_task(task.pk)

        tool_message = task.conversation.messages.get(role=AIMessage.Role.TOOL)
        self.assertIn('"status":"completed"', tool_message.content)
        self.assertIn("TOOL-1", tool_message.content)
        self.assertFalse(AIToolApproval.objects.filter(task=task).exists())

    def test_global_durable_memory_is_bounded_and_added_to_agent_context(self):
        AICoworkerMemory.objects.create(
            agent=self.agent,
            memory_type=AICoworkerMemory.MemoryType.MEMORY,
            content="Operations must distinguish confirmed facts from assumptions.",
        )
        task = self._task(task_type=AITask.TaskType.PLAN)
        messages = _messages(task, None)
        self.assertIn("Administrator-maintained durable memory", messages[0]["content"])
        self.assertIn("confirmed facts", messages[0]["content"])

    def test_subagent_is_finalized_when_its_child_task_completes(self):
        self.agent.owner_user = self.user
        self.agent.save(update_fields=["owner_user", "updated_at"])
        subagent, child_task = queue_subagent(
            agent=self.agent,
            description="Review the weekly exception list",
            prompt="Create a bounded review plan.",
        )
        with patch("addons.ai.erpshop_ai_coworker.services.request_completion", return_value=PROVIDER_RESULT):
            result = execute_coworker_task(child_task.pk)

        subagent.refresh_from_db()
        self.assertEqual(result["status"], AITask.Status.COMPLETED)
        self.assertEqual(subagent.state, AICoworkerSubagent.State.COMPLETED)
        self.assertEqual(subagent.result, PROVIDER_RESULT["content"])
        self.assertGreaterEqual(subagent.duration_ms, 0)

    def test_due_cron_and_heartbeat_queue_one_bounded_task_each(self):
        self.agent.owner_user = self.user
        self.agent.save(update_fields=["owner_user", "updated_at"])
        now = timezone.now()
        AICoworkerCron.objects.create(
            name="Hourly plan",
            agent=self.agent,
            schedule_type=AICoworkerCron.ScheduleType.EVERY,
            interval_seconds=60,
            prompt="Review the current operational plan.",
        )
        AICoworkerHeartbeat.objects.create(
            name="Daily readiness",
            agent=self.agent,
            instruction="Check whether there are pending operational actions.",
            interval_seconds=60,
        )

        cron_ids = queue_due_crons(now=now)
        heartbeat_ids = queue_due_heartbeats(now=now)

        self.assertEqual(len(cron_ids), 1)
        self.assertEqual(len(heartbeat_ids), 1)
        self.assertEqual(AITask.objects.filter(pk__in=[*cron_ids, *heartbeat_ids]).count(), 2)

    def test_runtime_pages_and_memory_save_are_manager_scoped(self):
        response = self.client.get(reverse("erpshop_ai_coworker:runtime"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Durable memory")
        saved = self.client.post(
            reverse("erpshop_ai_coworker:memory_save"),
            {"agent": self.agent.pk, "content": "Use branch-scoped evidence in every analysis."},
        )
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(
            AICoworkerMemory.objects.get(agent=self.agent, participant=None).content,
            "Use branch-scoped evidence in every analysis.",
        )
        self.assertEqual(self.client.get(reverse("erpshop_ai_coworker:automation")).status_code, 200)
        self.assertEqual(self.client.get(reverse("erpshop_ai_coworker:subagents")).status_code, 200)

    def test_channel_inbound_is_deduplicated_and_deliverable(self):
        channel = self._telegram_channel()
        payload = {
            "update_id": 12,
            "message": {
                "message_id": 81,
                "from": {"id": 1200, "username": "operator"},
                "chat": {"id": 2200},
                "text": "What requires attention?",
            },
        }
        with patch("addons.ai.erpshop_ai_coworker.channel_runtime.process_coworker_task.delay") as queued, self.captureOnCommitCallbacks(execute=True):
            task = queue_inbound(channel, payload, "203.0.113.20")
            duplicate = queue_inbound(channel, payload, "203.0.113.20")

        self.assertIsNotNone(task)
        self.assertIsNone(duplicate)
        self.assertEqual(task.input_data["channel"]["channel_id"], channel.pk)
        self.assertEqual(task.input_data["channel"]["chat_id"], "2200")
        self.assertTrue(queued.called)
        session = AIChannelSession.objects.get(agent=self.agent)
        task.result_text = "Please review the purchase queue."
        task.save(update_fields=["result_text", "updated_at"])
        with patch("addons.ai.erpshop_ai_coworker.channel_runtime.send_text") as sent:
            deliver_task_output(task)
        sent.assert_called_once_with(channel, "2200", task.result_text)
        self.assertTrue(
            AIChannelMessage.objects.filter(session=session, role=AIChannelMessage.Role.ASSISTANT, content=task.result_text).exists()
        )

    def test_inbound_channel_queue_limit_rejects_new_work_before_it_reaches_provider(self):
        self.agent.max_inbound_queue_size = 1
        self.agent.save(update_fields=["max_inbound_queue_size", "updated_at"])
        channel = self._telegram_channel()
        self._task(task_type=AITask.TaskType.PLAN, input_data={"channel": {"channel_id": channel.pk}})
        payload = {
            "update_id": 13,
            "message": {
                "message_id": 82,
                "from": {"id": 1201, "username": "operator"},
                "chat": {"id": 2201},
                "text": "New inbound message",
            },
        }
        with patch("addons.ai.erpshop_ai_coworker.channel_runtime.process_coworker_task.delay") as queued:
            self.assertIsNone(queue_inbound(channel, payload, "203.0.113.21"))
        event = AIChannelInboundEvent.objects.get(channel=channel, external_message_id="82")
        self.assertEqual(event.dispatch_state, AIChannelInboundEvent.State.IGNORED)
        self.assertEqual(event.error, "Inbound queue limit reached.")
        queued.assert_not_called()

    def test_concurrent_dispatch_limit_defers_a_queued_task_without_provider_work(self):
        self.agent.max_concurrent_dispatches = 1
        self.agent.save(update_fields=["max_concurrent_dispatches", "updated_at"])
        running = self._task(task_type=AITask.TaskType.PLAN)
        running.status = AITask.Status.RUNNING
        running.save(update_fields=["status", "updated_at"])
        waiting = self._task(task_type=AITask.TaskType.PLAN)
        with patch("addons.ai.erpshop_ai_coworker.services.request_completion") as provider:
            result = execute_coworker_task(waiting.pk)
        waiting.refresh_from_db()
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(waiting.status, AITask.Status.QUEUED)
        provider.assert_not_called()

    def test_telegram_webhook_requires_the_configured_secret_token(self):
        channel = self._telegram_channel(callback_secret="telegram-callback-secret")
        url = reverse("erpshop_ai_coworker:channel_webhook", args=[channel.pk])
        payload = '{"message":{"message_id":7,"from":{"id":1},"chat":{"id":1},"text":"hello"}}'
        with patch.dict("os.environ", {"TEST_TELEGRAM_CALLBACK_SECRET": "telegram-callback-secret"}):
            rejected = self.client.post(url, data=payload, content_type="application/json")
            self.assertEqual(rejected.status_code, 401)
            with patch("addons.ai.erpshop_ai_coworker.channel_runtime.queue_inbound") as queued:
                accepted = self.client.post(
                    url,
                    data=payload,
                    content_type="application/json",
                    HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="telegram-callback-secret",
                )
        self.assertEqual(accepted.status_code, 202)
        queued.assert_called_once()

    def test_channel_and_webhook_management_keep_secrets_out_of_the_database(self):
        self.agent.owner_user = self.user
        self.agent.save(update_fields=["owner_user", "updated_at"])
        channel_response = self.client.post(
            reverse("erpshop_ai_coworker:channel_save"),
            {
                "name": "Support Telegram",
                "channel_type": AIChannelConfiguration.ChannelType.TELEGRAM,
                "credential_env_var": "SUPPORT_TELEGRAM_TOKEN",
                "app_secret_env_var": "SUPPORT_TELEGRAM_CALLBACK_SECRET",
                "enabled": "on",
                "agent": self.agent.pk,
                "config_json": "{}",
            },
        )
        self.assertEqual(channel_response.status_code, 302)
        configured = AIChannelConfiguration.objects.get(name="Support Telegram")
        self.assertEqual(configured.credential_env_var, "SUPPORT_TELEGRAM_TOKEN")
        self.assertEqual(configured.config, {})
        self.assertEqual(configured.coworker_link.agent_id, self.agent.pk)
        self.assertContains(self.client.get(reverse("erpshop_ai_coworker:channels")), "Inbound channels")

        response = self.client.post(
            reverse("erpshop_ai_coworker:webhook_save", args=[self.agent.pk]),
            {
                "name": "Operations event",
                "hmac_secret_env_var": "OPERATIONS_WEBHOOK_SECRET",
                "hmac_header": "X-Hub-Signature-256",
                "allowed_ips": "",
                "rate_limit_per_minute": "60",
                "max_body_bytes": "65536",
                "active": "on",
                "expires_at": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        endpoint = AIWebhookEndpoint.objects.get(name="Operations event")
        self.assertEqual(len(endpoint.token_hash), 64)
        first_view = self.client.get(response.url)
        self.assertContains(first_view, "Copy this endpoint now")
        second_view = self.client.get(response.url)
        self.assertNotContains(second_view, "Copy this endpoint now")

    def test_model_template_pricing_creates_one_usage_event_and_credit_ledger_entry(self):
        provider = AIProvider.objects.create(
            name="test-openai",
            display_name="Test OpenAI",
            api_base_url="https://provider.example.test/v1",
            credential_env_var="TEST_COWORKER_API_KEY",
        )
        template = AIModelTemplate.objects.create(
            name="Operations model",
            provider=provider,
            model_name="operations-test-model",
            max_output_tokens=512,
            context_window_tokens=4096,
        )
        AIModelPricing.objects.create(
            template=template,
            input_price_per_mtok=Decimal("2"),
            output_price_per_mtok=Decimal("4"),
            effective_from=timezone.now() - timedelta(minutes=1),
        )
        credit = AICompanyCredit.objects.create(company=self.company, credit_balance=Decimal("10"), currency="USD")
        self.agent.model_template = template
        self.agent.save(update_fields=["model_template", "updated_at"])

        with patch("addons.ai.erpshop_ai_coworker.services.request_completion", return_value=PROVIDER_RESULT):
            execute_coworker_task(self._task().pk)

        usage = AIUsageEvent.objects.get()
        self.assertEqual(usage.model_name, "operations-test-model")
        self.assertEqual(usage.total_cost, Decimal("0.000072"))
        self.assertEqual(self.company.ai_credit_ledger_entries.count(), 1)
        credit.refresh_from_db()
        self.assertEqual(credit.credit_balance, Decimal("9.999928"))
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.total_cost, Decimal("0.000072"))

    def test_enforced_credit_limit_blocks_provider_work_before_a_request_is_sent(self):
        AICompanyCredit.objects.create(
            company=self.company,
            credit_balance=Decimal("0"),
            credit_limit=Decimal("0"),
            enforce_credits=True,
            currency="USD",
        )
        task = self._task(task_type=AITask.TaskType.PLAN)
        with patch("addons.ai.erpshop_ai_coworker.services.request_completion") as provider:
            result = execute_coworker_task(task.pk)

        task.refresh_from_db()
        self.assertEqual(result["status"], AITask.Status.FAILED)
        self.assertIn("credit balance is at or below", task.error_message)
        provider.assert_not_called()

    def test_operations_dashboard_decides_pending_approval_and_records_a_trace(self):
        self.agent.allowed_tools = ["erp_read", "web_search"]
        self.agent.save(update_fields=["allowed_tools", "updated_at"])
        task = self._task(task_type=AITask.TaskType.PLAN)
        approval, created = request_approval(
            task=task,
            tool_name="web_search",
            request_preview={"query": "inventory trend"},
        )
        self.assertTrue(created)
        dashboard = self.client.get(reverse("erpshop_ai_coworker:operations"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "Pending tool requests")
        self.assertContains(dashboard, "inventory trend")

        response = self.client.post(
            reverse("erpshop_ai_coworker:approval_decide", args=[approval.pk]),
            {"decision": "approve"},
        )
        self.assertEqual(response.status_code, 302)
        approval.refresh_from_db()
        self.assertEqual(approval.state, AIToolApproval.State.APPROVED)
        self.assertEqual(approval.decided_by_id, self.user.pk)
        self.assertTrue(task.events.filter(event="tool_approved", actor=self.user).exists())

    def test_expired_tool_approvals_are_closed_idempotently_by_the_scheduler(self):
        self.agent.allowed_tools = ["erp_read", "web_search"]
        self.agent.save(update_fields=["allowed_tools", "updated_at"])
        task = self._task(task_type=AITask.TaskType.PLAN)
        approval, _created = request_approval(
            task=task,
            tool_name="web_search",
            request_preview={"query": "stale inventory trend"},
        )
        now = timezone.now()
        approval.expires_at = now - timedelta(seconds=1)
        approval.save(update_fields=["expires_at", "updated_at"])

        self.assertEqual(expire_due_tool_approvals(now=now), [approval.pk])
        approval.refresh_from_db()
        self.assertEqual(approval.state, AIToolApproval.State.EXPIRED)
        self.assertTrue(task.events.filter(event="tool_expired").exists())
        self.assertEqual(expire_due_tool_approvals(now=now), [])
        dashboard = self.client.get(reverse("erpshop_ai_coworker:operations"))
        self.assertNotContains(dashboard, "stale inventory trend")

    def test_completed_task_feedback_is_company_scoped_and_audited(self):
        task = self._task(task_type=AITask.TaskType.PLAN)
        task.status = AITask.Status.COMPLETED
        task.result_text = "A bounded operational recommendation."
        task.completed_at = timezone.now()
        task.save(update_fields=["status", "result_text", "completed_at", "updated_at"])
        detail = self.client.get(reverse("erpshop_ai_coworker:task_detail", args=[task.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Quality signal")

        response = self.client.post(
            reverse("erpshop_ai_coworker:task_feedback", args=[task.pk]),
            {"feedback": AITask.Feedback.UNHELPFUL, "note": "Please distinguish confirmed facts from assumptions."},
        )
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.feedback, AITask.Feedback.UNHELPFUL)
        self.assertEqual(task.feedback_note, "Please distinguish confirmed facts from assumptions.")
        self.assertIsNotNone(task.feedback_at)
        self.assertTrue(task.events.filter(event="task_feedback", actor=self.user).exists())
        dashboard = self.client.get(reverse("erpshop_ai_coworker:operations"))
        self.assertContains(dashboard, "Quality feedback")
        self.assertContains(dashboard, "1 needs improvement")

    def test_system_admin_manages_model_catalog_without_storing_credential_values(self):
        catalog = self.client.get(reverse("erpshop_ai_coworker:model_catalog"))
        self.assertEqual(catalog.status_code, 200)
        self.assertContains(catalog, "Credential boundary")
        provider_response = self.client.post(
            reverse("erpshop_ai_coworker:provider_save"),
            {
                "name": "catalog-openai",
                "display_name": "Catalog OpenAI",
                "backend": AIProvider.Backend.OPENAI_COMPAT,
                "api_base_url": "https://provider.example.test/v1",
                "credential_env_var": "CATALOG_PROVIDER_API_KEY",
                "active": "on",
            },
        )
        self.assertEqual(provider_response.status_code, 302)
        provider = AIProvider.objects.get(name="catalog-openai")
        self.assertEqual(provider.credential_env_var, "CATALOG_PROVIDER_API_KEY")
        audit = AIModelCatalogAuditEvent.objects.get(object_id=provider.pk, event="provider_saved")
        self.assertEqual(audit.actor_id, self.user.pk)
        self.assertNotIn("credential_env_var", audit.details)

        template_response = self.client.post(
            reverse("erpshop_ai_coworker:model_template_save"),
            {
                "name": "Catalog operations model",
                "description": "A bounded test model.",
                "provider": provider.pk,
                "model_name": "catalog-model",
                "max_output_tokens": "512",
                "context_window_tokens": "4096",
                "temperature": "0.20",
                "reasoning_effort": AIModelTemplate.ReasoningEffort.ADAPTIVE,
                "max_tool_iterations": "12",
                "max_tool_result_chars": "8000",
                "max_concurrent_interactive": "3",
                "max_concurrent_background": "1",
                "concurrent_queue_timeout_seconds": "60",
                "workspace_retention_days": "7",
                "subagent_max_depth": "3",
                "subagent_max_children_per_parent": "5",
                "subagent_timeout_seconds": "300",
                "active": "on",
            },
        )
        self.assertEqual(template_response.status_code, 302)
        template = AIModelTemplate.objects.get(name="Catalog operations model")
        self.assertEqual(template.provider_id, provider.pk)
        self.assertTrue(AIModelCatalogAuditEvent.objects.filter(object_id=template.pk, event="model_template_saved").exists())
        pricing_response = self.client.post(
            reverse("erpshop_ai_coworker:model_pricing_save"),
            {
                "template": template.pk,
                "min_context_tokens": "0",
                "max_context_tokens": "",
                "input_price_per_mtok": "2.000000",
                "output_price_per_mtok": "4.000000",
                "cache_read_price_per_mtok": "0",
                "cache_write_5m_price_per_mtok": "0",
                "cache_write_1h_price_per_mtok": "0",
                "currency": "USD",
                "effective_from": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "effective_to": "",
                "source": "catalog-test",
                "active": "on",
            },
        )
        self.assertEqual(pricing_response.status_code, 302)
        pricing = AIModelPricing.objects.get(template=template)
        self.assertTrue(AIModelCatalogAuditEvent.objects.filter(object_id=pricing.pk, event="model_pricing_saved").exists())
        other = get_user_model().objects.create_user(username="catalog-operator", password="safe-password")
        other.user_permissions.add(
            Permission.objects.get(codename="view_aiagent"),
            Permission.objects.get(codename="add_aitask"),
        )
        access = UserOrganizationAccess.objects.create(
            user=other,
            default_company=self.company,
            default_branch=self.branch,
        )
        access.companies.add(self.company)
        access.branches.add(self.branch)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("erpshop_ai_coworker:model_catalog")).status_code, 404)

    def test_system_admin_manages_credit_policy_through_an_audited_ledger(self):
        policy = self.client.post(
            reverse("erpshop_ai_coworker:credit_policy_save"),
            {
                "credit_limit": "0",
                "enforce_credits": "on",
                "currency": "usd",
                "low_balance_threshold": "2",
            },
        )
        self.assertEqual(policy.status_code, 302)
        credit = AICompanyCredit.objects.get(company=self.company)
        self.assertEqual(credit.credit_balance, Decimal("0"))
        self.assertEqual(credit.currency, "USD")
        self.assertTrue(credit.enforce_credits)

        adjustment = self.client.post(
            reverse("erpshop_ai_coworker:credit_adjust"),
            {
                "entry_type": AICreditLedger.EntryType.GRANT,
                "amount": "10.25000000",
                "description": "Initial AI budget",
            },
        )
        self.assertEqual(adjustment.status_code, 302)
        credit.refresh_from_db()
        entry = AICreditLedger.objects.get(company=self.company, entry_type=AICreditLedger.EntryType.GRANT)
        self.assertEqual(credit.credit_balance, Decimal("10.25000000"))
        self.assertEqual(entry.balance_after, credit.credit_balance)
        self.assertEqual(entry.created_by_id, self.user.pk)
        page = self.client.get(reverse("erpshop_ai_coworker:credit_policy"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Initial AI budget")

    def test_query_count_does_not_grow_with_context_rows(self):
        Product.objects.bulk_create(
            [Product(company=self.company, sku=f"SMALL-{index}", name=f"Small {index}") for index in range(2)]
        )
        with patch("addons.ai.erpshop_ai_coworker.services.request_completion", return_value=PROVIDER_RESULT):
            execute_coworker_task(self._task().pk)
            small_task = self._task()
            with CaptureQueriesContext(connection) as small:
                execute_coworker_task(small_task.pk)
            Product.objects.bulk_create(
                [Product(company=self.company, sku=f"LARGE-{index}", name=f"Large {index}") for index in range(12)]
            )
            large_task = self._task()
            with CaptureQueriesContext(connection) as large:
                execute_coworker_task(large_task.pk)
        self.assertEqual(len(small), len(large))

    def test_planning_task_never_reads_erp_context(self):
        task = self._task(task_type=AITask.TaskType.PLAN)
        with patch("addons.ai.erpshop_ai_coworker.services.bridge_api.search_read") as bridge, patch(
            "addons.ai.erpshop_ai_coworker.services.request_completion", return_value=PROVIDER_RESULT
        ):
            execute_coworker_task(task.pk)
        bridge.assert_not_called()

    def test_cancelled_task_is_idempotently_ignored_by_worker(self):
        task = self._task()
        response = self.client.post(reverse("erpshop_ai_coworker:cancel_task", args=[task.pk]))
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, AITask.Status.CANCELLED)
        self.assertEqual(execute_coworker_task(task.pk), {"status": "ignored"})
        self.assertEqual(AITaskEvent.objects.filter(task=task, event="cancelled").count(), 1)

    def test_non_manager_cannot_read_another_users_task(self):
        other = get_user_model().objects.create_user(username="coworker-user", password="safe-password")
        other.user_permissions.add(
            Permission.objects.get(codename="view_aiagent"),
            Permission.objects.get(codename="add_aitask"),
        )
        access = UserOrganizationAccess.objects.create(
            user=other,
            default_company=self.company,
            default_branch=self.branch,
        )
        access.companies.add(self.company)
        access.branches.add(self.branch)
        task = self._task()
        self.client.force_login(other)
        response = self.client.get(reverse("erpshop_ai_coworker:task_detail", args=[task.pk]))
        self.assertEqual(response.status_code, 404)

    def test_unexposed_field_is_rejected_before_queueing(self):
        response = self.client.post(
            reverse("erpshop_ai_coworker:create_task"),
            {
                "agent": self.agent.pk,
                "task_type": AITask.TaskType.SUMMARIZE,
                "title": "Unsafe field",
                "instruction": "Read an unavailable field.",
                "entity": "products",
                "field_names": "id,password",
                "filters_json": "[]",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AITask.objects.filter(title="Unsafe field").exists())

    def test_secret_references_merge_system_and_participant_environment_values(self):
        participant = AICoworkerParticipant.objects.create(
            agent=self.agent,
            channel_type="telegram",
            external_user_id="coworker-secret-user",
            external_chat_id="coworker-secret-chat",
        )
        system_secret = AICoworkerSecret(
            agent=self.agent,
            scope=AICoworkerSecret.Scope.SYSTEM,
            key="shared-token",
            credential_env_var="COWORKER_SHARED_TOKEN",
        )
        system_secret.full_clean()
        system_secret.save()
        participant_secret = AICoworkerSecret(
            agent=self.agent,
            participant=participant,
            scope=AICoworkerSecret.Scope.PARTICIPANT,
            key="shared-token",
            credential_env_var="COWORKER_PARTICIPANT_TOKEN",
        )
        participant_secret.full_clean()
        participant_secret.save()

        with patch.dict(
            "os.environ",
            {"COWORKER_SHARED_TOKEN": "system-value", "COWORKER_PARTICIPANT_TOKEN": "participant-value"},
        ):
            self.assertTrue(system_secret.is_available)
            self.assertEqual(
                AICoworkerSecret.resolve_for_participant(
                    agent_id=self.agent.pk,
                    participant_id=participant.pk,
                ),
                {"shared-token": "participant-value"},
            )
        self.assertFalse(system_secret.is_available)

    def test_approved_filesystem_tool_is_scoped_to_the_coworker_workspace(self):
        self.agent.allowed_tools = ["filesystem"]
        self.agent.save(update_fields=["allowed_tools", "updated_at"])
        task = self._task(task_type=AITask.TaskType.PLAN)
        approval, _created = request_approval(
            task=task,
            tool_name="filesystem",
            request_preview={"tool": "filesystem", "arguments": {"operation": "write", "path": "notes/today.txt", "content": "safe"}},
        )
        approval = decide_approval(approval=approval, actor=self.user, approve=True)
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            executed = execute_approved_tool(approval)
            self.assertEqual(executed.state, AIToolApproval.State.EXECUTED)
            self.assertEqual(executed.result["status"], "completed")
            self.assertEqual(executed.result["bytes_written"], 4)
