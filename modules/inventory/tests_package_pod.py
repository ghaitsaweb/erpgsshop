import base64
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.forms import inlineformset_factory
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.models import Company, Product, Warehouse
from modules.organization.models import Branch
from modules.organization.context import (
    ACTIVE_BRANCH_SESSION_KEY,
    ACTIVE_COMPANY_SESSION_KEY,
    SELECTED_BRANCHES_SESSION_KEY,
    SELECTED_COMPANIES_SESSION_KEY,
)

from .forms import InventoryPackageAllocationForm, InventoryPackageAllocationFormSet
from .models import (
    InventoryLocation,
    InventoryPackage,
    InventoryPackageAllocation,
    InventoryPackageMovementHistory,
    InventoryPackageType,
    InventoryPicking,
    InventoryPickingLine,
    InventoryProofOfDelivery,
    InventoryStockBalance,
)
from .services import (
    check_inventory_picking_availability,
    confirm_inventory_picking,
    save_inventory_package_allocations,
    set_inventory_picking_done_quantities,
    validate_inventory_picking,
)


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlGQAAAAASUVORK5CYII="
)


@override_settings(MEDIA_ROOT="/tmp/erpgsshop-inventory-test-media")
class InventoryPackageAndPODTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Package Co", code="PKG")
        self.branch = Branch.objects.get(company=self.company, is_main=True)
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            branch=self.branch,
            code="PKG-WH",
            name="Package Warehouse",
        )
        self.location = InventoryLocation.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.warehouse,
            name="Packing Zone",
            is_packing_zone=True,
        )
        self.user = get_user_model().objects.create_user(
            username="package-user",
            password="test-only-password",
        )
        self.package_type = InventoryPackageType.objects.get(
            company=self.company,
            code="BOX",
        )

    def _ready_picking(self, sequence=1, quantity=Decimal("4")):
        product = Product.objects.create(
            company=self.company,
            sku=f"PACK-{sequence}",
            name=f"Pack Product {sequence}",
            product_type=Product.ProductType.STOCKABLE,
            current_stock=quantity,
        )
        InventoryStockBalance.objects.create(
            company=self.company,
            product=product,
            warehouse=self.warehouse,
            location=self.location,
            qty_on_hand=quantity,
        )
        picking = InventoryPicking.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.warehouse,
            destination_name="Mobile Customer",
        )
        line = InventoryPickingLine.objects.create(
            picking=picking,
            product=product,
            requested_quantity=quantity,
        )
        confirm_inventory_picking(picking, self.user)
        check_inventory_picking_availability(picking, self.user)
        picking.refresh_from_db()
        line.refresh_from_db()
        return picking, line

    def _package(self, sequence, *, reusable=False, return_expected=False):
        package_type = self.package_type
        if reusable:
            package_type = InventoryPackageType.objects.create(
                company=self.company,
                code=f"RT-{sequence}",
                name=f"Reusable {sequence}",
                reusable=True,
            )
        return InventoryPackage.objects.create(
            company=self.company,
            package_type=package_type,
            code=f"PACKAGE-{sequence}",
            owner_warehouse=self.warehouse,
            current_location=self.location,
            return_expected=return_expected,
        )

    def _packing_formset(self, picking, allocations):
        formset_class = inlineformset_factory(
            InventoryPicking,
            InventoryPackageAllocation,
            form=InventoryPackageAllocationForm,
            formset=InventoryPackageAllocationFormSet,
            fields=["picking_line", "package", "quantity"],
            extra=len(allocations),
            can_delete=True,
            max_num=500,
            validate_max=True,
            absolute_max=520,
        )
        prefix = "document_lines_1"
        data = {
            f"{prefix}-TOTAL_FORMS": str(len(allocations)),
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "500",
        }
        for index, (line, package, quantity) in enumerate(allocations):
            data[f"{prefix}-{index}-picking_line"] = str(line.pk)
            data[f"{prefix}-{index}-package"] = str(package.pk)
            data[f"{prefix}-{index}-quantity"] = str(quantity)
        formset = formset_class(data=data, instance=picking, prefix=prefix)
        self.assertTrue(formset.is_valid(), formset.errors or formset.non_form_errors())
        return formset

    def test_ready_picking_can_split_into_packages_and_dispatch_atomically(self):
        picking, line = self._ready_picking()
        first = self._package(1)
        second = self._package(2, reusable=True, return_expected=True)
        formset = self._packing_formset(
            picking,
            [
                (line, first, Decimal("1.500")),
                (line, second, Decimal("2.500")),
            ],
        )

        save_inventory_package_allocations(picking, formset, self.user)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, InventoryPackage.Status.IN_USE)
        self.assertEqual(second.status, InventoryPackage.Status.IN_USE)
        self.assertEqual(first.current_picking_id, picking.pk)
        self.assertEqual(
            InventoryPackageAllocation.objects.filter(picking=picking).count(),
            2,
        )
        self.assertEqual(
            InventoryPackageMovementHistory.objects.filter(
                picking=picking,
                movement="packed",
            ).count(),
            2,
        )

        set_inventory_picking_done_quantities(picking, self.user)
        validate_inventory_picking(picking, self.user)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, InventoryPackage.Status.IN_TRANSIT)
        self.assertEqual(second.status, InventoryPackage.Status.AWAITING_RETURN)
        self.assertEqual(first.current_holder_type, InventoryPackage.HolderType.CUSTOMER)
        self.assertTrue(
            InventoryPackageMovementHistory.objects.filter(
                package=first,
                movement="dispatched",
            ).exists()
        )

    def test_packing_formset_rejects_overallocation(self):
        picking, line = self._ready_picking(quantity=Decimal("2"))
        package = self._package(1)
        formset_class = inlineformset_factory(
            InventoryPicking,
            InventoryPackageAllocation,
            form=InventoryPackageAllocationForm,
            formset=InventoryPackageAllocationFormSet,
            fields=["picking_line", "package", "quantity"],
            extra=1,
            can_delete=True,
        )
        prefix = "document_lines_1"
        formset = formset_class(
            data={
                f"{prefix}-TOTAL_FORMS": "1",
                f"{prefix}-INITIAL_FORMS": "0",
                f"{prefix}-MIN_NUM_FORMS": "0",
                f"{prefix}-MAX_NUM_FORMS": "500",
                f"{prefix}-0-picking_line": str(line.pk),
                f"{prefix}-0-package": str(package.pk),
                f"{prefix}-0-quantity": "2.001",
            },
            instance=picking,
            prefix=prefix,
        )
        self.assertFalse(formset.is_valid())
        self.assertIn("cannot exceed", str(formset.non_form_errors()))

    def test_pod_state_timestamp_and_signature_immutability(self):
        picking = InventoryPicking.objects.create(
            company=self.company,
            branch=self.branch,
            warehouse=self.warehouse,
            destination_name="POD Customer",
        )
        pod = InventoryProofOfDelivery.objects.get(picking=picking)
        pod.sender_name = "Draft Sender"
        pod.sender_signature = SimpleUploadedFile("sender.png", PNG_1X1, content_type="image/png")
        with self.assertRaises(ValidationError):
            pod.save()

        product = Product.objects.create(
            company=self.company,
            sku="POD-PRODUCT",
            name="POD Product",
            product_type=Product.ProductType.STOCKABLE,
            current_stock=Decimal("1"),
        )
        InventoryStockBalance.objects.create(
            company=self.company,
            product=product,
            warehouse=self.warehouse,
            location=self.location,
            qty_on_hand=Decimal("1"),
        )
        line = InventoryPickingLine.objects.create(
            picking=picking,
            product=product,
            requested_quantity=Decimal("1"),
        )
        confirm_inventory_picking(picking, self.user)
        pod.refresh_from_db()
        pod.sender_name = "Warehouse Sender"
        pod.sender_signature = SimpleUploadedFile("sender.png", PNG_1X1, content_type="image/png")
        pod.save()
        pod.refresh_from_db()
        self.assertEqual(pod.status, InventoryProofOfDelivery.Status.PENDING_RECEIVER)
        self.assertIsNotNone(pod.sender_signed_at)

        pod.receiver_name = "Early Receiver"
        pod.receiver_signature = SimpleUploadedFile("receiver.png", PNG_1X1, content_type="image/png")
        with self.assertRaises(ValidationError):
            pod.save()

        check_inventory_picking_availability(picking, self.user)
        set_inventory_picking_done_quantities(picking, self.user)
        validate_inventory_picking(picking, self.user)
        pod.refresh_from_db()
        pod.receiver_name = "Customer Receiver"
        pod.receiver_signature = SimpleUploadedFile("receiver.png", PNG_1X1, content_type="image/png")
        pod.save()
        pod.refresh_from_db()
        self.assertEqual(pod.status, InventoryProofOfDelivery.Status.COMPLETED)
        self.assertIsNotNone(pod.receiver_signed_at)

        pod.sender_signature = SimpleUploadedFile("replacement.png", PNG_1X1, content_type="image/png")
        with self.assertRaises(ValidationError):
            pod.save()

        pod.refresh_from_db()
        pod.notes = "Evidence remains editable after completion."
        pod.evidence_document = SimpleUploadedFile(
            "proof.pdf",
            b"%PDF-1.4 test proof",
            content_type="application/pdf",
        )
        pod.save()
        pod.refresh_from_db()
        self.assertIsNotNone(pod.evidence_uploaded_at)

    def test_picking_document_exposes_hmx_tabs_and_mobile_pod_link(self):
        admin = get_user_model().objects.create_superuser(
            username="inventory-admin",
            email="inventory@example.com",
            password="test-only-password",
        )
        picking, _line = self._ready_picking()
        self.client.force_login(admin)
        session = self.client.session
        session[ACTIVE_COMPANY_SESSION_KEY] = self.company.pk
        session[ACTIVE_BRANCH_SESSION_KEY] = self.branch.pk
        session[SELECTED_COMPANIES_SESSION_KEY] = [self.company.pk]
        session[SELECTED_BRANCHES_SESSION_KEY] = [self.branch.pk]
        session.save()

        response = self.client.get(
            reverse("entity_edit", args=["inventory-pickings", picking.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Picking Lines")
        self.assertContains(response, "Packages")
        self.assertContains(response, "Proof of Delivery")
        self.assertContains(response, "Open mobile POD capture")

    def test_packing_service_query_growth_is_bounded(self):
        def build(count, sequence):
            picking, line = self._ready_picking(sequence=sequence, quantity=Decimal(count))
            packages = [self._package(sequence * 100 + index) for index in range(count)]
            formset = self._packing_formset(
                picking,
                [(line, package, Decimal("1")) for package in packages],
            )
            return picking, formset

        small, small_formset = build(2, 10)
        with CaptureQueriesContext(connection) as small_queries:
            save_inventory_package_allocations(small, small_formset, self.user)

        large, large_formset = build(12, 20)
        with CaptureQueriesContext(connection) as large_queries:
            save_inventory_package_allocations(large, large_formset, self.user)

        self.assertLessEqual(len(large_queries), len(small_queries) + 2)
