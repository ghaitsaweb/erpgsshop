from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse


class AdminThemeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="theme-admin",
            email="theme-admin@example.test",
            password="safe-password",
        )
        self.client.force_login(self.user)

    def test_admin_overview_uses_the_normal_erp_shell_and_menu_classes(self):
        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/base.html")
        self.assertTemplateUsed(response, "base.html")
        self.assertContains(response, 'class="app-shell"')
        self.assertContains(response, 'class="sidebar"')
        self.assertContains(response, 'class="topbar"')
        self.assertContains(response, 'data-nav-mode="admin"')
        self.assertContains(response, "Admin Settings")
        self.assertContains(response, 'class="nav-section nav-group erp-admin-nav-group"')
        self.assertContains(response, "nav-item nav-child")
        self.assertNotContains(response, '<a class="global-menu-result" data-global-menu-item')
        self.assertContains(response, reverse("admin:auth_user_changelist"))
        self.assertContains(response, "css/admin-theme")
        self.assertNotContains(response, 'id="header"')

    def test_admin_changelist_keeps_crud_permissions_inside_the_erp_shell(self):
        response = self.client.get(reverse("admin:auth_user_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="app-shell"')
        self.assertContains(response, 'id="changelist-form"')
        self.assertContains(response, reverse("admin:auth_user_add"))
        self.assertContains(response, 'aria-current="page"')

    def test_admin_change_form_keeps_django_widgets_inside_the_erp_shell(self):
        response = self.client.get(reverse("admin:auth_user_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="app-shell"')
        self.assertContains(response, 'id="user_form"')
        self.assertContains(response, 'class="submit-row"')
        self.assertContains(response, "admin/css/forms")

    def test_admin_sidebar_only_lists_models_allowed_by_django_permissions(self):
        limited_user = get_user_model().objects.create_user(
            username="limited-theme-admin",
            password="safe-password",
            is_staff=True,
        )
        limited_user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="auth",
                content_type__model="user",
                codename="view_user",
            )
        )
        self.client.force_login(limited_user)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("admin:auth_user_changelist"))
        self.assertNotContains(response, reverse("admin:auth_group_changelist"))

    def test_admin_login_uses_the_same_login_template_as_the_erp(self):
        self.client.logout()

        response = self.client.get(reverse("admin:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="login-layout"')
        self.assertContains(response, 'class="login-story"')
        self.assertContains(response, 'class="login-card"')
        self.assertContains(response, "css/app.css")
        self.assertNotContains(response, "admin/css/login.css")
