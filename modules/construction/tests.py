from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Contact, Employee, Product
from modules.apphub.workspaces import APP_WORKSPACES, app_menu_groups, iter_menu_items
from modules.operations.models import ConstructionProject

from .access import CONSTRUCTION_MANAGER, scope_construction_queryset
from .dashboard import ANALYTIC_FEATURES
from .entity_config import CONSTRUCTION_ENTITY_CONFIG
from .models import (
    ConstructionBOQ,
    ConstructionBOQCostLine,
    ConstructionBOQImportLog,
    ConstructionBOQItem,
    ConstructionContract,
    ConstructionContractDPLine,
    ConstructionContractMilestone,
    ConstructionJobOrder,
)
from .security import ensure_construction_roles
from .services import recalculate_boq


class ConstructionParityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="builder", password="test-password-123")
        cls.user.is_staff = True
        cls.user.is_superuser = True
        cls.user.save(update_fields=["is_staff", "is_superuser"])
        cls.company = Company.objects.create(code="BUILD", name="Builder Company")
        cls.customer = Contact.objects.create(company=cls.company, code="CUS-B", name="Construction Customer")
        cls.project = ConstructionProject.objects.create(
            company=cls.company,
            code="PRJ-B",
            name="Tower Project",
            customer=cls.customer,
            start_date=timezone.localdate(),
            contract_value=Decimal("1500000"),
        )

    def setUp(self):
        call_command("sync_apps", verbosity=0)
        self.client.force_login(self.user)

    def test_hmx_construction_catalog_is_exposed_without_dead_entities(self):
        entities = APP_WORKSPACES["construction"]["entities"]
        self.assertGreaterEqual(len(entities), 30)
        shared_entities = {
            "construction-projects", "work-packages", "purchase-requests", "purchase-quotations",
            "purchases", "direct-purchases", "goods-receipts", "purchase-inventory-transfers",
            "warehouses", "employees", "assets", "accounts",
        }
        self.assertTrue(set(entities).issubset(CONSTRUCTION_ENTITY_CONFIG.keys() | shared_entities))
        groups = app_menu_groups("construction")
        self.assertEqual(
            [group["label"] for group in groups],
            [
                "Projects", "Budget & Cost Control", "Procurements", "Subcontracting",
                "Operations", "Execution", "Timesheets",
                "Quantity Surveying", "Progressive Claim", "Risk & Issue Management",
                "Resource Management", "Reporting",
            ],
        )
        labels = [[item["label"] for item in iter_menu_items(group["items"])] for group in groups]
        self.assertEqual(
            labels[0],
            [
                "All Projects", "Bill of Quantity", "Contracts", "Cost Sheet",
                "Group of Product", "BoQ Template", "Project Account Settings", "OPBAL Import", "OPBAL Import List",
                "S-Curve", "Project Gantt Chart",
            ],
        )
        self.assertEqual(labels[1:11], [
            ["Budget Change Request"],
            ["Purchase Requests", "Request for Quotation", "Purchase Orders", "Direct Purchase"],
            ["Subcon Purchase Request", "Subcon Request for Quotation", "Subcon Purchase Orders", "Subcon Direct Purchase", "Subcon Job Order"],
            ["Goods Receipt", "Goods Issue"],
            ["Job Orders", "Resource Actualization"],
            ["My Timesheets", "Manage Timesheets", "Timesheet Report"],
            ["Quantity Surveys"],
            ["Progressive Claims"],
            ["Risk Register", "Issue Log"],
            ["Employees", "Assets", "Resource Report"],
        ])
        self.assertEqual(len(labels[11]), 12)

    def test_contract_workflow_activates_from_draft(self):
        contract = ConstructionContract.objects.create(
            project=self.project,
            number="CON-001",
            customer=self.customer,
            title="Main Contract",
            start_date=timezone.localdate(),
            contract_value=Decimal("1500000"),
        )

        response = self.client.post(f"/app/construction-contracts/{contract.pk}/action/activate/")

        self.assertEqual(response.status_code, 302)
        contract.refresh_from_db()
        self.assertEqual(contract.status, ConstructionContract.Status.ACTIVE)

    def test_every_visible_construction_analysis_route_renders(self):
        for feature in ANALYTIC_FEATURES:
            with self.subTest(feature=feature):
                response = self.client.get(f"/apps/construction/feature/{feature}/")
                self.assertEqual(response.status_code, 200)

    def test_contract_rejects_cross_company_customer(self):
        other_company = Company.objects.create(code="OTHER", name="Other Company")
        other_customer = Contact.objects.create(company=other_company, code="CUS-O", name="Other Customer")
        contract = ConstructionContract(
            project=self.project,
            number="CON-X",
            customer=other_customer,
            title="Invalid Contract",
            start_date=timezone.localdate(),
        )

        with self.assertRaises(ValidationError):
            contract.full_clean()

    def test_manager_scope_is_limited_to_employee_company(self):
        scoped_user = get_user_model().objects.create_user(username="construction-manager")
        Employee.objects.create(company=self.company, user=scoped_user, employee_id="EMP-CM", name="Construction Manager")
        ensure_construction_roles()
        scoped_user.groups.add(Group.objects.get(name=CONSTRUCTION_MANAGER))
        other_company = Company.objects.create(code="OUT", name="Outside Company")
        other_customer = Contact.objects.create(company=other_company, code="CUS-OUT", name="Outside Customer")
        ConstructionProject.objects.create(
            company=other_company,
            code="PRJ-OUT",
            name="Outside Project",
            customer=other_customer,
            start_date=timezone.localdate(),
        )

        visible = scope_construction_queryset(
            scoped_user,
            "construction-projects",
            ConstructionProject.objects.all(),
        )

        self.assertEqual(list(visible.values_list("code", flat=True)), ["PRJ-B"])

    def test_job_order_analysis_query_count_does_not_grow_with_records(self):
        today = timezone.localdate()
        ConstructionJobOrder.objects.bulk_create(
            [
                ConstructionJobOrder(
                    project=self.project,
                    number=f"JO-S-{index}",
                    title=f"Small job {index}",
                    planned_start=today,
                )
                for index in range(2)
            ]
        )
        url = "/apps/construction/feature/job-order-analysis/"
        self.client.get(url)
        with CaptureQueriesContext(connection) as small_context:
            small_response = self.client.get(url)
        ConstructionJobOrder.objects.bulk_create(
            [
                ConstructionJobOrder(
                    project=self.project,
                    number=f"JO-L-{index}",
                    title=f"Large job {index}",
                    planned_start=today + timedelta(days=index),
                )
                for index in range(20)
            ]
        )
        with CaptureQueriesContext(connection) as large_context:
            large_response = self.client.get(url)

        self.assertEqual(small_response.status_code, 200)
        self.assertEqual(large_response.status_code, 200)
        self.assertEqual(len(small_context), len(large_context))

    def test_contract_document_saves_dp_and_milestone_sections_together(self):
        today = timezone.localdate()
        response = self.client.post(
            "/app/construction-contracts/new/",
            {
                "project": self.project.pk,
                "number": "CON-MULTI-001",
                "contract_type": "owner",
                "customer": self.customer.pk,
                "title": "Multi-line Contract",
                "contract_date": today.isoformat(),
                "billing_type": "milestone",
                "currency": "IDR",
                "start_date": today.isoformat(),
                "contract_value": "1000000.00",
                "dp_type": "percent",
                "down_payment_percent": "10.00",
                "down_payment_amount": "100000.00",
                "down_payment_status": "pending",
                "retention_percent": "5.00",
                "retention2_percent": "0.00",
                "document_lines_0-TOTAL_FORMS": "1",
                "document_lines_0-INITIAL_FORMS": "0",
                "document_lines_0-MIN_NUM_FORMS": "0",
                "document_lines_0-MAX_NUM_FORMS": "1000",
                "document_lines_0-0-sequence": "10",
                "document_lines_0-0-portion_percent": "10.00",
                "document_lines_0-0-invoice_amount": "100000.00",
                "document_lines_0-0-payment_status": "pending",
                "document_lines_1-TOTAL_FORMS": "1",
                "document_lines_1-INITIAL_FORMS": "0",
                "document_lines_1-MIN_NUM_FORMS": "0",
                "document_lines_1-MAX_NUM_FORMS": "1000",
                "document_lines_1-0-sequence": "10",
                "document_lines_1-0-name": "Foundation Complete",
                "document_lines_1-0-description": "Foundation milestone",
                "document_lines_1-0-percentage": "25.00",
                "document_lines_1-0-amount": "250000.00",
                "document_lines_1-0-status": "pending",
            },
        )

        self.assertEqual(response.status_code, 302)
        contract = ConstructionContract.objects.get(number="CON-MULTI-001")
        self.assertTrue(ConstructionContractDPLine.objects.filter(contract=contract, portion_percent=10).exists())
        self.assertTrue(ConstructionContractMilestone.objects.filter(contract=contract, name="Foundation Complete").exists())

    def test_boq_resource_recalculation_is_batched_and_updates_commercial_totals(self):
        product = Product.objects.create(
            company=self.company, sku="MAT-001", name="Concrete", purchase_price=Decimal("20")
        )
        boq = ConstructionBOQ.objects.create(
            project=self.project, number="BOQ-BATCH", title="Batch BOQ",
            global_markup_type="percent", global_markup_value=Decimal("10"), global_tax_percent=Decimal("11"),
        )
        first_items = ConstructionBOQItem.objects.bulk_create([
            ConstructionBOQItem(
                boq=boq, sequence=index * 10, description=f"Work {index}", quantity=Decimal("2"),
                unit_price=Decimal("100"), budget_cost=Decimal("20"),
            )
            for index in range(1, 3)
        ])
        ConstructionBOQCostLine.objects.bulk_create([
            ConstructionBOQCostLine(
                boq_item=item, product=product, description="Concrete", quantity=Decimal("2"),
                unit_rate=Decimal("20"), amount=Decimal("0"),
            )
            for item in first_items
        ])
        with CaptureQueriesContext(connection) as small_context:
            recalculate_boq(boq.pk)

        more_items = ConstructionBOQItem.objects.bulk_create([
            ConstructionBOQItem(
                boq=boq, sequence=index * 10, description=f"Work {index}", quantity=Decimal("2"),
                unit_price=Decimal("100"), budget_cost=Decimal("20"),
            )
            for index in range(3, 23)
        ])
        ConstructionBOQCostLine.objects.bulk_create([
            ConstructionBOQCostLine(
                boq_item=item, product=product, description="Concrete", quantity=Decimal("2"),
                unit_rate=Decimal("20"), amount=Decimal("0"),
            )
            for item in more_items
        ])
        with CaptureQueriesContext(connection) as large_context:
            recalculated = recalculate_boq(boq.pk)

        self.assertEqual(len(small_context), len(large_context))
        self.assertEqual(recalculated.total_cost, Decimal("880"))
        self.assertEqual(recalculated.total_revenue_before_tax, Decimal("4840"))
        self.assertEqual(recalculated.total_tax_amount, Decimal("532.4"))

    def test_boq_import_validates_then_creates_detail_lines_idempotently_by_action(self):
        boq = ConstructionBOQ.objects.create(project=self.project, number="BOQ-IMPORT", title="Imported BOQ")
        import_job = ConstructionBOQImportLog.objects.create(
            boq=boq,
            source_type="json",
            payload=[
                {"wbs_code": "1.1", "description": "Excavation", "unit": "m3", "quantity": "12", "unit_price": "50", "budget_cost": "35"},
                {"wbs_code": "1.2", "description": "Concrete", "unit": "m3", "quantity": "8", "unit_price": "100", "budget_cost": "70"},
            ],
            imported_by=self.user,
        )

        validate_response = self.client.post(
            f"/app/construction-boq-imports/{import_job.pk}/action/validate/"
        )
        self.assertEqual(validate_response.status_code, 302)
        import_job.refresh_from_db()
        self.assertEqual(import_job.state, ConstructionBOQImportLog.State.VALIDATED)

        import_response = self.client.post(
            f"/app/construction-boq-imports/{import_job.pk}/action/import/"
        )
        self.assertEqual(import_response.status_code, 302)
        import_job.refresh_from_db()
        self.assertEqual(import_job.state, ConstructionBOQImportLog.State.IMPORTED)
        self.assertEqual(boq.items.count(), 2)
