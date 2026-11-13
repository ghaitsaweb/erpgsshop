import base64
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .document_pdf import REPORT_CONFIG, _company_logo_flowable, render_document_pdf
from .forms import CompanyForm
from .models import Company, Contact, Product, SaleOrder, SaleOrderLine
from .views import DOCUMENT_CONFIG


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class CompanyLogoPDFTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(self.media_directory.cleanup)

    def _company_form(self, *, code, name, logo=None):
        return CompanyForm(
            data={
                "code": code,
                "name": name,
                "email": "company@example.com",
                "phone": "0215550123",
                "address": "Jakarta",
                "currency": "IDR",
                "active": True,
            },
            files={"logo": logo} if logo else None,
        )

    def test_company_form_uploads_a_valid_image(self):
        form = self._company_form(
            code="LOGO",
            name="Logo Company",
            logo=SimpleUploadedFile("company.png", PNG_1X1, content_type="image/png"),
        )

        self.assertTrue(form.is_valid(), form.errors)
        company = form.save()
        self.assertTrue(company.logo.name.startswith("company-logos/"))
        self.assertIsNotNone(_company_logo_flowable(company))

    def test_sales_pdf_uses_company_logo_and_company_pdf_metadata(self):
        form = self._company_form(
            code="BRAND",
            name="Branded Company",
            logo=SimpleUploadedFile("brand.png", PNG_1X1, content_type="image/png"),
        )
        self.assertTrue(form.is_valid(), form.errors)
        company = form.save()
        customer = Contact.objects.create(company=company, code="CUS-1", name="Customer")
        product = Product.objects.create(company=company, sku="SKU-1", name="Product")
        order = SaleOrder.objects.create(company=company, number="SO-BRAND", customer=customer)
        line = SaleOrderLine.objects.create(
            order=order,
            product=product,
            description="Branded line",
            quantity=1,
            unit_price=10,
        )

        pdf = render_document_pdf("sales", order, [line], DOCUMENT_CONFIG["sales"])

        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertIn(b"/Author (Branded Company)", pdf)

    def test_missing_logo_falls_back_to_erp_brand_for_every_report_type(self):
        company = Company.objects.create(code="FALLBACK", name="Fallback Company")
        self.assertIsNone(_company_logo_flowable(company))
        self.assertTrue(all(config.get("company_path") for config in REPORT_CONFIG.values()))

        customer = Contact.objects.create(company=company, code="CUS-2", name="Customer")
        product = Product.objects.create(company=company, sku="SKU-2", name="Product")
        order = SaleOrder.objects.create(company=company, number="SO-FALLBACK", customer=customer)
        line = SaleOrderLine.objects.create(order=order, product=product, quantity=1, unit_price=10)

        pdf = render_document_pdf("sales", order, [line], DOCUMENT_CONFIG["sales"])

        self.assertIn(b"/Author (ERP GSSHOP)", pdf)
