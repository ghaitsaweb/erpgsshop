from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from modules.apphub.discovery import discover_app_catalog
from modules.apphub.models import ERPApp
from modules.apphub.workspaces import app_menu_groups
from modules.finance.models import Account, JournalEntry, JournalLine
from modules.hr.access import HR_MANAGER, HR_SUPERVISOR, HR_USER, scope_hr_queryset
from modules.hr.models import (
    EmployeeChangeRequest,
    EmployeeLoanRequest,
    LoanType,
    Payslip,
    TrainingConduct,
    TrainingConductParticipant,
    TrainingConductSchedule,
    TrainingCourse,
    TrainingTrainer,
)
from modules.hr.security import ensure_hr_roles
from modules.pos.models import POSOrder, POSOrderLine, POSRegister, POSSession
from modules.purchase.access import PURCHASE_MANAGER
from modules.purchase.entity_config import ENTITY_MODELS as PURCHASE_ENTITY_MODELS
from modules.purchase.models import PurchaseOrderControl, PurchaseRequest
from modules.purchase.security import ensure_purchase_roles

from .models import Company, Contact, Employee, Product, PurchaseOrder, SaleOrder, SaleOrderLine, StockMove, Warehouse
from .document_pdf import REPORT_CONFIG, _footer
from .views import DOCUMENT_CONFIG, ENTITY_CONFIG


class ERPIntegrityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="tester", password="test-password-123")
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        self.company = Company.objects.create(code="TST", name="Test Company")
        self.customer = Contact.objects.create(company=self.company, code="C-1", name="Customer")
        self.product = Product.objects.create(company=self.company, sku="P-1", name="Product", sale_price=100)
        self.warehouse = Warehouse.objects.create(company=self.company, code="WH-1", name="Main")

    def test_stock_ledger_updates_balance_and_blocks_negative_stock(self):
        StockMove.objects.create(
            company=self.company,
            product=self.product,
            warehouse=self.warehouse,
            direction=StockMove.Direction.IN,
            quantity=Decimal("5"),
            created_by=self.user,
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.current_stock, Decimal("5"))

        with self.assertRaises(ValidationError):
            StockMove.objects.create(
                company=self.company,
                product=self.product,
                warehouse=self.warehouse,
                direction=StockMove.Direction.OUT,
                quantity=Decimal("6"),
                created_by=self.user,
            )

    def test_sale_total_recalculates_from_lines(self):
        order = SaleOrder.objects.create(company=self.company, number="SO-1", customer=self.customer)
        SaleOrderLine.objects.create(order=order, product=self.product, quantity=Decimal("2"), unit_price=Decimal("100"))
        order.refresh_from_db()
        self.assertEqual(order.total, Decimal("200"))

    def test_document_pdf_is_available_from_sales_order(self):
        call_command("sync_apps", verbosity=0)
        order = SaleOrder.objects.create(
            company=self.company,
            number="SO-PDF-1",
            customer=self.customer,
            customer_reference="CUSTOMER-REF",
            salesperson_name="Administrator",
            payment_terms="Due on receipt",
        )
        SaleOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Rental equipment",
            quantity=Decimal("2"),
            unit_price=Decimal("100"),
        )
        self.client.force_login(self.user)

        form_response = self.client.get(f"/app/sales/{order.pk}/edit/")
        self.assertContains(form_response, f'/app/sales/{order.pk}/print.pdf')
        response = self.client.get(f"/app/sales/{order.pk}/print.pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn('filename="sales-SO-PDF-1.pdf"', response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertGreater(len(response.content), 2500)

    def test_every_document_menu_has_a_pdf_report_definition(self):
        self.assertEqual(set(DOCUMENT_CONFIG), set(REPORT_CONFIG))
        self.assertEqual(REPORT_CONFIG["sales"]["title"], "PESANAN")

    def test_product_master_search_is_bounded_and_query_count_is_constant(self):
        self.client.force_login(self.user)
        Product.objects.bulk_create(
            [
                Product(company=self.company, sku=f"SCALE-{index:03d}", name=f"Scale Product {index:03d}")
                for index in range(2)
            ]
        )
        url = "/api/products/search/?q=Scale%20Product"
        self.client.get(url)
        with CaptureQueriesContext(connection) as small_context:
            small_response = self.client.get(url)
        Product.objects.bulk_create(
            [
                Product(company=self.company, sku=f"SCALE-{index:03d}", name=f"Scale Product {index:03d}")
                for index in range(2, 42)
            ]
        )
        with CaptureQueriesContext(connection) as large_context:
            large_response = self.client.get(url)

        self.assertEqual(small_response.status_code, 200)
        self.assertEqual(large_response.status_code, 200)
        self.assertEqual(len(large_response.json()["results"]), 20)
        self.assertEqual(len(small_context), len(large_context))

    def test_customer_master_search_is_bounded_and_query_count_is_constant(self):
        self.client.force_login(self.user)
        Contact.objects.bulk_create(
            [
                Contact(
                    company=self.company,
                    code=f"C-SCALE-{index:03d}",
                    name=f"Scale Customer {index:03d}",
                    contact_type=Contact.ContactType.CUSTOMER,
                )
                for index in range(2)
            ]
        )
        Contact.objects.create(
            company=self.company,
            code="V-SCALE-001",
            name="Scale Customer Vendor Only",
            contact_type=Contact.ContactType.VENDOR,
        )
        url = "/api/customers/search/?q=Scale%20Customer"
        self.client.get(url)
        with CaptureQueriesContext(connection) as small_context:
            small_response = self.client.get(url)
        Contact.objects.bulk_create(
            [
                Contact(
                    company=self.company,
                    code=f"C-SCALE-{index:03d}",
                    name=f"Scale Customer {index:03d}",
                    contact_type=Contact.ContactType.CUSTOMER,
                )
                for index in range(2, 42)
            ]
        )
        with CaptureQueriesContext(connection) as large_context:
            large_response = self.client.get(url)

        self.assertEqual(small_response.status_code, 200)
        self.assertEqual(large_response.status_code, 200)
        self.assertEqual(len(small_response.json()["results"]), 2)
        self.assertEqual(len(large_response.json()["results"]), 20)
        self.assertEqual(len(small_context), len(large_context))

    def test_completed_sales_pdf_renders_paid_watermark(self):
        call_command("sync_apps", verbosity=0)
        order = SaleOrder.objects.create(
            company=self.company,
            number="SO-PDF-PAID",
            customer=self.customer,
            status=SaleOrder.Status.COMPLETED,
        )
        SaleOrderLine.objects.create(order=order, product=self.product, description="Paid rental", quantity=1, unit_price=10)
        self.client.force_login(self.user)

        response = self.client.get(f"/app/sales/{order.pk}/print.pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF-"))

        canvas = MagicMock()
        _footer(canvas, SimpleNamespace(show_paid_watermark=True, page=1))
        canvas.setFillAlpha.assert_called_once_with(0.10)
        canvas.setFont.assert_any_call("Helvetica-Bold", 180)
        canvas.drawCentredString.assert_any_call(0, -58, "LUNAS")

    def test_sales_pdf_query_count_does_not_grow_with_line_count(self):
        call_command("sync_apps", verbosity=0)
        order = SaleOrder.objects.create(company=self.company, number="SO-PDF-SCALE", customer=self.customer)
        SaleOrderLine.objects.create(order=order, product=self.product, description="Initial", quantity=1, unit_price=10)
        self.client.force_login(self.user)
        url = f"/app/sales/{order.pk}/print.pdf"
        self.client.get(url)
        with CaptureQueriesContext(connection) as small_context:
            small_response = self.client.get(url)
        SaleOrderLine.objects.bulk_create(
            [
                SaleOrderLine(
                    order=order,
                    product=self.product,
                    description=f"Batch line {index}",
                    quantity=1,
                    unit_price=10,
                )
                for index in range(12)
            ]
        )
        with CaptureQueriesContext(connection) as large_context:
            large_response = self.client.get(url)
        self.assertEqual(small_response.status_code, 200)
        self.assertEqual(large_response.status_code, 200)
        self.assertEqual(len(small_context), len(large_context))

    def test_dashboard_and_module_pages_render(self):
        call_command("sync_apps", verbosity=0)
        self.client.force_login(self.user)
        dashboard_response = self.client.get("/")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, "Search all menus")
        self.assertContains(dashboard_response, 'data-search="training conduct training human resources people installed menu"')
        self.assertContains(dashboard_response, 'href="/app/training-conducts/"')
        for entity in ENTITY_CONFIG:
            with self.subTest(entity=entity):
                self.assertEqual(self.client.get(f"/app/{entity}/").status_code, 200)
                self.assertEqual(self.client.get(f"/app/{entity}/new/").status_code, 200)

    def test_prd_hr_extended_domains_are_exposed_in_crud_and_navigation(self):
        expected_entities = {
            "job-classifications",
            "job-experience-levels",
            "employee-categories",
            "employee-addresses",
            "employee-relations",
            "working-calendars",
            "work-patterns",
            "schedule-exchanges",
            "contract-types",
            "contract-allowances",
            "contract-letters",
            "disciplinary-stages",
            "attendance-change-requests",
            "live-tracking-sessions",
            "attendance-recomputes",
            "leave-structures",
            "leave-opening-balances",
            "leave-movements",
            "leave-encashments",
            "payroll-variables",
            "salary-packages",
            "payroll-report-templates",
            "employee-loan-requests",
            "loan-installments",
            "loan-repayments",
            "loan-eligibility",
            "training-requests",
            "training-progress",
            "advance-cycles",
            "expense-cycles",
            "advance-limit-consumptions",
            "expense-limit-consumptions",
        }
        navigation_entities = {
            item["entity"]
            for group in app_menu_groups("hr")
            for item in group["items"]
            if "entity" in item
        }

        self.assertTrue(expected_entities.issubset(ENTITY_CONFIG))
        self.assertTrue(expected_entities.issubset(navigation_entities))

    def test_prd_purchase_domains_are_exposed_in_crud_and_navigation(self):
        expected_entities = set(PURCHASE_ENTITY_MODELS)
        navigation_entities = {
            item["entity"]
            for group in app_menu_groups("purchase")
            for item in group["items"]
            if "entity" in item
        }

        self.assertEqual(len(expected_entities), 40)
        self.assertTrue(expected_entities.issubset(ENTITY_CONFIG))
        self.assertTrue(expected_entities.issubset(navigation_entities))
        self.assertTrue(
            {"sent", "waiting_approval", "rejected", "closed", "revised", "expired"}.issubset(
                set(PurchaseOrder.Status.values)
            )
        )
        self.assertTrue(
            {
                "quotation",
                "blanket_order",
                "vendor_pricelist",
                "tax_percent",
                "discount_percent",
                "cost_center",
                "contract_terms",
                "communication_history",
            }.issubset({field.name for field in PurchaseOrderControl._meta.fields})
        )

    def test_purchase_request_list_query_count_does_not_grow(self):
        self.client.force_login(self.user)
        PurchaseRequest.objects.bulk_create(
            [
                PurchaseRequest(
                    company=self.company,
                    number=f"SCALE-PR-{index:03d}",
                    requestor=self.user,
                    required_date=timezone.localdate(),
                    justification="Scale test",
                )
                for index in range(2)
            ]
        )
        url = "/app/purchase-requests/"
        self.client.get(url)
        with CaptureQueriesContext(connection) as small_context:
            small_response = self.client.get(url)
        PurchaseRequest.objects.bulk_create(
            [
                PurchaseRequest(
                    company=self.company,
                    number=f"SCALE-PR-{index:03d}",
                    requestor=self.user,
                    required_date=timezone.localdate(),
                    justification="Scale test",
                )
                for index in range(2, 22)
            ]
        )
        with CaptureQueriesContext(connection) as large_context:
            large_response = self.client.get(url)

        self.assertEqual(small_response.status_code, 200)
        self.assertEqual(large_response.status_code, 200)
        self.assertEqual(len(small_context), len(large_context))

    def test_purchase_manager_permissions_do_not_include_sales_orders(self):
        ensure_purchase_roles()
        permissions = Group.objects.get(name=PURCHASE_MANAGER).permissions.values_list(
            "content_type__app_label",
            "content_type__model",
        )
        self.assertIn(("core", "purchaseorder"), permissions)
        self.assertNotIn(("core", "saleorder"), permissions)

    def test_employee_loan_list_query_count_does_not_grow(self):
        self.client.force_login(self.user)
        loan_type = LoanType.objects.create(
            company=self.company,
            code="SCALE-LOAN",
            name="Scale Loan",
        )
        employee = Employee.objects.create(
            company=self.company,
            employee_id="SCALE-EMPLOYEE",
            name="Scale Employee",
        )
        EmployeeLoanRequest.objects.bulk_create(
            [
                EmployeeLoanRequest(
                    employee=employee,
                    loan_type=loan_type,
                    number=f"SCALE-LOAN-{index:03d}",
                    amount=1000,
                )
                for index in range(2)
            ]
        )
        url = "/app/employee-loan-requests/"
        self.client.get(url)
        with CaptureQueriesContext(connection) as small_context:
            small_response = self.client.get(url)
        EmployeeLoanRequest.objects.bulk_create(
            [
                EmployeeLoanRequest(
                    employee=employee,
                    loan_type=loan_type,
                    number=f"SCALE-LOAN-{index:03d}",
                    amount=1000,
                )
                for index in range(2, 22)
            ]
        )
        with CaptureQueriesContext(connection) as large_context:
            large_response = self.client.get(url)

        self.assertEqual(small_response.status_code, 200)
        self.assertEqual(large_response.status_code, 200)
        self.assertEqual(len(small_context), len(large_context))

    def test_global_menu_search_includes_only_installed_apps_and_admin_users(self):
        call_command("sync_apps", verbosity=0)
        ERPApp.objects.filter(slug="pos").update(enabled=False)
        self.client.force_login(self.user)

        response = self.client.get("/")
        items = response.context["global_menu_items"]
        users_item = next(item for item in items if item["label"] == "Users")

        self.assertEqual(users_item["url"], "/admin/auth/user/")
        self.assertTrue(users_item["installed"])
        self.assertTrue(
            any(item["kind"] == "application" and item["app_slug"] == "hr" for item in items)
        )
        app_manager_item = next(
            item
            for item in items
            if item["kind"] == "application" and item["app_slug"] == "app-manager"
        )
        self.assertEqual(app_manager_item["url"], "/apps/")
        self.assertIn("apps applications modules install uninstall upgrade", app_manager_item["search"])
        self.assertFalse(
            any(item["kind"] == "application" and item["app_slug"] == "pos" for item in items)
        )
        self.assertContains(response, "ERP Foundation · Administration · Installed")
        stylesheet = (Path(__file__).resolve().parent.parent / "static" / "css" / "app.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".global-menu-result[hidden]", stylesheet)

    def test_app_manager_renders_synced_catalog(self):
        call_command("sync_apps", verbosity=0)
        self.client.force_login(self.user)
        response = self.client.get("/apps/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI Coworker")
        self.assertContains(response, "Installed")
        self.assertContains(response, 'id="menu-search-input"')
        self.assertContains(response, 'href="/apps/hr/"')
        self.assertContains(response, "Refresh Apps")
        refresh_response = self.client.post("/apps/refresh/")
        self.assertRedirects(refresh_response, "/apps/")
        search_response = self.client.get("/apps/", {"q": "Human Resources"})
        self.assertEqual(search_response.context["result_count"], 1)
        self.assertContains(search_response, "Human Resources")
        hr_response = self.client.get("/apps/hr/")
        self.assertContains(hr_response, "Employees")
        self.assertContains(hr_response, "Organizations")
        self.assertContains(hr_response, "Onboarding &amp; Offboarding")
        self.assertContains(hr_response, "Loan Management")
        self.assertContains(hr_response, "Career Transition")
        self.assertContains(hr_response, "Employee Expenses")
        self.assertContains(hr_response, "15 groups")
        self.assertContains(hr_response, 'href="/app/business-units/"')
        self.assertContains(hr_response, 'href="/app/employee-onboarding/"')
        self.assertContains(hr_response, 'href="/app/leave-types/"')
        self.assertContains(hr_response, 'href="/app/travel-requests/"')
        self.assertContains(hr_response, 'class="active-app-card"')
        self.assertContains(hr_response, 'id="application-switcher"')
        self.assertContains(hr_response, 'id="application-search-input"')
        self.assertContains(hr_response, 'data-nav-mode="application"')
        self.assertContains(hr_response, "Recently opened")
        self.assertContains(hr_response, "#icon-users")
        self.assertContains(hr_response, 'href="/app/departments/"')
        self.assertContains(hr_response, 'href="/app/leave-requests/"')
        self.assertContains(hr_response, 'href="/apps/pos/"')
        self.assertContains(hr_response, "Typical flow")
        employee_response = self.client.get("/app/employees/")
        self.assertContains(employee_response, "Human Resources")
        self.assertContains(employee_response, "Employment Contracts")
        self.assertContains(employee_response, "Overtime Requests")
        self.assertContains(employee_response, 'class="breadcrumbs"')
        organization_chart = self.client.get("/apps/hr/feature/organization-chart/")
        self.assertEqual(organization_chart.status_code, 200)
        self.assertContains(organization_chart, "Organization Chart")
        self.assertContains(organization_chart, "Employees by department")
        payslip_response = self.client.get("/app/payslips/")
        self.assertEqual(payslip_response.context["active_app_slug"], "hr")
        self.assertContains(payslip_response, "Human Resources")
        self.assertContains(payslip_response, "Payroll Run")
        for definition in discover_app_catalog():
            with self.subTest(app=definition["slug"]):
                self.assertEqual(self.client.get(f"/apps/{definition['slug']}/").status_code, 200)

    def test_all_hr_analytics_pages_render(self):
        call_command("sync_apps", verbosity=0)
        self.client.force_login(self.user)
        analytic_features = [
            "my-dashboard",
            "director-dashboard",
            "hr-operational",
            "organization-chart",
            "onboarding-dashboard",
            "offboarding-dashboard",
            "leave-dashboard",
            "leave-reporting",
            "overtime-analysis",
            "loan-analysis",
            "career-transition-analysis",
            "travel-reporting",
            "expense-analysis",
            "employee-analysis",
            "attendance-analysis",
            "leave-analysis",
            "payroll-analysis",
        ]
        for feature in analytic_features:
            with self.subTest(feature=feature):
                response = self.client.get(f"/apps/hr/feature/{feature}/")
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "Navigation is ready")

    def test_hr_approval_workflow_uses_guarded_status_transitions(self):
        employee = Employee.objects.create(company=self.company, employee_id="E-WF", name="Workflow Employee")
        change_request = EmployeeChangeRequest.objects.create(
            employee=employee,
            field_name="phone",
            current_value="100",
            requested_value="200",
        )
        self.client.force_login(self.user)

        edit_response = self.client.get(f"/app/employee-change-requests/{change_request.pk}/edit/")
        self.assertContains(edit_response, "Submit")
        response = self.client.post(f"/app/employee-change-requests/{change_request.pk}/action/submit/")
        self.assertRedirects(response, f"/app/employee-change-requests/{change_request.pk}/edit/")
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, "submitted")

        self.client.post(f"/app/employee-change-requests/{change_request.pk}/action/approve/")
        change_request.refresh_from_db()
        self.assertEqual(change_request.status, "approved")
        self.assertEqual(change_request.approver, self.user)
        locked_response = self.client.get(f"/app/employee-change-requests/{change_request.pk}/edit/")
        self.assertContains(locked_response, "Locked · Approved")

    def test_addon_manifest_is_discovered_without_catalog_change(self):
        with TemporaryDirectory() as temporary_root:
            addon_directory = Path(temporary_root) / "warehouse-extension"
            addon_directory.mkdir()
            (addon_directory / "manifest.json").write_text(
                json.dumps(
                    {
                        "name": "Warehouse Extension",
                        "category": "Custom",
                        "summary": "Extra warehouse controls.",
                        "dependencies": ["inventory"],
                        "django_app": "addons.warehouse_extension.apps.WarehouseExtensionConfig",
                        "urlconf": "addons.warehouse_extension.urls",
                    }
                ),
                encoding="utf-8",
            )

            catalog = discover_app_catalog(paths=[Path(temporary_root)])

        discovered = next(item for item in catalog if item["slug"] == "warehouse-extension")
        self.assertEqual(discovered["dependencies"], ["inventory"])
        self.assertEqual(discovered["status"], "beta")
        self.assertEqual(discovered["route"], "")

    def test_app_install_uninstall_is_reversible_and_enforces_access(self):
        call_command("sync_apps", verbosity=0)
        self.client.force_login(self.user)
        app = ERPApp.objects.get(slug="pos-fnb")

        response = self.client.post(f"/apps/{app.slug}/installation/", {"action": "uninstall"})
        self.assertEqual(response.status_code, 302)
        app.refresh_from_db()
        self.assertFalse(app.enabled)
        self.assertRedirects(self.client.get("/app/restaurant-tables/"), f"/apps/{app.slug}/")

        ERPApp.objects.filter(slug="pos").update(enabled=False)
        self.client.post(f"/apps/{app.slug}/installation/", {"action": "install"})
        app.refresh_from_db()
        self.assertTrue(app.enabled)
        self.assertTrue(ERPApp.objects.get(slug="pos").enabled)

    def test_document_header_lines_and_workflow(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/app/sales/new/",
            {
                "company": self.company.pk,
                "number": "SO-LINES-1",
                "customer": self.customer.pk,
                "order_date": timezone.localdate(),
                "notes": "Header and lines test",
                "document_lines-TOTAL_FORMS": "1",
                "document_lines-INITIAL_FORMS": "0",
                "document_lines-MIN_NUM_FORMS": "0",
                "document_lines-MAX_NUM_FORMS": "1000",
                "document_lines-0-product": self.product.pk,
                "document_lines-0-description": "Test line",
                "document_lines-0-quantity": "2",
                "document_lines-0-unit_price": "125",
                "document_lines-0-discount_percent": "",
            },
        )
        order = SaleOrder.objects.get(number="SO-LINES-1")
        self.assertRedirects(response, f"/app/sales/{order.pk}/edit/")
        order.refresh_from_db()
        self.assertEqual(order.lines.count(), 1)
        self.assertEqual(order.total, Decimal("250"))
        self.assertContains(self.client.get(f"/app/sales/{order.pk}/edit/"), "Order Lines")

        response = self.client.post(f"/app/sales/{order.pk}/action/confirm/")
        self.assertRedirects(response, f"/app/sales/{order.pk}/edit/")
        order.refresh_from_db()
        self.assertEqual(order.status, SaleOrder.Status.CONFIRMED)

    def test_payslip_net_salary_is_calculated(self):
        from .models import Employee

        employee = Employee.objects.create(company=self.company, employee_id="E-1", name="Employee")
        payslip = Payslip.objects.create(
            employee=employee,
            number="PAY-1",
            period_start=timezone.localdate(),
            period_end=timezone.localdate(),
            basic_salary=Decimal("1000"),
            allowances=Decimal("250"),
            deductions=Decimal("100"),
        )
        self.assertEqual(payslip.net_salary, Decimal("1150"))

    def test_journal_and_pos_totals_use_lines(self):
        debit_account = Account.objects.create(company=self.company, code="1100", name="Cash", account_type=Account.AccountType.ASSET)
        credit_account = Account.objects.create(company=self.company, code="4100", name="Revenue", account_type=Account.AccountType.INCOME)
        entry = JournalEntry.objects.create(company=self.company, number="JE-1")
        JournalLine.objects.create(entry=entry, account=debit_account, debit=Decimal("200"))
        JournalLine.objects.create(entry=entry, account=credit_account, credit=Decimal("200"))
        entry.refresh_from_db()
        self.assertEqual(entry.total_debit, Decimal("200"))
        self.assertEqual(entry.total_credit, Decimal("200"))
        self.assertTrue(entry.balanced)

        register = POSRegister.objects.create(company=self.company, code="POS-1", name="Register", warehouse=self.warehouse)
        session = POSSession.objects.create(register=register, opened_by=self.user)
        order = POSOrder.objects.create(session=session, number="POS-1")
        POSOrderLine.objects.create(order=order, product=self.product, quantity=Decimal("2"), unit_price=Decimal("125"))
        order.refresh_from_db()
        self.assertEqual(order.total, Decimal("250"))

    def test_training_conduct_has_dedicated_kanban_and_header_line_form(self):
        employee = Employee.objects.create(company=self.company, employee_id="TC-PIC", name="Training PIC")
        course = TrainingCourse.objects.create(company=self.company, code="TC-01", name="Leadership Foundation")
        trainer = TrainingTrainer.objects.create(company=self.company, name="Training Facilitator")
        self.client.force_login(self.user)

        response = self.client.post(
            "/app/training-conducts/new/",
            {
                "company": self.company.pk,
                "name": "Leadership Conduct Batch 1",
                "training_type": TrainingConduct.TrainingType.INTERNAL,
                "training_source": TrainingConduct.TrainingSource.COURSE,
                "course": course.pk,
                "program": "",
                "pic": employee.pk,
                "location": "Training Room",
                "active": "on",
                "schedule_lines-TOTAL_FORMS": "1",
                "schedule_lines-INITIAL_FORMS": "0",
                "schedule_lines-MIN_NUM_FORMS": "1",
                "schedule_lines-MAX_NUM_FORMS": "1000",
                "schedule_lines-0-sequence": "1",
                "schedule_lines-0-course": course.pk,
                "schedule_lines-0-trainer": trainer.pk,
                "schedule_lines-0-notes": "Opening session",
                "schedule_lines-0-start_date": "2026-11-02",
                "schedule_lines-0-end_date": "2026-11-03",
                "schedule_lines-0-start_time": "09:00",
                "schedule_lines-0-end_time": "16:00",
                "participant_lines-TOTAL_FORMS": "1",
                "participant_lines-INITIAL_FORMS": "0",
                "participant_lines-MIN_NUM_FORMS": "0",
                "participant_lines-MAX_NUM_FORMS": "1000",
                "participant_lines-0-employee": employee.pk,
                "participant_lines-0-status": TrainingConductParticipant.Status.ENROLLED,
                "participant_lines-0-notes": "Confirmed",
            },
        )
        conduct = TrainingConduct.objects.get(name="Leadership Conduct Batch 1")
        self.assertRedirects(response, f"/app/training-conducts/{conduct.pk}/edit/")
        self.assertEqual(conduct.schedule_lines.count(), 1)
        self.assertEqual(conduct.participant_lines.count(), 1)
        conduct.refresh_from_db()
        self.assertEqual(str(conduct.date_start), "2026-11-02")
        self.assertEqual(conduct.participant_count, 1)

        list_response = self.client.get("/app/training-conducts/")
        self.assertContains(list_response, "Internal Training")
        self.assertContains(list_response, "Public Training")
        self.assertContains(list_response, "Open Form")
        self.assertContains(list_response, "Leadership Conduct Batch 1")
        form_response = self.client.get(f"/app/training-conducts/{conduct.pk}/edit/")
        self.assertContains(form_response, "Basic Information")
        self.assertContains(form_response, "Planning Date")
        self.assertContains(form_response, "Training Schedule")
        self.assertContains(form_response, "Training Participants")
        self.assertContains(form_response, "Open")
        form_html = form_response.content.decode()
        self.assertRegex(form_html, r'<input type="date" name="schedule_lines-0-start_date"')
        self.assertNotIn('id="tc-plan-start-date"', form_html)

    def test_training_conduct_workflow_is_guarded(self):
        employee = Employee.objects.create(company=self.company, employee_id="TC-WF", name="Workflow PIC")
        course = TrainingCourse.objects.create(company=self.company, code="TC-WF", name="Workflow Course")
        conduct = TrainingConduct.objects.create(
            company=self.company,
            name="Workflow Conduct",
            course=course,
            pic=employee,
        )
        self.client.force_login(self.user)
        for action, expected in [("open", "open"), ("set-upcoming", "upcoming"), ("start", "on_going")]:
            response = self.client.post(f"/app/training-conducts/{conduct.pk}/action/{action}/")
            self.assertRedirects(response, f"/app/training-conducts/{conduct.pk}/edit/")
            conduct.refresh_from_db()
            self.assertEqual(conduct.status, expected)

    def test_training_conduct_list_query_count_does_not_grow(self):
        employee = Employee.objects.create(company=self.company, employee_id="TC-Q", name="Query PIC")
        course = TrainingCourse.objects.create(company=self.company, code="TC-Q", name="Query Course")
        TrainingConduct.objects.create(company=self.company, name="Query Conduct 0", course=course, pic=employee)
        call_command("sync_apps", verbosity=0)
        self.client.force_login(self.user)
        with CaptureQueriesContext(connection) as small_context:
            response = self.client.get("/app/training-conducts/")
            self.assertEqual(response.status_code, 200)
        TrainingConduct.objects.bulk_create(
            [
                TrainingConduct(
                    company=self.company,
                    name=f"Query Conduct {index}",
                    course=course,
                    pic=employee,
                    reference=f"TRN/INT/Q{index:04d}",
                )
                for index in range(1, 13)
            ]
        )
        with CaptureQueriesContext(connection) as large_context:
            response = self.client.get("/app/training-conducts/")
            self.assertEqual(response.status_code, 200)
        self.assertEqual(len(small_context), len(large_context))

    def test_rental_sales_fields_detail_view_and_query_growth(self):
        order = SaleOrder.objects.create(company=self.company, number="SO-RENT-1", customer=self.customer)
        rental = SaleOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Rental product",
            quantity=Decimal("2"),
            unit_price=Decimal("100"),
            discount_percent=Decimal("10"),
            rental_start_date=timezone.localdate(),
            rental_return_date=timezone.localdate(),
            rental_status=SaleOrderLine.RentalStatus.AT_CUSTOMER,
        )
        order.refresh_from_db()
        self.assertEqual(rental.subtotal, Decimal("180"))
        self.assertEqual(order.total, Decimal("180"))

        call_command("sync_apps", verbosity=0)
        self.client.force_login(self.user)
        form_response = self.client.get("/app/sales/new/")
        self.assertContains(form_response, "Expiration Date")
        self.assertContains(form_response, "Tanggal Sewa")
        self.assertContains(form_response, "Status Barang")
        self.assertRegex(
            form_response.content.decode(),
            r'<input type="date" name="document_lines-0-rental_start_date"',
        )
        self.assertRegex(
            form_response.content.decode(),
            r'<input type="date" name="document_lines-0-rental_return_date"',
        )
        self.assertContains(form_response, 'data-master-search-url="/api/products/search/"')
        self.assertContains(form_response, 'data-master-search-url="/api/customers/search/"')
        edit_response = self.client.get(f"/app/sales/{order.pk}/edit/")
        self.assertContains(edit_response, f'value="{timezone.localdate().isoformat()}"')
        self.assertContains(edit_response, f"{self.product.sku} — {self.product.name}")
        with CaptureQueriesContext(connection) as small_context:
            detail_response = self.client.get("/app/rental-details/")
            self.assertEqual(detail_response.status_code, 200)
            self.assertContains(detail_response, "Detail Sewa")
            self.assertContains(detail_response, "Grouped by Tanggal Sewa")
            self.assertContains(detail_response, timezone.localdate().strftime("%B %Y"))
        list_response = self.client.get("/app/rental-details/?view=list")
        self.assertContains(list_response, "Rental product")
        self.assertContains(list_response, "rental-overdue")
        SaleOrderLine.objects.bulk_create(
            [
                SaleOrderLine(
                    order=order,
                    product=self.product,
                    description=f"Rental batch {index}",
                    quantity=1,
                    unit_price=100,
                    rental_start_date=timezone.localdate(),
                    rental_return_date=timezone.localdate(),
                    rental_status=SaleOrderLine.RentalStatus.AT_CUSTOMER,
                )
                for index in range(12)
            ]
        )
        with CaptureQueriesContext(connection) as large_context:
            detail_response = self.client.get("/app/rental-details/")
            self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(len(small_context), len(large_context))

    def test_odoo_rental_import_is_idempotent(self):
        payload = """order_source_id,order_number,order_date,validity_date,order_status,customer_reference,notes,partner_source_id,partner_name,partner_email,partner_phone,partner_address,line_source_id,product_source_id,product_name,description,quantity,unit_price,discount_percent,rental_start_date,rental_return_date,rental_status,salesperson_name,price_list,payment_terms,unit_name,display_type
10,S00010,2026-10-01,2026-11-01,sale,REF-10,,20,Imported Customer,,,,30,40,Wheelchair Rental,Wheelchair Rental,2,100000,10,2026-10-01,2026-11-01,customer,Sales User,Default,,Units,
10,S00010,2026-10-01,2026-11-01,sale,REF-10,,20,Imported Customer,,,,31,41,Delivery,Delivery,1,50000,0,2026-10-01,,kembaii,Sales User,Default,,Units,
10,S00010,2026-10-01,2026-11-01,sale,REF-10,,20,Imported Customer,,,,32,,,Rental note,0,0,0,,,,Sales User,Default,,Units,line_section
"""
        for _index in range(2):
            with patch("sys.stdin", StringIO(payload)):
                call_command("import_odoo_rentals", company_code=self.company.code, verbosity=0)
        order = SaleOrder.objects.get(odoo_source_id=10)
        self.assertEqual(order.lines.count(), 3)
        self.assertEqual(order.lines.filter(display_type=SaleOrderLine.DisplayType.SECTION, product__isnull=True).count(), 1)
        self.assertEqual(order.total, Decimal("230000"))
        self.assertEqual(Contact.objects.filter(company=self.company, code="ODOO-C-20").count(), 1)
        self.assertEqual(Product.objects.filter(company=self.company, sku__startswith="ODOO-P-").count(), 2)

    def test_rental_status_quick_edit_and_guarded_line_delete(self):
        order = SaleOrder.objects.create(
            company=self.company,
            number="SO-RENT-ACTION",
            customer=self.customer,
            status=SaleOrder.Status.CONFIRMED,
        )
        imported_line = SaleOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Recoverable imported rental",
            quantity=2,
            unit_price=100,
            rental_start_date=timezone.localdate(),
            rental_status=SaleOrderLine.RentalStatus.AT_CUSTOMER,
            odoo_source_id=901,
        )
        manual_line = SaleOrderLine.objects.create(
            order=order,
            product=self.product,
            description="Protected manual rental",
            quantity=1,
            unit_price=100,
            rental_start_date=timezone.localdate(),
            rental_status=SaleOrderLine.RentalStatus.AT_CUSTOMER,
        )
        call_command("sync_apps", verbosity=0)
        self.client.force_login(self.user)
        return_path = "/app/rental-details/?view=list"

        response = self.client.post(
            f"/app/rental-details/{imported_line.pk}/status/",
            {"rental_status": SaleOrderLine.RentalStatus.RETURNED, "next": return_path},
        )
        self.assertRedirects(response, return_path)
        imported_line.refresh_from_db()
        self.assertEqual(imported_line.rental_status, SaleOrderLine.RentalStatus.RETURNED)

        list_response = self.client.get(return_path)
        self.assertContains(list_response, 'name="rental_status"')
        self.assertContains(list_response, 'class="rental-delete"')

        response = self.client.post(
            f"/app/rental-details/{imported_line.pk}/line-delete/",
            {"next": return_path},
        )
        self.assertRedirects(response, return_path)
        self.assertFalse(SaleOrderLine.objects.filter(pk=imported_line.pk).exists())
        order.refresh_from_db()
        self.assertEqual(order.total, Decimal("100"))

        response = self.client.post(
            f"/app/rental-details/{manual_line.pk}/line-delete/",
            {"next": return_path},
            follow=True,
        )
        self.assertContains(response, "Manual lines on confirmed orders cannot be deleted")
        self.assertTrue(SaleOrderLine.objects.filter(pk=manual_line.pk).exists())


class HRRecordRuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ensure_hr_roles()
        user_model = get_user_model()
        cls.company_a = Company.objects.create(code="SEC-A", name="Security Company A")
        cls.company_b = Company.objects.create(code="SEC-B", name="Security Company B")
        cls.supervisor_user = user_model.objects.create_user(username="hr-supervisor", password="test-password-123")
        cls.employee_user = user_model.objects.create_user(username="hr-employee", password="test-password-123")
        cls.outsider_user = user_model.objects.create_user(username="hr-outsider", password="test-password-123")
        cls.manager_user = user_model.objects.create_user(username="hr-manager", password="test-password-123")
        cls.supervisor_user.groups.add(Group.objects.get(name=HR_SUPERVISOR))
        cls.employee_user.groups.add(Group.objects.get(name=HR_USER))
        cls.outsider_user.groups.add(Group.objects.get(name=HR_USER))
        cls.manager_user.groups.add(Group.objects.get(name=HR_MANAGER))

        cls.supervisor_employee = Employee.objects.create(
            company=cls.company_a,
            user=cls.supervisor_user,
            employee_id="SEC-SUP",
            name="Supervisor Scope",
        )
        cls.direct_employee = Employee.objects.create(
            company=cls.company_a,
            user=cls.employee_user,
            supervisor=cls.supervisor_employee,
            employee_id="SEC-DIRECT",
            name="Direct Report Scope",
        )
        cls.outsider_employee = Employee.objects.create(
            company=cls.company_a,
            user=cls.outsider_user,
            employee_id="SEC-OUT",
            name="Outside Supervisor Scope",
        )
        cls.manager_employee = Employee.objects.create(
            company=cls.company_a,
            user=cls.manager_user,
            employee_id="SEC-MGR",
            name="Manager Scope",
        )
        cls.other_company_employee = Employee.objects.create(
            company=cls.company_b,
            employee_id="SEC-B-EMP",
            name="Other Company Scope",
        )
        cls.direct_request = EmployeeChangeRequest.objects.create(
            employee=cls.direct_employee,
            field_name="phone",
            requested_value="DIRECT-VALUE",
        )
        cls.outsider_request = EmployeeChangeRequest.objects.create(
            employee=cls.outsider_employee,
            field_name="phone",
            requested_value="OUTSIDER-VALUE",
        )
        cls.other_company_request = EmployeeChangeRequest.objects.create(
            employee=cls.other_company_employee,
            field_name="phone",
            requested_value="OTHER-COMPANY-VALUE",
        )

    def test_supervisor_sees_own_and_direct_reports_but_not_other_employees(self):
        self.client.force_login(self.supervisor_user)
        response = self.client.get("/app/employee-change-requests/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Direct Report Scope")
        self.assertNotContains(response, "Outside Supervisor Scope")
        self.assertNotContains(response, "Other Company Scope")

    def test_record_rule_blocks_direct_url_and_workflow_idor(self):
        self.client.force_login(self.supervisor_user)
        edit_url = f"/app/employee-change-requests/{self.outsider_request.pk}/edit/"
        action_url = f"/app/employee-change-requests/{self.outsider_request.pk}/action/submit/"
        self.assertEqual(self.client.get(edit_url).status_code, 404)
        self.assertEqual(self.client.post(action_url).status_code, 404)
        self.outsider_request.refresh_from_db()
        self.assertEqual(self.outsider_request.status, "draft")

    def test_self_service_cannot_approve_own_request(self):
        self.client.force_login(self.employee_user)
        submit_url = f"/app/employee-change-requests/{self.direct_request.pk}/action/submit/"
        approve_url = f"/app/employee-change-requests/{self.direct_request.pk}/action/approve/"
        self.client.post(submit_url)
        self.client.post(approve_url)
        self.direct_request.refresh_from_db()
        self.assertEqual(self.direct_request.status, "submitted")

    def test_supervisor_can_approve_direct_report_request(self):
        self.direct_request.status = "submitted"
        self.direct_request.save(update_fields=["status"])
        self.client.force_login(self.supervisor_user)
        approve_url = f"/app/employee-change-requests/{self.direct_request.pk}/action/approve/"
        self.client.post(approve_url)
        self.direct_request.refresh_from_db()
        self.assertEqual(self.direct_request.status, "approved")
        self.assertEqual(self.direct_request.approver, self.supervisor_user)

    def test_manager_is_company_scoped(self):
        self.client.force_login(self.manager_user)
        response = self.client.get("/app/employee-change-requests/")
        self.assertContains(response, "Direct Report Scope")
        self.assertContains(response, "Outside Supervisor Scope")
        self.assertNotContains(response, "Other Company Scope")

    def test_self_service_payslip_hides_draft_records(self):
        today = timezone.localdate()
        Payslip.objects.create(
            employee=self.direct_employee,
            number="SEC-DRAFT-PAY",
            period_start=today,
            period_end=today,
            status=Payslip.Status.DRAFT,
        )
        Payslip.objects.create(
            employee=self.direct_employee,
            number="SEC-CONFIRMED-PAY",
            period_start=today,
            period_end=today,
            status=Payslip.Status.CONFIRMED,
        )
        self.client.force_login(self.employee_user)
        response = self.client.get("/app/payslips/")
        self.assertNotContains(response, "SEC-DRAFT-PAY")
        self.assertContains(response, "SEC-CONFIRMED-PAY")
        confirmed = Payslip.objects.get(number="SEC-CONFIRMED-PAY")
        edit_response = self.client.get(f"/app/payslips/{confirmed.pk}/edit/")
        self.assertRedirects(edit_response, "/app/payslips/")

    def test_self_service_menu_hides_configuration(self):
        call_command("sync_apps", verbosity=0)
        self.client.force_login(self.employee_user)
        response = self.client.get("/apps/hr/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/app/leave-requests/"')
        self.assertNotContains(response, 'href="/app/hr-settings/"')
        dashboard_response = self.client.get("/")
        self.assertNotContains(dashboard_response, 'href="/app/hr-settings/"')

    def test_supervisor_query_count_does_not_grow_with_team_size(self):
        list(self.supervisor_user.groups.values_list("name", flat=True))
        self.supervisor_user._gsshop_hr_group_names = {HR_SUPERVISOR}
        with CaptureQueriesContext(connection) as small_context:
            list(scope_hr_queryset(self.supervisor_user, "employees", Employee.objects.all()))
        Employee.objects.bulk_create(
            [
                Employee(
                    company=self.company_a,
                    supervisor=self.supervisor_employee,
                    employee_id=f"SEC-BATCH-{index}",
                    name=f"Batch Employee {index}",
                )
                for index in range(12)
            ]
        )
        with CaptureQueriesContext(connection) as large_context:
            list(scope_hr_queryset(self.supervisor_user, "employees", Employee.objects.all()))
        self.assertEqual(len(small_context), len(large_context))

    def test_training_conduct_is_visible_but_read_only_for_supervisor(self):
        course = TrainingCourse.objects.create(company=self.company_a, code="SEC-TC", name="Security Training")
        conduct = TrainingConduct.objects.create(
            company=self.company_a,
            name="Supervisor Visible Training",
            course=course,
            pic=self.supervisor_employee,
        )
        self.client.force_login(self.supervisor_user)
        list_response = self.client.get("/app/training-conducts/")
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "Supervisor Visible Training")
        form_response = self.client.get(f"/app/training-conducts/{conduct.pk}/edit/")
        self.assertEqual(form_response.status_code, 200)
        self.assertNotContains(form_response, "Save Training Conduct")
        post_response = self.client.post(f"/app/training-conducts/{conduct.pk}/edit/", {"name": "Forbidden Change"})
        self.assertRedirects(post_response, f"/app/training-conducts/{conduct.pk}/edit/")
        conduct.refresh_from_db()
        self.assertEqual(conduct.name, "Supervisor Visible Training")
