from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Contact, Product, PurchaseOrder, PurchaseOrderLine, Warehouse

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseSetting,
    VendorCategory,
    VendorPerformanceEvaluation,
    VendorPerformanceScoreHistory,
    VendorPerformanceTemplate,
    VendorPerformanceTemplateLine,
    VendorProfile,
)
from .services import recalculate_vendor_performance, transition_vendor_performance_evaluation


class VendorPerformanceParityTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username="vendor-performance-admin",
            password="test-password",
            email="vendor-performance@example.com",
        )
        self.company = Company.objects.create(code="VPE", name="Vendor Performance")
        self.category = VendorCategory.objects.create(
            company=self.company,
            code="VPE",
            name="Performance Vendors",
        )
        self.vendor_contact = Contact.objects.create(
            company=self.company,
            code="VPE-VENDOR",
            name="Performance Vendor",
            contact_type=Contact.ContactType.VENDOR,
        )
        self.vendor = VendorProfile.objects.create(
            company=self.company,
            partner=self.vendor_contact,
            category=self.category,
            status=VendorProfile.Status.ACTIVE,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            code="VPE-WH",
            name="Performance Warehouse",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="VPE-PRODUCT",
            name="Performance Product",
            product_type=Product.ProductType.STOCKABLE,
        )
        self.template = VendorPerformanceTemplate.objects.create(
            company=self.company,
            name="Balanced Vendor Evaluation",
            qualitative_weight=Decimal("40"),
            quantitative_weight=Decimal("60"),
            delivery_accuracy_weight=Decimal("50"),
            rejection_rate_weight=Decimal("50"),
            minimum_transaction_threshold=0,
        )
        VendorPerformanceTemplateLine.objects.bulk_create(
            [
                VendorPerformanceTemplateLine(
                    template=self.template,
                    sequence=10,
                    parameter_name="Accepted quality",
                    weight=Decimal("50"),
                    maximum_score=Decimal("5"),
                ),
                VendorPerformanceTemplateLine(
                    template=self.template,
                    sequence=20,
                    parameter_name="Service response",
                    weight=Decimal("50"),
                    maximum_score=Decimal("5"),
                ),
            ]
        )
        self.setting = PurchaseSetting.objects.create(
            company=self.company,
            require_vendor_prequalification=False,
            vendor_performance_default_template=self.template,
        )

    def evaluation(self, suffix="001"):
        today = timezone.localdate()
        return VendorPerformanceEvaluation.objects.create(
            company=self.company,
            vendor=self.vendor,
            template=self.template,
            evaluator=self.actor,
            period_start=today - timedelta(days=30),
            period_end=today,
            notes=f"Evaluation {suffix}",
        )

    def complete_evaluation(self, evaluation):
        evaluation.lines.update(assessment="Good")
        return evaluation

    def receipt_evidence(self, *, line_count=2, number_prefix="VPE"):
        order = PurchaseOrder.objects.create(
            company=self.company,
            number=f"PO-{number_prefix}",
            vendor=self.vendor_contact,
            warehouse=self.warehouse,
            status=PurchaseOrder.Status.CONFIRMED,
        )
        lines = PurchaseOrderLine.objects.bulk_create(
            [
                PurchaseOrderLine(
                    order=order,
                    product=self.product,
                    quantity=Decimal("10"),
                    unit_price=Decimal("20"),
                    received_quantity=Decimal("10") if index == 0 else Decimal("8"),
                )
                for index in range(line_count)
            ]
        )
        receipt = GoodsReceipt.objects.create(
            company=self.company,
            number=f"GR-{number_prefix}",
            order=order,
            warehouse=self.warehouse,
            receipt_date=timezone.localdate(),
            delivery_status=GoodsReceipt.DeliveryStatus.ON_TIME,
            status=GoodsReceipt.Status.RECEIVED,
        )
        GoodsReceiptLine.objects.bulk_create(
            [
                GoodsReceiptLine(
                    receipt=receipt,
                    order_line=line,
                    received_quantity=Decimal("10"),
                    accepted_quantity=Decimal("10") if index == 0 else Decimal("8"),
                    rejected_quantity=Decimal("0") if index == 0 else Decimal("2"),
                )
                for index, line in enumerate(lines)
            ]
        )
        return order

    def test_template_snapshot_and_approval_lifecycle_match_hmx(self):
        evaluation = self.evaluation()

        self.assertTrue(self.template.code.startswith("VPT-"))
        self.assertTrue(evaluation.number.startswith("VPE-"))
        self.assertEqual(
            list(evaluation.lines.values_list("parameter_name", flat=True)),
            ["Accepted quality", "Service response"],
        )

        self.complete_evaluation(evaluation)
        transition_vendor_performance_evaluation(evaluation, "submit", self.actor)
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, VendorPerformanceEvaluation.Status.WAITING_APPROVAL)
        self.assertEqual(evaluation.qualitative_score, Decimal("4.00"))

        with self.assertRaisesMessage(ValidationError, "at least 10 characters"):
            transition_vendor_performance_evaluation(evaluation, "reject", self.actor, reason="short")
        transition_vendor_performance_evaluation(
            evaluation,
            "reject",
            self.actor,
            reason="Vendor evidence requires correction",
        )
        evaluation.refresh_from_db()
        self.assertEqual(evaluation.status, VendorPerformanceEvaluation.Status.REJECTED)
        with self.assertRaisesMessage(ValidationError, "Only draft"):
            evaluation.notes = "Late edit"
            evaluation.save()

    def test_scoring_settings_require_an_active_valid_default_template(self):
        self.setting.vendor_performance_scoring_enabled = True
        self.setting.vendor_performance_default_template = None
        with self.assertRaisesMessage(ValidationError, "default Vendor Performance Template is required"):
            self.setting.save()

        self.setting.vendor_performance_default_template = self.template
        self.template.active = False
        self.template.save(update_fields=["active", "updated_at"])
        with self.assertRaisesMessage(ValidationError, "must be active"):
            self.setting.save()

        self.template.active = True
        self.template.save(update_fields=["active", "updated_at"])
        self.template.lines.filter(sequence=10).update(weight=Decimal("40"))
        with self.assertRaisesMessage(ValidationError, "criterion weights must total 100%"):
            self.setting.save()

        self.template.lines.filter(sequence=10).update(weight=Decimal("50"))
        self.template.vendor_categories.add(self.category)
        self.setting.vendor_performance_evaluation_period = 0
        with self.assertRaises(ValidationError) as invalid_period:
            self.setting.save()
        self.assertIn("vendor_performance_evaluation_period", invalid_period.exception.message_dict)

        self.setting.vendor_performance_evaluation_period = 12
        self.setting.save()
        self.setting.refresh_from_db()
        self.assertTrue(self.setting.vendor_performance_scoring_enabled)
        self.assertEqual(self.setting.vendor_performance_evaluation_period, 12)
        self.assertEqual(list(self.template.vendor_categories.all()), [self.category])

    def test_approval_calculates_receipt_score_and_immutable_history(self):
        self.receipt_evidence()
        self.setting.vendor_performance_scoring_enabled = True
        self.setting.save(update_fields=["vendor_performance_scoring_enabled", "updated_at"])
        evaluation = self.complete_evaluation(self.evaluation("SCORE"))

        transition_vendor_performance_evaluation(evaluation, "submit", self.actor)
        transition_vendor_performance_evaluation(evaluation, "approve", self.actor)

        evaluation.refresh_from_db()
        history = VendorPerformanceScoreHistory.objects.get(qualitative_evaluation=evaluation)
        self.vendor.refresh_from_db()
        self.assertEqual(evaluation.status, VendorPerformanceEvaluation.Status.CONFIRMED)
        self.assertEqual(evaluation.confirmed_by, self.actor)
        self.assertIsNotNone(evaluation.confirmed_at)
        self.assertEqual(history.qualitative_score, Decimal("4.00"))
        self.assertEqual(history.delivery_accuracy_score, Decimal("5.00"))
        self.assertEqual(history.rejection_rate_score, Decimal("4.50"))
        self.assertEqual(history.quantitative_score, Decimal("4.75"))
        self.assertEqual(history.final_score, Decimal("4.45"))
        self.assertEqual(self.vendor.latest_performance_score, Decimal("4.45"))
        with self.assertRaisesMessage(ValidationError, "immutable"):
            history.final_score = Decimal("5")
            history.save()

    def test_recalculation_query_count_does_not_grow_with_receipt_lines(self):
        self.setting.vendor_performance_scoring_enabled = True
        self.setting.save(update_fields=["vendor_performance_scoring_enabled", "updated_at"])
        evaluation = self.complete_evaluation(self.evaluation("QUERY"))
        transition_vendor_performance_evaluation(evaluation, "submit", self.actor)
        transition_vendor_performance_evaluation(evaluation, "approve", self.actor)
        self.receipt_evidence(line_count=1, number_prefix="QUERY-ONE")

        with CaptureQueriesContext(connection) as small_queries:
            recalculate_vendor_performance(self.vendor, self.actor)

        self.receipt_evidence(line_count=20, number_prefix="QUERY-MANY")
        with CaptureQueriesContext(connection) as many_queries:
            recalculate_vendor_performance(self.vendor, self.actor)

        self.assertLessEqual(len(many_queries), len(small_queries) + 1)
