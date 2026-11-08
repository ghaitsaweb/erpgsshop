from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from .models import Company, Contact, SaleOrder
from .record_navigation import build_record_navigation


class RecordNavigationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="record-navigator",
            password="test-password-123",
            is_staff=True,
            is_superuser=True,
        )
        self.company = Company.objects.create(code="NAV", name="Navigation Company")
        self.other_company = Company.objects.create(code="OTH", name="Other Company")
        self.customer = Contact.objects.create(
            company=self.company,
            code="NAV-CUSTOMER",
            name="Navigation Customer",
            contact_type=Contact.ContactType.CUSTOMER,
        )
        self.other_customer = Contact.objects.create(
            company=self.other_company,
            code="OTHER-CUSTOMER",
            name="Other Customer",
            contact_type=Contact.ContactType.CUSTOMER,
        )

    def _order(self, number, company=None, customer=None):
        return SaleOrder.objects.create(
            company=company or self.company,
            customer=customer or self.customer,
            number=number,
        )

    def test_navigation_is_scoped_bounded_and_query_count_does_not_grow(self):
        previous = self._order("SO-NAV-001")
        current = self._order("SO-NAV-002")
        self._order(
            "SO-OTHER-001",
            company=self.other_company,
            customer=self.other_customer,
        )
        following = self._order("SO-NAV-003")
        scoped = SaleOrder.objects.filter(company=self.company)

        with CaptureQueriesContext(connection) as small_context:
            small = build_record_navigation(scoped, current, "sales", "from=list")

        SaleOrder.objects.bulk_create(
            [
                SaleOrder(
                    company=self.company,
                    customer=self.customer,
                    number=f"SO-NAV-SCALE-{index:02d}",
                )
                for index in range(10)
            ]
        )
        with CaptureQueriesContext(connection) as large_context:
            large = build_record_navigation(scoped, current, "sales", "from=list")

        self.assertEqual(len(small_context), 2)
        self.assertEqual(len(large_context), 2)
        self.assertEqual(small, large)
        self.assertEqual(small["current_pk"], current.pk)
        self.assertIn(f"/app/sales/{previous.pk}/edit/?from=list", small["previous_url"])
        self.assertIn(f"/app/sales/{following.pk}/edit/?from=list", small["next_url"])
        for query in small_context.captured_queries + large_context.captured_queries:
            self.assertIn("LIMIT 1", query["sql"].upper())

    def test_typed_record_jump_is_scoped_and_keeps_query_count_constant(self):
        current = self._order("SO-JUMP-001")
        target = self._order("SO-JUMP-002")
        outside_scope = self._order(
            "SO-JUMP-OTHER",
            company=self.other_company,
            customer=self.other_customer,
        )
        self.client.force_login(self.user)
        jump_url = "/app/sales/jump/"
        params = {
            "record": target.pk,
            "current": current.pk,
            "return_query": "source=kanban",
        }

        with patch(
            "core.views._scope_queryset",
            return_value=SaleOrder.objects.filter(company=self.company),
        ):
            with CaptureQueriesContext(connection) as small_context:
                small_response = self.client.get(jump_url, params)
            SaleOrder.objects.bulk_create(
                [
                    SaleOrder(
                        company=self.company,
                        customer=self.customer,
                        number=f"SO-JUMP-SCALE-{index:02d}",
                    )
                    for index in range(10)
                ]
            )
            with CaptureQueriesContext(connection) as large_context:
                large_response = self.client.get(jump_url, params)
            denied_response = self.client.get(
                jump_url,
                {"record": outside_scope.pk, "current": current.pk},
            )

        expected = f"/app/sales/{target.pk}/edit/?source=kanban"
        self.assertRedirects(small_response, expected, fetch_redirect_response=False)
        self.assertRedirects(large_response, expected, fetch_redirect_response=False)
        self.assertEqual(len(small_context), len(large_context))
        self.assertRedirects(
            denied_response,
            f"/app/sales/{current.pk}/edit/",
            fetch_redirect_response=False,
        )
