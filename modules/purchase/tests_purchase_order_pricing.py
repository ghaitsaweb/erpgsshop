from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.forms import inlineformset_factory
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse

from .line_merge import save_and_merge_purchase_document_lines
from .models import (
    PurchaseOrderLinePricingProvenance,
    PurchaseQuotationLine,
    PurchaseSetting,
    VendorPricelist,
    VendorPricelistItem,
    VendorPricelistTier,
    VendorProfile,
)
from .services import confirm_purchase_order, update_purchase_order_prices


ZERO = Decimal("0")


class PurchaseOrderPricingParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="po-pricing-admin",
            password="test-password",
            email="po-pricing@example.com",
        )
        self.company = Company.objects.create(code="PO-PRICE", name="PO Pricing")
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="PO-PRICE-WH",
            name="PO Pricing Warehouse",
        )
        self.vendor = Contact.objects.create(
            company=self.company,
            code="PO-PRICE-VENDOR",
            name="PO Pricing Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.profile = VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor,
            status=VendorProfile.Status.ACTIVE,
        )
        self.setting = PurchaseSetting.objects.create(
            company=self.company,
            require_vendor_prequalification=False,
        )
        self.pricelist = VendorPricelist.objects.create(
            company=self.company,
            vendor=self.profile,
            name="PO direct prices",
            status=VendorPricelist.Status.ACTIVE,
            date_end=timezone.localdate() + timedelta(days=30),
        )

    def _product(self, code):
        return Product.objects.create(
            company=self.company,
            sku=code,
            name=code.replace("-", " ").title(),
            purchase_price=Decimal("1"),
        )

    def _order(self, number):
        return PurchaseOrder.objects.create(
            company=self.company,
            number=number,
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=timezone.localdate(),
            status=PurchaseOrder.Status.DRAFT,
        )

    def _priced_item(self, product, bonus=None):
        item = VendorPricelistItem.objects.create(
            pricelist=self.pricelist,
            product=product,
            unit_price=Decimal("6000"),
            lead_time_days=Decimal("1"),
        )
        tier = VendorPricelistTier.objects.create(
            item=item,
            sequence=1,
            minimum_quantity=Decimal("1"),
            tier_price=Decimal("5500"),
            discount_formula="10%",
            free_product=bonus,
            free_quantity=Decimal("1") if bonus else ZERO,
        )
        return item, tier

    def test_manual_po_form_save_applies_pricelist_and_free_benefit(self):
        product = self._product("PO-FORM-PRODUCT")
        bonus = self._product("PO-FORM-BONUS")
        _item, tier = self._priced_item(product, bonus)
        order = self._order("PO-FORM-PRICE")
        formset_class = inlineformset_factory(
            PurchaseOrder,
            PurchaseOrderLine,
            fields=["product", "description", "warehouse", "quantity", "unit_price"],
            extra=1,
            can_delete=True,
        )
        formset = formset_class(
            {
                "document_lines-TOTAL_FORMS": "1",
                "document_lines-INITIAL_FORMS": "0",
                "document_lines-MIN_NUM_FORMS": "0",
                "document_lines-MAX_NUM_FORMS": "1000",
                "document_lines-0-product": str(product.pk),
                "document_lines-0-description": "Direct priced product",
                "document_lines-0-warehouse": str(self.warehouse.pk),
                "document_lines-0-quantity": "30",
                "document_lines-0-unit_price": "0",
            },
            instance=order,
            prefix="document_lines",
        )
        self.assertTrue(formset.is_valid(), formset.errors)

        save_and_merge_purchase_document_lines(order, formset)

        paid_provenance = PurchaseOrderLinePricingProvenance.objects.get(
            order_line__order=order,
            free_product=False,
        )
        free_provenance = PurchaseOrderLinePricingProvenance.objects.get(
            order_line__order=order,
            free_product=True,
        )
        self.assertEqual(paid_provenance.order_line.unit_price, Decimal("4950"))
        self.assertEqual(paid_provenance.price_source, PurchaseQuotationLine.PriceSource.PRICELIST)
        self.assertEqual(paid_provenance.applied_pricelist_tier, tier)
        self.assertIsNone(paid_provenance.quotation_line)
        self.assertEqual(free_provenance.order_line.product, bonus)
        self.assertEqual(free_provenance.order_line.quantity, Decimal("1"))
        self.assertEqual(free_provenance.order_line.unit_price, ZERO)
        self.assertEqual(free_provenance.free_product_source_order_line, paid_provenance.order_line)

    def test_manual_po_price_survives_quantity_change_and_matching_value_restores_tier(self):
        product = self._product("PO-MANUAL-PRODUCT")
        bonus = self._product("PO-MANUAL-BONUS")
        _item, tier = self._priced_item(product, bonus)
        order = self._order("PO-MANUAL-PRICE")
        line = PurchaseOrderLine.objects.create(
            order=order,
            product=product,
            warehouse=self.warehouse,
            quantity=Decimal("5"),
            unit_price=Decimal("777"),
        )

        update_purchase_order_prices(
            order,
            line_ids={line.pk},
            manual_price_line_ids={line.pk},
        )
        provenance = line.purchase_pricing_provenance
        self.assertEqual(provenance.price_source, PurchaseQuotationLine.PriceSource.MANUAL)
        self.assertFalse(PurchaseOrderLinePricingProvenance.objects.filter(order_line__order=order, free_product=True).exists())

        line.quantity = Decimal("20")
        line.save(update_fields=["quantity", "updated_at"])
        update_purchase_order_prices(order, line_ids={line.pk})
        line.refresh_from_db()
        self.assertEqual(line.unit_price, Decimal("777"))
        self.assertEqual(line.purchase_pricing_provenance.price_source, PurchaseQuotationLine.PriceSource.MANUAL)

        line.unit_price = Decimal("4950")
        line.save(update_fields=["unit_price", "updated_at"])
        update_purchase_order_prices(
            order,
            line_ids={line.pk},
            manual_price_line_ids={line.pk},
        )
        line.refresh_from_db()
        self.assertEqual(line.purchase_pricing_provenance.price_source, PurchaseQuotationLine.PriceSource.PRICELIST)
        self.assertEqual(line.purchase_pricing_provenance.applied_pricelist_tier, tier)
        benefit = PurchaseOrderLinePricingProvenance.objects.get(order_line__order=order, free_product=True)
        self.assertEqual(benefit.order_line.product, bonus)

    def test_generated_free_benefit_is_excluded_from_confirmation_eligibility(self):
        self.setting.restrict_product_by_vendor_pricelist = True
        self.setting.save(update_fields=["restrict_product_by_vendor_pricelist", "updated_at"])
        product = self._product("PO-CONFIRM-PRODUCT")
        bonus = self._product("PO-CONFIRM-BONUS")
        self._priced_item(product, bonus)
        order = self._order("PO-CONFIRM-FREE")
        line = PurchaseOrderLine.objects.create(
            order=order,
            product=product,
            warehouse=self.warehouse,
            quantity=Decimal("5"),
            unit_price=ZERO,
        )
        update_purchase_order_prices(order, line_ids={line.pk})

        confirm_purchase_order(order, self.actor)

        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)
        self.assertEqual(
            PurchaseOrderLinePricingProvenance.objects.filter(
                order_line__order=order,
                free_product=True,
            ).count(),
            1,
        )

    def test_enforced_moq_warns_in_draft_preserves_manual_price_and_blocks_confirmation(self):
        product = self._product("PO-MOQ-ENFORCED")
        item = VendorPricelistItem.objects.create(
            pricelist=self.pricelist,
            product=product,
            unit_price=Decimal("200"),
            lead_time_days=Decimal("1"),
            enforce_moq=True,
        )
        VendorPricelistTier.objects.create(
            item=item,
            sequence=1,
            minimum_quantity=Decimal("10"),
            tier_price=Decimal("175"),
        )
        order = self._order("PO-MOQ-ENFORCED")
        line = PurchaseOrderLine.objects.create(
            order=order,
            product=product,
            warehouse=self.warehouse,
            quantity=Decimal("5"),
            unit_price=Decimal("99"),
        )

        update_purchase_order_prices(
            order,
            line_ids={line.pk},
            manual_price_line_ids={line.pk},
        )

        line.refresh_from_db()
        provenance = line.purchase_pricing_provenance
        control = order.purchase_control
        self.assertEqual(line.unit_price, Decimal("99"))
        self.assertEqual(provenance.price_source, PurchaseQuotationLine.PriceSource.MANUAL)
        self.assertEqual(provenance.applicable_vendor_moq, Decimal("10"))
        self.assertTrue(provenance.is_below_vendor_moq)
        self.assertIn("minimum order quantity of 10", provenance.moq_warning_message)
        self.assertEqual(control.vendor_moq_warning_message, provenance.moq_warning_message)
        with self.assertRaisesRegex(ValidationError, "below the vendor MOQ"):
            confirm_purchase_order(order, self.actor)

        line.quantity = Decimal("10")
        line.save(update_fields=["quantity", "updated_at"])
        update_purchase_order_prices(order, line_ids={line.pk})
        line.refresh_from_db()
        order.purchase_control.refresh_from_db()
        self.assertEqual(line.unit_price, Decimal("99"))
        self.assertFalse(line.purchase_pricing_provenance.is_below_vendor_moq)
        self.assertEqual(order.purchase_control.vendor_moq_warning_message, "")

        confirm_purchase_order(order, self.actor)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)

    def test_disabled_moq_below_first_tier_remains_no_match_without_warning(self):
        product = self._product("PO-MOQ-DISABLED")
        item = VendorPricelistItem.objects.create(
            pricelist=self.pricelist,
            product=product,
            unit_price=Decimal("200"),
            lead_time_days=Decimal("1"),
            enforce_moq=False,
        )
        VendorPricelistTier.objects.create(
            item=item,
            sequence=1,
            minimum_quantity=Decimal("10"),
            tier_price=Decimal("175"),
        )
        order = self._order("PO-MOQ-DISABLED")
        line = PurchaseOrderLine.objects.create(
            order=order,
            product=product,
            warehouse=self.warehouse,
            quantity=Decimal("5"),
            unit_price=ZERO,
        )

        update_purchase_order_prices(order, line_ids={line.pk})

        line.refresh_from_db()
        provenance = line.purchase_pricing_provenance
        self.assertEqual(line.unit_price, ZERO)
        self.assertEqual(provenance.price_source, PurchaseQuotationLine.PriceSource.NO_MATCH)
        self.assertEqual(provenance.applicable_vendor_moq, ZERO)
        self.assertFalse(provenance.is_below_vendor_moq)
        self.assertEqual(order.purchase_control.vendor_moq_warning_message, "")

    def test_enforced_moq_uses_first_tier_sequence_while_pricing_matching_tier(self):
        product = self._product("PO-MOQ-SEQUENCE")
        item = VendorPricelistItem.objects.create(
            pricelist=self.pricelist,
            product=product,
            unit_price=Decimal("250"),
            lead_time_days=Decimal("1"),
            enforce_moq=True,
        )
        VendorPricelistTier.objects.create(
            item=item,
            sequence=1,
            minimum_quantity=Decimal("20"),
            maximum_quantity=Decimal("1000000"),
            tier_price=Decimal("200"),
        )
        matching_tier = VendorPricelistTier.objects.create(
            item=item,
            sequence=2,
            minimum_quantity=Decimal("10"),
            maximum_quantity=Decimal("19"),
            tier_price=Decimal("175"),
        )
        order = self._order("PO-MOQ-SEQUENCE")
        line = PurchaseOrderLine.objects.create(
            order=order,
            product=product,
            warehouse=self.warehouse,
            quantity=Decimal("15"),
            unit_price=ZERO,
        )

        update_purchase_order_prices(order, line_ids={line.pk})

        line.refresh_from_db()
        provenance = line.purchase_pricing_provenance
        self.assertEqual(line.unit_price, Decimal("175"))
        self.assertEqual(provenance.applied_pricelist_tier, matching_tier)
        self.assertEqual(provenance.applicable_vendor_moq, Decimal("20"))
        self.assertTrue(provenance.is_below_vendor_moq)

    def test_po_price_refresh_query_count_does_not_grow_with_lines(self):
        bonus = self._product("PO-PRICE-SCALE-BONUS")
        small_products = [self._product(f"PO-PRICE-SMALL-{index}") for index in range(2)]
        large_products = [self._product(f"PO-PRICE-LARGE-{index}") for index in range(12)]
        items = VendorPricelistItem.objects.bulk_create(
            [
                VendorPricelistItem(
                    pricelist=self.pricelist,
                    product=product,
                    unit_price=Decimal("30"),
                    lead_time_days=Decimal("1"),
                )
                for product in small_products + large_products
            ]
        )
        VendorPricelistTier.objects.bulk_create(
            [
                VendorPricelistTier(
                    item=item,
                    sequence=1,
                    minimum_quantity=Decimal("1"),
                    tier_price=Decimal("25"),
                    free_product=bonus,
                    free_quantity=Decimal("1"),
                )
                for item in items
            ]
        )
        small_order = self._order("PO-PRICE-Q-SMALL")
        large_order = self._order("PO-PRICE-Q-LARGE")
        small_lines = PurchaseOrderLine.objects.bulk_create(
            [
                PurchaseOrderLine(
                    order=small_order,
                    product=product,
                    warehouse=self.warehouse,
                    quantity=Decimal("2"),
                    unit_price=ZERO,
                )
                for product in small_products
            ]
        )
        large_lines = PurchaseOrderLine.objects.bulk_create(
            [
                PurchaseOrderLine(
                    order=large_order,
                    product=product,
                    warehouse=self.warehouse,
                    quantity=Decimal("2"),
                    unit_price=ZERO,
                )
                for product in large_products
            ]
        )

        with CaptureQueriesContext(connection) as small_queries:
            update_purchase_order_prices(small_order, line_ids={line.pk for line in small_lines})
        with CaptureQueriesContext(connection) as large_queries:
            update_purchase_order_prices(large_order, line_ids={line.pk for line in large_lines})

        self.assertEqual(
            PurchaseOrderLinePricingProvenance.objects.filter(
                order_line__order=small_order,
                free_product=True,
            ).count(),
            2,
        )
        self.assertEqual(
            PurchaseOrderLinePricingProvenance.objects.filter(
                order_line__order=large_order,
                free_product=True,
            ).count(),
            12,
        )
        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"PO price-refresh queries grew from {len(small_queries)} to {len(large_queries)}.",
        )
