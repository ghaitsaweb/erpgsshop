"""Expense claim line totals and bounded draft persistence."""

from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from .models import ExpenseClaimLine


def validate_expense_claim_lines(expense):
    summary = ExpenseClaimLine.objects.filter(expense_id=expense.pk).aggregate(
        line_count=Count("pk"), line_total=Sum("subtotal"),
        invalid_products=Count("pk", filter=(
            ~Q(product__company_id=F("expense__company_id"))
            | ~Q(product__activity_type="expense")
        )),
    )
    total = summary["line_total"] or Decimal("0.00")
    if not summary["line_count"] or total <= 0:
        raise ValidationError("Add at least one expense line before submitting.")
    if summary["invalid_products"]:
        raise ValidationError("Expense lines must use products of the claim company and expense activity.")
    if expense.amount != total:
        raise ValidationError("Expense amount does not match the detail lines. Save the document again.")


def save_expense_claim_lines(expense, formset):
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
        line.expense = expense
        line.subtotal = (line.quantity * line.unit_price).quantize(
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
        owned_ids = set(ExpenseClaimLine.objects.filter(
            expense_id=expense.pk, pk__in=touched_ids
        ).values_list("pk", flat=True))
        if owned_ids != touched_ids:
            raise ValidationError("Expense line does not belong to this document.")
    if delete_ids:
        ExpenseClaimLine.objects.filter(expense_id=expense.pk, pk__in=delete_ids).delete()
    if updates:
        ExpenseClaimLine.objects.bulk_update(
            updates, ["date", "description", "product", "quantity", "unit_price", "subtotal", "updated_at"]
        )
    if creates:
        ExpenseClaimLine.objects.bulk_create(creates)
