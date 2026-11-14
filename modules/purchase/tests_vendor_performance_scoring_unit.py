from datetime import date, datetime
from decimal import Decimal
from unittest import TestCase

from .vendor_performance_scoring import VendorPerformanceScoringService


class VendorPerformanceScoringUnitTests(TestCase):
    def test_delivery_accuracy_uses_on_time_completed_lines(self):
        self.assertEqual(
            VendorPerformanceScoringService.delivery_accuracy_score(4, 3, 5),
            Decimal("3.75"),
        )

    def test_partial_receipt_is_pending_and_not_completed(self):
        evidence = VendorPerformanceScoringService.delivery_evidence(
            [
                {
                    "id": 11,
                    "expected_date": date(2026, 8, 1),
                    "actual_date": datetime(2026, 8, 1, 12, 30),
                    "fully_received": True,
                },
                {
                    "id": 12,
                    "expected_date": date(2026, 8, 1),
                    "actual_date": None,
                    "fully_received": False,
                },
            ],
            date(2026, 1, 1),
            date(2026, 8, 31),
        )
        self.assertEqual(
            evidence,
            {
                "completed_count": 1,
                "on_time_count": 1,
                "pending_count": 1,
                "source_line_ids": [11],
            },
        )

    def test_delivery_evidence_excludes_missing_expected_and_outside_period(self):
        evidence = VendorPerformanceScoringService.delivery_evidence(
            [
                {
                    "id": 21,
                    "expected_date": None,
                    "actual_date": date(2026, 8, 1),
                    "fully_received": True,
                },
                {
                    "id": 22,
                    "expected_date": date(2025, 12, 31),
                    "actual_date": date(2025, 12, 31),
                    "fully_received": True,
                },
            ],
            date(2026, 1, 1),
            date(2026, 8, 31),
        )
        self.assertEqual(evidence["completed_count"], 0)
        self.assertEqual(evidence["pending_count"], 0)
        self.assertEqual(evidence["source_line_ids"], [])

    def test_zero_rejection_gets_maximum_score(self):
        self.assertEqual(
            VendorPerformanceScoringService.rejection_rate_score(20, 0, 5),
            Decimal("5.00"),
        )

    def test_rejection_score_is_clamped_for_invalid_over_rejection(self):
        self.assertEqual(
            VendorPerformanceScoringService.rejection_rate_score(5, 8, 5),
            Decimal("0.00"),
        )

    def test_both_weight_levels_are_applied(self):
        quantitative = VendorPerformanceScoringService.quantitative_score(4, 2, 75, 25)
        self.assertEqual(quantitative, Decimal("3.50"))
        self.assertEqual(
            VendorPerformanceScoringService.final_score(4, 3, 40, 60),
            Decimal("3.40"),
        )
