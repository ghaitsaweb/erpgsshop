from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse
from modules.organization.models import Branch

from .forms import PurchaseTeamForm, PurchaseTeamMemberForm
from .models import (
    PurchaseOrderControl,
    PurchaseOrderType,
    PurchaseProductCategory,
    PurchaseRequest,
    PurchaseSetting,
    PurchaseTeam,
    PurchaseTeamMember,
    VendorCategory,
    VendorProfile,
)
from .services import auto_route_purchase_orders, auto_route_purchase_requests, confirm_purchase_order


class PurchaseTeamRoutingParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="purchase-routing-admin",
            password="test-password",
            email="purchase-routing@example.com",
        )
        self.buyer = get_user_model().objects.create_user(
            username="purchase-routing-buyer",
            password="test-password",
        )
        self.company = Company.objects.create(code="PTR", name="Purchase Team Routing")
        self.other_company = Company.objects.create(code="PTR-OTHER", name="Other Routing Company")
        self.branch, _created = Branch.objects.get_or_create(
            company=self.company,
            code="PTR-MAIN",
            defaults={"name": "Main Routing Branch", "is_main": True},
        )
        self.category = PurchaseProductCategory.objects.create(
            company=self.company,
            code="PTR-HW",
            name="Hardware",
        )
        self.other_category = PurchaseProductCategory.objects.create(
            company=self.other_company,
            code="PTR-OTHER",
            name="Other Company Category",
        )
        self.order_type = PurchaseOrderType.objects.create(
            company=self.company,
            name="Goods",
        )
        self.order_type.product_categories.add(self.category)
        self.vendor_category = VendorCategory.objects.create(
            company=self.company,
            code="PTR-IT",
            name="IT Supplier",
        )
        self.vendor = Contact.objects.create(
            company=self.company,
            code="PTR-VENDOR",
            name="Routing Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor,
            category=self.vendor_category,
            status=VendorProfile.Status.ACTIVE,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="PTR-WH",
            name="Routing Warehouse",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="PTR-PRODUCT",
            name="Routing Product",
            purchase_price=Decimal("10"),
        )
        PurchaseSetting.objects.create(company=self.company, require_vendor_prequalification=False)

    def _team(self, name, *, parameter=PurchaseTeam.RoutingParameter.PRODUCT_CATEGORY):
        team = PurchaseTeam.objects.create(
            company=self.company,
            name=name,
            manager=self.actor,
            parameter=parameter,
        )
        if parameter == PurchaseTeam.RoutingParameter.PRODUCT_CATEGORY:
            team.product_categories.add(self.category)
        elif parameter == PurchaseTeam.RoutingParameter.ORDER_TYPE:
            team.order_types.add(self.order_type)
        elif parameter == PurchaseTeam.RoutingParameter.VENDOR_CATEGORY:
            team.vendor_categories.add(self.vendor_category)
        return team

    def _request(self, suffix, *, team=None):
        return PurchaseRequest.objects.create(
            company=self.company,
            order_type=self.order_type,
            team=team,
            number=f"PR-PTR-{suffix}",
            requestor=self.actor,
            request_date=timezone.localdate(),
            required_date=timezone.localdate() + timedelta(days=1),
            destination_warehouse=self.warehouse,
            justification="Team routing parity",
        )

    def _order(self, suffix, *, order_type=True):
        order = PurchaseOrder.objects.create(
            company=self.company,
            number=f"PO-PTR-{suffix}",
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=timezone.localdate(),
        )
        PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            quantity=Decimal("2"),
            unit_price=Decimal("10"),
        )
        if order_type:
            PurchaseOrderControl.objects.create(order=order, order_type=self.order_type)
        return order

    def test_team_lead_membership_and_member_category_rules(self):
        team = self._team("Lead Membership")
        self.assertTrue(PurchaseTeamMember.objects.filter(team=team, user=self.actor).exists())

        invalid_member = PurchaseTeamMemberForm(
            data={
                "team": team.pk,
                "user": self.buyer.pk,
                "role": "Buyer",
                "is_primary": True,
                "active": True,
            }
        )
        self.assertFalse(invalid_member.is_valid())
        self.assertIn("at least one assigned category", invalid_member.errors["is_primary"][0])

        valid_member = PurchaseTeamMemberForm(
            data={
                "team": team.pk,
                "user": self.buyer.pk,
                "role": "Buyer",
                "categories": [self.category.pk],
                "is_primary": True,
                "active": True,
            }
        )
        self.assertTrue(valid_member.is_valid(), valid_member.errors)
        valid_member.save()

        overlapping_member = PurchaseTeamMemberForm(
            data={
                "team": team.pk,
                "user": get_user_model().objects.create_user(username="overlap-buyer").pk,
                "role": "Buyer",
                "categories": [self.category.pk],
                "is_primary": False,
                "active": True,
            }
        )
        self.assertTrue(overlapping_member.is_valid(), overlapping_member.errors)

        other_company_team = PurchaseTeamForm(
            data={
                "company": self.company.pk,
                "name": "Invalid Cross Company Category",
                "manager": self.actor.pk,
                "parameter": PurchaseTeam.RoutingParameter.PRODUCT_CATEGORY,
                "product_categories": [self.other_category.pk],
                "active": True,
            }
        )
        self.assertFalse(other_company_team.is_valid())
        self.assertIn("must belong", other_company_team.errors["product_categories"][0])

    def test_request_routing_matches_categories_and_uses_newest_team_once(self):
        older = self._team("Older Hardware Team")
        newer = self._team("Newer Hardware Team")
        first = self._request("001")
        second = self._request("002")

        self.assertTrue(auto_route_purchase_requests([first, second], self.actor))

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.team, newer)
        self.assertEqual(second.team, older)

    def test_request_routing_skips_existing_inactive_and_other_company_teams(self):
        existing = self._team("Existing Team")
        inactive = self._team("Inactive Team")
        inactive.active = False
        inactive.save(update_fields=["active"])
        foreign = PurchaseTeam.objects.create(
            company=self.other_company,
            name="Foreign Team",
            manager=self.actor,
        )
        foreign.product_categories.add(self.other_category)
        request = self._request("EXISTING", team=existing)

        self.assertFalse(auto_route_purchase_requests(request, self.actor))
        request.refresh_from_db()
        self.assertEqual(request.team, existing)

    def test_order_routing_supports_order_type_product_and_vendor_parameters(self):
        product_team = self._team("Product Team")
        product_order = self._order("PRODUCT")
        self.assertTrue(auto_route_purchase_orders(product_order, self.actor))
        product_order.purchase_control.refresh_from_db()
        self.assertEqual(product_order.purchase_control.team, product_team)

        product_team.active = False
        product_team.save(update_fields=["active"])
        order_type_team = self._team(
            "Order Type Team",
            parameter=PurchaseTeam.RoutingParameter.ORDER_TYPE,
        )
        order_type_order = self._order("ORDER-TYPE")
        self.assertTrue(auto_route_purchase_orders(order_type_order, self.actor))
        order_type_order.purchase_control.refresh_from_db()
        self.assertEqual(order_type_order.purchase_control.team, order_type_team)

        order_type_team.active = False
        order_type_team.save(update_fields=["active"])
        vendor_team = self._team(
            "Vendor Category Team",
            parameter=PurchaseTeam.RoutingParameter.VENDOR_CATEGORY,
        )
        vendor_order = self._order("VENDOR", order_type=False)
        self.assertTrue(auto_route_purchase_orders(vendor_order, self.actor))
        self.assertEqual(vendor_order.purchase_control.team, vendor_team)

    def test_order_confirmation_routes_automatically_and_rolls_into_control(self):
        team = self._team("Confirmation Team", parameter=PurchaseTeam.RoutingParameter.ORDER_TYPE)
        order = self._order("CONFIRM")

        confirmed = confirm_purchase_order(order, self.actor)

        confirmed.refresh_from_db()
        confirmed.purchase_control.refresh_from_db()
        self.assertEqual(confirmed.status, PurchaseOrder.Status.CONFIRMED)
        self.assertEqual(confirmed.purchase_control.team, team)
        self.assertIsNotNone(confirmed.purchase_control.confirmed_at)

    def test_manual_document_action_preserves_request_status(self):
        team = self._team("Manual Route Team")
        purchase_request = self._request("MANUAL")
        self.client.force_login(self.actor)
        session = self.client.session
        session["selected_company_ids"] = [self.company.pk]
        session["active_company_id"] = self.company.pk
        session["selected_branch_ids"] = [self.branch.pk]
        session["active_branch_id"] = self.branch.pk
        session.save()

        detail = self.client.get(
            reverse("entity_edit", kwargs={"entity": "purchase-requests", "pk": purchase_request.pk})
        )
        self.assertContains(detail, "Auto Route to Purchase Team")
        self.assertContains(detail, 'name="team"')
        self.assertContains(detail, "Purchase routing")
        response = self.client.post(
            reverse(
                "document_action",
                kwargs={
                    "entity": "purchase-requests",
                    "pk": purchase_request.pk,
                    "action": "auto-route",
                },
            )
        )

        self.assertEqual(response.status_code, 302)
        purchase_request.refresh_from_db()
        self.assertEqual(purchase_request.status, PurchaseRequest.Status.DRAFT)
        self.assertEqual(purchase_request.team, team)

    def _routing_query_fixture(self, suffix, count):
        company = Company.objects.create(code=f"PTR-Q-{suffix}", name=f"Routing Query {suffix}")
        category = PurchaseProductCategory.objects.create(
            company=company,
            code=f"PTR-Q-C-{suffix}",
            name=f"Query Category {suffix}",
        )
        order_type = PurchaseOrderType.objects.create(company=company, name=f"Query Type {suffix}")
        order_type.product_categories.add(category)
        teams = []
        for index in range(count):
            team = PurchaseTeam.objects.create(
                company=company,
                name=f"Query Team {suffix} {index}",
                manager=self.actor,
            )
            team.product_categories.add(category)
            teams.append(team)
        requests = PurchaseRequest.objects.bulk_create(
            [
                PurchaseRequest(
                    company=company,
                    order_type=order_type,
                    number=f"PR-Q-{suffix}-{index}",
                    requestor=self.actor,
                    request_date=timezone.localdate(),
                    required_date=timezone.localdate(),
                    justification="Query growth",
                )
                for index in range(count)
            ]
        )
        return requests

    def test_request_routing_query_count_does_not_grow_with_batch_size(self):
        small = self._routing_query_fixture("SMALL", 2)
        with CaptureQueriesContext(connection) as baseline:
            auto_route_purchase_requests(small, self.actor)

        larger = self._routing_query_fixture("LARGE", 12)
        with CaptureQueriesContext(connection) as grown:
            auto_route_purchase_requests(larger, self.actor)

        self.assertLessEqual(len(grown), len(baseline) + 1)
