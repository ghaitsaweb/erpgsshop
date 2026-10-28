"""Cash advance document invariants and bounded detail-line persistence."""

from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from .models import CashAdvanceLine


def validate_cash_advance_lines(advance):
    summary = CashAdvanceLine.objects.filter(advance_id=advance.pk).aggregate(
        line_count=Count("pk"), line_total=Sum("subtotal"),
        invalid_products=Count("pk", filter=(
            ~Q(product__company_id=F("advance__employee__company_id"))
            | ~Q(product__activity_type="advance")
        )),
    )
    total = summary["line_total"] or Decimal("0.00")
    if not summary["line_count"] or total <= 0:
        raise ValidationError("Add at least one advance request line before submitting.")
    if summary["invalid_products"]:
        raise ValidationError("Advance lines must use products of the employee company and advance activity.")
    if advance.amount != total:
        raise ValidationError("Cash advance amount does not match the request lines. Save the document again.")


def save_cash_advance_lines(advance, formset):
    """Persist only validated lines of this advance with bounded batch writes."""
    creates, updates, delete_ids = [], [], []
    now = timezone.now()
    for form in formset.forms:
        data = form.cleaned_data
        if not data:
            continue
        if data.get("DELETE"):
            if form.instance.pk:
                delete_ids.append(form.instance.pk)
            continue
        line = form.save(commit=False)
        line.advance = advance
        line.subtotal = (line.quantity * line.request_amount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        line.updated_at = now
        if line.pk:
            updates.append(line)
        else:
            line.created_at = now
            creates.append(line)
    touched_ids = {line.pk for line in updates} | set(delete_ids)
    if touched_ids:
        owned_ids = set(CashAdvanceLine.objects.filter(
            advance_id=advance.pk, pk__in=touched_ids
        ).values_list("pk", flat=True))
        if owned_ids != touched_ids:
            raise ValidationError("Advance line does not belong to this document.")
    if delete_ids:
        CashAdvanceLine.objects.filter(advance_id=advance.pk, pk__in=delete_ids).delete()
    if updates:
        CashAdvanceLine.objects.bulk_update(
            updates, ["sequence", "product", "quantity", "request_amount", "subtotal", "updated_at"]
        )
    if creates:
        CashAdvanceLine.objects.bulk_create(creates)
