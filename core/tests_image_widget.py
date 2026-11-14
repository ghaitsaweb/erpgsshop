from django.test import SimpleTestCase

from .forms import CompanyForm
from .models import Company


class ImagePreviewWidgetTests(SimpleTestCase):
    def test_company_logo_widget_template_is_discoverable_without_storage_link(self):
        company = Company(code="PREVIEW", name="Preview", logo="company-logos/preview.png")

        html = CompanyForm(instance=company)["logo"].as_widget()

        self.assertIn('class="image-file-input"', html)
        self.assertIn("Remove current image", html)
        self.assertNotIn("Currently:", html)
        self.assertNotIn("/media/company-logos/preview.png", html)
