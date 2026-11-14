"""ORM-independent Vendor Performance scoring contract.

The helpers mirror the HMX scoring service while returning ``Decimal`` values
that can be persisted without a float round-trip.
"""

from datetime import date, datetime
from decimal import Decimal


ZERO = Decimal("0")
HUNDRED = Decimal("100")
SCORE_QUANTUM = Decimal("0.01")


def _decimal(value):
    if value in (None, ""):
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class VendorPerformanceScoringService:
    """Pure scoring helpers shared by the Purchase service and unit tests."""

    QUALITATIVE_ONLY = "qualitative_only"
    MARK_INSUFFICIENT = "mark_insufficient"
    BLOCK_FINAL_SCORE = "block_final_score"

    @classmethod
    def _round(cls, value):
        return _decimal(value).quantize(SCORE_QUANTUM)

    @classmethod
    def _clamp(cls, value, score_scale):
        scale = max(_decimal(score_scale), ZERO)
        return min(max(_decimal(value), ZERO), scale)

    @classmethod
    def delivery_accuracy_score(cls, completed_lines, on_time_lines, score_scale):
        completed = _decimal(completed_lines)
        on_time = _decimal(on_time_lines)
        scale = _decimal(score_scale)
        if completed <= 0 or scale <= 0:
            return ZERO
        return cls._round(cls._clamp(on_time / completed * scale, scale))

    @classmethod
    def rejection_rate_score(cls, received_quantity, rejected_quantity, score_scale):
        received = _decimal(received_quantity)
        rejected = max(_decimal(rejected_quantity), ZERO)
        scale = _decimal(score_scale)
        if scale <= 0:
            return ZERO
        if received <= 0:
            return cls._round(scale)
        rejection_ratio = min(max(rejected / received, ZERO), Decimal("1"))
        return cls._round(cls._clamp((Decimal("1") - rejection_ratio) * scale, scale))

    @classmethod
    def final_score(
        cls,
        qualitative_score,
        quantitative_score,
        qualitative_weight,
        quantitative_weight,
    ):
        qualitative = _decimal(qualitative_score)
        quantitative = _decimal(quantitative_score)
        qualitative_ratio = _decimal(qualitative_weight) / HUNDRED
        quantitative_ratio = _decimal(quantitative_weight) / HUNDRED
        return cls._round(
            qualitative * qualitative_ratio + quantitative * quantitative_ratio
        )

    @classmethod
    def quantitative_score(
        cls,
        delivery_score,
        rejection_score,
        delivery_weight,
        rejection_weight,
    ):
        delivery_ratio = _decimal(delivery_weight) / HUNDRED
        rejection_ratio = _decimal(rejection_weight) / HUNDRED
        return cls._round(
            _decimal(delivery_score) * delivery_ratio
            + _decimal(rejection_score) * rejection_ratio
        )

    @classmethod
    def delivery_evidence(cls, lines, period_start, period_end):
        completed_count = 0
        on_time_count = 0
        pending_count = 0
        source_line_ids = []
        for line in lines or []:
            expected = cls._date_only(line.get("expected_date"))
            actual = cls._date_only(line.get("actual_date"))
            fully_received = bool(line.get("fully_received"))
            if not fully_received or not actual:
                pending_count += 1
                continue
            if (period_start and actual < period_start) or (period_end and actual > period_end):
                continue
            if not expected:
                continue
            completed_count += 1
            if actual <= expected:
                on_time_count += 1
            if line.get("id"):
                source_line_ids.append(line["id"])
        return {
            "completed_count": completed_count,
            "on_time_count": on_time_count,
            "pending_count": pending_count,
            "source_line_ids": source_line_ids,
        }

    @staticmethod
    def _date_only(value):
        if not value:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])
