from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
import json

from django.test import TestCase

from core.forms import WarehouseForm
from core.models import Company, Product, Warehouse

from .context import OrganizationContext, scope_form, scope_queryset, switch_context
from .forms import OrganizationAccessForm
from .models import Branch, UserOrganizationAccess


class OrganizationContextTests(TestCase):
    def setUp(self):
        self.company_a = Company.objects.create(code="ALPHA", name="Alpha Retail")
        self.company_b = Company.objects.create(code="BETA", name="Beta Services")
        self.main_a = self.company_a.branches.get(is_main=True)
        self.main_b = self.company_b.branches.get(is_main=True)
        self.branch_a2 = Branch.objects.create(company=self.company_a, code="ALPHA-EAST", name="East Outlet")
        self.superuser = get_user_model().objects.create_superuser(
            username="root-admin",
            email="root@example.test",
            password="safe-test-password",
        )

    def test_company_creation_provisions_main_branch(self):
        self.assertEqual(self.main_a.code, "ALPHA-MAIN")
        self.assertEqual(self.company_a.branches.filter(is_main=True).count(), 1)

    def test_branch_parent_must_share_company(self):
        branch = Branch(company=self.company_a, parent=self.main_b, code="INVALID", name="Invalid")
        with self.assertRaises(ValidationError):
            branch.full_clean()

    def test_explicit_user_access_limits_options(self):
        user = get_user_model().objects.create_user(username="operator", password="safe-test-password")
        access = UserOrganizationAccess.objects.create(user=user, default_company=self.company_a, default_branch=self.main_a)
        access.companies.add(self.company_a)
        access.branches.add(self.main_a)
        self.client.force_login(user)

        response = self.client.get("/organization/context/options/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([company["id"] for company in payload["companies"]], [self.company_a.pk])
        self.assertEqual(payload["companies"][0]["branches"][0]["id"], self.main_a.pk)

    def test_server_rejects_company_outside_user_access(self):
        user = get_user_model().objects.create_user(username="restricted", password="safe-test-password")
        access = UserOrganizationAccess.objects.create(user=user)
        access.companies.add(self.company_a)
        access.branches.add(self.main_a)
        self.client.force_login(user)

        response = self.client.post(
            "/organization/context/select/",
            data={"company_id": self.company_b.pk, "branch_id": self.main_b.pk},
        )

        self.assertEqual(response.status_code, 403)

    def test_switch_reconciles_branch_with_company(self):
        session = self.client.session
        context = switch_context(self.superuser, session, self.company_a.pk, self.branch_a2.pk)
        self.assertEqual(context.company_id, self.company_a.pk)
        self.assertEqual(context.branch_id, self.branch_a2.pk)
        with self.assertRaises(ValueError):
            switch_context(self.superuser, session, self.company_a.pk, self.main_b.pk)

    def test_queryset_scope_filters_company_and_branch(self):
        Warehouse.objects.create(company=self.company_a, branch=self.main_a, code="A-MAIN", name="Main Warehouse")
        Warehouse.objects.create(company=self.company_a, branch=self.branch_a2, code="A-EAST", name="East Warehouse")
        Warehouse.objects.create(company=self.company_b, branch=self.main_b, code="B-MAIN", name="Beta Warehouse")
        Product.objects.create(company=self.company_a, sku="A-1", name="Alpha Product")
        Product.objects.create(company=self.company_b, sku="B-1", name="Beta Product")
        context = OrganizationContext(
            company_id=self.company_a.pk,
            branch_id=self.branch_a2.pk,
            allowed_company_ids=(self.company_a.pk, self.company_b.pk),
            allowed_branch_ids=(self.main_a.pk, self.branch_a2.pk, self.main_b.pk),
        )

        self.assertEqual(list(scope_queryset(Warehouse.objects.all(), context).values_list("code", flat=True)), ["A-EAST"])
        self.assertEqual(list(scope_queryset(Product.objects.all(), context).values_list("sku", flat=True)), ["A-1"])

    def test_form_choices_and_defaults_follow_active_context(self):
        warehouse = Warehouse.objects.create(
            company=self.company_a,
            branch=self.branch_a2,
            code="A-EAST",
            name="East Warehouse",
        )
        context = OrganizationContext(
            company_id=self.company_a.pk,
            branch_id=self.branch_a2.pk,
            allowed_company_ids=(self.company_a.pk,),
            allowed_branch_ids=(self.branch_a2.pk,),
        )

        form = scope_form(WarehouseForm(), context)

        self.assertEqual(list(form.fields["company"].queryset), [self.company_a])
        self.assertEqual(list(form.fields["branch"].queryset), [self.branch_a2])
        self.assertEqual(form.initial["company"], self.company_a.pk)
        self.assertEqual(form.initial["branch"], self.branch_a2.pk)
        self.assertEqual(warehouse.branch, self.branch_a2)

    def test_authenticated_layout_displays_active_pair(self):
        self.client.force_login(self.superuser)
        response = self.client.get("/dashboard/")
        self.assertContains(response, self.company_a.name)
        self.assertContains(response, self.main_a.name)
        self.assertContains(response, "data-organization-open")

    def test_company_master_lists_every_allowed_company_and_creates_main_branch(self):
        self.client.force_login(self.superuser)
        list_response = self.client.get("/app/companies/")
        self.assertContains(list_response, self.company_a.name)
        self.assertContains(list_response, self.company_b.name)

        create_response = self.client.post(
            "/app/companies/new/",
            data={
                "code": "GAMMA",
                "name": "Gamma Distribution",
                "currency": "IDR",
                "active": "on",
            },
        )

        self.assertRedirects(create_response, "/app/companies/")
        gamma = Company.objects.get(code="GAMMA")
        self.assertTrue(gamma.branches.filter(is_main=True).exists())

    def test_branch_master_uses_active_company_and_supports_create(self):
        self.client.force_login(self.superuser)
        list_response = self.client.get("/app/branches/")
        self.assertContains(list_response, self.main_a.name)
        self.assertContains(list_response, self.branch_a2.name)
        self.assertNotContains(list_response, self.main_b.name)

        create_response = self.client.post(
            "/app/branches/new/",
            data={
                "company": self.company_a.pk,
                "parent": self.main_a.pk,
                "code": "ALPHA-WEST",
                "name": "West Outlet",
                "sequence": 20,
                "active": "on",
            },
        )

        self.assertRedirects(create_response, "/app/branches/")
        self.assertTrue(Branch.objects.filter(company=self.company_a, code="ALPHA-WEST").exists())

    def test_organization_access_form_rejects_branch_outside_selected_companies(self):
        user = get_user_model().objects.create_user(username="branch-mismatch", password="safe-test-password")
        form = OrganizationAccessForm(
            data={
                "user": user.pk,
                "companies": [self.company_a.pk],
                "branches": [self.main_b.pk],
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Every selected branch", form.errors["branches"][0])

    def test_user_admin_embeds_and_persists_multiple_companies_and_branches(self):
        user = get_user_model().objects.create_user(username="regional-manager", password="safe-test-password")
        form = OrganizationAccessForm(
            data={
                "user": user.pk,
                "companies": [self.company_a.pk, self.company_b.pk],
                "branches": [self.main_a.pk, self.branch_a2.pk, self.main_b.pk],
                "default_company": self.company_a.pk,
                "default_branch": self.branch_a2.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        access = form.save()
        self.assertSetEqual(set(access.companies.all()), {self.company_a, self.company_b})
        self.assertSetEqual(set(access.branches.all()), {self.main_a, self.branch_a2, self.main_b})

        self.client.force_login(self.superuser)
        admin_response = self.client.get(f"/admin/auth/user/{user.pk}/change/")
        self.assertContains(admin_response, "Company &amp; branch access")
        self.assertContains(admin_response, self.company_a.name)
        self.assertContains(admin_response, self.branch_a2.name)

        self.client.force_login(user)
        options = self.client.get("/organization/context/options/").json()["companies"]
        self.assertEqual({item["id"] for item in options}, {self.company_a.pk, self.company_b.pk})
        self.assertEqual(
            {branch["id"] for company in options for branch in company["branches"]},
            {self.main_a.pk, self.branch_a2.pk, self.main_b.pk},
        )

    def test_multi_selected_scope_keeps_one_primary_and_filters_lists_and_forms(self):
        Warehouse.objects.create(company=self.company_a, branch=self.main_a, code="A-MAIN", name="Main Warehouse")
        Warehouse.objects.create(company=self.company_a, branch=self.branch_a2, code="A-EAST", name="East Warehouse")
        Warehouse.objects.create(company=self.company_b, branch=self.main_b, code="B-MAIN", name="Beta Warehouse")
        Product.objects.create(company=self.company_a, sku="A-MULTI", name="Alpha Multi")
        Product.objects.create(company=self.company_b, sku="B-MULTI", name="Beta Multi")

        context = switch_context(
            self.superuser,
            self.client.session,
            [self.company_a.pk, self.company_b.pk],
            [self.branch_a2.pk, self.main_b.pk],
            primary_company_id=self.company_a.pk,
            primary_branch_id=self.branch_a2.pk,
        )

        self.assertEqual(context.company_id, self.company_a.pk)
        self.assertEqual(context.branch_id, self.branch_a2.pk)
        self.assertEqual(context.active_company_ids, (self.company_a.pk, self.company_b.pk))
        self.assertEqual(context.active_branch_ids, (self.branch_a2.pk, self.main_b.pk))
        self.assertSetEqual(
            set(scope_queryset(Warehouse.objects.all(), context).values_list("code", flat=True)),
            {"A-EAST", "B-MAIN"},
        )
        self.assertSetEqual(
            set(scope_queryset(Product.objects.all(), context).values_list("sku", flat=True)),
            {"A-MULTI", "B-MULTI"},
        )
        form = scope_form(WarehouseForm(), context)
        self.assertSetEqual(set(form.fields["company"].queryset), {self.company_a, self.company_b})
        self.assertSetEqual(set(form.fields["branch"].queryset), {self.branch_a2, self.main_b})
        self.assertEqual(form.initial["company"], self.company_a.pk)
        self.assertEqual(form.initial["branch"], self.branch_a2.pk)

    def test_multi_selected_api_returns_primary_and_selected_sets(self):
        self.client.force_login(self.superuser)
        response = self.client.post(
            "/organization/context/select/",
            data=json.dumps(
                {
                    "company_ids": [self.company_a.pk, self.company_b.pk],
                    "branch_ids": [self.branch_a2.pk, self.main_b.pk],
                    "active_company_id": self.company_a.pk,
                    "active_branch_id": self.branch_a2.pk,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["active_company_id"], self.company_a.pk)
        self.assertEqual(payload["active_branch_id"], self.branch_a2.pk)
        self.assertEqual(payload["selected_company_ids"], [self.company_a.pk, self.company_b.pk])
        self.assertEqual(payload["selected_branch_ids"], [self.branch_a2.pk, self.main_b.pk])

    def test_developer_mode_requires_technical_permission_and_persists_in_session(self):
        self.client.force_login(self.superuser)
        response = self.client.post("/apps/developer-mode/", data={"enabled": "1", "next": "/dashboard/"})
        self.assertRedirects(response, "/dashboard/")
        self.assertTrue(self.client.session["developer_mode"])
        dashboard = self.client.get("/dashboard/")
        self.assertContains(dashboard, 'data-developer-mode="true"')
        self.assertContains(dashboard, "js/developer-mode.js")

    def test_all_branches_is_limited_to_allowed_companies(self):
        user = get_user_model().objects.create_user(username="company-manager", password="safe-test-password")
        access = UserOrganizationAccess.objects.create(
            user=user,
            all_branches=True,
            default_company=self.company_a,
            default_branch=self.main_a,
        )
        access.companies.add(self.company_a)
        self.client.force_login(user)

        options = self.client.get("/organization/context/options/").json()["companies"]

        self.assertEqual([company["id"] for company in options], [self.company_a.pk])
        self.assertEqual(
            {branch["id"] for branch in options[0]["branches"]},
            {self.main_a.pk, self.branch_a2.pk},
        )
