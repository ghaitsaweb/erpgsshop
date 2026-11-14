"""Pure helpers for HMX-shaped Vendor Transaction History evidence."""

from datetime import date, datetime
from decimal import Decimal


ZERO = Decimal("0")


def as_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def as_decimal(value):
    if value in (None, ""):
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def delivery_snapshot(*, ordered_quantity, received_quantity, expected_date, actual_date):
    ordered = as_decimal(ordered_quantity)
    received = as_decimal(received_quantity)
    expected = as_date(expected_date)
    actual = as_date(actual_date)
    remaining = max(ordered - received, ZERO)
    if actual is None:
        status = "Pending"
        variance = None
    elif expected is None:
        status = "Received"
        variance = None
    else:
        variance = (actual - expected).days
        status = "On Time" if variance <= 0 else "Late"
    return {
        "remaining_quantity": remaining,
        "expected_date": expected,
        "actual_delivery_date": actual,
        "delivery_status": status,
        "lead_time_variance": variance,
    }
