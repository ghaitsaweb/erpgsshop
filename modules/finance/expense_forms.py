from decimal import Decimal, ROUND_HALF_UP

from django import forms
from django.forms.models import BaseInlineFormSet
from django.urls import reverse

from core.forms import SearchableMasterWidget, StyledModelForm
from modules.hr.models import ExpenseProduct

from .models import ExpenseClaim, ExpenseClaimLine


class ExpenseClaimForm(StyledModelForm):
    class Meta:
        model = ExpenseClaim
        fields = ["company", "employee", "cash_advance", "reference", "claim_date", "category", "description"]


class ExpenseClaimLineForm(StyledModelForm):
    product = forms.ModelChoiceField(
        queryset=ExpenseProduct.objects.filter(active=True, activity_type=ExpenseProduct.ActivityType.EXPENSE),
        widget=SearchableMasterWidget(),
    )
    subtotal_display = forms.DecimalField(label="Subtotal", required=False, disabled=True, decimal_places=2)

    class Meta:
        model = ExpenseClaimLine
        fields = ["date", "description", "product", "quantity", "unit_price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].widget.attrs.update({
            "data-master-search-url": reverse("expense_claim_product_search"),
            "data-master-search-placeholder": "Search expense product...",
        })
        if self.instance and self.instance.product_id:
            product = self.instance.product
            self.fields["product"].widget.attrs["data-initial-label"] = f"{product.code} — {product.name}"
            self.fields["subtotal_display"].initial = self.instance.subtotal


class ExpenseClaimLineFormSet(BaseInlineFormSet):
    total_amount = Decimal("0.00")

    def clean(self):
        super().clean()
        posted_ids = [
            value for form in self.forms
            if (value := form.data.get(form.add_prefix("id")))
        ]
        if len(posted_ids) != len(set(posted_ids)) or any(not value.isdecimal() for value in posted_ids):
            raise forms.ValidationError("Invalid expense line references.")
        allowed_ids = set(ExpenseClaimLine.objects.filter(
            expense_id=self.instance.pk, pk__in=posted_ids
        ).values_list("pk", flat=True)) if self.instance.pk else set()
        if {int(value) for value in posted_ids} != allowed_ids:
            raise forms.ValidationError("Expense line does not belong to this document.")
        if any(self.errors):
            return
        active = [data for data in self.cleaned_data if data and not data.get("DELETE")]
        if not active:
            raise forms.ValidationError("Add at least one expense line.")
        self.total_amount = sum(
            ((data["quantity"] * data["unit_price"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
             for data in active),
            Decimal("0.00"),
        )
