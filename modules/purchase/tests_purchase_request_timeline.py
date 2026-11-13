from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from core.models import Company, Contact, Product, Warehouse

from .models import (
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseSetting,
    VendorProfile,
)
from .services import create_purchase_request_rfqs, sync_purchase_request_line_timeline


class PurchaseRequestTimelineParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="pr-timeline-admin",
            password="test-password",
            email="pr-timeline@example.com",
        )
        self.company = Company.objects.create(code="PRTIME", name="PR Timeline")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="PRTIME-VENDOR",
            name="Timeline Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor,
            status=VendorProfile.Status.ACTIVE,
        )
        PurchaseSetting.objects.create(
            company=self.company,
            require_vendor_prequalification=False,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="PRTIME-WH",
            name="Timeline Warehouse",
        )
        self.other_warehouse = Warehouse.objects.create(
            company=self.company,
            code="PRTIME-WH-2",
            name="Timeline Warehouse 2",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="PRTIME-PRODUCT",
            name="Timeline Product",
            purchase_price=Decimal("20"),
        )

    def _request(self, suffix, *, expected=None, expiry=None, warehouse=None):
        return PurchaseRequest.objects.create(
            company=self.company,
            number=f"PR-TIME-{suffix}",
            requestor=self.actor,
            request_date=date(2027, 1, 1),
            required_date=expected,
            expiry_date=expiry,
            destination_warehouse=warehouse,
            justification="Purchase Request timeline parity",
        )

    def _line(self, request, **overrides):
        values = {
            "request": request,
            "product": self.product,
            "description": self.product.name,
            "quantity": Decimal("2"),
            "estimated_unit_price": Decimal("20"),
        }
        values.update(overrides)
        return PurchaseRequestLine.objects.create(**values)

    def test_header_timeline_defaults_to_line(self):
        request = self._request(
            "DEFAULT",
            expected=date(2027, 2, 1),
            expiry=date(2027, 1, 20),
            warehouse=self.warehouse,
        )
        line = self._line(request)

        self.assertEqual(line.expected_date, date(2027, 2, 1))
        self.assertEqual(line.expiry_date, date(2027, 1, 20))
        self.assertEqual(line.destination_warehouse, self.warehouse)

    def test_header_changes_update_inherited_lines_and_preserve_overrides(self):
        old_expected = date(2027, 2, 1)
        old_expiry = date(2027, 1, 20)
        request = self._request(
            "PROPAGATE",
            expected=old_expected,
            expiry=old_expiry,
            warehouse=self.warehouse,
        )
        inherited = self._line(request)
        overridden = self._line(
            request,
            description="Override",
            expected_date=date(2027, 3, 5),
            expiry_date=date(2027, 2, 10),
            destination_warehouse=self.other_warehouse,
        )
        request.required_date = date(2027, 4, 1)
        request.expiry_date = date(2027, 3, 1)
        request.destination_warehouse = self.other_warehouse
        request.save(
            update_fields=[
                "required_date",
                "expiry_date",
                "destination_warehouse",
                "updated_at",
            ]
        )
        request._purchase_previous_expected_date = old_expected
        request._purchase_previous_expiry_date = old_expiry
        request._purchase_previous_destination_warehouse_id = self.warehouse.pk

        sync_purchase_request_line_timeline(request)

        inherited.refresh_from_db()
        overridden.refresh_from_db()
        self.assertEqual(inherited.expected_date, date(2027, 4, 1))
        self.assertEqual(inherited.expiry_date, date(2027, 3, 1))
        self.assertEqual(inherited.destination_warehouse, self.other_warehouse)
        self.assertEqual(overridden.expected_date, date(2027, 3, 5))
        self.assertEqual(overridden.expiry_date, date(2027, 2, 10))
        self.assertEqual(overridden.destination_warehouse, self.other_warehouse)

    def test_first_line_timeline_fills_empty_header(self):
        request = self._request("FILL")
        line = self._line(
            request,
            expected_date=date(2027, 5, 10),
            expiry_date=date(2027, 4, 10),
            destination_warehouse=self.warehouse,
        )

        sync_purchase_request_line_timeline(request)

        request.refresh_from_db()
        self.assertEqual(request.required_date, date(2027, 5, 10))
        self.assertEqual(request.expiry_date, date(2027, 4, 10))
        self.assertEqual(request.destination_warehouse, self.warehouse)
        self.assertEqual(line.expected_date, request.required_date)

    def test_rfq_lines_keep_per_request_line_expected_and_expiry_dates(self):
        request = self._request(
            "RFQ",
            expected=date(2027, 5, 10),
            expiry=date(2027, 4, 20),
            warehouse=self.warehouse,
        )
        request.status = PurchaseRequest.Status.APPROVED
        request.save(update_fields=["status", "updated_at"])
        line = self._line(
            request,
            expected_date=date(2027, 6, 15),
            expiry_date=date(2027, 4, 25),
            approved_quantity=Decimal("2"),
            line_status=PurchaseRequestLine.Status.APPROVED,
        )

        quotations = create_purchase_request_rfqs(
            request,
            allocations=[
                {
                    "line_id": line.pk,
                    "quantity": Decimal("2"),
                    "vendor_ids": [self.vendor.pk],
                }
            ],
            response_deadline=date(2027, 4, 30),
            actor=self.actor,
        )

        quotation_line = quotations[0].lines.get(free_product=False)
        self.assertEqual(quotation_line.promised_date, date(2027, 6, 15))
        self.assertEqual(quotation_line.expiry_date, date(2027, 4, 25))

    def test_timeline_sync_query_count_does_not_grow_with_lines(self):
        small = self._request(
            "QUERY-SMALL",
            expected=date(2027, 2, 1),
            expiry=date(2027, 1, 20),
            warehouse=self.warehouse,
        )
        large = self._request(
            "QUERY-LARGE",
            expected=date(2027, 2, 1),
            expiry=date(2027, 1, 20),
            warehouse=self.warehouse,
        )
        PurchaseRequestLine.objects.bulk_create(
            [
                PurchaseRequestLine(
                    request=small,
                    product=self.product,
                    description=f"Small {index}",
                    quantity=Decimal("1"),
                )
                for index in range(2)
            ]
        )
        PurchaseRequestLine.objects.bulk_create(
            [
                PurchaseRequestLine(
                    request=large,
                    product=self.product,
                    description=f"Large {index}",
                    quantity=Decimal("1"),
                )
                for index in range(12)
            ]
        )

        with CaptureQueriesContext(connection) as small_queries:
            sync_purchase_request_line_timeline(small)
        with CaptureQueriesContext(connection) as large_queries:
            sync_purchase_request_line_timeline(large)

        self.assertLessEqual(
            len(large_queries),
            len(small_queries),
            f"PR timeline queries grew from {len(small_queries)} to {len(large_queries)}.",
        )
