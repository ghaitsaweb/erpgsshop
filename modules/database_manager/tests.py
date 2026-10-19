from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import connections
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .models import DatabaseOperation, ManagedDatabase
from .runtime import _registered_aliases, database_prefix, primary_database_name, register_database, validate_database_name
from .services import ensure_primary_registry


class DatabaseRuntimeTests(SimpleTestCase):
    def test_name_validation_enforces_gsshop_namespace(self):
        valid_name = f"{database_prefix()}_jakarta"
        self.assertEqual(validate_database_name(valid_name, allow_primary=False), valid_name)
        for invalid in ("postgres", "UPPERCASE", "../escape", "x"):
            with self.subTest(name=invalid), self.assertRaises(ValueError):
                validate_database_name(invalid, allow_primary=False)

    def test_dynamic_alias_is_stable_and_keeps_default_configuration(self):
        name = f"{database_prefix()}_alias_test"
        alias = None
        try:
            alias = register_database(name)
            second = register_database(name)
            self.assertEqual(alias, second)
            self.assertNotEqual(alias, "default")
        finally:
            if alias:
                connections[alias].close()
                connections.databases.pop(alias, None)
                settings.DATABASES.pop(alias, None)
            _registered_aliases.pop(name, None)


class DatabaseManagerViewTests(TestCase):
    def setUp(self):
        self.primary = ensure_primary_registry()
        self.operator_password = "Operator-passphrase-918!"
        self.operator = get_user_model().objects.create_superuser(
            username="db-operator",
            email="operator@example.com",
            password=self.operator_password,
        )

    def unlock(self):
        return self.client.post(
            reverse("database_manager_unlock"),
            {"username": self.operator.username, "password": self.operator_password},
        )

    def test_root_is_the_database_command_center(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Database command center")
        self.assertContains(response, self.primary.name)
        self.assertContains(response, "Choose a database")

    def test_primary_database_can_be_selected_without_unlock(self):
        response = self.client.post(reverse("database_manager_choose"), {"database": primary_database_name()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.cookies["erp_gsshop_database"].value, primary_database_name())
        self.assertEqual(response.json()["redirect"], "/login/")

    def test_only_primary_superuser_can_unlock_controls(self):
        denied = self.client.post(
            reverse("database_manager_unlock"),
            {"username": self.operator.username, "password": "wrong-password"},
        )
        self.assertEqual(denied.status_code, 403)
        allowed = self.unlock()
        self.assertEqual(allowed.status_code, 200)
        self.assertTrue(allowed.json()["success"])

    def test_unlock_is_rate_limited_after_repeated_failures(self):
        for _attempt in range(5):
            response = self.client.post(
                reverse("database_manager_unlock"),
                {"username": self.operator.username, "password": "wrong-password"},
            )
            self.assertEqual(response.status_code, 403)
        blocked = self.client.post(
            reverse("database_manager_unlock"),
            {"username": self.operator.username, "password": self.operator_password},
        )
        self.assertEqual(blocked.status_code, 429)

    def test_activity_requires_operator_unlock(self):
        response = self.client.get(reverse("database_manager_operations"))
        self.assertEqual(response.status_code, 401)
        self.unlock()
        response = self.client.get(reverse("database_manager_operations"))
        self.assertEqual(response.status_code, 200)

    @patch("modules.database_manager.views.execute_database_operation.delay")
    @patch("modules.database_manager.views._validated_target")
    def test_create_is_queued_with_step_up_password(self, validated_target, delay):
        validated_target.return_value = f"{database_prefix()}_newco"
        delay.return_value = SimpleNamespace(id="task-123")
        self.unlock()
        response = self.client.post(
            reverse("database_manager_create"),
            {
                "name": f"{database_prefix()}_newco",
                "label": "New Company",
                "admin_username": "new-admin",
                "admin_email": "new-admin@example.com",
                "admin_password": "Strong-new-admin-918!",
                "operator_password": self.operator_password,
            },
        )
        self.assertEqual(response.status_code, 202)
        operation = DatabaseOperation.objects.get()
        self.assertEqual(operation.target_name, f"{database_prefix()}_newco")
        self.assertEqual(operation.requested_by, self.operator.username)
        self.assertEqual(operation.task_id, "task-123")

    def test_primary_protection_cannot_be_removed(self):
        self.unlock()
        response = self.client.post(
            reverse("database_manager_protection"),
            {
                "database": self.primary.name,
                "protect": "false",
                "operator_password": self.operator_password,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("primary", response.json()["message"].lower())

    def test_database_inventory_endpoint_never_exposes_credentials(self):
        response = self.client.get(reverse("database_manager_databases"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        serialized = str(payload).lower()
        self.assertNotIn("password", serialized)
        self.assertNotIn("secret", serialized)
