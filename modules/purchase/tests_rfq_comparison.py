from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse
from modules.finance.models import CurrencyRate

from .models import (
    GoodsReceipt,
    PurchaseOrderControl,
    PurchaseOrderLinePricingProvenance,
    PurchaseQuotation,
    PurchaseQuotationLine,
    PurchaseSetting,
    VendorProfile,
)
from .rfq_comparison import compare_purchase_quotations


class RFQComparisonParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="rfq-comparison-admin",
            password="test-password",
            email="rfq-comparison@example.com",
        )
        self.company = Company.objects.create(
            code="RFQC",
            name="RFQ Comparison",
            currency="IDR",
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="RFQC-WH",
            name="RFQ Comparison Warehouse",
        )
        PurchaseSetting.objects.create(
            company=self.company,
            require_vendor_prequalification=False,
        )
        self.vendors = [
            Contact.objects.create(
                company=self.company,
                code=f"RFQC-V-{index}",
                name=f"RFQ Vendor {index}",
                contact_type=Contact.ContactType.VENDOR,
                email=f"vendor-{index}@example.com",
                phone=f"0800000{index:02d}",
            )
            for index in range(12)
        ]
        self.products = [
            Product.objects.create(
                company=self.company,
                sku=f"RFQC-P-{index}",
                name=f"RFQ Product {index}",
                unit="Unit",
                purchase_price=Decimal("100"),
            )
            for index in range(3)
        ]

    def _quotation(self, number, vendor, specs, *, currency="IDR", status=PurchaseQuotation.Status.DRAFT):
        quotation = PurchaseQuotation.objects.create(
            company=self.company,
            number=number,
            vendor=vendor,
            warehouse=self.warehouse,
            issue_date=timezone.localdate(),
            response_deadline=timezone.localdate() + timedelta(days=5),
            expected_date=timezone.localdate() + timedelta(days=7),
            currency=currency,
            status=status,
        )
        PurchaseQuotationLine.objects.bulk_create(
            [
                PurchaseQuotationLine(
                    quotation=quotation,
                    product=spec["product"],
                    description=spec["product"].name,
                    quantity=spec.get("quantity", Decimal("1")),
                    unit_price=spec.get("price", Decimal("1")),
                    discount_percent=spec.get("discount", Decimal("0")),
                    tax_percent=spec.get("tax", Decimal("0")),
                    promised_date=quotation.expected_date,
                    expiry_date=quotation.response_deadline,
                )
                for spec in specs
            ]
        )
        return quotation

    def test_comparison_requires_two_rfqs_and_one_shared_product(self):
        first = self._quotation(
            "RFQ-CMP-ONE",
            self.vendors[0],
            [{"product": self.products[0], "price": Decimal("100")}],
        )
        with self.assertRaisesMessage(ValidationError, "at least two RFQs"):
            compare_purchase_quotations([first.pk])

        second = self._quotation(
            "RFQ-CMP-NO-MATCH",
            self.vendors[1],
            [{"product": self.products[1], "price": Decimal("90")}],
        )
        with self.assertRaisesMessage(ValidationError, "No matching products found"):
            compare_purchase_quotations([first.pk, second.pk])

    def test_payload_highlights_prices_and_keeps_unquoted_products(self):
        VendorProfile.objects.create(
            company=self.company,
            partner=self.vendors[0],
            rating=Decimal("4"),
            status=VendorProfile.Status.ACTIVE,
        )
        historical = PurchaseOrder.objects.create(
            company=self.company,
            number="PO-RFQC-HISTORY",
            vendor=self.vendors[0],
            warehouse=self.warehouse,
            order_date=timezone.localdate(),
            status=PurchaseOrder.Status.CONFIRMED,
        )
        PurchaseOrderLine.objects.create(
            order=historical,
            product=self.products[0],
            warehouse=self.warehouse,
            quantity=Decimal("1"),
            received_quantity=Decimal("1"),
            unit_price=Decimal("100"),
        )
        GoodsReceipt.objects.create(
            company=self.company,
            number="GR-RFQC-HISTORY",
            order=historical,
            warehouse=self.warehouse,
            receipt_date=timezone.localdate(),
            delivery_status=GoodsReceipt.DeliveryStatus.ON_TIME,
            status=GoodsReceipt.Status.RECEIVED,
        )
        first = self._quotation(
            "RFQ-CMP-A",
            self.vendors[0],
            [
                {
                    "product": self.products[0],
                    "price": Decimal("145"),
                    "quantity": Decimal("2"),
                    "discount": Decimal("5"),
                },
                {"product": self.products[1], "price": Decimal("90")},
            ],
        )
        second = self._quotation(
            "RFQ-CMP-B",
            self.vendors[1],
            [{"product": self.products[0], "price": Decimal("130"), "quantity": Decimal("2")}],
            status=PurchaseQuotation.Status.SENT,
        )
        third = self._quotation(
            "RFQ-CMP-C",
            self.vendors[2],
            [
                {"product": self.products[0], "price": Decimal("160"), "quantity": Decimal("2")},
                {"product": self.products[2], "price": Decimal("75")},
            ],
            status=PurchaseQuotation.Status.RECEIVED,
        )

        payload = compare_purchase_quotations([first.pk, second.pk, third.pk])

        self.assertEqual(payload["title"], "RFQ Comparison")
        self.assertEqual(payload["selected_count"], 3)
        self.assertEqual(payload["total_vendors"], 3)
        self.assertEqual(payload["total_products"], 3)
        self.assertEqual(payload["columns"][0]["code"], first.number)
        self.assertEqual(payload["columns"][0]["delivery_on_schedule"]["display"], "100%")
        self.assertEqual(payload["columns"][0]["delivery_fulfillment"]["display"], "100%")
        self.assertEqual(payload["columns"][0]["star_rating"]["display"], "4")
        product_a = next(row for row in payload["product_rows"] if row["product_id"] == self.products[0].pk)
        self.assertEqual([cell["highlight"] for cell in product_a["cells"]], ["", "best", "highest"])
        self.assertEqual(product_a["cells"][0]["discount"], "5%")
        self.assertTrue(product_a["cells"][0]["line_id"])
        product_b = next(row for row in payload["product_rows"] if row["product_id"] == self.products[1].pk)
        self.assertEqual([cell["quoted"] for cell in product_b["cells"]], [True, False, False])

    def test_equal_prices_are_not_highlighted_and_currency_is_converted(self):
        CurrencyRate.objects.create(
            company=self.company,
            currency="USD",
            rate_date=timezone.localdate(),
            rate=Decimal("15000"),
            source="Comparison test",
        )
        first = self._quotation(
            "RFQ-CMP-IDR",
            self.vendors[0],
            [{"product": self.products[0], "price": Decimal("150000")}],
        )
        second = self._quotation(
            "RFQ-CMP-USD",
            self.vendors[1],
            [{"product": self.products[0], "price": Decimal("10")}],
            currency="USD",
        )

        payload = compare_purchase_quotations([first.pk, second.pk])
        cells = payload["product_rows"][0]["cells"]

        self.assertTrue(payload["has_mixed_currencies"])
        self.assertEqual(cells[1]["unit_price"]["amount"], Decimal("150000"))
        self.assertTrue(cells[1]["unit_price"]["converted"])
        self.assertIn("USD", cells[1]["unit_price"]["secondary"])
        self.assertEqual([cell["highlight"] for cell in cells], ["", ""])
        self.assertEqual([cell["badges"] for cell in cells], [[], []])

    def test_bulk_action_renders_comparison_and_confirms_through_po_flow(self):
        first = self._quotation(
            "RFQ-CMP-UI-A",
            self.vendors[0],
            [{"product": self.products[0], "price": Decimal("100"), "tax": Decimal("10")}],
        )
        second = self._quotation(
            "RFQ-CMP-UI-B",
            self.vendors[1],
            [{"product": self.products[0], "price": Decimal("90")}],
        )
        self.client.force_login(self.actor)

        listing = self.client.get(reverse("entity_list", args=["purchase-quotations"]))
        comparison = self.client.post(
            reverse("purchase_quotation_compare"),
            {"selected_ids": [first.pk, second.pk]},
        )

        self.assertContains(listing, "Compare RFQs")
        self.assertEqual(comparison.status_code, 200)
        self.assertContains(comparison, "RFQ Comparison")
        self.assertContains(comparison, "Best")
        self.assertContains(comparison, "Confirm as PO")

        confirmed = self.client.post(reverse("purchase_quotation_compare_confirm", args=[first.pk]))

        control = PurchaseOrderControl.objects.select_related("order").get(quotation=first)
        order = control.order
        first.refresh_from_db()
        provenance = PurchaseOrderLinePricingProvenance.objects.get(order_line__order=order)
        self.assertRedirects(confirmed, reverse("entity_edit", args=["purchases", order.pk]))
        self.assertEqual(first.status, PurchaseQuotation.Status.SELECTED)
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)
        self.assertEqual(order.total, Decimal("110"))
        self.assertEqual(Decimal(provenance.tax_snapshot["total_amount"]), Decimal("110"))

    def test_comparison_query_count_does_not_grow_with_rfq_count(self):
        quotations = [
            self._quotation(
                f"RFQ-CMP-Q-{index}",
                self.vendors[index],
                [{"product": self.products[0], "price": Decimal("100") + index}],
            )
            for index in range(12)
        ]

        with CaptureQueriesContext(connection) as small_queries:
            compare_purchase_quotations([quotation.pk for quotation in quotations[:2]])
        with CaptureQueriesContext(connection) as large_queries:
            payload = compare_purchase_quotations([quotation.pk for quotation in quotations])

        self.assertEqual(payload["selected_count"], 12)
        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Comparison queries grew from {len(small_queries)} to {len(large_queries)}.",
        )
