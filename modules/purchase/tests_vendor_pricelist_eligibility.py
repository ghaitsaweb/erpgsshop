from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.forms import modelform_factory
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse

from .access import scope_purchase_form
from .models import (
    BlanketOrder,
    BlanketOrderLine,
    PurchaseSetting,
    VendorPricelist,
    VendorPricelistItem,
    VendorProfile,
)
from .services import confirm_purchase_order, transition_blanket_order
from .vendor_pricelist_eligibility import validate_vendor_pricelist_product_eligibility


class VendorPricelistEligibilityTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.company = Company.objects.create(code="VPE", name="Vendor Product Eligibility")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="VPE-VENDOR",
            name="Eligible Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.profile = VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor,
            registration_number="VPE-VENDOR",
            status=VendorProfile.Status.ACTIVE,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="VPE-WH",
            name="Eligibility Warehouse",
        )
        self.eligible_product = Product.objects.create(
            company=self.company,
            sku="VPE-OK",
            name="Pricelist Product",
            product_type=Product.ProductType.STOCKABLE,
        )
        self.invalid_product = Product.objects.create(
            company=self.company,
            sku="VPE-NO",
            name="Outside Pricelist",
            product_type=Product.ProductType.STOCKABLE,
        )
        self.setting = PurchaseSetting.objects.create(
            company=self.company,
            restrict_product_by_vendor_pricelist=True,
        )
        self.pricelist = VendorPricelist.objects.create(
            company=self.company,
            name="Active Vendor Pricelist",
            code="VPE-PL",
            vendor=self.profile,
            date_start=self.today - timedelta(days=1),
            date_end=self.today + timedelta(days=1),
            status=VendorPricelist.Status.ACTIVE,
            active=True,
        )
        VendorPricelistItem.objects.create(
            pricelist=self.pricelist,
            product=self.eligible_product,
            unit_price=Decimal("10"),
        )

    def _order(self, number, products):
        order = PurchaseOrder.objects.create(
            company=self.company,
            number=number,
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=self.today,
        )
        lines = PurchaseOrderLine.objects.bulk_create(
            [
                PurchaseOrderLine(
                    order=order,
                    product=product,
                    quantity=Decimal("1"),
                    unit_price=Decimal("10"),
                )
                for product in products
            ]
        )
        return order, lines

    def test_confirmation_rejects_product_outside_active_vendor_pricelist(self):
        order, _lines = self._order("PO-VPE-INVALID", [self.invalid_product])

        with self.assertRaisesMessage(ValidationError, "not available in an active vendor pricelist"):
            confirm_purchase_order(order)

        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.DRAFT)

    def test_valid_item_allows_confirmation_and_disabled_setting_allows_fallback(self):
        valid_order, _lines = self._order("PO-VPE-VALID", [self.eligible_product])
        confirm_purchase_order(valid_order)
        valid_order.refresh_from_db()
        self.assertEqual(valid_order.status, PurchaseOrder.Status.CONFIRMED)

        self.setting.restrict_product_by_vendor_pricelist = False
        self.setting.save(update_fields=["restrict_product_by_vendor_pricelist", "updated_at"])
        fallback_order, _lines = self._order("PO-VPE-FALLBACK", [self.invalid_product])
        confirm_purchase_order(fallback_order)
        fallback_order.refresh_from_db()
        self.assertEqual(fallback_order.status, PurchaseOrder.Status.CONFIRMED)

    def test_blanket_order_submit_uses_same_company_vendor_and_date_contract(self):
        blanket = BlanketOrder.objects.create(
            company=self.company,
            number="BO-VPE",
            vendor=self.vendor,
            warehouse=self.warehouse,
            date_start=self.today,
            date_end=self.today + timedelta(days=30),
        )
        BlanketOrderLine.objects.create(
            blanket_order=blanket,
            product=self.invalid_product,
            agreed_quantity=Decimal("10"),
            unit_price=Decimal("10"),
        )

        with self.assertRaisesMessage(ValidationError, "Outside Pricelist"):
            transition_blanket_order(blanket, "submit")

        blanket.refresh_from_db()
        self.assertEqual(blanket.status, BlanketOrder.Status.DRAFT)

    def test_purchase_line_form_limits_product_choices_for_selected_vendor(self):
        order, _lines = self._order("PO-VPE-FORM", [])
        line_form_class = modelform_factory(PurchaseOrderLine, fields=["product"])
        form = line_form_class(instance=PurchaseOrderLine(order=order))
        user = get_user_model().objects.create_superuser(
            username="vendor-product-filter-admin",
            password="test-password",
            email="vendor-product-filter@example.com",
        )

        scope_purchase_form(form, user, "purchases")

        self.assertEqual(
            list(form.fields["product"].queryset.order_by("pk").values_list("pk", flat=True)),
            [self.eligible_product.pk],
        )

    def test_validation_query_count_does_not_grow_with_line_count(self):
        extra_products = Product.objects.bulk_create(
            [
                Product(
                    company=self.company,
                    sku=f"VPE-Q-{index}",
                    name=f"Eligible Query Product {index}",
                    product_type=Product.ProductType.STOCKABLE,
                )
                for index in range(12)
            ]
        )
        VendorPricelistItem.objects.bulk_create(
            [
                VendorPricelistItem(
                    pricelist=self.pricelist,
                    product=product,
                    unit_price=Decimal("10"),
                )
                for product in extra_products
            ]
        )
        small, small_lines = self._order("PO-VPE-Q2", extra_products[:2])
        large, large_lines = self._order("PO-VPE-Q12", extra_products)

        with CaptureQueriesContext(connection) as small_queries:
            validate_vendor_pricelist_product_eligibility(
                document=small,
                lines=small_lines,
                transaction_date=small.order_date,
            )
        with CaptureQueriesContext(connection) as large_queries:
            validate_vendor_pricelist_product_eligibility(
                document=large,
                lines=large_lines,
                transaction_date=large.order_date,
            )

        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Vendor product eligibility queries grew from {len(small_queries)} to {len(large_queries)}.",
        )
