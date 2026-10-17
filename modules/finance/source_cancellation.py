"""Explicit source-document cancellation adapters.

The journal reversal is accounting truth, while this registry keeps the source
workflow in sync and rejects unsupported source types before anything mutates.
"""

from dataclasses import dataclass

from django.apps import apps
from django.core.exceptions import ValidationError


@dataclass(frozen=True)
class CancellationAdapter:
    model_label: str
    cancelled_status: str
    allowed_statuses: frozenset[str]
    journal_source_types: tuple[str, ...] = ()


_ADAPTERS = {
    "account_invoice": CancellationAdapter(
        "core.Invoice", "cancelled", frozenset({"posted", "paid"}), ("account_invoice", "core.invoice")
    ),
    "core.invoice": CancellationAdapter(
        "core.Invoice", "cancelled", frozenset({"posted", "paid"}), ("account_invoice", "core.invoice")
    ),
    "core.saleorder": CancellationAdapter(
        "core.SaleOrder", "cancelled", frozenset({"confirmed", "completed"}), ("core.saleorder",)
    ),
    "core.purchaseorder": CancellationAdapter(
        "core.PurchaseOrder", "cancelled", frozenset({"confirmed", "completed"}), ("core.purchaseorder",)
    ),
    "other_income_expense": CancellationAdapter(
        "gsshop_finance.OtherIncomeExpense", "reversed", frozenset({"posted"}),
        ("other_income_expense", "gsshop_finance.otherincomeexpense")
    ),
    "gsshop_finance.otherincomeexpense": CancellationAdapter(
        "gsshop_finance.OtherIncomeExpense", "reversed", frozenset({"posted"}),
        ("other_income_expense", "gsshop_finance.otherincomeexpense")
    ),
}


def prepare_source_cancellation(cancellation):
    source_type = cancellation.source_model.strip().lower()
    adapter = _ADAPTERS.get(source_type)
    if adapter is None:
        raise ValidationError(
            f"Source type {cancellation.source_model!r} has no registered cancellation adapter."
        )
    model = apps.get_model(adapter.model_label)
    source = model.objects.select_for_update().filter(pk=cancellation.source_id).first()
    if source is None:
        raise ValidationError("The source document no longer exists.")
    company_id = getattr(source, "company_id", None)
    if company_id != cancellation.company_id:
        raise ValidationError("The source document does not belong to the cancellation company.")
    status = str(getattr(source, "status", ""))
    if status == adapter.cancelled_status:
        raise ValidationError("The source document is already cancelled or reversed.")
    if status not in adapter.allowed_statuses:
        raise ValidationError(f"Source document status {status!r} does not support cancellation.")
    return source, adapter


def apply_source_cancellation(source, adapter):
    source.status = adapter.cancelled_status
    update_fields = ["status"]
    if hasattr(source, "updated_at"):
        update_fields.append("updated_at")
    source.save(update_fields=update_fields)
    return source
