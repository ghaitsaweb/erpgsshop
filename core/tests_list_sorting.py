from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from .models import Company, Product


class EntityListServerSortingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="list-sorter", password="test-password-123"
        )
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        self.company = Company.objects.create(code="SORT", name="Sorting Company")
        Product.objects.create(company=self.company, sku="MID", name="Middle Product")
        self.client.force_login(self.user)

    def _product_names(self, response):
        return [row["object"].name for row in response.context["rows"]]

    def test_visible_database_column_toggles_ascending_and_descending(self):
        Product.objects.bulk_create(
            [
                Product(company=self.company, sku="ZULU", name="Zulu Product"),
                Product(company=self.company, sku="ALPHA", name="Alpha Product"),
            ]
        )

        ascending = self.client.get("/app/products/?sort=name&direction=asc")
        descending = self.client.get("/app/products/?sort=name&direction=desc")

        self.assertEqual(
            self._product_names(ascending),
            ["Alpha Product", "Middle Product", "Zulu Product"],
        )
        self.assertEqual(
            self._product_names(descending),
            ["Zulu Product", "Middle Product", "Alpha Product"],
        )
        self.assertContains(ascending, 'aria-sort="ascending"')
        self.assertContains(ascending, "↑")
        self.assertContains(ascending, "?sort=name&amp;direction=desc")
        self.assertContains(descending, 'aria-sort="descending"')
        self.assertContains(descending, "↓")

    def test_sort_allowlist_rejects_unknown_column(self):
        response = self.client.get(
            "/app/products/?sort=company__name&direction=desc"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["current_sort"], "")
        self.assertEqual(response.context["current_sort_direction"], "desc")
        self.assertEqual(self._product_names(response), ["Middle Product"])

    def test_sorting_preserves_search_and_pagination_with_fixed_query_count(self):
        Product.objects.bulk_create(
            [
                Product(
                    company=self.company,
                    sku=f"SCALE-{index:03d}",
                    name=f"Scale Product {index:03d}",
                )
                for index in range(2)
            ]
        )
        url = "/app/products/?q=Scale&sort=name&direction=asc"
        self.client.get(url)
        with CaptureQueriesContext(connection) as small_context:
            small_response = self.client.get(url)

        Product.objects.bulk_create(
            [
                Product(
                    company=self.company,
                    sku=f"SCALE-{index:03d}",
                    name=f"Scale Product {index:03d}",
                )
                for index in range(2, 30)
            ]
        )
        with CaptureQueriesContext(connection) as large_context:
            large_response = self.client.get(url)

        self.assertEqual(len(small_context), len(large_context))
        self.assertEqual(len(small_context), 15)
        self.assertEqual(len(self._product_names(small_response)), 2)
        self.assertEqual(len(self._product_names(large_response)), 25)
        self.assertEqual(self._product_names(large_response)[0], "Scale Product 000")
        self.assertContains(
            large_response,
            "?q=Scale&amp;sort=name&amp;direction=asc&amp;page=2",
        )
        self.assertContains(large_response, 'name="sort" value="name"')
        self.assertContains(large_response, 'name="direction" value="asc"')
