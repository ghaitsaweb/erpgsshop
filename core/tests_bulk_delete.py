from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Company, Employee, Product, StockMove, Warehouse


class EntityBulkDeleteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="bulk-delete-admin",
            password="test-password-123",
            email="bulk-delete@example.com",
        )
        self.company = Company.objects.create(code="BLK", name="Bulk Delete Company")
        self.first = Product.objects.create(
            company=self.company,
            sku="BULK-1",
            name="Bulk Product One",
        )
        self.second = Product.objects.create(
            company=self.company,
            sku="BULK-2",
            name="Bulk Product Two",
        )
        self.keep = Product.objects.create(
            company=self.company,
            sku="BULK-KEEP",
            name="Product That Must Stay",
        )
        self.client.force_login(self.user)
        self.url = reverse("entity_bulk_delete", args=["products"])

    def _delete(self, *products, next_url="/app/products/", confirmed=True):
        payload = {
            "selected_ids": [str(product.pk) for product in products],
            "next": next_url,
        }
        if confirmed:
            payload["confirmation"] = "delete-selected-records"
        return self.client.post(
            self.url,
            payload,
        )

    def test_list_supports_individual_and_current_page_selection(self):
        response = self.client.get(reverse("entity_list", args=["products"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-select-all')
        self.assertContains(response, 'data-select-record', count=3)
        self.assertContains(response, 'data-delete-wizard')
        self.assertContains(response, "Review your selection")
        self.assertContains(response, self.url)
        self.assertNotContains(response, 'class="danger-link"')

    def test_wizard_confirmation_is_required_and_deletes_exact_selection(self):
        return_url = "/app/products/?q=Bulk"
        unconfirmed = self._delete(
            self.first,
            self.second,
            next_url=return_url,
            confirmed=False,
        )

        self.assertRedirects(unconfirmed, return_url, fetch_redirect_response=False)
        self.assertEqual(Product.objects.filter(pk__in=[self.first.pk, self.second.pk]).count(), 2)

        response = self._delete(self.first, self.second, next_url=return_url)

        self.assertRedirects(response, return_url, fetch_redirect_response=False)
        self.assertFalse(Product.objects.filter(pk__in=[self.first.pk, self.second.pk]).exists())
        self.assertTrue(Product.objects.filter(pk=self.keep.pk).exists())

    def test_missing_or_out_of_scope_id_fails_closed(self):
        response = self.client.post(
            self.url,
            {
                "selected_ids": [str(self.first.pk), "999999"],
                "confirmation": "delete-selected-records",
                "next": "/app/products/",
            },
            follow=True,
        )

        self.assertContains(response, "selection changed or contains records outside your access scope")
        self.assertTrue(Product.objects.filter(pk=self.first.pk).exists())

    def test_selection_is_bounded_to_one_page(self):
        response = self.client.post(
            self.url,
            {
                "selected_ids": [str(index) for index in range(1, 27)],
                "confirmation": "delete-selected-records",
            },
            follow=True,
        )

        self.assertContains(response, "Select no more than 25 records at a time")
        self.assertEqual(Product.objects.filter(company=self.company).count(), 3)

    def test_protected_record_rolls_back_the_whole_selection(self):
        protected_product = Product.objects.create(
            company=self.company,
            sku="BULK-PROTECTED",
            name="Protected Product",
        )
        warehouse = Warehouse.objects.create(
            company=self.company,
            code="BULK-WH",
            name="Bulk Warehouse",
        )
        StockMove.objects.create(
            company=self.company,
            product=protected_product,
            warehouse=warehouse,
            direction=StockMove.Direction.IN,
            quantity=1,
            created_by=self.user,
        )
        response = self._delete(self.first, protected_product)
        response = self.client.get(response.url, follow=True)

        self.assertContains(response, "still used by other business records")
        self.assertTrue(Product.objects.filter(pk=self.first.pk).exists())
        self.assertTrue(Product.objects.filter(pk=protected_product.pk).exists())

    def test_return_path_cannot_redirect_to_an_external_site(self):
        response = self._delete(
            self.first,
            next_url="https://example.invalid/steal-selection",
            confirmed=False,
        )

        self.assertRedirects(response, "/app/products/", fetch_redirect_response=False)
        self.assertTrue(Product.objects.filter(pk=self.first.pk).exists())

    def test_employee_list_uses_the_same_guarded_bulk_delete(self):
        employee = Employee.objects.create(
            company=self.company,
            employee_id="EMP-BULK-1",
            name="Unreferenced Employee",
        )
        list_url = reverse("entity_list", args=["employees"])
        bulk_url = reverse("entity_bulk_delete", args=["employees"])

        list_response = self.client.get(list_url)

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, 'data-select-all')
        self.assertContains(list_response, f'value="{employee.pk}"')

        response = self.client.post(
            bulk_url,
            {
                "selected_ids": [str(employee.pk)],
                "confirmation": "delete-selected-records",
                "next": list_url,
            },
        )

        self.assertRedirects(response, list_url, fetch_redirect_response=False)
        self.assertFalse(Employee.objects.filter(pk=employee.pk).exists())
