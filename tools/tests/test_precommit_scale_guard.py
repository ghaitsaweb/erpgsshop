import importlib.util
import sys
from pathlib import Path
from unittest import TestCase


SCANNER = (
    Path(__file__).resolve().parents[2]
    / ".agents"
    / "skills"
    / "precommit-scale-guard"
    / "scripts"
    / "scale_guard_scan.py"
)
SPEC = importlib.util.spec_from_file_location("erp_bundled_scale_guard", SCANNER)
scale_guard_scan = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = scale_guard_scan
SPEC.loader.exec_module(scale_guard_scan)


def findings(source):
    visitor = scale_guard_scan.ScaleVisitor("example.py", source.splitlines())
    visitor.visit(scale_guard_scan.ast.parse(source))
    return visitor.findings


class BundledScaleGuardTests(TestCase):
    def test_set_update_inside_plain_loop_is_not_an_orm_write(self):
        self.assertEqual(
            findings(
                """
for name in names:
    queryset_names.update({name})
"""
            ),
            [],
        )

    def test_queryset_update_inside_loop_is_sg003(self):
        result = findings(
            """
for company_id in company_ids:
    queryset.update(company_id=company_id)
"""
        )
        self.assertIn("SG003", [finding.rule for finding in result])
        self.assertEqual(result[0].severity, "medium")

    def test_write_inside_direct_queryset_loop_is_high(self):
        result = findings(
            """
for company in Company.objects.all():
    Product.objects.filter(company=company).update(active=False)
"""
        )
        sg003 = [finding for finding in result if finding.rule == "SG003"]
        self.assertEqual(len(sg003), 1)
        self.assertEqual(sg003[0].severity, "high")

    def test_dictionary_get_inside_plain_loop_is_clean(self):
        self.assertEqual(
            findings(
                """
for payload in payloads:
    names.append(payload.get("name"))
"""
            ),
            [],
        )

    def test_rule_specific_annotation_suppresses_the_exact_orm_call(self):
        self.assertEqual(
            findings(
                """
for batch in batches:
    # scale-guard: allow SG001 — one query per bounded five-hundred-row batch
    Product.objects.filter(pk__in=batch)
"""
            ),
            [],
        )
