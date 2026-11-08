import csv
import io
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.models import Company, Contact, Product

from .models import (
    VendorPricelist,
    VendorPricelistImportLog,
    VendorPricelistItem,
    VendorPricelistTier,
    VendorProfile,
)
from .services import (
    process_vendor_pricelist_import,
    transition_vendor_pricelist_import,
    validate_vendor_pricelist_import,
)


IMPORT_HEADERS = [
    "code",
    "name",
    "vendor_code",
    "currency_id",
    "effective_date",
    "expiry_date",
    "active",
    "pricelist_item_ids/product_id",
    "pricelist_item_ids/uom",
    "pricelist_item_ids/unit_price",
    "pricelist_item_ids/enforce_moq",
    "pricelist_item_ids/lead_time",
    "pricelist_item_ids/pricelist_tier_ids/tier",
    "pricelist_item_ids/pricelist_tier_ids/min_qty",
    "pricelist_item_ids/pricelist_tier_ids/max_qty",
    "pricelist_item_ids/pricelist_tier_ids/tier_price",
]


class VendorPricelistImportParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="vendor-import-admin",
            password="test-password",
            email="vendor-import@example.com",
        )
        self.company = Company.objects.create(code="VPI", name="Vendor Import")
        contact = Contact.objects.create(
            company=self.company,
            code="VPI-VENDOR",
            name="Vendor Import Partner",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.profile = VendorProfile.objects.create(
            company=self.company,
            partner=contact,
            registration_number="VPI-VENDOR",
            status=VendorProfile.Status.ACTIVE,
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="VPI-PRODUCT",
            name="Imported Product",
        )

    def _file(self, rows, *, name="vendor-pricelist.csv"):
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(IMPORT_HEADERS)
        writer.writerows(rows)
        return SimpleUploadedFile(name, stream.getvalue().encode("utf-8"), content_type="text/csv")

    def _row(self, **overrides):
        values = {
            "code": "VPL-IMPORT-1",
            "name": "Imported Pricelist",
            "vendor_code": "VPI-VENDOR",
            "currency_id": "IDR",
            "effective_date": "2026-01-01",
            "expiry_date": "2026-12-31",
            "active": "TRUE",
            "pricelist_item_ids/product_id": "VPI-PRODUCT",
            "pricelist_item_ids/uom": "Unit",
            "pricelist_item_ids/unit_price": "125.50",
            "pricelist_item_ids/enforce_moq": "TRUE",
            "pricelist_item_ids/lead_time": "3",
            "pricelist_item_ids/pricelist_tier_ids/tier": "1",
            "pricelist_item_ids/pricelist_tier_ids/min_qty": "5",
            "pricelist_item_ids/pricelist_tier_ids/max_qty": "10",
            "pricelist_item_ids/pricelist_tier_ids/tier_price": "120",
        }
        values.update(overrides)
        return [values[header] for header in IMPORT_HEADERS]

    def _log(self, rows, *, name="vendor-pricelist.csv"):
        return VendorPricelistImportLog.objects.create(
            company=self.company,
            source_file=self._file(rows, name=name),
        )

    def test_csv_preview_and_nested_import_create_parent_item_and_tiers(self):
        tier_two = self._row(
            code="",
            name="",
            vendor_code="",
            currency_id="",
            effective_date="",
            expiry_date="",
            active="",
            **{
                "pricelist_item_ids/product_id": "",
                "pricelist_item_ids/uom": "",
                "pricelist_item_ids/unit_price": "",
                "pricelist_item_ids/enforce_moq": "",
                "pricelist_item_ids/lead_time": "",
                "pricelist_item_ids/pricelist_tier_ids/tier": "2",
                "pricelist_item_ids/pricelist_tier_ids/min_qty": "11",
                "pricelist_item_ids/pricelist_tier_ids/max_qty": "0",
                "pricelist_item_ids/pricelist_tier_ids/tier_price": "110",
            },
        )
        import_log = self._log([self._row(), tier_two])

        import_log = validate_vendor_pricelist_import(import_log, self.actor)
        self.assertEqual(import_log.status, VendorPricelistImportLog.Status.VALIDATED)
        self.assertEqual(import_log.valid_rows, 2)
        self.assertEqual(import_log.error_rows, 0)
        self.assertEqual(len(import_log.preview.valid_data), 2)

        import_log.status = VendorPricelistImportLog.Status.PROCESSING
        import_log.save(update_fields=["status", "updated_at"])
        import_log = process_vendor_pricelist_import(import_log)

        pricelist = VendorPricelist.objects.get(code="VPL-IMPORT-1")
        item = VendorPricelistItem.objects.get(pricelist=pricelist, product=self.product)
        self.assertEqual(import_log.status, VendorPricelistImportLog.Status.COMPLETED)
        self.assertEqual(import_log.inserted_count, 1)
        self.assertEqual(item.unit_price, Decimal("125.50"))
        self.assertEqual(item.tiers.count(), 2)
        self.assertEqual(
            list(item.tiers.order_by("sequence").values_list("tier_price", flat=True)),
            [Decimal("120.00"), Decimal("110.00")],
        )
        self.assertEqual(pricelist.source_import, import_log)

    def test_import_upserts_existing_pricelist_and_item(self):
        first = self._log([self._row()], name="vendor-pricelist-first.csv")
        validate_vendor_pricelist_import(first, self.actor)
        first.status = VendorPricelistImportLog.Status.PROCESSING
        first.save(update_fields=["status", "updated_at"])
        process_vendor_pricelist_import(first)

        second = self._log(
            [self._row(**{"pricelist_item_ids/unit_price": "150"})],
            name="vendor-pricelist-second.csv",
        )
        validate_vendor_pricelist_import(second, self.actor)
        second.status = VendorPricelistImportLog.Status.PROCESSING
        second.save(update_fields=["status", "updated_at"])
        second = process_vendor_pricelist_import(second)

        self.assertEqual(VendorPricelist.objects.filter(code="VPL-IMPORT-1").count(), 1)
        self.assertEqual(second.updated_count, 1)
        self.assertEqual(
            VendorPricelistItem.objects.get(pricelist__code="VPL-IMPORT-1").unit_price,
            Decimal("150.00"),
        )

    def test_invalid_relation_is_reported_in_preview_without_writes(self):
        import_log = self._log(
            [self._row(**{"pricelist_item_ids/product_id": "MISSING-SKU"})],
            name="vendor-pricelist-invalid.csv",
        )

        import_log = validate_vendor_pricelist_import(import_log, self.actor)

        self.assertEqual(import_log.status, VendorPricelistImportLog.Status.FAILED)
        self.assertEqual(import_log.valid_rows, 0)
        self.assertEqual(import_log.error_rows, 1)
        self.assertIn("MISSING-SKU", import_log.preview.error_data[0]["error"])
        self.assertFalse(VendorPricelist.objects.exists())

    def test_import_action_queues_existing_worker_task(self):
        import_log = self._log([self._row()], name="vendor-pricelist-queued.csv")
        validate_vendor_pricelist_import(import_log, self.actor)

        with patch("modules.purchase.tasks.process_vendor_pricelist_import_task.apply_async") as apply_async:
            with self.captureOnCommitCallbacks(execute=True):
                import_log = transition_vendor_pricelist_import(import_log, "import", self.actor)

        self.assertEqual(import_log.status, VendorPricelistImportLog.Status.PROCESSING)
        apply_async.assert_called_once_with(kwargs={"import_log_id": import_log.pk})

    def test_validation_query_count_does_not_grow_with_rows(self):
        products = Product.objects.bulk_create(
            [
                Product(company=self.company, sku=f"VPI-Q-{index}", name=f"Query Product {index}")
                for index in range(20)
            ]
        )
        small_rows = [
            self._row(
                code="VPL-Q-SMALL",
                name="Small Query Import",
                **{"pricelist_item_ids/product_id": products[index].sku},
            )
            for index in range(2)
        ]
        large_rows = [
            self._row(
                code="VPL-Q-LARGE",
                name="Large Query Import",
                **{"pricelist_item_ids/product_id": product.sku},
            )
            for product in products
        ]
        small_log = self._log(small_rows, name="vendor-pricelist-query-small.csv")
        large_log = self._log(large_rows, name="vendor-pricelist-query-large.csv")

        with CaptureQueriesContext(connection) as small_queries:
            validate_vendor_pricelist_import(small_log, self.actor)
        with CaptureQueriesContext(connection) as large_queries:
            validate_vendor_pricelist_import(large_log, self.actor)

        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"Import validation queries grew from {len(small_queries)} to {len(large_queries)}.",
        )
