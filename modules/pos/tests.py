from decimal import Decimal
from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Contact, Employee, Product, Warehouse
from modules.apphub.models import ERPApp
from modules.apphub.workspaces import app_menu_groups, iter_menu_items

from .dashboard import FNB_FEATURES, POS_FEATURES, build_pos_analytics
from .access import POS_MANAGER, scope_pos_queryset
from .entity_config import POS_DOCUMENT_CONFIG, POS_ENTITY_CONFIG
from .models import POSOrder, POSOrderLine, POSRegister, POSSession, RestaurantTable
from .retail_models import (
    POSComboCategory,
    POSPricelist,
    POSProfile,
    POSPromotion,
    POSReservation,
)


class RetailFNBParityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser("pos-admin", "pos@example.com", "secret")
        cls.company = Company.objects.create(code="RTL", name="Retail Company")
        cls.warehouse = Warehouse.objects.create(company=cls.company, code="MAIN", name="Main")
        cls.customer = Contact.objects.create(company=cls.company, code="CUST", name="Customer")
        cls.product = Product.objects.create(company=cls.company, sku="MENU-1", name="Menu", sale_price=Decimal("50"), purchase_price=Decimal("20"))
        cls.profile = POSProfile.objects.create(company=cls.company, code="POS", name="Main POS", warehouse=cls.warehouse, profile_type="pos_fnb", use_area_table=True)
        cls.register = POSRegister.objects.create(company=cls.company, profile=cls.profile, code="REG", name="Register", warehouse=cls.warehouse, register_type="pos_fnb")
        cls.session = POSSession.objects.create(register=cls.register, opened_by=cls.user, opening_cash=Decimal("100"))

    def test_full_entity_catalog_and_detail_documents_are_registered(self):
        self.assertGreaterEqual(len(POS_ENTITY_CONFIG), 70)
        for entity in (
            "pos-profiles", "pos-stations", "pos-devices", "pos-pricelists", "pos-promotions",
            "pos-receipt-templates", "loyalty-wallets", "pos-reservations", "kitchen-tickets",
            "employee-meal-programs", "pos-internal-usages", "pos-outlet-orders", "pos-membership-themes",
        ):
            self.assertIn(entity, POS_ENTITY_CONFIG)
        for document in (
            "pos-orders", "pos-pricelists", "pos-boms", "pos-campaigns", "pos-reservations",
            "kitchen-tickets", "employee-meal-programs", "employee-meal-records",
            "pos-internal-usages", "pos-bank-payments", "pos-outlet-orders",
        ):
            self.assertIn(document, POS_DOCUMENT_CONFIG)

    def test_hmx_intelligence_and_fnb_reporting_menu_surfaces_exist(self):
        pos_features = {
            item["feature"]
            for group in app_menu_groups("pos")
            for item in iter_menu_items(group["items"])
            if item.get("feature")
        }
        self.assertTrue(POS_FEATURES.issubset(pos_features))
        fnb_features = {
            item["feature"]
            for group in app_menu_groups("pos-fnb")
            for item in iter_menu_items(group["items"])
            if item.get("feature")
        }
        self.assertEqual(fnb_features, {"other-fnb-orders", "menu-engineering", "employee-meals"})

    def test_hmx_pos_active_menu_hierarchy_is_preserved(self):
        groups = app_menu_groups("pos")
        self.assertEqual(
            [group["label"] for group in groups],
            [
                "Dashboard", "Orders", "Pricing & Promotions", "Master Data", "Trends",
                "Reporting", "FNB Reporting", "Log", "Member", "Customer Categorization",
                "Configuration", "Intelligence",
            ],
        )
        labels = {group["label"]: [item["label"] for item in iter_menu_items(group["items"])] for group in groups}
        self.assertEqual(labels["Orders"], ["POS Order", "Pre-Orders", "Other FNB Order", "Kitchen Order Tickets", "Reservation Orders", "Sessions", "Outlet Orders"])
        self.assertEqual(labels["Master Data"], ["PoS Product Category", "Home Delivery Fee", "Product", "Product Variant", "Attributes", "Product Bundle", "Product Combo", "POS BOM", "POS BOM Options", "POS Users"])
        self.assertEqual(labels["FNB Reporting"], ["Menu Engineering", "Employee Meal Program"])
        self.assertNotIn("Sales", labels["Intelligence"])
        self.assertNotIn("Products", labels["Intelligence"])
        self.assertNotIn("Sessions", labels["Intelligence"])

    def test_preorder_lifecycle_requires_fulfillment_and_return_source_pair(self):
        preorder = POSOrder(
            session=self.session,
            profile=self.profile,
            number="PRE-001",
            is_preorder=True,
        )
        with self.assertRaises(ValidationError):
            preorder.full_clean()
        preorder.fulfillment_date = timezone.localdate() + timedelta(days=1)
        preorder.full_clean()

        returned = POSOrder(session=self.session, profile=self.profile, number="RET-001", return_type="return")
        with self.assertRaises(ValidationError):
            returned.full_clean()

    def test_order_line_detail_recalculates_header_totals(self):
        order = POSOrder.objects.create(
            session=self.session,
            profile=self.profile,
            number="POS-001",
            customer=self.customer,
            order_type=POSOrder.OrderType.RETAIL,
            discount_amount=Decimal("2"),
            service_charge_amount=Decimal("3"),
            takeaway_fee_amount=Decimal("1"),
            tax_amount=Decimal("4"),
        )
        POSOrderLine.objects.create(
            order=order,
            product=self.product,
            quantity=Decimal("2"),
            unit_price=Decimal("50"),
            discount_percent=Decimal("10"),
            discount_amount=Decimal("5"),
            tax_amount=Decimal("2"),
        )
        order.refresh_from_db()
        self.assertEqual(order.subtotal_amount, Decimal("87"))
        self.assertEqual(order.total, Decimal("93"))

    def test_dine_in_order_requires_table(self):
        order = POSOrder(session=self.session, number="POS-002", order_type=POSOrder.OrderType.DINE_IN)
        with self.assertRaises(ValidationError):
            order.full_clean()

    def test_table_must_belong_to_session_register(self):
        other_register = POSRegister.objects.create(company=self.company, code="OTHER", name="Other", warehouse=self.warehouse)
        table = RestaurantTable.objects.create(register=other_register, name="T1")
        order = POSOrder(session=self.session, number="POS-003", order_type=POSOrder.OrderType.DINE_IN, restaurant_table=table)
        with self.assertRaises(ValidationError):
            order.full_clean()

    def test_profile_rejects_warehouse_from_another_company(self):
        other_company = Company.objects.create(code="OTH", name="Other")
        other_warehouse = Warehouse.objects.create(company=other_company, code="OTH", name="Other")
        profile = POSProfile(company=self.company, code="BAD", name="Bad", warehouse=other_warehouse)
        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_pricelist_rejects_reversed_validity(self):
        pricelist = POSPricelist(company=self.company, code="PL", name="Pricelist", valid_from=timezone.localdate(), valid_until=timezone.localdate() - timedelta(days=1))
        with self.assertRaises(ValidationError):
            pricelist.full_clean()

    def test_manual_only_promotion_controls(self):
        promotion = POSPromotion(company=self.company, code="BDAY", name="Birthday", usage="automatic", birthday_promotion=True)
        with self.assertRaises(ValidationError):
            promotion.full_clean()

    def test_combo_pick_limits_are_validated(self):
        category = POSComboCategory(name="Sides", minimum_pick=2, maximum_pick=1)
        with self.assertRaises(ValidationError):
            category.full_clean(exclude=["combo"])

    def test_reservation_requires_customer_or_guest_name(self):
        reservation = POSReservation(
            reference="RSV-1", profile=self.profile, reservation_date=timezone.localdate(),
            start_time=time(12, 0), area_id=None,
        )
        with self.assertRaises(ValidationError):
            reservation.full_clean(exclude=["area"])

    def test_pos_analytics_query_count_does_not_grow_with_orders(self):
        def create_orders(start, count):
            POSOrder.objects.bulk_create([
                POSOrder(session=self.session, profile=self.profile, number=f"Q-{index:03d}")
                for index in range(start, start + count)
            ])

        create_orders(0, 2)
        with CaptureQueriesContext(connection) as small:
            build_pos_analytics("overview", self.user)
        create_orders(2, 10)
        with CaptureQueriesContext(connection) as large:
            build_pos_analytics("overview", self.user)
        self.assertEqual(len(small), len(large))

    def test_company_scope_is_preserved_for_pos_records(self):
        operator = get_user_model().objects.create_user("operator", password="secret")
        operator.groups.add(Group.objects.get_or_create(name=POS_MANAGER)[0])
        Employee.objects.create(company=self.company, user=operator, employee_id="POS-EMP", name="Operator")
        other_company = Company.objects.create(code="OTHER", name="Other Company")
        other_warehouse = Warehouse.objects.create(company=other_company, code="MAIN", name="Other Main")
        POSProfile.objects.create(company=other_company, code="OTHER", name="Other POS", warehouse=other_warehouse)
        visible = scope_pos_queryset(operator, "pos-profiles", POSProfile.objects.all())
        self.assertEqual(list(visible.values_list("code", flat=True)), ["POS"])

    def test_retail_and_fnb_analytics_routes_render(self):
        ERPApp.objects.create(slug="pos", name="Point of Sale", category="Retail & F&B", enabled=True, status="stable", route="entity_list:pos-orders")
        ERPApp.objects.create(slug="pos-fnb", name="POS F&B", category="Retail & F&B", enabled=True, status="stable", route="entity_list:restaurant-tables")
        self.client.force_login(self.user)
        for feature in POS_FEATURES:
            with self.subTest(feature=feature):
                self.assertEqual(self.client.get(reverse("pos_feature", args=[feature])).status_code, 200)
        for feature in FNB_FEATURES:
            with self.subTest(feature=feature):
                self.assertEqual(self.client.get(reverse("pos_fnb_feature", args=[feature])).status_code, 200)
