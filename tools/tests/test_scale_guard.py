import importlib.util
import sys
from pathlib import Path
from unittest import TestCase


CHECKER = Path(__file__).resolve().parents[1] / "scale_guard.py"
SPEC = importlib.util.spec_from_file_location("erp_scale_guard", CHECKER)
scale_guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = scale_guard
SPEC.loader.exec_module(scale_guard)


class ScaleGuardTests(TestCase):
    def rules(self, source):
        return [finding.rule for finding in scale_guard.scan_source(source)]

    def test_query_inside_queryset_loop_is_sg001(self):
        self.assertIn(
            "SG001",
            self.rules(
                """
for company in Company.objects.all()[:10]:
    Product.objects.filter(company=company).exists()
"""
            ),
        )

    def test_batch_read_before_loop_is_clean(self):
        findings = scale_guard.scan_source(
            """
products = list(Product.objects.filter(company_id=company_id)[:100])
for product in products:
    result[product.pk] = product.name
"""
        )
        self.assertEqual(findings, [])

    def test_dictionary_get_inside_plain_list_loop_is_clean(self):
        findings = scale_guard.scan_source(
            """
for payload in payloads:
    names.append(payload.get("name"))
"""
        )
        self.assertEqual(findings, [])

    def test_dictionary_values_loop_is_not_a_queryset(self):
        findings = scale_guard.scan_source(
            """
for field in self.fields.values():
    existing = field.widget.attrs.get("class", "")
"""
        )
        self.assertEqual(findings, [])

    def test_queryset_none_is_bounded(self):
        findings = scale_guard.scan_source("rows = list(Product.objects.none())")
        self.assertEqual(findings, [])

    def test_bulk_create_of_explicit_batches_is_not_sg003(self):
        findings = scale_guard.scan_source(
            """
for batch in batches:
    Product.objects.bulk_create(batch, batch_size=200)
"""
        )
        self.assertNotIn("SG003", [finding.rule for finding in findings])

    def test_unbounded_queryset_evaluation_is_sg002(self):
        finding = scale_guard.scan_source("rows = list(Product.objects.all())")[0]
        self.assertEqual(finding.rule, "SG002")
        self.assertEqual(finding.severity, "high")

    def test_tenant_only_filter_is_still_unbounded(self):
        finding = scale_guard.scan_source(
            "rows = list(Product.objects.filter(company_id=company_id))"
        )[0]
        self.assertEqual(finding.rule, "SG002")
        self.assertEqual(finding.severity, "high")

    def test_business_scoped_queryset_is_medium_for_review(self):
        finding = scale_guard.scan_source(
            "rows = list(Line.objects.filter(order_id=order_id))"
        )[0]
        self.assertEqual(finding.rule, "SG002")
        self.assertEqual(finding.severity, "medium")

    def test_explicit_in_filter_is_input_bounded(self):
        findings = scale_guard.scan_source(
            "rows = list(Product.objects.filter(pk__in=product_ids))"
        )
        self.assertEqual(findings, [])

    def test_exact_primary_key_filter_is_input_bounded(self):
        findings = scale_guard.scan_source(
            "rows = list(Product.objects.filter(pk=product_id))"
        )
        self.assertEqual(findings, [])

    def test_single_record_get_result_is_not_a_queryset(self):
        findings = scale_guard.scan_source(
            """
order = Order.objects.get(pk=order_id)
for line in payload_lines:
    names.append(order.name)
"""
        )
        self.assertEqual(findings, [])

    def test_sliced_queryset_evaluation_is_bounded(self):
        self.assertNotIn("SG002", self.rules("rows = list(Product.objects.all()[:100])"))

    def test_write_inside_queryset_loop_is_sg003(self):
        self.assertIn(
            "SG003",
            self.rules(
                """
for product in Product.objects.filter(active=True):
    product.save(update_fields=[\"name\"])
"""
            ),
        )

    def test_explicit_compute_call_is_sg004(self):
        self.assertIn("SG004", self.rules("records._compute_balance()"))

    def test_dict_comprehension_is_scanned(self):
        self.assertIn(
            "SG002",
            self.rules(
                "{product.pk: product.name for product in Product.objects.all()}"
            ),
        )

    def test_reviewable_annotation_suppresses_matching_rule(self):
        finding = scale_guard.scan_source(
            """
for product in Product.objects.all()[:10]:
    # scale-guard: allow SG001 - bounded admin preview with ten rows
    Product.objects.filter(pk=product.pk).exists()
"""
        )[0]
        self.assertTrue(finding.suppressed)
        self.assertEqual(finding.rule, "SG001")

    def test_generic_annotation_does_not_suppress(self):
        finding = scale_guard.scan_source(
            """
for product in Product.objects.all()[:10]:
    # scale-guard: allow - bounded admin preview with ten rows
    Product.objects.filter(pk=product.pk).exists()
"""
        )[0]
        self.assertFalse(finding.suppressed)

    def test_django_test_modules_are_excluded(self):
        self.assertFalse(scale_guard.is_scannable(Path("addons/example/tests.py")))
        self.assertFalse(scale_guard.is_scannable(Path("addons/example/test_models.py")))
        self.assertFalse(scale_guard.is_scannable(Path("addons/example/tests_models.py")))
        self.assertFalse(scale_guard.is_scannable(Path("addons/example/models_test.py")))
