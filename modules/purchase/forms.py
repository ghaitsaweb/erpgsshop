from decimal import Decimal

from django import forms
from django.forms import formset_factory
from django.utils import timezone

from core.models import Contact

from .models import AwardStrategy, PurchaseTender
from .services import MAX_PURCHASE_DOCUMENT_LINES


class PurchaseRequestRFQForm(forms.Form):
    vendors = forms.ModelMultipleChoiceField(
        queryset=Contact.objects.none(),
        help_text="One draft RFQ is created for each selected vendor.",
    )
    response_deadline = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def __init__(self, *args, company_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vendors"].queryset = Contact.objects.filter(company_id=company_id).order_by("name", "pk")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean_response_deadline(self):
        value = self.cleaned_data["response_deadline"]
        if value < timezone.localdate():
            raise forms.ValidationError("Response deadline cannot be in the past.")
        return value


class PurchaseRequestRFQLineForm(forms.Form):
    include = forms.BooleanField(required=False, initial=True)
    line_id = forms.IntegerField(widget=forms.HiddenInput)
    product = forms.CharField(disabled=True, required=False)
    description = forms.CharField(disabled=True, required=False)
    approved_quantity = forms.DecimalField(disabled=True, max_digits=18, decimal_places=3)
    already_converted = forms.DecimalField(disabled=True, max_digits=18, decimal_places=3)
    remaining_quantity = forms.DecimalField(disabled=True, max_digits=18, decimal_places=3)
    quantity = forms.DecimalField(
        max_digits=18,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )
    vendors = forms.ModelMultipleChoiceField(
        queryset=Contact.objects.none(),
        required=False,
        help_text="Optional override; blank uses the vendors selected above.",
    )

    def __init__(self, *args, company_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vendors"].queryset = Contact.objects.filter(company_id=company_id).order_by("name", "pk")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("include"):
            return cleaned
        quantity = cleaned.get("quantity")
        remaining = cleaned.get("remaining_quantity")
        if quantity is not None and remaining is not None and quantity > remaining:
            self.add_error(
                "quantity",
                f"RFQ quantity cannot exceed remaining quantity ({remaining}).",
            )
        return cleaned


PurchaseRequestRFQLineFormSet = formset_factory(
    PurchaseRequestRFQLineForm,
    extra=0,
    max_num=MAX_PURCHASE_DOCUMENT_LINES,
    validate_max=True,
    absolute_max=MAX_PURCHASE_DOCUMENT_LINES + 20,
)


class PurchaseRequestConversionLineForm(forms.Form):
    include = forms.BooleanField(required=False, initial=True)
    line_id = forms.IntegerField(widget=forms.HiddenInput)
    product = forms.CharField(disabled=True, required=False)
    description = forms.CharField(disabled=True, required=False)
    approved_quantity = forms.DecimalField(disabled=True, max_digits=18, decimal_places=3)
    remaining_quantity = forms.DecimalField(disabled=True, max_digits=18, decimal_places=3)
    quantity = forms.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0.001"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("include"):
            return cleaned
        quantity = cleaned.get("quantity")
        remaining = cleaned.get("remaining_quantity")
        if quantity is not None and remaining is not None and quantity > remaining:
            self.add_error("quantity", f"Quantity cannot exceed remaining quantity ({remaining}).")
        return cleaned


PurchaseRequestConversionLineFormSet = formset_factory(
    PurchaseRequestConversionLineForm,
    extra=0,
    max_num=MAX_PURCHASE_DOCUMENT_LINES,
    validate_max=True,
    absolute_max=MAX_PURCHASE_DOCUMENT_LINES + 20,
)


class BlanketOrderRFQLineForm(forms.Form):
    include = forms.BooleanField(required=False, initial=True)
    line_id = forms.IntegerField(widget=forms.HiddenInput)
    product = forms.CharField(disabled=True, required=False)
    description = forms.CharField(disabled=True, required=False)
    agreed_quantity = forms.DecimalField(disabled=True, max_digits=18, decimal_places=3)
    released_quantity = forms.DecimalField(disabled=True, max_digits=18, decimal_places=3)
    remaining_quantity = forms.DecimalField(disabled=True, max_digits=18, decimal_places=3)
    quantity = forms.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0.001"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("include"):
            return cleaned
        quantity = cleaned.get("quantity")
        remaining = cleaned.get("remaining_quantity")
        if quantity is not None and remaining is not None and quantity > remaining:
            self.add_error("quantity", f"Release quantity cannot exceed remaining quantity ({remaining}).")
        return cleaned


BlanketOrderRFQLineFormSet = formset_factory(
    BlanketOrderRFQLineForm,
    extra=0,
    max_num=MAX_PURCHASE_DOCUMENT_LINES,
    validate_max=True,
    absolute_max=MAX_PURCHASE_DOCUMENT_LINES + 20,
)


class PurchaseRequestApprovalLineForm(forms.Form):
    line_id = forms.IntegerField(widget=forms.HiddenInput)
    product = forms.CharField(disabled=True, required=False)
    description = forms.CharField(disabled=True, required=False)
    requested_quantity = forms.DecimalField(disabled=True, max_digits=18, decimal_places=3)
    action = forms.ChoiceField(choices=[("approve", "Approve"), ("reject", "Reject")])
    approved_quantity = forms.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0"))
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()
        action = cleaned.get("action")
        quantity = cleaned.get("approved_quantity")
        requested = cleaned.get("requested_quantity")
        reason = (cleaned.get("reason") or "").strip()
        if action == "approve" and (quantity is None or quantity <= 0):
            self.add_error("approved_quantity", "Approved quantity must be greater than zero.")
        if action == "reject" and quantity not in {None, Decimal("0")}:
            self.add_error("approved_quantity", "Rejected lines must have zero approved quantity.")
        if (action == "reject" or (action == "approve" and quantity != requested)) and len(reason) < 10:
            self.add_error("reason", "Reason must be at least 10 characters.")
        return cleaned


PurchaseRequestApprovalLineFormSet = formset_factory(
    PurchaseRequestApprovalLineForm,
    extra=0,
    max_num=MAX_PURCHASE_DOCUMENT_LINES,
    validate_max=True,
    absolute_max=MAX_PURCHASE_DOCUMENT_LINES + 20,
)


class PurchaseRequestReRequestLineForm(forms.Form):
    include = forms.BooleanField(required=False, initial=True)
    line_id = forms.IntegerField(widget=forms.HiddenInput)
    product = forms.CharField(disabled=True, required=False)
    description = forms.CharField(disabled=True, required=False)
    original_quantity = forms.DecimalField(disabled=True, max_digits=18, decimal_places=3)
    approved_quantity = forms.DecimalField(disabled=True, max_digits=18, decimal_places=3)
    already_re_requested = forms.DecimalField(disabled=True, max_digits=18, decimal_places=3)
    remaining_quantity = forms.DecimalField(disabled=True, max_digits=18, decimal_places=3)
    quantity = forms.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal("0.001"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("include"):
            return cleaned
        quantity = cleaned.get("quantity")
        remaining = cleaned.get("remaining_quantity")
        if quantity is not None and remaining is not None and quantity > remaining:
            self.add_error("quantity", f"Re-Request quantity cannot exceed remaining quantity ({remaining}).")
        return cleaned


PurchaseRequestReRequestLineFormSet = formset_factory(
    PurchaseRequestReRequestLineForm,
    extra=0,
    max_num=MAX_PURCHASE_DOCUMENT_LINES,
    validate_max=True,
    absolute_max=MAX_PURCHASE_DOCUMENT_LINES + 20,
)


class PurchaseRequestBlanketForm(forms.Form):
    vendor = forms.ModelChoiceField(queryset=Contact.objects.none())
    date_end = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, company_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vendor"].queryset = Contact.objects.filter(company_id=company_id).order_by("name", "pk")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class PurchaseRequestTenderForm(forms.Form):
    title = forms.CharField(max_length=180)
    tender_type = forms.ChoiceField(choices=PurchaseTender.TenderType.choices)
    method = forms.ChoiceField(choices=PurchaseTender.Method.choices)
    award_strategy = forms.ModelChoiceField(queryset=AwardStrategy.objects.none())
    close_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, company_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["award_strategy"].queryset = AwardStrategy.objects.filter(
            company_id=company_id,
            active=True,
        ).order_by("name", "pk")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class PurchaseRequestDirectForm(forms.Form):
    vendor = forms.ModelChoiceField(queryset=Contact.objects.none())
    justification = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, company_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vendor"].queryset = Contact.objects.filter(company_id=company_id).order_by("name", "pk")
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
