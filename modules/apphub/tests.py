from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from core.models import Product

from .models import ERPApp


class ApplicationGroupAccessTests(TestCase):
    def setUp(self):
        self.app, _created = ERPApp.objects.update_or_create(
            slug="product",
            defaults={
                "name": "Catalog Test",
                "category": "Operations",
                "summary": "Test catalog access.",
                "enabled": True,
                "status": ERPApp.Status.STABLE,
            },
        )
        self.user = get_user_model().objects.create_user(username="no-group-user", password="safe-test-password")

    def test_user_without_group_cannot_see_or_open_application(self):
        self.client.force_login(self.user)

        app_manager = self.client.get("/apps/")
        self.assertRedirects(app_manager, "/dashboard/")
        workspace = self.client.get("/apps/product/")
        self.assertRedirects(workspace, "/dashboard/")

    def test_group_permission_exposes_matching_application(self):
        group = Group.objects.create(name="Catalog User")
        group.permissions.add(
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(Product),
                codename="view_product",
            )
        )
        self.user.groups.add(group)
        self.client.force_login(self.user)

        dashboard = self.client.get("/dashboard/")
        self.assertContains(dashboard, "Catalog Test")
        self.assertNotContains(dashboard, "Manage applications")
        workspace = self.client.get("/apps/product/")
        self.assertEqual(workspace.status_code, 200)

    def test_only_superuser_can_open_application_manager(self):
        admin = get_user_model().objects.create_superuser(
            username="catalog-admin",
            email="catalog-admin@example.com",
            password="safe-test-password",
        )
        self.client.force_login(admin)

        response = self.client.get("/apps/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Catalog Test")

    def test_operational_application_workspace_redirects_to_its_dashboard(self):
        ERPApp.objects.update_or_create(
            slug="inventory",
            defaults={
                "name": "Inventory",
                "category": "Operations",
                "summary": "Inventory operations.",
                "enabled": True,
                "status": ERPApp.Status.STABLE,
            },
        )
        admin = get_user_model().objects.create_superuser(
            username="inventory-launch-admin",
            email="inventory-launch@example.com",
            password="safe-test-password",
        )
        self.client.force_login(admin)

        response = self.client.get("/apps/inventory/")

        self.assertRedirects(
            response,
            "/apps/inventory/feature/dashboard/",
            fetch_redirect_response=False,
        )
