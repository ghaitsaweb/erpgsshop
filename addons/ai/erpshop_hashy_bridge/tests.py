import json
import os
import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Company, Product
from modules.organization.models import UserOrganizationAccess

from .auth import ISSUER, encode_token
from .models import BridgeAuditLog


BRIDGE_ENV = {
    "ERPGSSHOP_HASHY_BRIDGE_ENABLED": "true",
    "ERPGSSHOP_HASHY_BRIDGE_SECRET": "test-only-secret-that-is-longer-than-32-bytes",
    "ERPGSSHOP_HASHY_BRIDGE_INSTANCE_CODE": "gsshop-test",
    "ERPGSSHOP_PUBLIC_BASE_URL": "https://erp.example.test",
}


class HashyBridgeTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(code="BRIDGE", name="Bridge Company")
        self.branch = self.company.branches.get(is_main=True)
        self.user = get_user_model().objects.create_superuser(
            username="bridge-admin",
            email="bridge-admin@example.test",
            password="test-password",
        )

    def _token(self, user=None, company=None, branch=None, **overrides):
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": "gsshop-test",
            "sub": (user or self.user).get_username(),
            "iat": now,
            "exp": now + 300,
            "company_id": (company or self.company).pk,
            "branch_id": (branch or self.branch).pk,
            "run": "test-run",
        }
        claims.update(overrides)
        with patch.dict(os.environ, BRIDGE_ENV):
            return encode_token(claims)

    def _post(self, name, payload, *, token=None):
        return self.client.post(
            reverse(f"erpshop_hashy_bridge:{name}"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token or self._token()}",
        )

    def test_ping_is_public_but_authenticated_endpoints_fail_closed(self):
        ping = self.client.get(reverse("erpshop_hashy_bridge:ping"))
        self.assertEqual(ping.status_code, 200)
        self.assertEqual(ping.json()["data"]["module"], "erpshop-hashy-bridge")

        disabled = self._post("health", {})
        self.assertEqual(disabled.status_code, 503)
        self.assertEqual(disabled.json()["error"]["code"], "bridge_disabled")

        with patch.dict(os.environ, BRIDGE_ENV):
            invalid = self._post("health", {}, token="invalid")
        self.assertEqual(invalid.status_code, 401)
        self.assertFalse(BridgeAuditLog.objects.exists())

    def test_health_and_instance_info_report_read_only_features(self):
        with patch.dict(os.environ, BRIDGE_ENV):
            health = self._post("health", {})
            info = self._post("instance_info", {})
        self.assertEqual(health.status_code, 200)
        self.assertFalse(health.json()["data"]["features"]["writes"])
        self.assertEqual(info.json()["data"]["public_base_url"], "https://erp.example.test")
        self.assertEqual(BridgeAuditLog.objects.filter(ok=True).count(), 2)

    def test_expired_or_wrong_audience_tokens_are_rejected(self):
        now = int(time.time())
        with patch.dict(os.environ, BRIDGE_ENV):
            expired = self._post("health", {}, token=self._token(iat=now - 600, exp=now - 300))
            wrong_audience = self._post("health", {}, token=self._token(aud="another-instance"))
        self.assertEqual(expired.status_code, 401)
        self.assertEqual(wrong_audience.status_code, 401)
        self.assertFalse(BridgeAuditLog.objects.exists())

    def test_search_read_uses_keyset_pagination_and_company_scope(self):
        Product.objects.bulk_create(
            [
                Product(company=self.company, sku=f"BR-{index:03d}", name=f"Bridge Product {index:03d}")
                for index in range(125)
            ]
        )
        other = Company.objects.create(code="OTHER-BRIDGE", name="Other Bridge Company")
        Product.objects.create(company=other, sku="HIDDEN", name="Hidden Product")
        payload = {"entity": "products", "fields": ["id", "sku", "name"], "page_size": 100}
        with patch.dict(os.environ, BRIDGE_ENV):
            first = self._post("search_read", payload)
            second = self._post("search_read", {**payload, "after_id": first.json()["data"]["next_after_id"]})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.json()["data"]["records"]), 100)
        self.assertTrue(first.json()["data"]["has_more"])
        self.assertEqual(len(second.json()["data"]["records"]), 25)
        self.assertFalse(second.json()["data"]["has_more"])
        self.assertNotIn("HIDDEN", {row["sku"] for row in first.json()["data"]["records"]})

    def test_search_read_rejects_unavailable_fields_and_permissions(self):
        with patch.dict(os.environ, BRIDGE_ENV):
            bad_field = self._post("search_read", {"entity": "products", "fields": ["not_a_field"]})
        self.assertEqual(bad_field.status_code, 400)

        user = get_user_model().objects.create_user(username="bridge-limited", password="test-password")
        access = UserOrganizationAccess.objects.create(user=user, default_company=self.company, default_branch=self.branch)
        access.companies.add(self.company)
        access.branches.add(self.branch)
        with patch.dict(os.environ, BRIDGE_ENV):
            forbidden = self._post(
                "search_read",
                {"entity": "products", "fields": ["id"]},
                token=self._token(user=user),
            )
        self.assertEqual(forbidden.status_code, 403)

    def test_query_count_does_not_grow_with_page_rows(self):
        Product.objects.bulk_create(
            [Product(company=self.company, sku=f"SMALL-{index}", name=f"Small {index}") for index in range(2)]
        )
        payload = {"entity": "products", "fields": ["id", "sku", "name"], "page_size": 50}
        with patch.dict(os.environ, BRIDGE_ENV):
            self._post("search_read", payload)
            with CaptureQueriesContext(connection) as small:
                small_response = self._post("search_read", payload)
            Product.objects.bulk_create(
                [Product(company=self.company, sku=f"LARGE-{index}", name=f"Large {index}") for index in range(12)]
            )
            with CaptureQueriesContext(connection) as large:
                large_response = self._post("search_read", payload)
        self.assertEqual(small_response.status_code, 200)
        self.assertEqual(large_response.status_code, 200)
        self.assertEqual(len(small), len(large))

    def test_read_group_schema_and_batch_are_bounded(self):
        Product.objects.bulk_create(
            [
                Product(company=self.company, sku=f"GROUP-{index}", name=f"Group {index}", product_type="service")
                for index in range(4)
            ]
        )
        with patch.dict(os.environ, BRIDGE_ENV):
            grouped = self._post(
                "read_group",
                {"entity": "products", "group_by": "product_type", "measures": ["sale_price"]},
            )
            schema = self._post("schema", {"entity": "products"})
            batch = self._post(
                "batch",
                {
                    "operations": [
                        {"method": "schema", "payload": {"entity": "products"}},
                        {"method": "search_read", "payload": {"entity": "products", "fields": ["id"], "page_size": 2}},
                    ]
                },
            )
            oversized = self._post(
                "batch",
                {"operations": [{"method": "schema", "payload": {}} for _ in range(21)]},
            )
        self.assertEqual(grouped.status_code, 200)
        self.assertEqual(grouped.json()["data"]["groups"][0]["record_count"], 4)
        self.assertEqual(schema.status_code, 200)
        self.assertEqual(schema.json()["data"]["entities"][0]["entity"], "products")
        self.assertEqual(len(batch.json()["data"]["results"]), 2)
        self.assertEqual(oversized.status_code, 400)
