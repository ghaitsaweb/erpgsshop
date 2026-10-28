from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from addons.ai.erpshop_moduleai_assistant.models import AssistantConfiguration
from core.models import Company, Product
from modules.apphub.models import ERPApp
from modules.organization.models import UserOrganizationAccess

from .models import AIAgent, AIConversation, AIMessage, AITask, AITaskEvent, AIWorkspace
from .services import execute_coworker_task


PROVIDER_RESULT = {
    "content": "Bounded advisory result.",
    "input_tokens": 20,
    "output_tokens": 8,
    "provider_message_id": "coworker-test",
    "latency_ms": 12,
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
