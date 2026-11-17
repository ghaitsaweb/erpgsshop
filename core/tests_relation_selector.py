from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import Company, Product
from modules.organization.context import (
    ACTIVE_BRANCH_SESSION_KEY,
    ACTIVE_COMPANY_SESSION_KEY,
    SELECTED_BRANCHES_SESSION_KEY,
    SELECTED_COMPANIES_SESSION_KEY,
)


class RelationSelectorTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="relation-selector-admin",
            email="relations@example.com",
            password="safe-test-password",
        )
        self.company = Company.objects.create(code="REL", name="Relation Company")
        for product_index in range(5):
            Product.objects.create(
                company=self.company,
                sku=f"SKU-{product_index}",
                name=f"Relation Product {product_index}",
            )
        self.client.force_login(self.user)
        branch = self.company.branches.get(is_main=True)
        session = self.client.session
        session[ACTIVE_COMPANY_SESSION_KEY] = self.company.pk
        session[ACTIVE_BRANCH_SESSION_KEY] = branch.pk
        session[SELECTED_COMPANIES_SESSION_KEY] = [self.company.pk]
        session[SELECTED_BRANCHES_SESSION_KEY] = [branch.pk]
        session.save()

    def test_generic_form_uses_server_searched_relation_control(self):
        response = self.client.get("/app/products/new/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'data-relation-search-url="/api/relations/products/company/search/"',
        )
        self.assertContains(response, "relation-selector.js")

    def test_relation_search_is_bounded_then_expands_in_search_more(self):
        endpoint = "/api/relations/stock-moves/product/search/"

        suggestions = self.client.get(endpoint).json()["results"]
        expanded_response = self.client.get(f"{endpoint}?more=1")
        expanded = expanded_response.json()["results"]
        filtered = self.client.get(endpoint, {"q": "Product 4"}).json()["results"]

        self.assertEqual(len(suggestions), 3)
        self.assertEqual(expanded_response.wsgi_request.GET.get("more"), "1")
        self.assertEqual(Product.objects.filter(company=self.company).count(), 5)
        self.assertEqual(len(expanded), 5)
        self.assertEqual(len(filtered), 1)
        self.assertIn("Relation Product 4", filtered[0]["text"])

    def test_unknown_relation_field_is_not_searchable(self):
        response = self.client.get("/api/relations/products/sale_price/search/")

        self.assertEqual(response.status_code, 404)
