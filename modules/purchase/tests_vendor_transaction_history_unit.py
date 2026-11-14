from datetime import date, datetime
from decimal import Decimal
from unittest import TestCase

from .vendor_transaction_history_contract import as_date, delivery_snapshot


class VendorTransactionHistoryContractTests(TestCase):
    def test_on_time_delivery_uses_expected_and_actual_dates(self):
        snapshot = delivery_snapshot(
            ordered_quantity="10",
            received_quantity=Decimal("10"),
            expected_date=date(2026, 11, 14),
            actual_date=datetime(2026, 11, 13, 15, 30),
        )

        self.assertEqual(snapshot["delivery_status"], "On Time")
        self.assertEqual(snapshot["lead_time_variance"], -1)
        self.assertEqual(snapshot["remaining_quantity"], Decimal("0"))

    def test_late_partial_delivery_keeps_remaining_quantity(self):
        snapshot = delivery_snapshot(
            ordered_quantity=10,
            received_quantity=4,
            expected_date="2026-11-10",
            actual_date="2026-11-14",
        )

        self.assertEqual(snapshot["delivery_status"], "Late")
        self.assertEqual(snapshot["lead_time_variance"], 4)
        self.assertEqual(snapshot["remaining_quantity"], Decimal("6"))

    def test_missing_actual_date_is_pending(self):
        snapshot = delivery_snapshot(
            ordered_quantity=10,
            received_quantity=0,
            expected_date=date(2026, 11, 14),
            actual_date=None,
        )

        self.assertEqual(snapshot["delivery_status"], "Pending")
        self.assertIsNone(snapshot["lead_time_variance"])

    def test_missing_expected_date_is_received_without_invented_variance(self):
        snapshot = delivery_snapshot(
            ordered_quantity=10,
            received_quantity=10,
            expected_date=None,
            actual_date=date(2026, 11, 14),
        )

        self.assertEqual(snapshot["delivery_status"], "Received")
        self.assertIsNone(snapshot["lead_time_variance"])

    def test_remaining_quantity_never_becomes_negative(self):
        snapshot = delivery_snapshot(
            ordered_quantity=10,
            received_quantity=12,
            expected_date=None,
            actual_date=None,
        )

        self.assertEqual(snapshot["remaining_quantity"], Decimal("0"))

    def test_as_date_accepts_iso_and_datetime_values(self):
        self.assertEqual(as_date("2026-11-14T22:30:00"), date(2026, 11, 14))
        self.assertEqual(as_date(datetime(2026, 11, 14, 22, 30)), date(2026, 11, 14))
