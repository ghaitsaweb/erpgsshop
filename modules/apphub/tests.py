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
        self.assertNotContains(app_manager, "Catalog Test")
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

        app_manager = self.client.get("/apps/")
        self.assertContains(app_manager, "Catalog Test")
        workspace = self.client.get("/apps/product/")
        self.assertEqual(workspace.status_code, 200)
