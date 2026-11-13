from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Company, Contact, Product, PurchaseOrder, Warehouse
from core.views import DOCUMENT_CONFIG

from .forms import PurchaseOrderDocumentForm, PurchaseRequestDocumentForm
from .models import (
    AwardStrategy,
    PurchaseAccountingSetting,
    PurchaseMilestoneTemplate,
    PurchaseMilestoneTemplateLine,
    PurchaseOrderControl,
    PurchaseOrderTemplate,
    PurchaseOrderTemplateLine,
    PurchaseOrderType,
    PurchaseQuotation,
    PurchaseQuotationLine,
    PurchaseRemarkTemplate,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseRemarkTemplate,
    PurchaseSetting,
    PurchaseTender,
    VendorProfile,
    VendorCategory,
)
from .services import (
    allocate_purchase_document_number,
    allocate_purchase_document_numbers,
    apply_purchase_remark_template,
    apply_purchase_milestone_template,
    apply_purchase_order_template,
    confirm_purchase_order,
    duplicate_purchase_order_template,
    snapshot_purchase_order_commercial_defaults,
)


class PurchaseConfigurationParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="purchase-config-admin",
            password="test-password",
            email="purchase-config@example.com",
        )
        self.company = Company.objects.create(code="PCFG", name="Purchase Configuration")
        self.vendor = Contact.objects.create(
            company=self.company,
            code="PCFG-VENDOR",
            name="Configuration Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.warehouse = Warehouse.objects.create(company=self.company, code="PCFG-WH", name="Warehouse")
        self.product = Product.objects.create(
            company=self.company,
            sku="PCFG-P",
            name="Configured Product",
            purchase_price=Decimal("25"),
        )
        self.order_type = PurchaseOrderType.objects.create(company=self.company, name="Standard Purchase")
        self.order_type.companies.add(self.company)
        self.order_template = PurchaseOrderTemplate.objects.create(
            company=self.company,
            name="Standard Products",
            vendor=self.vendor,
            order_type=self.order_type,
        )
        PurchaseOrderTemplateLine.objects.create(
            order_template=self.order_template,
            product=self.product,
            quantity=Decimal("2"),
            price=Decimal("25"),
        )
        self.milestone_template = PurchaseMilestoneTemplate.objects.create(
            company=self.company,
            name="Half and Half",
        )
        PurchaseMilestoneTemplateLine.objects.bulk_create(
            [
                PurchaseMilestoneTemplateLine(
                    template=self.milestone_template,
                    sequence=10,
                    milestone_name="Mobilization",
                    contract_term_percentage=Decimal("50"),
                ),
                PurchaseMilestoneTemplateLine(
                    template=self.milestone_template,
                    sequence=20,
                    milestone_name="Completion",
                    contract_term_percentage=Decimal("50"),
                    due_days=30,
                ),
            ]
        )

    def _order(self, suffix):
        return PurchaseOrder.objects.create(
            company=self.company,
            number=f"DRAFT-{suffix}",
            vendor=self.vendor,
            warehouse=self.warehouse,
            order_date=timezone.localdate(),
        )

    def test_company_sequence_allocates_monthly_document_numbers_in_one_batch(self):
        numbers = allocate_purchase_document_numbers(
            self.order_type.pk,
            self.company.pk,
            "rfq",
            count=3,
        )
        next_number = allocate_purchase_document_number(
            self.order_type.pk,
            self.company.pk,
            "rfq",
        )
        self.assertEqual([number.rsplit("/", 1)[-1] for number in numbers], ["001", "002", "003"])
        self.assertTrue(next_number.endswith("/004"))
        self.assertIn(self.order_type.prefix_sequence, next_number)

    def test_order_and_milestone_templates_apply_to_draft_purchase_order(self):
        order = self._order("APPLY")
        apply_purchase_order_template(order, self.order_template, self.actor)
        apply_purchase_milestone_template(order, self.milestone_template, self.actor)
        order.refresh_from_db()
        self.assertEqual(order.lines.get().quantity, Decimal("2.000"))
        self.assertEqual(order.total, Decimal("50.00"))
        self.assertEqual(list(order.milestones.values_list("percentage", flat=True)), [Decimal("50.00"), Decimal("50.00")])
        control = PurchaseOrderControl.objects.get(order=order)
        self.assertEqual(control.order_template, self.order_template)
        self.assertEqual(control.milestone_template, self.milestone_template)

    def test_duplicate_order_template_copies_lines_with_unique_names(self):
        first = duplicate_purchase_order_template(self.order_template, self.actor)
        second = duplicate_purchase_order_template(self.order_template, self.actor)

        source_line = self.order_template.lines.get()
        first_line = first.lines.get()
        second_line = second.lines.get()
        self.assertEqual(first.name, f"{self.order_template.name} (Copy)")
        self.assertEqual(second.name, f"{self.order_template.name} (Copy) 2")
        self.assertNotEqual(source_line.pk, first_line.pk)
        self.assertNotEqual(first_line.pk, second_line.pk)
        self.assertEqual(first_line.product, source_line.product)
        self.assertEqual(first_line.quantity, source_line.quantity)
        self.assertEqual(first_line.price, source_line.price)

    def test_duplicate_order_template_collection_action_requires_one_selection(self):
        self.client.force_login(self.actor)
        response = self.client.post(
            reverse("purchase_order_template_duplicate"),
            {"selected_ids": [str(self.order_template.pk)]},
        )

        duplicate = PurchaseOrderTemplate.objects.exclude(pk=self.order_template.pk).get()
        self.assertRedirects(
            response,
            reverse("entity_edit", kwargs={"entity": "purchase-order-templates", "pk": duplicate.pk}),
            fetch_redirect_response=False,
        )
        self.assertEqual(duplicate.lines.count(), 1)

        response = self.client.post(reverse("purchase_order_template_duplicate"), {"selected_ids": []})
        self.assertRedirects(
            response,
            reverse("entity_list", kwargs={"entity": "purchase-order-templates"}),
            fetch_redirect_response=False,
        )

    def test_confirmation_promotes_draft_sequence_to_purchase_order_sequence(self):
        order = self._order("CONFIRM")
        apply_purchase_order_template(order, self.order_template, self.actor)
        confirm_purchase_order(order, self.actor)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)
        self.assertTrue(order.number.startswith(f"PO/{self.order_type.prefix_sequence}/"))
        self.assertIsNotNone(order.purchase_control.confirmed_at)

    def test_remark_template_code_is_generated_and_immutable(self):
        template = PurchaseRemarkTemplate.objects.create(
            company=self.company,
            name="Standard Delivery Note",
            remark_type=PurchaseRemarkTemplate.RemarkType.DELIVERY,
            applicable_document=PurchaseRemarkTemplate.ApplicableDocument.ALL,
            content="Deliver during warehouse receiving hours.",
        )

        self.assertTrue(template.code.startswith("PRM-PCFG-"))
        template.code = "MANUAL"
        with self.assertRaisesMessage(ValidationError, "cannot be changed"):
            template.save()

        with self.assertRaisesMessage(ValidationError, "generated automatically"):
            PurchaseRemarkTemplate.objects.create(
                company=self.company,
                code="MANUAL",
                name="Manual Code",
                content="Manual codes are not accepted.",
            )

    def test_settings_default_document_expiry_and_manual_vendor_code(self):
        PurchaseSetting.objects.create(
            company=self.company,
            vendor_sequence_mode=PurchaseSetting.SequenceMode.MANUAL,
            pr_expiry_period_days=7,
            rfq_expiry_period_days=9,
            pt_expiry_period_days=11,
        )
        today = timezone.localdate()
        request = PurchaseRequest.objects.create(
            company=self.company,
            number="PR-EXPIRY",
            requestor=self.actor,
            request_date=today,
            required_date=today,
            destination_warehouse=self.warehouse,
            justification="Expiry settings parity",
        )
        request_line = PurchaseRequestLine.objects.create(
            request=request,
            product=self.product,
            description=self.product.name,
        )
        quotation = PurchaseQuotation.objects.create(
            company=self.company,
            number="RFQ-EXPIRY",
            request=request,
            vendor=self.vendor,
            warehouse=self.warehouse,
            issue_date=today,
        )
        quotation_line = PurchaseQuotationLine.objects.create(
            quotation=quotation,
            request_line=request_line,
            product=self.product,
            description=self.product.name,
        )
        strategy = AwardStrategy.objects.create(company=self.company, name="Expiry Strategy")
        tender = PurchaseTender.objects.create(
            company=self.company,
            number="TNR-EXPIRY",
            title="Expiry Tender",
            category=PurchaseTender.Category.OPEX,
            tender_type=PurchaseTender.TenderType.OPEN,
            award_strategy=strategy,
        )

        self.assertEqual(request.expiry_date, today + timedelta(days=7))
        self.assertEqual(request_line.expiry_date, request.expiry_date)
        self.assertEqual(quotation.response_deadline, today + timedelta(days=9))
        self.assertEqual(quotation_line.expiry_date, quotation.response_deadline)
        self.assertEqual(tender.close_date, today + timedelta(days=11))

        with self.assertRaisesMessage(ValidationError, "Vendor Code is required"):
            VendorProfile.objects.create(company=self.company, partner=self.vendor)
        profile = VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor,
            registration_number="MANUAL-VENDOR",
        )
        self.assertEqual(profile.registration_number, "MANUAL-VENDOR")

    def test_vendor_category_crud_supports_description_and_generated_code(self):
        category = VendorCategory.objects.create(
            company=self.company,
            name="Contractors",
            description="External service contractors.",
        )
        self.assertTrue(category.code.startswith("VC-"))
        self.assertEqual(category.description, "External service contractors.")

        category.name = "Preferred Contractors"
        category.description = "Updated description."
        category.save(update_fields=["name", "description", "updated_at"])
        category.refresh_from_db()
        self.assertEqual(category.name, "Preferred Contractors")
        self.assertEqual(category.description, "Updated description.")

        category_id = category.pk
        category.delete()
        self.assertFalse(VendorCategory.objects.filter(pk=category_id).exists())

    def test_purchase_document_form_persists_module_owned_control_fields(self):
        order = self._order("CONTROL-FORM")
        expiry_date = timezone.localdate() + timedelta(days=14)
        form = PurchaseOrderDocumentForm(
            {
                "company": str(self.company.pk),
                "number": order.number,
                "vendor": str(self.vendor.pk),
                "warehouse": str(self.warehouse.pk),
                "invoicing_policy": PurchaseOrder.InvoicingPolicy.ORDERED,
                "order_date": order.order_date.isoformat(),
                "notes": "Control field test",
                "order_type": str(self.order_type.pk),
                "order_template": "",
                "milestone_template": "",
                "purchase_team": "",
                "assigned_to": "",
                "expiry_date": expiry_date.isoformat(),
                "cost_center": "OPS-JKT",
                "contract_terms": "Net 30 after accepted delivery.",
            },
            instance=order,
        )
        self.assertTrue(form.is_valid(), form.errors)

        form.save()

        control = PurchaseOrderControl.objects.get(order=order)
        self.assertEqual(control.expiry_date, expiry_date)
        self.assertEqual(control.cost_center, "OPS-JKT")
        self.assertEqual(control.contract_terms, "Net 30 after accepted delivery.")
        self.assertIs(DOCUMENT_CONFIG["purchases"]["header_form"], PurchaseOrderDocumentForm)

    def test_vendor_payment_terms_and_addresses_are_snapshotted_by_po_form(self):
        PurchaseSetting.objects.create(
            company=self.company,
            is_use_vendor_payment_terms=True,
        )
        VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor,
            payment_terms="Net 45 from accepted invoice.",
        )
        self.vendor.address = "Jl. Vendor Lama No. 10"
        self.vendor.save(update_fields=["address", "updated_at"])
        order = self._order("VENDOR-DEFAULTS")
        form = PurchaseOrderDocumentForm(
            {
                "company": str(self.company.pk),
                "number": order.number,
                "vendor": str(self.vendor.pk),
                "warehouse": str(self.warehouse.pk),
                "invoicing_policy": PurchaseOrder.InvoicingPolicy.ORDERED,
                "order_date": order.order_date.isoformat(),
                "notes": "Vendor defaults",
                "order_type": str(self.order_type.pk),
                "order_template": "",
                "milestone_template": "",
                "purchase_team": "",
                "assigned_to": "",
                "payment_terms": "",
                "delivery_terms": "",
                "legal_terms": "",
                "vendor_address": "",
                "bill_address": "",
            },
            instance=order,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        control = PurchaseOrderControl.objects.get(order=order)
        self.assertEqual(control.payment_terms, "Net 45 from accepted invoice.")
        self.assertEqual(control.vendor_address, "Jl. Vendor Lama No. 10")
        self.assertEqual(control.bill_address, "Jl. Vendor Lama No. 10")

        profile = self.vendor.purchase_vendor_profile
        profile.payment_terms = "Net 7"
        profile.save(update_fields=["payment_terms", "updated_at"])
        self.vendor.address = "Alamat vendor baru"
        self.vendor.save(update_fields=["address", "updated_at"])
        control.refresh_from_db()
        self.assertEqual(control.payment_terms, "Net 45 from accepted invoice.")
        self.assertEqual(control.vendor_address, "Jl. Vendor Lama No. 10")

    def test_vendor_payment_term_default_can_be_disabled(self):
        PurchaseSetting.objects.create(
            company=self.company,
            is_use_vendor_payment_terms=False,
        )
        VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor,
            payment_terms="Net 45",
        )
        order = self._order("NO-VENDOR-TERM")

        snapshot_purchase_order_commercial_defaults(order)

        self.assertEqual(order.purchase_control.payment_terms, "")

    def test_order_template_terms_are_immutable_po_snapshots(self):
        self.order_template.payment_terms = "50% advance, 50% on delivery"
        self.order_template.delivery_terms = "Delivered duty paid"
        self.order_template.legal_terms = "Indonesian law applies"
        self.order_template.save(
            update_fields=[
                "payment_terms",
                "delivery_terms",
                "legal_terms",
                "updated_at",
            ]
        )
        order = self._order("TERM-SNAPSHOT")

        apply_purchase_order_template(order, self.order_template, self.actor)

        control = PurchaseOrderControl.objects.get(order=order)
        self.assertEqual(control.payment_terms, "50% advance, 50% on delivery")
        self.assertEqual(control.delivery_terms, "Delivered duty paid")
        self.assertEqual(control.legal_terms, "Indonesian law applies")
        self.order_template.payment_terms = "Changed later"
        self.order_template.save(update_fields=["payment_terms", "updated_at"])
        control.refresh_from_db()
        self.assertEqual(control.payment_terms, "50% advance, 50% on delivery")

    def test_commercial_default_snapshot_query_count_is_batch_bounded(self):
        PurchaseSetting.objects.create(company=self.company)
        VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor,
            payment_terms="Net 30",
        )
        baseline_orders = [self._order(f"COMM-BASE-{index}") for index in range(2)]
        with CaptureQueriesContext(connection) as baseline:
            snapshot_purchase_order_commercial_defaults(baseline_orders)

        grown_orders = [self._order(f"COMM-GROWN-{index}") for index in range(12)]
        with CaptureQueriesContext(connection) as grown:
            snapshot_purchase_order_commercial_defaults(grown_orders)

        self.assertLessEqual(len(grown), len(baseline) + 1)

    def test_required_cost_center_blocks_confirmation_until_control_is_completed(self):
        PurchaseAccountingSetting.objects.create(
            company=self.company,
            payable_account_code="2100-AP",
            default_journal="PURCHASE",
            require_cost_center=True,
        )
        order = self._order("COST-CENTER")
        apply_purchase_order_template(order, self.order_template, self.actor)

        with self.assertRaisesMessage(ValidationError, "Cost Center is required"):
            confirm_purchase_order(order, self.actor)

        control = PurchaseOrderControl.objects.get(order=order)
        control.cost_center = "OPS-JKT"
        control.save(update_fields=["cost_center", "updated_at"])
        confirm_purchase_order(order, self.actor)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.CONFIRMED)

    def test_remark_template_is_snapshotted_to_request_and_order(self):
        template = PurchaseRemarkTemplate.objects.create(
            company=self.company,
            name="Delivery Snapshot",
            remark_type=PurchaseRemarkTemplate.RemarkType.DELIVERY,
            applicable_document=PurchaseRemarkTemplate.ApplicableDocument.ALL,
            content="Deliver during business hours.",
        )
        request = PurchaseRequest.objects.create(
            company=self.company,
            order_type=self.order_type,
            number="PR-REMARK-SNAPSHOT",
            requestor=self.actor,
            request_date=timezone.localdate(),
            required_date=timezone.localdate() + timedelta(days=3),
            destination_warehouse=self.warehouse,
            justification="Remark template parity",
        )
        order = self._order("REMARK-SNAPSHOT")

        apply_purchase_remark_template(request, template)
        apply_purchase_remark_template(order, template)

        request.refresh_from_db()
        order.refresh_from_db()
        control = PurchaseOrderControl.objects.get(order=order)
        self.assertEqual(request.notes, "Deliver during business hours.")
        self.assertEqual(request.remark_template_code, template.code)
        self.assertEqual(request.remark_template_name, template.name)
        self.assertEqual(order.notes, "Deliver during business hours.")
        self.assertEqual(control.remark_template_code, template.code)
        self.assertEqual(control.remark_template_name, template.name)

        template.content = "Updated wording."
        template.save(update_fields=["content", "updated_at"])
        request.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(request.notes, "Deliver during business hours.")
        self.assertEqual(order.notes, "Deliver during business hours.")
        self.assertIs(DOCUMENT_CONFIG["purchase-requests"]["header_form"], PurchaseRequestDocumentForm)

    def test_inactive_or_wrong_document_remark_template_is_rejected(self):
        request = PurchaseRequest.objects.create(
            company=self.company,
            number="PR-REMARK-REJECT",
            requestor=self.actor,
            request_date=timezone.localdate(),
            required_date=timezone.localdate() + timedelta(days=3),
            destination_warehouse=self.warehouse,
            justification="Remark rejection parity",
        )
        inactive = PurchaseRemarkTemplate.objects.create(
            company=self.company,
            name="Inactive Remark",
            content="Inactive",
            active=False,
        )
        po_only = PurchaseRemarkTemplate.objects.create(
            company=self.company,
            name="PO Only Remark",
            applicable_document=PurchaseRemarkTemplate.ApplicableDocument.PURCHASE_ORDER,
            content="PO only",
        )

        with self.assertRaisesMessage(ValidationError, "active Purchase Remark Template"):
            apply_purchase_remark_template(request, inactive)
        with self.assertRaisesMessage(ValidationError, "not applicable to Purchase Requests"):
            apply_purchase_remark_template(request, po_only)

    def test_template_application_query_count_does_not_grow_with_lines(self):
        baseline_order = self._order("BASE")
        with CaptureQueriesContext(connection) as baseline:
            apply_purchase_order_template(baseline_order, self.order_template, self.actor)

        PurchaseOrderTemplateLine.objects.bulk_create(
            [
                PurchaseOrderTemplateLine(
                    order_template=self.order_template,
                    sequence=20 + index,
                    product=self.product,
                    description=f"Configured Product {index}",
                    quantity=Decimal("1"),
                    uom="Unit",
                    price=Decimal("25"),
                )
                for index in range(30)
            ]
        )
        grown_order = self._order("GROWN")
        with CaptureQueriesContext(connection) as grown:
            apply_purchase_order_template(grown_order, self.order_template, self.actor)

        self.assertLessEqual(len(grown), len(baseline) + 1)
