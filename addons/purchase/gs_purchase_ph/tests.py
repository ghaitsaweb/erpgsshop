from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine
from modules.apphub.models import ERPApp
from modules.finance.models import CountryTaxProfile


class GSPurchasePhilippinesPrintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username="gs-purchase-ph",
            email="buyer-ph@example.com",
            password="test",
        )
        cls.company = Company.objects.create(
            code="GSPH",
            name="GS Philippines",
            address="Buyer Street 1",
        )
        cls.vendor = Contact.objects.create(
            company=cls.company,
            code="GSPH-VENDOR",
            name="PH Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        cls.product = Product.objects.create(
            company=cls.company,
            sku="GSPH-PRODUCT",
            name="PH Product",
            purchase_price=Decimal("25000"),
        )
        cls.app = ERPApp.objects.create(
            slug="gs-purchase-ph",
            name="GS Purchase Philippines",
            category="Purchase Add-ons",
            summary="Philippine Purchase reports.",
            status=ERPApp.Status.BETA,
            enabled=True,
        )
        CountryTaxProfile.objects.create(
            company=cls.company,
            country_code="PH",
            registration_number="123-456-789-000",
            configuration={
                "software_name": "ERP GSSHOP",
                "software_version": "2026.11",
            },
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _order(self, number):
        order = PurchaseOrder.objects.create(
            company=self.company,
            number=number,
            vendor=self.vendor,
            order_date=timezone.localdate(),
        )
        PurchaseOrderLine.objects.create(
            order=order,
            product=self.product,
            quantity=1,
            unit_price=Decimal("25000"),
        )
        return order

    def test_enabled_addon_injects_ph_purchase_pdf_metadata(self):
        order = self._order("PO-GSPH-PRINT")
        captured = {}

        def render_probe(entity, instance, lines, document):
            del lines, document
            captured.update(instance._pdf_localization_metadata)
            self.assertEqual(entity, "purchases")
            return b"%PDF-1.4\n"

        with patch("core.views.render_document_pdf", side_effect=render_probe):
            response = self.client.get(reverse("document_pdf", args=["purchases", order.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["country_code"], "PH")
        self.assertEqual(captured["taxpayer_name"], self.company.name)
        self.assertEqual(captured["address"], self.company.address)
        self.assertEqual(captured["tin"], "123-456-789-000")
        self.assertEqual(captured["software_name"], "ERP GSSHOP")
        self.assertEqual(captured["software_version"], "2026.11")
        self.assertEqual(captured["printed_by"], self.user.username)

    def test_disabled_addon_does_not_inject_ph_metadata(self):
        self.app.enabled = False
        self.app.save(update_fields=["enabled"])
        order = self._order("PO-GSPH-DISABLED")

        def render_probe(entity, instance, lines, document):
            del entity, lines, document
            self.assertFalse(hasattr(instance, "_pdf_localization_metadata"))
            return b"%PDF-1.4\n"

        with patch("core.views.render_document_pdf", side_effect=render_probe):
            response = self.client.get(reverse("document_pdf", args=["purchases", order.pk]))

        self.assertEqual(response.status_code, 200)
