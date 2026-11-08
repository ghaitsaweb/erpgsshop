from decimal import Decimal, InvalidOperation

from django import forms
from django.utils import timezone

from .models import Account, AccountJournal, AccountPaymentMethod


class InvoicePaymentForm(forms.Form):
    PAYMENT_DIFFERENCE_CHOICES = (
        ("keep_open", "Keep Open"),
        ("mark_as_fully_paid", "Mark as Fully Paid"),
    )

    payment_amount = forms.DecimalField(
        label="Payment Amount",
        min_value=Decimal("0.01"),
        max_value=Decimal("9999999999999.99"),
        max_digits=15,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01", "data-payment-amount": ""}),
    )
    currency = forms.CharField(label="Currency", disabled=True, required=False)
    payment_date = forms.DateField(
        label="Payment Date",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    payment_method = forms.ModelChoiceField(
        label="Payment Method",
        queryset=AccountPaymentMethod.objects.none(),
    )
    journal = forms.CharField(label="Journal", disabled=True, required=False)
    invoice_amount = forms.DecimalField(label="Invoice Amount", disabled=True, required=False)
    amount_due = forms.DecimalField(label="Invoice Amount Due", disabled=True, required=False)
    payment_difference = forms.DecimalField(label="Payment Difference", disabled=True, required=False)
    payment_difference_handling = forms.ChoiceField(
        label="Payment Difference Handling",
        choices=PAYMENT_DIFFERENCE_CHOICES,
        initial="keep_open",
    )
    writeoff_account = forms.ModelChoiceField(
        label="Write-off Account",
        queryset=Account.objects.none(),
        required=False,
    )
    reference = forms.CharField(
        label="Reference",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g., TRX-2026-001 or bank reference"}),
    )

    def __init__(self, *args, invoice, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice
        is_customer = invoice.invoice_type == invoice.InvoiceType.CUSTOMER
        method_directions = ["incoming", "both"] if is_customer else ["outgoing", "both"]
        methods = AccountPaymentMethod.objects.filter(
            company_id=invoice.company_id,
            active=True,
            direction__in=method_directions,
            journal__active=True,
            journal__journal_type__in=[AccountJournal.JournalType.BANK, AccountJournal.JournalType.CASH],
        ).select_related("journal").order_by("code", "pk")
        self.fields["payment_method"].queryset = methods
        self.fields["writeoff_account"].queryset = Account.objects.filter(
            company_id=invoice.company_id,
            active=True,
            deprecated=False,
        ).order_by("code", "pk")

        selected_method = None
        selected_method_id = self.data.get("payment_method") if self.is_bound else None
        if selected_method_id and str(selected_method_id).isdigit():
            selected_method = methods.filter(pk=int(selected_method_id)).first()
        if selected_method is None:
            selected_method = methods.first()
        if selected_method is not None and not self.is_bound:
            self.initial.setdefault("payment_method", selected_method)

        payment_amount = invoice.balance
        if self.is_bound:
            try:
                payment_amount = Decimal(str(self.data.get("payment_amount", invoice.balance)))
            except (InvalidOperation, TypeError, ValueError):
                payment_amount = invoice.balance
        self.initial.update({
            "payment_amount": invoice.balance,
            "currency": invoice.company.currency,
            "journal": selected_method.journal if selected_method else "Configure an active payment method",
            "invoice_amount": invoice.total,
            "amount_due": invoice.balance,
            "payment_difference": invoice.balance - payment_amount,
            "reference": invoice.number,
        })
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"form-control {existing}".strip()
        self.fields["payment_method"].widget.attrs["data-payment-method"] = ""
        self.fields["journal"].widget.attrs["data-payment-journal"] = ""
        self.fields["payment_difference"].widget.attrs["data-payment-difference"] = ""
        self.fields["payment_difference_handling"].widget.attrs["data-payment-difference-handling"] = ""
        self.fields["writeoff_account"].widget.attrs["data-writeoff-account"] = ""

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get("payment_amount")
        handling = cleaned.get("payment_difference_handling")
        writeoff_account = cleaned.get("writeoff_account")
        if amount and amount < self.invoice.balance and handling == "mark_as_fully_paid" and not writeoff_account:
            self.add_error("writeoff_account", "Select a write-off account to mark the invoice as fully paid.")
        method = cleaned.get("payment_method")
        if method and not method.journal_id:
            self.add_error("payment_method", "The payment method must have a bank or cash journal.")
        return cleaned
