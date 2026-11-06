"""Offline acceptance of the HMX-shaped knowledge workflow and provider boundary."""

import hashlib
import json
from unittest.mock import patch
from urllib.error import HTTPError

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Company
from modules.apphub.models import ERPApp
from modules.organization.models import Branch

from .business_flows import MAX_FLOW_CANDIDATES, _flow_candidates, search_business_flows
from .flow_generation import execute_flow_generation, reason_about_business_flows
from .knowledge import _indexed_chunk_candidates, query_terms, retrieve_knowledge
from .knowledge_graph import build_knowledge_graph, graph_neighbors, graph_node, graph_path, graph_search
from .models import (
    AIKnowledgeBusinessFlow,
    AIKnowledgeBusinessFlowGenerationJob,
    AIKnowledgeCategory,
    AIKnowledgeCompanyProfile,
    AIKnowledgeChunk,
    AIKnowledgeDocument,
    AIKnowledgeRecordModelMapping,
    AssistantConfiguration,
    AssistantMessage,
    AssistantSession,
)
from .services import _request_messages, execute_message


class KnowledgeWorkflowAcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code="KWA", name="Knowledge Acceptance")
        cls.branch = cls.company.branches.get(is_main=True)
        cls.user = get_user_model().objects.create_superuser(username="knowledge-reviewer", password=None)
        cls.configuration = AssistantConfiguration.objects.create(
            company=cls.company,
            provider=AssistantConfiguration.Provider.OPENAI_COMPATIBLE,
            api_base_url="https://provider.example/v1",
            credential_env_var="AI_ACCEPTANCE_TEST_KEY",
            model_name="acceptance-model",
            active=True,
        )
        cls.profile = AIKnowledgeCompanyProfile.objects.create(
            company=cls.company, summary="Retail operations. Finance reviews expense receipts."
        )
        ERPApp.objects.create(
            slug="erpshop-moduleai-assistant", name="Hashy Assistant", category="AI & Data",
            summary="Acceptance test", enabled=True, status=ERPApp.Status.BETA, route="/ai/assistant/",
        )

    def setUp(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session["active_branch_id"] = self.branch.pk
        session.save()
        environment = patch.dict("os.environ", {"AI_ACCEPTANCE_TEST_KEY": "offline-test-only"})
        environment.start()
        self.addCleanup(environment.stop)

    def _new_job(self, **kwargs):
        return AIKnowledgeBusinessFlowGenerationJob.objects.create(
            company=self.company, configuration=self.configuration, requested_by=self.user, **kwargs
        )

    def _proposal(self, name="Expense reimbursement"):
        return {
            "name": name,
            "trigger": "An employee submits a receipt.",
            "description": "Finance reviews the expense before reimbursement.",
            "steps": [{"description": "Collect the receipt."}, {"description": "Finance approves reimbursement."}],
            "edges": [{"from_sequence": 1, "to_sequence": 2, "label": "review"}],
        }

    def _wire_reply(self, text, provider):
        if provider == AssistantConfiguration.Provider.ANTHROPIC:
            return {
                "id": "acceptance-reply", "content": [{"type": "text", "text": text}],
                "usage": {"input_tokens": 12, "output_tokens": 34},
            }
        return {
            "id": "acceptance-reply", "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 34},
        }

    def _acceptance_workflow(self, provider):
        self.configuration.provider = provider
        self.configuration.save(update_fields=["provider"])
        document_response = self.client.post(
            reverse("erpshop_moduleai_assistant:knowledge_documents"),
            {"title": "Expense policy", "category": "Finance", "extracted_text": "Expense reimbursement requires an original receipt."},
        )
        self.assertEqual(document_response.status_code, 302)
        foreign_company = Company.objects.create(code="KWAF", name="Other company")
        other_branch = Branch.objects.create(company=self.company, code="OTHER", name="Other branch")
        for company, branch, title in (
            (foreign_company, None, "Foreign secret"),
            (self.company, other_branch, "Other branch secret"),
            (self.company, self.branch, "Branch-only reference"),
        ):
            AIKnowledgeDocument.objects.create(
                company=company, branch=branch, title=title,
                extracted_text=f"Expense receipt {title}", status=AIKnowledgeDocument.Status.READY,
            )

        prerequisites = self.client.get(reverse("erpshop_moduleai_assistant:knowledge_flow_generation_prerequisites"))
        self.assertTrue(prerequisites.json()["can_generate"])
        with patch("addons.ai.erpshop_moduleai_assistant.views.queue_business_flow_generation.delay") as queue:
            submitted = self.client.post(
                reverse("erpshop_moduleai_assistant:generate_knowledge_flow"),
                data=json.dumps({"persist": True, "overwriteManual": False}), content_type="application/json",
            )
        self.assertEqual(submitted.status_code, 202)
        job_id = submitted.json()["job_id"]
        queue.assert_called_once_with(job_id)
        self.assertFalse(AIKnowledgeBusinessFlow.objects.exists())

        with patch("addons.ai.erpshop_moduleai_assistant.services.build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = json.dumps(self._wire_reply(json.dumps(self._proposal()), provider)).encode()
            self.assertEqual(execute_flow_generation(job_id)["status"], "completed")
            # A redelivered worker task cannot issue another paid provider request.
            self.assertEqual(execute_flow_generation(job_id)["status"], "completed")
            opener.return_value.open.assert_called_once()
            request = opener.return_value.open.call_args.args[0]
            generation_payload = json.loads(request.data)
        expected_endpoint = "/messages" if provider == AssistantConfiguration.Provider.ANTHROPIC else "/chat/completions"
        self.assertTrue(request.full_url.endswith(expected_endpoint))
        self.assertEqual(generation_payload["model"], "acceptance-model")
        self.assertIn("Expense policy", str(generation_payload))
        for excluded in ("Foreign secret", "Other branch secret", "Branch-only reference"):
            self.assertNotIn(excluded, str(generation_payload))

        job = AIKnowledgeBusinessFlowGenerationJob.objects.get(job_id=job_id)
        flow = job.result_flow
        self.assertEqual(flow.status, AIKnowledgeBusinessFlow.Status.DRAFT)
        self.assertEqual((job.input_tokens, job.output_tokens), (12, 34))
        polled = self.client.get(reverse("erpshop_moduleai_assistant:knowledge_flow_generation_job", args=[job_id]))
        self.assertEqual(polled.json()["business_flow_map_id"], flow.pk)
        mapped = self.client.get(reverse("erpshop_moduleai_assistant:knowledge_flow_map", args=[flow.pk]))
        self.assertEqual(len(mapped.json()["nodes"]), 2)
        self.assertEqual(mapped.json()["edges"][0]["label"], "review")

        session = AssistantSession.objects.create(
            company=self.company, branch=self.branch, configuration=self.configuration, user=self.user,
        )
        _, sources = _request_messages(session, knowledge_query="expense receipt", include_knowledge_sources=True)
        self.assertNotIn(flow.name, [source["title"] for source in sources["citations"]])
        activated = self.client.post(
            reverse("erpshop_moduleai_assistant:knowledge_flow_state", args=[flow.pk]), {"action": "activate"}
        )
        self.assertEqual(activated.status_code, 302)
        AIKnowledgeBusinessFlow.objects.bulk_create([
            AIKnowledgeBusinessFlow(
                company=self.company, name=f"Newer unrelated stocktake {index}",
                steps=[{"sequence": 1, "description": "Count items."}], status=AIKnowledgeBusinessFlow.Status.ACTIVE,
            ) for index in range(4)
        ])
        message = AssistantMessage.objects.create(
            session=session, role=AssistantMessage.Role.USER, state=AssistantMessage.State.PENDING,
            content="What is the expense reimbursement receipt process?",
        )
        with patch("addons.ai.erpshop_moduleai_assistant.services.build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = json.dumps(self._wire_reply("Submit the receipt to finance [F1] [K1].", provider)).encode()
            result = execute_message(message.pk)
            chat_payload = json.loads(opener.return_value.open.call_args.args[0].data)
        self.assertEqual(result["status"], "completed")
        self.assertIn("[F1] Expense reimbursement", str(chat_payload))
        self.assertIn("[K1]", str(chat_payload))
        for excluded in ("Foreign secret", "Other branch secret", "Newer unrelated stocktake"):
            self.assertNotIn(excluded, str(chat_payload))
        reply = AssistantMessage.objects.get(pk=result["response_id"])
        self.assertIn(flow.name, [source["title"] for source in reply.metadata["knowledge_citations"]])
        self.assertNotIn("offline-test-only", str(reply.metadata))
        reason = self.client.post(
            reverse("erpshop_moduleai_assistant:reason_knowledge_flow"),
            data=json.dumps({"query": "expense receipt"}), content_type="application/json",
        )
        self.assertEqual(reason.json()["matches"][0]["id"], flow.pk)
        self.client.post(reverse("erpshop_moduleai_assistant:knowledge_flow_state", args=[flow.pk]), {"action": "archive"})
        self.assertEqual(search_business_flows(self.company.pk, "expense receipt"), [])

    def test_hmx_workflow_with_openai_compatible_transport(self):
        self._acceptance_workflow(AssistantConfiguration.Provider.OPENAI_COMPATIBLE)

    def test_hmx_workflow_with_anthropic_transport(self):
        self._acceptance_workflow(AssistantConfiguration.Provider.ANTHROPIC)

    def test_queued_job_rechecks_references_before_provider_call(self):
        job = self._new_job()
        self.profile.active = False
        self.profile.save(update_fields=["active"])
        with patch("addons.ai.erpshop_moduleai_assistant.services.build_opener") as opener:
            self.assertEqual(execute_flow_generation(str(job.job_id))["status"], "failed")
            opener.assert_not_called()
        job.refresh_from_db()
        self.assertIsNotNone(job.completed_at)
        self.assertIsNone(job.result_flow_id)

    def test_queued_job_rechecks_provider_before_call(self):
        job = self._new_job()
        self.configuration.active = False
        self.configuration.save(update_fields=["active"])
        with patch("addons.ai.erpshop_moduleai_assistant.services.build_opener") as opener:
            self.assertEqual(execute_flow_generation(str(job.job_id))["status"], "failed")
            opener.assert_not_called()

    def test_worker_rejects_foreign_company_configuration(self):
        foreign_company = Company.objects.create(code="KWBAD", name="Foreign provider")
        foreign_configuration = AssistantConfiguration.objects.create(company=foreign_company)
        job = self._new_job()
        AIKnowledgeBusinessFlowGenerationJob.objects.filter(pk=job.pk).update(configuration=foreign_configuration)
        with patch("addons.ai.erpshop_moduleai_assistant.services.build_opener") as opener:
            self.assertEqual(execute_flow_generation(str(job.job_id))["status"], "failed")
            opener.assert_not_called()

    def test_provider_failure_and_invalid_proposal_never_publish_a_flow(self):
        for outcome in (HTTPError("https://provider.example/v1", 503, "Unavailable", {}, None), b"not json"):
            with self.subTest(outcome=type(outcome).__name__):
                job = self._new_job()
                with patch("addons.ai.erpshop_moduleai_assistant.services.build_opener") as opener:
                    if isinstance(outcome, HTTPError):
                        opener.return_value.open.side_effect = outcome
                    else:
                        opener.return_value.open.return_value.__enter__.return_value.read.return_value = outcome
                    self.assertEqual(execute_flow_generation(str(job.job_id))["status"], "failed")
                self.assertFalse(AIKnowledgeBusinessFlow.objects.exists())
                job.refresh_from_db()
                self.assertNotIn("offline-test-only", job.error_reason)

    def test_generation_preserves_existing_flow_unless_overwrite_was_requested(self):
        flow = AIKnowledgeBusinessFlow.objects.create(
            company=self.company, name="Expense reimbursement", description="Reviewed original.",
            steps=[{"sequence": 1, "description": "Original step."}], status=AIKnowledgeBusinessFlow.Status.ACTIVE,
        )
        for overwrite, expected in ((False, "failed"), (True, "completed")):
            with self.subTest(overwrite=overwrite):
                job = self._new_job(overwrite_manual=overwrite)
                with patch("addons.ai.erpshop_moduleai_assistant.services.build_opener") as opener:
                    opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(
                        self._wire_reply(json.dumps(self._proposal()), self.configuration.provider)
                    ).encode()
                    self.assertEqual(execute_flow_generation(str(job.job_id))["status"], expected)
                flow.refresh_from_db()
                self.assertEqual(flow.status, "draft" if overwrite else "active")
                if not overwrite:
                    self.assertEqual(flow.description, "Reviewed original.")
        self.assertEqual(AIKnowledgeBusinessFlow.objects.count(), 1)

    def test_foreign_job_and_flow_are_not_visible_through_local_company_routes(self):
        foreign_company = Company.objects.create(code="KWFOR", name="Foreign company")
        configuration = AssistantConfiguration.objects.create(company=foreign_company)
        flow = AIKnowledgeBusinessFlow.objects.create(company=foreign_company, name="Foreign flow")
        job = AIKnowledgeBusinessFlowGenerationJob.objects.create(
            company=foreign_company, configuration=configuration, requested_by=self.user,
        )
        for route, identifier in (("knowledge_flow_generation_job", job.job_id), ("knowledge_flow_map", flow.pk)):
            self.assertEqual(self.client.get(reverse(f"erpshop_moduleai_assistant:{route}", args=[identifier])).status_code, 404)

    def test_flow_relevance_survives_growth_with_one_bounded_query(self):
        target = AIKnowledgeBusinessFlow.objects.create(
            company=self.company, name="Expense reimbursement",
            steps=[{"sequence": 1, "description": "Collect the receipt."}], status="active",
        )
        with CaptureQueriesContext(connection) as small_queries:
            small = search_business_flows(self.company.pk, "expense receipt")
        AIKnowledgeBusinessFlow.objects.bulk_create([
            AIKnowledgeBusinessFlow(
                company=self.company, name=f"Routine expense {index:03}",
                steps=[{"sequence": 1, "description": "Follow standard procedure."}], status="active",
            ) for index in range(125)
        ])
        foreign_company = Company.objects.create(code="KWSC", name="Other scope")
        AIKnowledgeBusinessFlow.objects.create(company=foreign_company, name="Expense receipt", status="active")
        AIKnowledgeBusinessFlow.objects.create(company=self.company, name="Draft expense receipt", status="draft")
        AIKnowledgeBusinessFlow.objects.create(company=self.company, name="Archived expense receipt", status="archived")
        with CaptureQueriesContext(connection) as large_queries:
            large = search_business_flows(self.company.pk, "expense receipt")
        self.assertEqual((len(small_queries), len(large_queries)), (1, 1))
        self.assertEqual(small[0]["flow"].pk, target.pk)
        self.assertEqual(large[0]["flow"].pk, target.pk)
        self.assertEqual(len(large), 3)
        self.assertIn(f"LIMIT {MAX_FLOW_CANDIDATES}", large_queries[0]["sql"])
        self.assertEqual(reason_about_business_flows(self.company.pk, "expense receipt")["matches"][0]["id"], target.pk)
        plan = json.loads(_flow_candidates(self.company.pk, query_terms("expense receipt")).explain(format="json", analyze=True))[0]
        self.assertLessEqual(plan["Plan"]["Actual Rows"], MAX_FLOW_CANDIDATES)
        print(f"Flow retrieval: 1 vs 126 active company flows; SQL queries 1 -> 1; candidate limit {MAX_FLOW_CANDIDATES}; EXPLAIN rows {plan['Plan']['Actual Rows']}.")

    def test_unrelated_question_and_json_keys_do_not_supply_flow_evidence(self):
        AIKnowledgeBusinessFlow.objects.create(
            company=self.company, name="Stocktake", steps=[{"sequence": 1, "description": "Count items."}], status="active",
        )
        self.assertEqual(search_business_flows(self.company.pk, "receipt"), [])
        self.assertEqual(search_business_flows(self.company.pk, "description sequence"), [])

    def test_two_letter_business_abbreviation_remains_searchable(self):
        flow = AIKnowledgeBusinessFlow.objects.create(
            company=self.company, name="PO approval", steps=[{"sequence": 1, "description": "Review purchase order."}], status="active",
        )
        self.assertEqual(reason_about_business_flows(self.company.pk, "PO")["matches"][0]["id"], flow.pk)

    def test_question_relevant_step_after_the_first_five_is_sent_to_provider(self):
        AIKnowledgeBusinessFlow.objects.create(
            company=self.company, name="Supplier onboarding",
            steps=[{"sequence": index, "description": "Complete standard review."} for index in range(1, 20)]
            + [{"sequence": 20, "description": "Check warranty coverage."}], status="active",
        )
        session = AssistantSession.objects.create(company=self.company, configuration=self.configuration, user=self.user)
        messages = _request_messages(session, knowledge_query="warranty")
        self.assertIn("20. Check warranty coverage.", str(messages))

    def test_bounded_context_contains_all_three_cited_flows_and_their_evidence(self):
        for index in range(3):
            AIKnowledgeBusinessFlow.objects.create(
                company=self.company, name=f"Expense {index} " + "n" * 170,
                trigger="t" * 200, description="d" * 800, status="active",
                steps=[{
                    "sequence": position,
                    "description": f"receipt {index}-{position} " + "x" * 450 + f" evidence-{index}-{position}",
                } for position in range(1, 6)],
            )
        session = AssistantSession.objects.create(company=self.company, configuration=self.configuration, user=self.user)
        messages, sources = _request_messages(session, knowledge_query="expense receipt", include_knowledge_sources=True)
        flow_context = next(message["content"] for message in messages if "Active company business-flow references." in message["content"])
        self.assertLessEqual(len(flow_context), 12_000)
        self.assertEqual(len(sources["citations"]), 3)
        for index, source in enumerate(sources["citations"]):
            self.assertIn(f"[{source['label']}] {source['title']}", flow_context)
            self.assertIn(f"evidence-{index}-5", flow_context)

    def test_document_retrieval_uses_company_branch_scope_and_fts_index(self):
        other_branch = Branch.objects.create(company=self.company, code="KWA-OTHER", name="Other branch")
        foreign_company = Company.objects.create(code="KWA-FTS", name="Foreign FTS company")
        documents = [
            AIKnowledgeDocument(
                company=self.company,
                title="Expense evidence",
                category="Finance",
                extracted_text="Expense reimbursement needs an original receipt.",
                status=AIKnowledgeDocument.Status.READY,
            ),
            AIKnowledgeDocument(
                company=self.company,
                branch=other_branch,
                title="Other branch evidence",
                extracted_text="Expense reimbursement receipt remains branch private.",
                status=AIKnowledgeDocument.Status.READY,
            ),
            AIKnowledgeDocument(
                company=foreign_company,
                title="Foreign evidence",
                extracted_text="Expense reimbursement receipt remains company private.",
                status=AIKnowledgeDocument.Status.READY,
            ),
        ]
        documents.extend(
            AIKnowledgeDocument(
                company=self.company,
                title=f"Unrelated policy {index:03}",
                extracted_text=f"Warehouse cycle count schedule {index}.",
                status=AIKnowledgeDocument.Status.READY,
            )
            for index in range(1_200)
        )
        AIKnowledgeDocument.objects.bulk_create(documents)
        documents = list(AIKnowledgeDocument.objects.filter(title__in=[document.title for document in documents]))
        AIKnowledgeChunk.objects.bulk_create(
            [
                AIKnowledgeChunk(
                    company=document.company,
                    branch=document.branch,
                    document=document,
                    ordinal=0,
                    content=document.extracted_text,
                    content_sha256=hashlib.sha256(document.extracted_text.encode()).hexdigest(),
                )
                for document in documents
            ],
            batch_size=100,
        )

        with CaptureQueriesContext(connection) as queries:
            result = retrieve_knowledge(self.company.pk, "expense reimbursement receipt", branch_id=self.branch.pk)
        self.assertEqual([citation["title"] for citation in result["citations"]], ["Expense evidence"])
        self.assertNotIn("Other branch evidence", result["context"])
        self.assertNotIn("Foreign evidence", result["context"])
        self.assertEqual(len(queries), 2)  # indexed candidates plus the bounded legacy-document fallback
        self.assertIn("to_tsquery", queries[0]["sql"])
        self.assertNotIn("UPPER(", queries[0]["sql"])

        candidates = _indexed_chunk_candidates(
            self.company.pk, query_terms("expense reimbursement receipt"), self.branch.pk
        )
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE erpshop_moduleai_assistant_aiknowledgedocument")
            cursor.execute("ANALYZE erpshop_moduleai_assistant_aiknowledgechunk")
            cursor.execute("SET LOCAL enable_seqscan = off")
        plan = json.loads(candidates.explain(format="json", analyze=True))[0]["Plan"]

        def index_names(node):
            names = [node.get("Index Name")] if node.get("Index Name") else []
            for child in node.get("Plans", []):
                names.extend(index_names(child))
            return names

        self.assertIn("ai_knowledge_chunk_fts", index_names(plan))
        self.assertLessEqual(plan["Actual Rows"], 1)
        print(
            "Knowledge FTS: 1,203 indexed documents; 2 retrieval queries; "
            "GIN index ai_knowledge_chunk_fts used; 1 scoped candidate returned."
        )

    def test_graph_search_and_neighbors_resolve_nodes_beyond_bounded_view(self):
        category = AIKnowledgeCategory.objects.create(
            company=self.company, name="ZZZ Late Finance", description="Late graph relationship"
        )
        target = AIKnowledgeDocument.objects.create(
            company=self.company,
            title="Needle invoice policy",
            category=category.name,
            extracted_text="Needle invoice review evidence.",
            status=AIKnowledgeDocument.Status.READY,
        )
        other_branch = Branch.objects.create(company=self.company, code="GRAPH-OTHER", name="Graph other branch")
        AIKnowledgeDocument.objects.create(
            company=self.company,
            branch=other_branch,
            title="Needle invoice branch secret",
            category=category.name,
            extracted_text="Hidden branch graph evidence.",
            status=AIKnowledgeDocument.Status.READY,
        )
        foreign_company = Company.objects.create(code="GRAPH-F", name="Foreign graph search")
        AIKnowledgeDocument.objects.create(
            company=foreign_company,
            title="Needle invoice policy",
            category=category.name,
            extracted_text="Hidden company graph evidence.",
            status=AIKnowledgeDocument.Status.READY,
        )

        with CaptureQueriesContext(connection) as small_queries:
            small = graph_search(self.company.pk, "needle invoice", branch_id=self.branch.pk, limit=20)
        with CaptureQueriesContext(connection) as small_deep_queries:
            small_deep = graph_neighbors(
                self.company.pk, f"document:{target.pk}", branch_id=self.branch.pk, depth=2, limit=20
            )
        AIKnowledgeDocument.objects.bulk_create(
            [
                AIKnowledgeDocument(
                    company=self.company,
                    title=f"AAA unrelated graph policy {index:03}",
                    category="AAA Operations",
                    extracted_text="Unrelated graph material.",
                    status=AIKnowledgeDocument.Status.READY,
                )
                for index in range(150)
            ]
        )
        with CaptureQueriesContext(connection) as large_queries:
            large = graph_search(self.company.pk, "needle invoice", branch_id=self.branch.pk, limit=20)
        with CaptureQueriesContext(connection) as large_deep_queries:
            large_deep = graph_neighbors(
                self.company.pk, f"document:{target.pk}", branch_id=self.branch.pk, depth=2, limit=20
            )
        small_query_count, large_query_count = len(small_queries), len(large_queries)
        small_deep_query_count, large_deep_query_count = len(small_deep_queries), len(large_deep_queries)

        expected_id = f"document:{target.pk}"
        self.assertEqual([node["id"] for node in small["results"]], [expected_id])
        self.assertEqual([node["id"] for node in large["results"]], [expected_id])
        self.assertEqual((small_query_count, large_query_count), (5, 5))
        expected_deep_ids = {f"category:{category.pk}", f"profile:{self.company.pk}"}
        self.assertEqual({node["id"] for node in small_deep["neighbors"]}, expected_deep_ids)
        self.assertEqual({node["id"] for node in large_deep["neighbors"]}, expected_deep_ids)
        self.assertEqual((small_deep_query_count, large_deep_query_count), (5, 5))
        self.assertEqual(large_deep["meta"]["depth"], 2)
        self.assertTrue(large_deep["meta"]["targeted"])
        bounded_view = build_knowledge_graph(self.company.pk, branch_id=self.branch.pk, limit=50)
        self.assertNotIn(expected_id, {node["id"] for node in bounded_view["graph"]["nodes"]})

        with CaptureQueriesContext(connection) as node_queries:
            resolved = graph_node(self.company.pk, expected_id, branch_id=self.branch.pk)
        with CaptureQueriesContext(connection) as neighbor_queries:
            neighborhood = graph_neighbors(self.company.pk, expected_id, branch_id=self.branch.pk, depth=1, limit=20)
        self.assertEqual(resolved["id"], expected_id)
        self.assertEqual(len(node_queries), 1)
        self.assertEqual(len(neighbor_queries), 2)
        self.assertEqual([node["id"] for node in neighborhood["neighbors"]], [f"category:{category.pk}"])
        self.assertEqual(neighborhood["edges"][0]["label"], "categorizes")
        self.assertTrue(neighborhood["meta"]["targeted"])
        self.assertNotIn("branch secret", str(large).lower())
        self.assertNotIn("hidden company", str(large).lower())
        self.assertNotIn("branch secret", str(large_deep).lower())
        self.assertNotIn("hidden company", str(large_deep).lower())

        search_response = self.client.get(
            reverse("erpshop_moduleai_assistant:knowledge_graph_search"), {"q": "needle invoice", "limit": 20}
        )
        detail_response = self.client.get(
            reverse("erpshop_moduleai_assistant:knowledge_graph_node", args=[expected_id])
        )
        neighbor_response = self.client.get(
            reverse("erpshop_moduleai_assistant:knowledge_graph_neighbors"),
            {"node_id": expected_id, "depth": 1, "limit": 20},
        )
        deep_neighbor_response = self.client.get(
            reverse("erpshop_moduleai_assistant:knowledge_graph_neighbors"),
            {"node_id": expected_id, "depth": 2, "limit": 20},
        )
        self.assertEqual(search_response.status_code, 200)
        self.assertEqual(detail_response.json()["node"]["id"], expected_id)
        self.assertEqual(neighbor_response.json()["neighbors"][0]["id"], f"category:{category.pk}")
        self.assertEqual(
            {node["id"] for node in deep_neighbor_response.json()["neighbors"]}, expected_deep_ids
        )
        print(
            "Knowledge graph: relevant node remained searchable beyond 150 earlier rows; "
            f"search queries {small_query_count} -> {large_query_count}; exact node 1 query; "
            f"depth-1 neighbors 2 queries; depth-2 neighbors {small_deep_query_count} -> {large_deep_query_count}."
        )

    def test_graph_path_resolves_both_endpoints_beyond_bounded_view(self):
        category = AIKnowledgeCategory.objects.create(
            company=self.company, name="ZZZ Path Finance", description="Late path relationship"
        )
        document = AIKnowledgeDocument.objects.create(
            company=self.company,
            title="ZZZ path invoice policy",
            category=category.name,
            extracted_text="Review the invoice before approval.",
            status=AIKnowledgeDocument.Status.READY,
        )
        flow = AIKnowledgeBusinessFlow.objects.create(
            company=self.company,
            name="ZZZ path approval",
            steps=[{"sequence": 1, "description": "Approve the reviewed invoice."}],
            status=AIKnowledgeBusinessFlow.Status.ACTIVE,
        )
        from_node_id, to_node_id = f"document:{document.pk}", f"flow:{flow.pk}"

        with CaptureQueriesContext(connection) as small_queries:
            small = graph_path(self.company.pk, from_node_id, to_node_id, branch_id=self.branch.pk)
        AIKnowledgeDocument.objects.bulk_create(
            [
                AIKnowledgeDocument(
                    company=self.company,
                    title=f"AAA unrelated path document {index:03}",
                    category="AAA Operations",
                    extracted_text="Unrelated path material.",
                    status=AIKnowledgeDocument.Status.READY,
                )
                for index in range(150)
            ]
        )
        AIKnowledgeBusinessFlow.objects.bulk_create(
            [
                AIKnowledgeBusinessFlow(
                    company=self.company,
                    name=f"AAA unrelated path flow {index:03}",
                    steps=[{"sequence": 1, "description": "Complete routine work."}],
                    status=AIKnowledgeBusinessFlow.Status.ACTIVE,
                )
                for index in range(80)
            ]
        )
        with CaptureQueriesContext(connection) as large_queries:
            large = graph_path(self.company.pk, from_node_id, to_node_id, branch_id=self.branch.pk)
        small_query_count, large_query_count = len(small_queries), len(large_queries)

        expected_ids = [from_node_id, f"category:{category.pk}", f"profile:{self.company.pk}", to_node_id]
        self.assertEqual([node["id"] for node in small["nodes"]], expected_ids)
        self.assertEqual([node["id"] for node in large["nodes"]], expected_ids)
        self.assertEqual((small_query_count, large_query_count), (5, 5))
        self.assertTrue(large["meta"]["targeted"])
        self.assertEqual(large["meta"]["max_length"], 6)
        too_short = graph_path(
            self.company.pk, from_node_id, to_node_id, branch_id=self.branch.pk, max_length=2
        )
        self.assertEqual(too_short["nodes"], [])
        self.assertTrue(too_short["meta"]["targeted"])

        bounded_view = build_knowledge_graph(self.company.pk, branch_id=self.branch.pk, limit=50)
        bounded_ids = {node["id"] for node in bounded_view["graph"]["nodes"]}
        self.assertNotIn(from_node_id, bounded_ids)
        self.assertNotIn(to_node_id, bounded_ids)
        path_response = self.client.get(
            reverse("erpshop_moduleai_assistant:knowledge_graph_path"),
            {"from_node_id": from_node_id, "to_node_id": to_node_id},
        )
        self.assertEqual(path_response.status_code, 200)
        self.assertEqual([node["id"] for node in path_response.json()["nodes"]], expected_ids)

        other_branch = Branch.objects.create(company=self.company, code="PATH-OTHER", name="Path other branch")
        hidden_document = AIKnowledgeDocument.objects.create(
            company=self.company,
            branch=other_branch,
            title="Hidden path document",
            status=AIKnowledgeDocument.Status.READY,
        )
        hidden_response = self.client.get(
            reverse("erpshop_moduleai_assistant:knowledge_graph_path"),
            {"from_node_id": f"document:{hidden_document.pk}", "to_node_id": to_node_id},
        )
        self.assertEqual(hidden_response.status_code, 404)
        print(
            "Knowledge graph path: two endpoints remained connected beyond 150 documents and 80 flows; "
            f"queries {small_query_count} -> {large_query_count}; branch isolation preserved."
        )

    def test_deep_neighbors_follow_mapping_flow_and_steps(self):
        category = AIKnowledgeCategory.objects.create(
            company=self.company, name="Deep Finance", description="Deep graph relationship"
        )
        document = AIKnowledgeDocument.objects.create(
            company=self.company,
            title="Deep invoice policy",
            category=category.name,
            status=AIKnowledgeDocument.Status.READY,
        )
        flow = AIKnowledgeBusinessFlow.objects.create(
            company=self.company,
            name="Deep invoice approval",
            steps=[
                {"sequence": 1, "description": "Review the invoice."},
                {"sequence": 2, "description": "Approve the invoice."},
            ],
            edges=[{"from_sequence": 1, "to_sequence": 2, "label": "approve"}],
            status=AIKnowledgeBusinessFlow.Status.ACTIVE,
        )
        mapping = AIKnowledgeRecordModelMapping.objects.create(
            company=self.company,
            record_model="deep_invoices",
            display_name="Deep invoices",
            category=category.name,
            business_flow=flow,
        )
        other_branch = Branch.objects.create(company=self.company, code="DEEP-OTHER", name="Deep other branch")
        hidden_mapping = AIKnowledgeRecordModelMapping.objects.create(
            company=self.company,
            branch=other_branch,
            record_model="deep_invoices",
            display_name="Hidden deep invoices",
            category=category.name,
            business_flow=flow,
        )
        AIKnowledgeCategory.objects.create(company=self.company, name="AAA Deep Baseline")
        AIKnowledgeBusinessFlow.objects.create(company=self.company, name="AAA Deep Baseline", steps=[])

        with CaptureQueriesContext(connection) as small_queries:
            small = graph_neighbors(
                self.company.pk, f"document:{document.pk}", branch_id=self.branch.pk, depth=4, limit=50
            )
        AIKnowledgeDocument.objects.bulk_create(
            [
                AIKnowledgeDocument(
                    company=self.company,
                    title=f"Unrelated deep document {index:03}",
                    category="Unrelated Deep",
                    status=AIKnowledgeDocument.Status.READY,
                )
                for index in range(150)
            ]
        )
        AIKnowledgeCategory.objects.bulk_create(
            [AIKnowledgeCategory(company=self.company, name=f"AAA Deep Category {index:03}") for index in range(20)]
        )
        AIKnowledgeBusinessFlow.objects.bulk_create(
            [
                AIKnowledgeBusinessFlow(company=self.company, name=f"AAA Deep Flow {index:03}", steps=[])
                for index in range(20)
            ]
        )
        with CaptureQueriesContext(connection) as large_queries:
            large = graph_neighbors(
                self.company.pk, f"document:{document.pk}", branch_id=self.branch.pk, depth=4, limit=50
            )
        small_query_count, large_query_count = len(small_queries), len(large_queries)
        self.assertEqual(small_query_count, large_query_count)
        result_ids = {node["id"] for node in large["neighbors"]}
        expected_ids = {
            f"category:{category.pk}",
            f"profile:{self.company.pk}",
            f"record_model:{mapping.pk}",
            f"flow:{flow.pk}",
            f"step:{flow.pk}:1",
            f"step:{flow.pk}:2",
        }
        self.assertTrue(expected_ids.issubset({node["id"] for node in small["neighbors"]}))
        self.assertTrue(expected_ids.issubset(result_ids))
        self.assertNotIn(f"record_model:{hidden_mapping.pk}", result_ids)
        self.assertTrue(large["meta"]["targeted"])
        self.assertEqual(large["meta"]["depth"], 4)
        edge_signatures = {
            (edge["source"], edge["target"], edge["label"]) for edge in large["edges"]
        }
        self.assertIn(
            (f"category:{category.pk}", f"record_model:{mapping.pk}", "maps entity"), edge_signatures
        )
        self.assertIn(
            (f"flow:{flow.pk}", f"record_model:{mapping.pk}", "uses entity"), edge_signatures
        )
        self.assertIn((f"flow:{flow.pk}", f"step:{flow.pk}:1", "has step"), edge_signatures)

        response = self.client.get(
            reverse("erpshop_moduleai_assistant:knowledge_graph_neighbors"),
            {"node_id": f"document:{document.pk}", "depth": 4, "limit": 50},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(expected_ids.issubset({node["id"] for node in response.json()["neighbors"]}))
        print(
            "Knowledge graph depth 4: document-to-step traversal stayed complete after 150 documents, "
            f"20 categories, and 20 flows; queries {small_query_count} -> {large_query_count}."
        )
