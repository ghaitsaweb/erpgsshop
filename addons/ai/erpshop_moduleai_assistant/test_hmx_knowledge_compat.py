"""Acceptance tests for the local HMX Base Knowledge compatibility contract."""

import json
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.models import Company
from modules.apphub.models import ERPApp

from .models import (
    AIKnowledgeBusinessFlow,
    AIKnowledgeCategory,
    AIKnowledgeCompanyProfile,
    AIKnowledgeDocument,
    AIKnowledgeRecordModelMapping,
)


BASE = "/hmx_api/ai/knowledge"


class HMXKnowledgeCompatibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code="HKC", name="HMX Knowledge Contract")
        cls.branch = cls.company.branches.get(is_main=True)
        cls.other_company = Company.objects.create(code="HKX", name="Foreign Knowledge Company")
        cls.other_branch = cls.other_company.branches.get(is_main=True)
        cls.user = get_user_model().objects.create_superuser(username="hmx-knowledge-admin", password=None)
        ERPApp.objects.create(
            slug="erpshop-moduleai-assistant",
            name="Hashy Assistant",
            category="AI & Data",
            summary="Compatibility test",
            enabled=True,
            status=ERPApp.Status.BETA,
            route="/ai/assistant/",
        )

    def setUp(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_company_id"] = self.company.pk
        session["active_branch_id"] = self.branch.pk
        session.save()

    def post_json(self, path, payload, method="post"):
        return getattr(self.client, method)(path, data=json.dumps(payload), content_type="application/json")

    def test_contract_requires_authentication_and_knowledge_admin(self):
        self.client.logout()
        response = self.client.get(f"{BASE}/base/company-profile")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "AUTHENTICATION_REQUIRED")

        viewer = get_user_model().objects.create_user(username="knowledge-viewer", password=None)
        self.client.force_login(viewer)
        response = self.client.get(f"{BASE}/base/company-profile")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "FORBIDDEN")

    def test_profile_category_and_document_crud_are_company_scoped_and_reversible(self):
        profile = self.post_json(
            f"{BASE}/base/company-profile",
            {"content": "Retail operating policy", "operating_context": "Indonesia", "status": "active"},
        )
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json()["data"]["content"], "Retail operating policy")

        category = self.post_json(
            f"{BASE}/base/categories",
            {"category": "Finance", "description": "Reviewed finance knowledge"},
        )
        self.assertEqual(category.status_code, 201)
        self.assertTrue(AIKnowledgeCategory.objects.get(company=self.company, name="Finance").active)

        foreign = AIKnowledgeDocument.objects.create(
            company=self.other_company,
            branch=self.other_branch,
            title="Foreign confidential reference",
            extracted_text="Never disclose this text.",
            status=AIKnowledgeDocument.Status.READY,
        )
        created = self.post_json(
            f"{BASE}/documents",
            {
                "document_title": "Expense policy",
                "division_id": self.branch.pk,
                "category": "Finance",
                "content": "Receipts are required for reimbursement.",
                "document_type": "manual",
            },
        )
        self.assertEqual(created.status_code, 201)
        document_id = created.json()["data"]["id"]
        document = AIKnowledgeDocument.objects.get(pk=document_id)
        self.assertEqual(document.company_id, self.company.pk)
        self.assertEqual(document.chunks.count(), 1)

        listed = self.client.get(f"{BASE}/documents", {"limit": 10})
        self.assertEqual(listed.status_code, 200)
        listed_ids = {row["id"] for row in listed.json()["data"]["items"]}
        self.assertIn(document_id, listed_ids)
        self.assertNotIn(foreign.pk, listed_ids)

        searched = self.client.get(f"{BASE}/documents", {"search": "reimbursement receipts"})
        self.assertEqual(searched.status_code, 200)
        self.assertEqual([row["id"] for row in searched.json()["data"]["items"]], [document_id])

        archived = self.client.delete(f"{BASE}/documents/{document_id}")
        self.assertEqual(archived.status_code, 200)
        document.refresh_from_db()
        self.assertFalse(document.active)
        self.assertEqual(document.status, AIKnowledgeDocument.Status.ARCHIVED)
        self.assertTrue(document.extracted_text)
        self.assertTrue(document.chunks.exists())

        restored = self.post_json(
            f"{BASE}/base/documents/{document_id}",
            {"lifecycle_status": "active"},
            method="put",
        )
        self.assertEqual(restored.status_code, 200)
        document.refresh_from_db()
        self.assertTrue(document.active)
        self.assertEqual(document.status, AIKnowledgeDocument.Status.READY)

        exact_archived = self.client.delete(f"{BASE}/base/documents/{document_id}")
        self.assertEqual(exact_archived.status_code, 200)
        document.refresh_from_db()
        self.assertFalse(document.active)
        self.assertEqual(document.status, AIKnowledgeDocument.Status.ARCHIVED)

        archived_category = self.client.delete(f"{BASE}/base/categories/Finance")
        self.assertEqual(archived_category.status_code, 200)
        self.assertFalse(AIKnowledgeCategory.objects.get(company=self.company, name="Finance").active)

    def test_document_list_has_hard_pagination_bounds_and_fixed_query_growth(self):
        AIKnowledgeDocument.objects.create(
            company=self.company,
            branch=self.branch,
            title="Bounded 0",
            extracted_text="bounded",
            status=AIKnowledgeDocument.Status.READY,
        )
        with CaptureQueriesContext(connection) as small_context:
            response = self.client.get(f"{BASE}/documents", {"limit": 50})
            self.assertEqual(response.status_code, 200)

        AIKnowledgeDocument.objects.bulk_create(
            [
                AIKnowledgeDocument(
                    company=self.company,
                    branch=self.branch,
                    title=f"Bounded {index}",
                    extracted_text="bounded",
                    status=AIKnowledgeDocument.Status.READY,
                )
                for index in range(1, 31)
            ]
        )
        with CaptureQueriesContext(connection) as large_context:
            response = self.client.get(f"{BASE}/documents", {"limit": 50})
            self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(large_context), len(small_context) + 1)

        self.assertEqual(self.client.get(f"{BASE}/documents", {"limit": 101}).status_code, 400)
        self.assertEqual(self.client.get(f"{BASE}/documents", {"page": 101}).status_code, 400)

    def test_upload_returns_a_pollable_local_job_without_exposing_another_company(self):
        upload = SimpleUploadedFile("policy.txt", b"Approved leave requests require manager review.", "text/plain")
        with tempfile.TemporaryDirectory(prefix="hmx-knowledge-upload-test-") as media_root:
            with self.settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    f"{BASE}/documents/upload",
                    {
                        "file": upload,
                        "document_title": "Leave policy",
                        "division_id": str(self.branch.pk),
                        "category": "People",
                        "metadata": json.dumps({"source": "acceptance"}),
                    },
                )
                self.assertEqual(response.status_code, 202)
                job_id = response.json()["data"]["job_id"]
                polled = self.client.get(f"{BASE}/base/documents/upload-jobs/{job_id}")
                self.assertEqual(polled.status_code, 200)
                self.assertEqual(polled.json()["data"]["status"], "completed")
                self.assertEqual(polled.json()["data"]["document"]["title"], "Leave policy")

                foreign_job = f"local-{AIKnowledgeDocument.objects.create(company=self.other_company, title='X').pk}"
                self.assertEqual(
                    self.client.get(f"{BASE}/base/documents/upload-jobs/{foreign_job}").status_code,
                    404,
                )

    def test_business_flow_mapping_and_graph_contract(self):
        AIKnowledgeCompanyProfile.objects.create(company=self.company, summary="Expense operations")
        flow_response = self.post_json(
            f"{BASE}/base/business-flow",
            {
                "name": "Expense reimbursement",
                "trigger": "Receipt submitted",
                "description": "Finance review",
                "steps": [{"description": "Submit receipt"}, {"description": "Finance approves"}],
                "edges": [{"from_sequence": 1, "to_sequence": 2, "label": "review"}],
                "status": "active",
            },
        )
        self.assertEqual(flow_response.status_code, 201)
        flow_id = flow_response.json()["data"]["id"]

        mapping_response = self.post_json(
            f"{BASE}/base/record-model-mappings",
            {
                "record_model": "finance.expense",
                "display_name": "Expense",
                "division_id": self.branch.pk,
                "category": "Finance",
                "module_name": "finance",
                "feature_tags": ["receipt", "approval"],
                "business_flow_id": flow_id,
            },
            method="put",
        )
        self.assertEqual(mapping_response.status_code, 201)
        self.assertTrue(
            AIKnowledgeRecordModelMapping.objects.filter(
                company=self.company, branch=self.branch, record_model="finance.expense"
            ).exists()
        )

        mapped = self.client.get(f"{BASE}/base/business-flow/map/{flow_id}")
        self.assertEqual(mapped.status_code, 200)
        self.assertEqual(len(mapped.json()["data"]["nodes"]), 2)
        self.assertEqual(mapped.json()["data"]["edges"][0]["label"], "review")

        graph = self.client.get(f"{BASE}/base/graph/view", {"limit": 50})
        self.assertEqual(graph.status_code, 200)
        graph_ids = {node["id"] for node in graph.json()["data"]["graph"]["nodes"]}
        self.assertIn(f"flow:{flow_id}", graph_ids)

        reason = self.post_json(f"{BASE}/base/business-flow/reason", {"query": "expense receipt"})
        self.assertEqual(reason.status_code, 200)
        self.assertEqual(reason.json()["data"]["matches"][0]["id"], flow_id)

        mapping_archived = self.client.delete(f"{BASE}/base/record-model-mappings/finance.expense")
        self.assertEqual(mapping_archived.status_code, 200)
        self.assertEqual(mapping_archived.json()["data"]["archived_count"], 1)
        self.assertFalse(
            AIKnowledgeRecordModelMapping.objects.get(
                company=self.company, branch=self.branch, record_model="finance.expense"
            ).active
        )

        archived = self.client.delete(f"{BASE}/base/business-flow/{flow_id}")
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(
            AIKnowledgeBusinessFlow.objects.get(pk=flow_id).status,
            AIKnowledgeBusinessFlow.Status.ARCHIVED,
        )

    def test_static_business_flow_routes_resolve_before_identifier_route(self):
        prerequisites = self.client.get(f"{BASE}/base/business-flow/generate/prerequisites")
        self.assertEqual(prerequisites.status_code, 200)
        self.assertIn("can_generate", prerequisites.json()["data"])

        missing_job = self.client.get(f"{BASE}/base/business-flow/generate/not-a-uuid")
        self.assertEqual(missing_job.status_code, 404)
        self.assertEqual(missing_job.json()["code"], "NOT_FOUND")

    def test_cross_company_identifiers_are_not_addressable(self):
        foreign_document = AIKnowledgeDocument.objects.create(
            company=self.other_company,
            branch=self.other_branch,
            title="Foreign document",
            extracted_text="foreign",
            status=AIKnowledgeDocument.Status.READY,
        )
        foreign_flow = AIKnowledgeBusinessFlow.objects.create(
            company=self.other_company,
            name="Foreign flow",
            steps=[{"sequence": 1, "description": "Foreign"}],
        )
        self.assertEqual(self.client.delete(f"{BASE}/documents/{foreign_document.pk}").status_code, 404)
        self.assertEqual(self.client.get(f"{BASE}/base/business-flow/{foreign_flow.pk}").status_code, 404)
        foreign_document.refresh_from_db()
        foreign_flow.refresh_from_db()
        self.assertTrue(foreign_document.active)
        self.assertEqual(foreign_flow.status, AIKnowledgeBusinessFlow.Status.DRAFT)
