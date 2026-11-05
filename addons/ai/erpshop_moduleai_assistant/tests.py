import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Company
from core.models import Product
from modules.apphub.models import ERPApp
from modules.organization.models import UserOrganizationAccess

from .forms import HashyOSConnectionForm
from .flow_generation import _generation_messages, execute_flow_generation, flow_generation_prerequisites, reason_about_business_flows
from .knowledge import rebuild_document_chunks, retrieve_knowledge
from .knowledge_graph import build_knowledge_graph
from .models import (
    AIWorkProfileRefreshSetting,
    AIWorkProfileSignal,
    AIWorkSignalOutbox,
    AIDigestRecord,
    AIDigestRule,
    AIDigestRuleSource,
    AIDigestRuleSourceAction,
    AIKnowledgeChunk,
    AIKnowledgeBusinessFlow,
    AIKnowledgeBusinessFlowGenerationJob,
    AIKnowledgeCategory,
    AIKnowledgeCompanyProfile,
    AIKnowledgeDocument,
    AIKnowledgeRecordModelMapping,
    AssistantConfiguration,
    AssistantMessage,
    AssistantSession,
    HashyOSConnection,
)
from .services import (
    MAX_PROVIDER_RESPONSE_BYTES,
    AssistantProviderError,
    _provider_endpoint,
    _request_messages,
    _response_text,
    _token_count,
    execute_message,
    request_agent_completion,
)
from .work_intelligence import profile_context_for, record_assistant_turn, refresh_work_profiles
from .digest import run_digest_rule


class HashyAssistantTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(code="AI", name="AI Test Company")
        self.branch = self.company.branches.get(is_main=True)
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
        self.assertEqual(session.branch, self.branch)
        with patch("addons.ai.erpshop_moduleai_assistant.views.process_assistant_message.delay") as task:
            sent = self.client.post(
                reverse("erpshop_moduleai_assistant:send_message", args=[session.pk]),
                {"content": "Summarize this budget."},
            )
        self.assertEqual(sent.status_code, 302)
        message = session.messages.get(role=AssistantMessage.Role.USER)
        self.assertEqual(message.state, AssistantMessage.State.PENDING)
        task.assert_called_once_with(message.pk)

    def test_knowledge_document_is_company_scoped_and_ready_from_reviewed_text(self):
        response = self.client.post(
            reverse("erpshop_moduleai_assistant:knowledge_documents"),
            {"title": "Expense policy", "category": "Finance", "extracted_text": "Expenses need a receipt."},
        )
        self.assertRedirects(response, reverse("erpshop_moduleai_assistant:knowledge_documents"))
        document = AIKnowledgeDocument.objects.get(title="Expense policy")
        self.assertEqual(document.company, self.company)
        self.assertEqual(document.status, AIKnowledgeDocument.Status.READY)
        self.assertTrue(document.active)

        archived = self.client.post(
            reverse("erpshop_moduleai_assistant:knowledge_document_state", args=[document.pk]), {"action": "archive"}
        )
        self.assertRedirects(archived, reverse("erpshop_moduleai_assistant:knowledge_documents"))
        document.refresh_from_db()
        self.assertEqual(document.status, AIKnowledgeDocument.Status.ARCHIVED)
        self.assertFalse(document.active)

    def test_branch_scoped_knowledge_includes_only_company_wide_and_active_session_branch(self):
        other_branch = self.company.branches.create(code="AI-OPS", name="Operations Branch", sequence=20)
        company_wide = AIKnowledgeDocument.objects.create(
            company=self.company,
            title="Company receipt policy",
            extracted_text="Every reimbursement needs an original receipt.",
            status=AIKnowledgeDocument.Status.READY,
        )
        current_branch = AIKnowledgeDocument.objects.create(
            company=self.company,
            branch=self.branch,
            title="Main branch receipt routing",
            extracted_text="Main branch receipt routing sends reimbursement receipts to the Jakarta finance desk.",
            status=AIKnowledgeDocument.Status.READY,
        )
        other_branch_document = AIKnowledgeDocument.objects.create(
            company=self.company,
            branch=other_branch,
            title="Operations receipt routing",
            extracted_text="Operations receipt routing sends reimbursement receipts to the warehouse controller.",
            status=AIKnowledgeDocument.Status.READY,
        )
        for document in (company_wide, current_branch, other_branch_document):
            rebuild_document_chunks(document)

        main_result = retrieve_knowledge(self.company.pk, "receipt reimbursement routing", branch_id=self.branch.pk)
        other_result = retrieve_knowledge(self.company.pk, "receipt reimbursement routing", branch_id=other_branch.pk)
        self.assertIn("Company receipt policy", main_result["context"])
        self.assertIn("Main branch receipt routing", main_result["context"])
        self.assertNotIn("Operations receipt routing", main_result["context"])
        self.assertIn("Operations receipt routing", other_result["context"])
        self.assertNotIn("Main branch receipt routing", other_result["context"])

        config = AssistantConfiguration.objects.create(company=self.company, allow_knowledge_context=True)
        session = AssistantSession.objects.create(
            company=self.company,
            branch=self.branch,
            configuration=config,
            user=self.user,
        )
        messages = _request_messages(session, knowledge_query="Where do I route a reimbursement receipt?")
        context = "\n".join(message["content"] for message in messages)
        self.assertIn("Main branch receipt routing", context)
        self.assertNotIn("Operations receipt routing", context)

    def test_knowledge_profile_and_category_are_company_scoped_and_in_context(self):
        profile_response = self.client.post(
            reverse("erpshop_moduleai_assistant:knowledge_documents"),
            {
                "action": "profile",
                "summary": "A multi-branch retail company.",
                "operating_context": "Receipts are reviewed by finance before reimbursement.",
                "active": "on",
            },
        )
        self.assertRedirects(profile_response, reverse("erpshop_moduleai_assistant:knowledge_documents"))
        profile = AIKnowledgeCompanyProfile.objects.get(company=self.company)
        self.assertEqual(profile.summary, "A multi-branch retail company.")

        category_response = self.client.post(
            reverse("erpshop_moduleai_assistant:knowledge_documents"),
            {"action": "category", "name": "Finance", "description": "Finance policies", "active": "on"},
        )
        self.assertRedirects(category_response, reverse("erpshop_moduleai_assistant:knowledge_documents"))
        category = AIKnowledgeCategory.objects.get(name="Finance")
        self.assertEqual(category.company, self.company)
        self.assertFalse(AIKnowledgeCategory.objects.filter(company__code="AI2", name="Finance").exists())

        archived_category = self.client.post(
            reverse("erpshop_moduleai_assistant:knowledge_category_state", args=[category.pk]), {"action": "archive"}
        )
        self.assertRedirects(archived_category, reverse("erpshop_moduleai_assistant:knowledge_documents"))
        category.refresh_from_db()
        self.assertFalse(category.active)

        config = AssistantConfiguration.objects.create(company=self.company, allow_knowledge_context=True)
        session = AssistantSession.objects.create(company=self.company, configuration=config, user=self.user)
        context = "\n".join(message["content"] for message in _request_messages(session))
        self.assertIn("A multi-branch retail company.", context)
        self.assertIn("Receipts are reviewed by finance", context)

    def test_active_business_flow_is_company_scoped_and_added_as_bounded_reference_context(self):
        response = self.client.post(
            reverse("erpshop_moduleai_assistant:knowledge_documents"),
            {
                "action": "flow",
                "name": "Expense reimbursement",
                "trigger": "An employee submits an eligible expense.",
                "description": "Reviewed reimbursement handoff between employee, manager, and finance.",
                "steps_text": "Collect the original receipt.\nObtain manager approval.\nFinance validates the claim.",
                "edges_text": "1 -> 2 : submit\n2 -> 3 : approve",
                "status": AIKnowledgeBusinessFlow.Status.ACTIVE,
            },
        )
        self.assertRedirects(response, reverse("erpshop_moduleai_assistant:knowledge_documents"))
        flow = AIKnowledgeBusinessFlow.objects.get(name="Expense reimbursement")
        self.assertEqual(flow.company, self.company)
        self.assertEqual(len(flow.steps), 3)
        self.assertEqual(len(flow.edges), 2)
        self.assertFalse(AIKnowledgeBusinessFlow.objects.filter(company__code="AI2", name=flow.name).exists())

        updated = self.client.post(
            reverse("erpshop_moduleai_assistant:knowledge_documents"),
            {
                "action": "flow_update",
                "flow_id": flow.pk,
                "name": flow.name,
                "trigger": "A corrected employee claim is submitted.",
                "description": "Updated reimbursement handoff.",
                "steps_text": "Collect the original receipt.\nFinance validates the claim.\nNotify the employee.",
                "edges_text": "1 -> 2 : validate\n2 -> 3 : notify",
                "status": AIKnowledgeBusinessFlow.Status.ACTIVE,
            },
        )
        self.assertRedirects(updated, reverse("erpshop_moduleai_assistant:knowledge_documents"))
        flow.refresh_from_db()
        self.assertEqual(flow.description, "Updated reimbursement handoff.")
        self.assertEqual(flow.edges[1]["label"], "notify")
        detail = self.client.get(reverse("erpshop_moduleai_assistant:knowledge_flow_detail", args=[flow.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["name"], flow.name)
        flow_map = self.client.get(reverse("erpshop_moduleai_assistant:knowledge_flow_map", args=[flow.pk]))
        self.assertEqual(flow_map.status_code, 200)
        self.assertEqual(
            [node["label"] for node in flow_map.json()["nodes"]],
            ["Collect the original receipt.", "Finance validates the claim.", "Notify the employee."],
        )
        self.assertEqual(flow_map.json()["edges"][0]["source_node_label"], "Collect the original receipt.")
        self.assertEqual(flow_map.json()["edges"][0]["target_node_label"], "Finance validates the claim.")
        knowledge_page = self.client.get(reverse("erpshop_moduleai_assistant:knowledge_documents"))
        self.assertContains(knowledge_page, "Generate reviewed draft")
        self.assertContains(knowledge_page, "data-flow-tools")

        foreign_company = Company.objects.create(code="AI2", name="Other AI Company")
        AIKnowledgeBusinessFlow.objects.create(
            company=foreign_company,
            name="Foreign process",
            description="Must remain outside this company context.",
            steps=[{"sequence": 1, "description": "Do not expose this step."}],
            status=AIKnowledgeBusinessFlow.Status.ACTIVE,
        )
        config = AssistantConfiguration.objects.create(company=self.company, allow_knowledge_context=True)
        session = AssistantSession.objects.create(company=self.company, configuration=config, user=self.user)
        messages, sources = _request_messages(session, knowledge_query="How do I submit an expense?", include_knowledge_sources=True)
        context = "\n".join(message["content"] for message in messages)
        self.assertIn("Expense reimbursement", context)
        self.assertIn("[F1]", context)
        self.assertNotIn("Foreign process", context)
        self.assertEqual(sources["citations"][0]["label"], "F1")

        archived = self.client.post(
            reverse("erpshop_moduleai_assistant:knowledge_flow_state", args=[flow.pk]), {"action": "archive"}
        )
        self.assertRedirects(archived, reverse("erpshop_moduleai_assistant:knowledge_documents"))
        flow.refresh_from_db()
        self.assertEqual(flow.status, AIKnowledgeBusinessFlow.Status.ARCHIVED)

    def test_branch_scoped_record_mappings_include_only_company_wide_and_active_branch(self):
        other_branch = self.company.branches.create(code="AI-OPS", name="Operations Branch", sequence=20)
        company_wide = AIKnowledgeRecordModelMapping.objects.create(
            company=self.company,
            record_model="products",
            display_name="Company product catalog",
        )
        main_branch = AIKnowledgeRecordModelMapping.objects.create(
            company=self.company,
            branch=self.branch,
            record_model="products",
            display_name="Main branch product catalog",
        )
        other_branch_mapping = AIKnowledgeRecordModelMapping.objects.create(
            company=self.company,
            branch=other_branch,
            record_model="products",
            display_name="Operations product catalog",
        )

        main_graph = build_knowledge_graph(self.company.pk, branch_id=self.branch.pk, limit=100)
        operations_graph = build_knowledge_graph(self.company.pk, branch_id=other_branch.pk, limit=100)
        company_graph = build_knowledge_graph(self.company.pk, limit=100)
        main_mapping_ids = {node["id"] for node in main_graph["graph"]["nodes"] if node["source"] == "mapping"}
        operations_mapping_ids = {
            node["id"] for node in operations_graph["graph"]["nodes"] if node["source"] == "mapping"
        }
        company_mapping_ids = {node["id"] for node in company_graph["graph"]["nodes"] if node["source"] == "mapping"}
        self.assertEqual(main_mapping_ids, {f"record_model:{company_wide.pk}", f"record_model:{main_branch.pk}"})
        self.assertEqual(
            operations_mapping_ids,
            {f"record_model:{company_wide.pk}", f"record_model:{other_branch_mapping.pk}"},
        )
        self.assertEqual(company_mapping_ids, {f"record_model:{company_wide.pk}"})
        self.assertEqual(main_graph["meta"]["scope"], "company-wide and active branch")

        foreign_company = Company.objects.create(code="AIMAP", name="Foreign mapping company")
        invalid_mapping = AIKnowledgeRecordModelMapping(
            company=self.company,
            branch=foreign_company.branches.get(is_main=True),
            record_model="warehouses",
            display_name="Invalid foreign branch mapping",
        )
        with self.assertRaises(ValidationError):
            invalid_mapping.full_clean()

    def test_business_flow_generation_prerequisites_and_reason_are_company_scoped(self):
        prerequisites = self.client.get(
            reverse("erpshop_moduleai_assistant:knowledge_flow_generation_prerequisites")
        )
        self.assertEqual(prerequisites.status_code, 200)
        self.assertFalse(prerequisites.json()["can_generate"])
        blocked = self.client.post(
            reverse("erpshop_moduleai_assistant:generate_knowledge_flow"),
            data=json.dumps({"overwriteManual": False}),
            content_type="application/json",
        )
        self.assertEqual(blocked.status_code, 409)

        local_flow = AIKnowledgeBusinessFlow.objects.create(
            company=self.company,
            name="Expense reimbursement",
            trigger="Employee submits an expense claim.",
            description="Manager and finance review an expense claim.",
            steps=[{"sequence": 1, "description": "Collect the original receipt."}],
            status=AIKnowledgeBusinessFlow.Status.ACTIVE,
        )
        foreign_company = Company.objects.create(code="AIREASON", name="Foreign reason company")
        AIKnowledgeBusinessFlow.objects.create(
            company=foreign_company,
            name="Foreign reimbursement",
            steps=[{"sequence": 1, "description": "Do not disclose this flow."}],
            status=AIKnowledgeBusinessFlow.Status.ACTIVE,
        )
        reason = self.client.post(
            reverse("erpshop_moduleai_assistant:reason_knowledge_flow"),
            data=json.dumps({"query": "expense receipt"}),
            content_type="application/json",
        )
        self.assertEqual(reason.status_code, 200)
        self.assertEqual(reason.json()["matches"][0]["id"], local_flow.pk)
        self.assertEqual(reason.json()["reasoning_summary"]["kind"], "decision_summary_not_raw_chain_of_thought")
        self.assertNotIn("Foreign reimbursement", str(reason.json()))

    def test_business_flow_generation_job_creates_a_draft_only_after_provider_result(self):
        configuration = AssistantConfiguration.objects.create(company=self.company)
        job = AIKnowledgeBusinessFlowGenerationJob.objects.create(
            company=self.company,
            configuration=configuration,
            requested_by=self.user,
        )
        generated = {
            "name": "Supplier onboarding",
            "trigger": "A supplier needs approval.",
            "description": "Reviewed supplier onboarding reference.",
            "steps": [
                {"description": "Collect supplier information."},
                {"description": "Review the supplier documentation."},
            ],
            "edges": [{"from_sequence": 1, "to_sequence": 2, "label": "review"}],
        }
        with patch(
            "addons.ai.erpshop_moduleai_assistant.flow_generation.request_completion",
            return_value={
                "content": json.dumps(generated),
                "input_tokens": 12,
                "output_tokens": 34,
                "latency_ms": 56,
                "provider_message_id": "flow-generation-test",
            },
        ):
            result = execute_flow_generation(str(job.job_id))
        self.assertEqual(result["status"], AIKnowledgeBusinessFlowGenerationJob.State.COMPLETED)
        job.refresh_from_db()
        self.assertEqual(job.state, AIKnowledgeBusinessFlowGenerationJob.State.COMPLETED)
        self.assertEqual(job.result_flow.status, AIKnowledgeBusinessFlow.Status.DRAFT)
        self.assertEqual(job.result_flow.steps[1]["description"], "Review the supplier documentation.")
        self.assertEqual((job.input_tokens, job.output_tokens, job.latency_ms), (12, 34, 56))
        job_response = self.client.get(
            reverse("erpshop_moduleai_assistant:knowledge_flow_generation_job", args=[job.job_id])
        )
        self.assertEqual(job_response.status_code, 200)
        self.assertEqual(job_response.json()["business_flow_map_id"], job.result_flow_id)

    def test_flow_generation_and_reason_queries_are_bounded_as_reference_data_grows(self):
        configuration = AssistantConfiguration.objects.create(company=self.company)
        AIKnowledgeBusinessFlow.objects.create(
            company=self.company,
            name="Expense reimbursement",
            trigger="Expense receipt review",
            steps=[{"sequence": 1, "description": "Review the expense receipt."}],
            status=AIKnowledgeBusinessFlow.Status.ACTIVE,
        )
        with CaptureQueriesContext(connection) as small_queries:
            flow_generation_prerequisites(self.company.pk, configuration)
            _generation_messages(self.company.pk)
            reason_about_business_flows(self.company.pk, "expense receipt")

        for index in range(12):
            AIKnowledgeDocument.objects.create(
                company=self.company,
                title=f"Company-wide generation reference {index}",
                extracted_text="Approved company-wide reference material.",
                status=AIKnowledgeDocument.Status.READY,
            )
            AIKnowledgeBusinessFlow.objects.create(
                company=self.company,
                name=f"Additional flow {index}",
                steps=[{"sequence": 1, "description": "Additional reviewed step."}],
                status=AIKnowledgeBusinessFlow.Status.ACTIVE,
            )
        with CaptureQueriesContext(connection) as larger_queries:
            flow_generation_prerequisites(self.company.pk, configuration)
            _generation_messages(self.company.pk)
            reason_about_business_flows(self.company.pk, "expense receipt")

        self.assertEqual(len(small_queries), len(larger_queries))

    def test_knowledge_graph_contract_is_company_scoped_and_has_fixed_query_growth(self):
        category = AIKnowledgeCategory.objects.create(
            company=self.company,
            name="Finance",
            description="Reviewed financial guidance.",
        )
        document = AIKnowledgeDocument.objects.create(
            company=self.company,
            title="Expense policy",
            category=category.name,
            extracted_text="A receipt is required for reimbursement.",
            status=AIKnowledgeDocument.Status.READY,
        )
        flow = AIKnowledgeBusinessFlow.objects.create(
            company=self.company,
            name="Expense reimbursement",
            description="Reimbursement handoff.",
            steps=[
                {"sequence": 1, "description": "Collect the original receipt."},
                {"sequence": 2, "description": "Finance validates the claim."},
            ],
            status=AIKnowledgeBusinessFlow.Status.ACTIVE,
        )
        mapping_response = self.client.post(
            reverse("erpshop_moduleai_assistant:knowledge_documents"),
            {
                "action": "mapping",
                "record_model": "products",
                "display_name": "Products",
                "category": category.name,
                "module_name": "Inventory",
                "feature_tags_text": "stock, replenishment",
                "business_flow": flow.pk,
                "active": "on",
            },
        )
        self.assertRedirects(mapping_response, reverse("erpshop_moduleai_assistant:knowledge_documents"))
        mapping = AIKnowledgeRecordModelMapping.objects.get(record_model="products")
        self.assertEqual(mapping.company, self.company)
        self.assertEqual(mapping.feature_tags, ["stock", "replenishment"])
        updated_mapping = self.client.post(
            reverse("erpshop_moduleai_assistant:knowledge_documents"),
            {
                "action": "mapping_update",
                "mapping_id": mapping.pk,
                "record_model": "products",
                "display_name": "Product catalog",
                "category": category.name,
                "module_name": "Inventory",
                "feature_tags_text": "stock, catalog",
                "business_flow": flow.pk,
                "active": "on",
            },
        )
        self.assertRedirects(updated_mapping, reverse("erpshop_moduleai_assistant:knowledge_documents"))
        mapping.refresh_from_db()
        self.assertEqual((mapping.display_name, mapping.feature_tags), ("Product catalog", ["stock", "catalog"]))
        foreign_company = Company.objects.create(code="AIGRAPH", name="Foreign graph company")
        AIKnowledgeDocument.objects.create(
            company=foreign_company,
            title="Foreign document",
            extracted_text="This must not enter the active company graph.",
            status=AIKnowledgeDocument.Status.READY,
        )

        with CaptureQueriesContext(connection) as small_queries:
            graph = build_knowledge_graph(self.company.pk, limit=50)
        for index in range(12):
            AIKnowledgeDocument.objects.create(
                company=self.company,
                title=f"Additional policy {index}",
                extracted_text="Reviewed local policy.",
                status=AIKnowledgeDocument.Status.READY,
            )
        with CaptureQueriesContext(connection) as larger_queries:
            larger_graph = build_knowledge_graph(self.company.pk, limit=50)

        node_ids = {node["id"] for node in graph["graph"]["nodes"]}
        self.assertIn(f"category:{category.pk}", node_ids)
        self.assertIn(f"document:{document.pk}", node_ids)
        self.assertIn(f"flow:{flow.pk}", node_ids)
        self.assertIn(f"record_model:{mapping.pk}", node_ids)
        self.assertIn(f"step:{flow.pk}:1", node_ids)
        self.assertNotIn("Foreign document", str(graph))
        self.assertIn(
            (f"category:{category.pk}", f"document:{document.pk}", "categorizes"),
            {(edge["source"], edge["target"], edge["label"]) for edge in graph["graph"]["edges"]},
        )
        self.assertIn(
            (f"flow:{flow.pk}", f"record_model:{mapping.pk}", "uses entity"),
            {(edge["source"], edge["target"], edge["label"]) for edge in graph["graph"]["edges"]},
        )
        self.assertEqual(len(small_queries), len(larger_queries))
        self.assertLessEqual(len(larger_graph["graph"]["nodes"]), 200)

        self.assertEqual(self.client.get(reverse("erpshop_moduleai_assistant:knowledge_graph_status")).status_code, 200)
        overview = self.client.get(reverse("erpshop_moduleai_assistant:knowledge_graph_overview"))
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["counts"]["documents"], 13)
        self.assertEqual(overview.json()["counts"]["record_model_mappings"], 1)
        view = self.client.get(reverse("erpshop_moduleai_assistant:knowledge_graph_view"), {"limit": "50"})
        self.assertEqual(view.status_code, 200)
        self.assertIn("nodes", view.json()["graph"])
        search = self.client.get(reverse("erpshop_moduleai_assistant:knowledge_graph_search"), {"q": "expense"})
        self.assertEqual(search.status_code, 200)
        self.assertIn(f"document:{document.pk}", {node["id"] for node in search.json()["results"]})
        detail = self.client.get(reverse("erpshop_moduleai_assistant:knowledge_graph_node", args=[f"document:{document.pk}"]))
        self.assertEqual(detail.status_code, 200)
        neighbors = self.client.get(
            reverse("erpshop_moduleai_assistant:knowledge_graph_neighbors"), {"node_id": f"category:{category.pk}"}
        )
        self.assertEqual(neighbors.status_code, 200)
        self.assertIn(f"document:{document.pk}", {node["id"] for node in neighbors.json()["neighbors"]})
        path = self.client.get(
            reverse("erpshop_moduleai_assistant:knowledge_graph_path"),
            {"from_node_id": f"category:{category.pk}", "to_node_id": f"document:{document.pk}"},
        )
        self.assertEqual(path.status_code, 200)
        self.assertEqual([node["id"] for node in path.json()["nodes"]], [f"category:{category.pk}", f"document:{document.pk}"])
        trace = self.client.get(
            reverse("erpshop_moduleai_assistant:knowledge_graph_trace"),
            {"target_type": "document", "target_id": document.pk},
        )
        self.assertEqual(trace.status_code, 200)
        self.assertEqual(trace.json()["node"]["id"], f"document:{document.pk}")
        archived_mapping = self.client.post(
            reverse("erpshop_moduleai_assistant:knowledge_mapping_state", args=[mapping.pk]), {"action": "archive"}
        )
        self.assertRedirects(archived_mapping, reverse("erpshop_moduleai_assistant:knowledge_documents"))
        mapping.refresh_from_db()
        self.assertFalse(mapping.active)
        graph_page = self.client.get(reverse("erpshop_moduleai_assistant:knowledge_graph_page"))
        self.assertEqual(graph_page.status_code, 200)
        self.assertContains(graph_page, "ai_base_knowledge_graph_canvas")
        self.assertContains(graph_page, "Loading knowledge graph")

    def test_proactive_greeting_uses_the_current_user_erp_scope(self):
        Product.objects.create(company=self.company, sku="GREETING-1", name="Greeting product")
        response = self.client.post(
            reverse("erpshop_moduleai_assistant:proactive_greeting"),
            data=json.dumps({"page_context": {"entity": "products", "view_type": "list"}, "language": "id"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["display"])
        self.assertEqual(payload["page"], {"entity": "products", "view_type": "list"})
        self.assertTrue(payload["intents"])

    def test_proactive_greeting_rejects_unknown_entity_hints(self):
        response = self.client.post(
            reverse("erpshop_moduleai_assistant:proactive_greeting"),
            data=json.dumps({"page_context": {"entity": "not-a-real-entity"}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"display": False, "reason_code": "UNTRUSTED_PAGE_ENTITY"})

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

    def test_session_owner_can_rename_archive_and_restore_a_conversation(self):
        config = AssistantConfiguration.objects.create(company=self.company)
        session = AssistantSession.objects.create(company=self.company, configuration=config, user=self.user, title="Initial name")

        renamed = self.client.post(
            reverse("erpshop_moduleai_assistant:rename_session", args=[session.pk]), {"title": "Travel review"}
        )
        self.assertRedirects(renamed, f"{reverse('erpshop_moduleai_assistant:home')}?session={session.pk}")
        session.refresh_from_db()
        self.assertEqual(session.title, "Travel review")
        self.assertTrue(session.audit_events.filter(event="session_renamed").exists())

        archived = self.client.post(reverse("erpshop_moduleai_assistant:archive_session", args=[session.pk]))
        self.assertRedirects(archived, reverse("erpshop_moduleai_assistant:home"))
        session.refresh_from_db()
        self.assertEqual(session.status, AssistantSession.Status.ARCHIVED)

        restored = self.client.post(reverse("erpshop_moduleai_assistant:restore_session", args=[session.pk]))
        self.assertRedirects(restored, f"{reverse('erpshop_moduleai_assistant:home')}?session={session.pk}")
        session.refresh_from_db()
        self.assertEqual(session.status, AssistantSession.Status.ACTIVE)
        self.assertTrue(session.audit_events.filter(event="session_restored").exists())

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

    def test_tool_capable_provider_turn_preserves_structured_calls(self):
        config = AssistantConfiguration.objects.create(
            company=self.company,
            provider=AssistantConfiguration.Provider.OPENAI_COMPATIBLE,
            api_base_url="https://provider.example/v1",
            credential_env_var="AI_TEST_KEY",
            model_name="test-model",
            active=True,
        )
        payload = {
            "id": "tool-reply-1",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "erp_read", "arguments": '{"entity":"products"}'},
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 3},
        }
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "erp_read",
                    "description": "Read ERP data.",
                    "parameters": {"type": "object", "properties": {"entity": {"type": "string"}}},
                },
            }
        ]
        with patch.dict("os.environ", {"AI_TEST_KEY": "test-only"}), patch(
            "addons.ai.erpshop_moduleai_assistant.services.build_opener"
        ) as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            result = request_agent_completion(config, [{"role": "user", "content": "Read products."}], tools)

        self.assertEqual(result["tool_calls"], [{"id": "call_1", "name": "erp_read", "arguments": {"entity": "products"}}])
        self.assertEqual(result["finish_reason"], "tool_calls")
        request_payload = json.loads(opener.return_value.open.call_args.args[0].data.decode())
        self.assertEqual(request_payload["tools"][0]["function"]["name"], "erp_read")

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

    def test_work_intelligence_derives_a_soft_profile_without_storing_message_content(self):
        config = AssistantConfiguration.objects.create(company=self.company)
        session = AssistantSession.objects.create(company=self.company, configuration=config, user=self.user)
        for index in range(3):
            request_message = AssistantMessage.objects.create(
                session=session,
                role=AssistantMessage.Role.USER,
                content=f"Sensitive request {index}",
                state=AssistantMessage.State.COMPLETED,
            )
            response_message = AssistantMessage.objects.create(
                session=session,
                role=AssistantMessage.Role.ASSISTANT,
                content=f"Sensitive response {index}",
                state=AssistantMessage.State.COMPLETED,
                input_tokens=10,
                output_tokens=5,
            )
            record_assistant_turn(
                session=session,
                request_message=request_message,
                response_message=response_message,
            )
        setting = AIWorkProfileRefreshSetting.objects.create(company=self.company)
        result = refresh_work_profiles(setting_id=setting.pk)
        self.assertEqual(result["scanned"], 3)
        signal = AIWorkProfileSignal.objects.get(company=self.company, workflow_scope="assistant_chat")
        self.assertGreaterEqual(signal.confidence, 0)
        self.assertNotIn("Sensitive", str(signal.profile_payload))
        self.assertEqual(len(profile_context_for(company_id=self.company.pk, actor_id=self.user.pk)), 1)
        outbox = AIWorkSignalOutbox.objects.get(company=self.company)
        self.assertNotIn("Sensitive", str(outbox.payload))

    def test_digest_rule_uses_only_privacy_filtered_work_signal_metadata(self):
        rule = AIDigestRule.objects.create(
            company=self.company,
            name="Assistant health",
            trigger_type=AIDigestRule.Trigger.EVENT,
        )
        source = AIDigestRuleSource.objects.create(
            rule=rule,
            module_key="ai",
            model_key="assistant_turn",
        )
        AIDigestRuleSourceAction.objects.create(
            source=source,
            canonical_action_key="assistant_reply",
            method_name="assistant_reply",
            friendly_label="Assistant reply",
        )
        AIWorkSignalOutbox.objects.create(
            company=self.company,
            actor=self.user,
            event_type="assistant_turn_completed",
            canonical_action="assistant_reply",
            payload={"message_id": 99, "input_tokens": 10},
        )
        record = run_digest_rule(rule_id=rule.pk)
        self.assertIsInstance(record, AIDigestRecord)
        self.assertEqual(record.evidence[0]["canonical_action"], "assistant_reply")
        self.assertNotIn("message_id", str(record.evidence))
        self.assertEqual(record.confidence_score, 1)

    def test_active_company_knowledge_is_bounded_in_assistant_context(self):
        config = AssistantConfiguration.objects.create(company=self.company, allow_knowledge_context=True)
        session = AssistantSession.objects.create(company=self.company, configuration=config, user=self.user)
        AIKnowledgeDocument.objects.create(
            company=self.company,
            title="Credit policy",
            category="Finance",
            extracted_text="Credit terms are net 30.",
            status=AIKnowledgeDocument.Status.READY,
        )
        other_company = Company.objects.create(code="AI2", name="Other AI Company")
        AIKnowledgeDocument.objects.create(
            company=other_company,
            title="Other policy",
            extracted_text="This must not cross company scope.",
            status=AIKnowledgeDocument.Status.READY,
        )
        rebuild_document_chunks(AIKnowledgeDocument.objects.get(title="Credit policy"))
        rebuild_document_chunks(AIKnowledgeDocument.objects.get(title="Other policy"))
        messages = _request_messages(session, knowledge_query="What credit terms apply?")
        context = "\n".join(message["content"] for message in messages)
        self.assertIn("Credit terms are net 30.", context)
        self.assertNotIn("This must not cross company scope.", context)

    def test_knowledge_retrieval_indexes_relevant_chunks_with_company_scope(self):
        finance = AIKnowledgeDocument.objects.create(
            company=self.company,
            title="Expense policy",
            category="Finance",
            extracted_text="Every expense reimbursement requires an original receipt and finance approval.",
            status=AIKnowledgeDocument.Status.READY,
        )
        irrelevant = AIKnowledgeDocument.objects.create(
            company=self.company,
            title="Warehouse policy",
            category="Operations",
            extracted_text="Stock counts occur every Friday before closing.",
            status=AIKnowledgeDocument.Status.READY,
        )
        other_company = Company.objects.create(code="AI3", name="Third AI Company")
        foreign = AIKnowledgeDocument.objects.create(
            company=other_company,
            title="Foreign policy",
            extracted_text="Foreign expense reimbursement must never appear here.",
            status=AIKnowledgeDocument.Status.READY,
        )
        self.assertEqual(rebuild_document_chunks(finance), 1)
        self.assertEqual(rebuild_document_chunks(irrelevant), 1)
        self.assertEqual(rebuild_document_chunks(foreign), 1)
        self.assertEqual(AIKnowledgeChunk.objects.filter(document=finance).count(), 1)

        result = retrieve_knowledge(self.company.pk, "Which receipt is needed for expense reimbursement?")

        self.assertIn("Expense policy", result["context"])
        self.assertNotIn("Warehouse policy", result["context"])
        self.assertNotIn("Foreign policy", result["context"])
        self.assertEqual(result["citations"][0]["title"], "Expense policy")
        self.assertGreater(result["confidence_percent"], 0)

    def test_completed_answer_keeps_knowledge_source_provenance(self):
        config = AssistantConfiguration.objects.create(company=self.company, allow_knowledge_context=True)
        session = AssistantSession.objects.create(company=self.company, configuration=config, user=self.user)
        document = AIKnowledgeDocument.objects.create(
            company=self.company,
            title="Travel policy",
            extracted_text="Travel reimbursement requires a boarding pass.",
            status=AIKnowledgeDocument.Status.READY,
        )
        rebuild_document_chunks(document)
        queued = AssistantMessage.objects.create(
            session=session,
            role=AssistantMessage.Role.USER,
            content="What is required for travel reimbursement?",
            state=AssistantMessage.State.PENDING,
        )
        provider_result = {
            "content": "A boarding pass is required [K1].",
            "input_tokens": 12,
            "output_tokens": 8,
            "provider_message_id": "test-response",
            "latency_ms": 5,
        }
        with patch("addons.ai.erpshop_moduleai_assistant.services.request_completion", return_value=provider_result):
            execute_message(queued.pk)
        response = session.messages.get(role=AssistantMessage.Role.ASSISTANT)
        self.assertEqual(response.metadata["knowledge_citations"][0]["title"], "Travel policy")
        self.assertGreater(response.metadata["knowledge_relevance_percent"], 0)

    def test_knowledge_retrieval_query_count_is_stable_as_chunk_count_grows(self):
        def add_documents(prefix, count):
            for index in range(count):
                document = AIKnowledgeDocument.objects.create(
                    company=self.company,
                    title=f"{prefix} {index}",
                    extracted_text=f"Expense policy {index} requires a receipt for reimbursement.",
                    status=AIKnowledgeDocument.Status.READY,
                )
                rebuild_document_chunks(document)

        add_documents("Small", 2)
        with CaptureQueriesContext(connection) as small_queries:
            small_result = retrieve_knowledge(self.company.pk, "expense reimbursement receipt")
        add_documents("Large", 10)
        with CaptureQueriesContext(connection) as large_queries:
            large_result = retrieve_knowledge(self.company.pk, "expense reimbursement receipt")

        self.assertTrue(small_result["citations"])
        self.assertTrue(large_result["citations"])
        self.assertEqual(len(small_queries), len(large_queries))
