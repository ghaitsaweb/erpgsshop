from decimal import Decimal
import uuid

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import Company, Contact, Employee, Invoice, InvoiceLine, TimeStampedModel


class Account(TimeStampedModel):
    class AccountType(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        EQUITY = "equity", "Equity"
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="accounts")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=160)
    account_type = models.CharField(max_length=16, choices=AccountType.choices)
    parent = models.ForeignKey("self", on_delete=models.PROTECT, blank=True, null=True, related_name="children")
    currency = models.CharField(max_length=3, blank=True, help_text="Leave blank to use the company currency.")
    allow_reconciliation = models.BooleanField(default=False)
    deprecated = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_account_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class AccountTag(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="account_tags")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    color = models.CharField(max_length=20, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_account_tag_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class AccountingCostCenter(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="accounting_cost_centers")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=140)
    parent = models.ForeignKey("self", on_delete=models.PROTECT, blank=True, null=True, related_name="children")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_cost_center_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class AccountJournal(TimeStampedModel):
    class JournalType(models.TextChoices):
        GENERAL = "general", "General"
        SALES = "sales", "Sales"
        PURCHASE = "purchase", "Purchase"
        CASH = "cash", "Cash"
        BANK = "bank", "Bank"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="account_journals")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    journal_type = models.CharField(max_length=16, choices=JournalType.choices, default=JournalType.GENERAL)
    default_debit_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="default_debit_journals"
    )
    default_credit_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="default_credit_journals"
    )
    currency = models.CharField(max_length=3, blank=True)
    sequence_prefix = models.CharField(max_length=20, default="JE")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["journal_type", "code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_journal_code_per_company")]

    def clean(self):
        errors = {}
        for field_name in ("default_debit_account", "default_credit_account"):
            account = getattr(self, field_name, None)
            if account and account.company_id != self.company_id:
                errors[field_name] = "The default account must belong to the journal company."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.code} - {self.name}"


class JournalEntry(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="journal_entries")
    journal = models.ForeignKey(
        AccountJournal, on_delete=models.PROTECT, blank=True, null=True, related_name="entries"
    )
    number = models.CharField(max_length=40)
    entry_date = models.DateField(default=timezone.localdate)
    reference = models.CharField(max_length=80, blank=True)
    description = models.CharField(max_length=240, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)
    source_type = models.CharField(max_length=50, blank=True)
    source_reference = models.CharField(max_length=80, blank=True)
    auto_post = models.BooleanField(default=False)
    reversal_of = models.OneToOneField(
        "self", on_delete=models.PROTECT, blank=True, null=True, related_name="reversal_entry"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    total_debit = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    total_credit = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    posted_at = models.DateTimeField(blank=True, null=True, editable=False)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True, related_name="posted_journal_entries"
    )
    cancellation_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-entry_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_journal_number_per_company")]

    @property
    def balanced(self):
        return self.total_debit == self.total_credit and self.total_debit > 0

    def __str__(self):
        return self.number


class JournalLine(TimeStampedModel):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="journal_lines")
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="journal_lines")
    cost_center = models.ForeignKey(
        AccountingCostCenter, on_delete=models.PROTECT, blank=True, null=True, related_name="journal_lines"
    )
    description = models.CharField(max_length=200, blank=True)
    debit = models.DecimalField(max_digits=18, decimal_places=2, default=0, validators=[MinValueValidator(Decimal("0"))])
    credit = models.DecimalField(max_digits=18, decimal_places=2, default=0, validators=[MinValueValidator(Decimal("0"))])
    currency = models.CharField(max_length=3, blank=True)
    amount_currency = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    due_date = models.DateField(blank=True, null=True)
    reconciled = models.BooleanField(default=False)

    def clean(self):
        if self.debit and self.credit:
            raise ValidationError("A journal line cannot contain both debit and credit.")
        if not self.debit and not self.credit:
            raise ValidationError("A journal line requires a debit or credit value.")
        if self.account_id and self.entry_id and self.account.company_id != self.entry.company_id:
            raise ValidationError("Account and journal entry must belong to the same company.")
        if self.partner_id and self.entry_id and self.partner.company_id != self.entry.company_id:
            raise ValidationError("Partner and journal entry must belong to the same company.")
        if self.cost_center_id and self.entry_id and self.cost_center.company_id != self.entry.company_id:
            raise ValidationError("Cost center and journal entry must belong to the same company.")

    def __str__(self):
        return f"{self.entry.number} · {self.account.code}"


class JournalLineCostCenterAllocation(TimeStampedModel):
    journal_line = models.ForeignKey(JournalLine, on_delete=models.CASCADE, related_name="cost_center_allocations")
    cost_center = models.ForeignKey(
        AccountingCostCenter,
        on_delete=models.PROTECT,
        related_name="journal_line_allocations",
    )
    percentage = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001")), MaxValueValidator(Decimal("100"))],
    )

    class Meta:
        ordering = ["journal_line", "id"]
        constraints = [
            models.UniqueConstraint(fields=["journal_line", "cost_center"], name="unique_journal_line_cost_center")
        ]

    @property
    def allocated_amount(self):
        return (self.journal_line.debit or self.journal_line.credit) * self.percentage / Decimal("100")

    def clean(self):
        if self.journal_line_id and self.cost_center_id:
            if self.journal_line.entry.company_id != self.cost_center.company_id:
                raise ValidationError({"cost_center": "Cost center must belong to the journal company."})

    def __str__(self):
        return f"{self.journal_line} · {self.cost_center} ({self.percentage}%)"


class FiscalYear(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="fiscal_years")
    name = models.CharField(max_length=80)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["-start_date"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_fiscal_year_per_company")]

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date must not be earlier than start date."})

    def __str__(self):
        return self.name


class FiscalPeriod(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"
        LOCKED = "locked", "Locked"

    fiscal_year = models.ForeignKey(FiscalYear, on_delete=models.PROTECT, related_name="periods")
    name = models.CharField(max_length=80)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["start_date"]
        constraints = [models.UniqueConstraint(fields=["fiscal_year", "name"], name="unique_period_per_fiscal_year")]

    @property
    def company(self):
        return self.fiscal_year.company

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date must not be earlier than start date."})
        if self.fiscal_year_id and (
            self.start_date < self.fiscal_year.start_date or self.end_date > self.fiscal_year.end_date
        ):
            raise ValidationError("Fiscal period dates must be inside the fiscal year.")

    def __str__(self):
        return f"{self.fiscal_year.name} · {self.name}"


class FiscalPosition(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="fiscal_positions")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    country_code = models.CharField(max_length=2, blank=True)
    note = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_fiscal_position_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class AccountTax(TimeStampedModel):
    class TaxScope(models.TextChoices):
        SALES = "sales", "Sales"
        PURCHASE = "purchase", "Purchase"
        BOTH = "both", "Sales & Purchase"

    class TaxMethod(models.TextChoices):
        PERCENT = "percent", "Percentage"
        FIXED = "fixed", "Fixed Amount"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="account_taxes")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    tax_scope = models.CharField(max_length=16, choices=TaxScope.choices, default=TaxScope.BOTH)
    method = models.CharField(max_length=16, choices=TaxMethod.choices, default=TaxMethod.PERCENT)
    rate = models.DecimalField(max_digits=9, decimal_places=4, default=0)
    price_included = models.BooleanField(default=False)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="taxes")
    refund_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="refund_taxes"
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_tax_code_per_company")]

    def clean(self):
        for account in (self.account, self.refund_account):
            if account and account.company_id != self.company_id:
                raise ValidationError("Tax accounts must belong to the tax company.")

    def __str__(self):
        return f"{self.code} - {self.name}"


class WithholdingTax(TimeStampedModel):
    class PartyType(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        VENDOR = "vendor", "Vendor"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="withholding_taxes")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    party_type = models.CharField(max_length=16, choices=PartyType.choices)
    rate = models.DecimalField(max_digits=9, decimal_places=4)
    minimum_base = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="withholding_taxes")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_wht_code_per_company")]

    def clean(self):
        if self.account_id and self.account.company_id != self.company_id:
            raise ValidationError({"account": "The account must belong to the withholding tax company."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class PaymentTerm(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="payment_terms")
    name = models.CharField(max_length=120)
    days = models.PositiveIntegerField(default=0)
    early_discount_percent = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    early_discount_days = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["days", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_payment_term_per_company")]

    def __str__(self):
        return self.name


class CompanyBankAccount(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="bank_accounts")
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="bank_accounts")
    bank_name = models.CharField(max_length=120)
    account_number = models.CharField(max_length=80)
    account_holder = models.CharField(max_length=160)
    currency = models.CharField(max_length=3, blank=True)
    iban = models.CharField(max_length=60, blank=True)
    swift_code = models.CharField(max_length=20, blank=True)
    opening_balance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    receipt_cheque_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="receipt_cheque_bank_accounts",
    )
    payment_cheque_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="payment_cheque_bank_accounts",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["bank_name", "account_number"]
        constraints = [models.UniqueConstraint(fields=["company", "account_number"], name="unique_bank_account_per_company")]

    def clean(self):
        if self.journal_id and self.journal.company_id != self.company_id:
            raise ValidationError({"journal": "The journal must belong to the bank account company."})
        if any(account and account.company_id != self.company_id for account in (self.receipt_cheque_account, self.payment_cheque_account)):
            raise ValidationError("Cheque clearing accounts must belong to the bank account company.")

    def __str__(self):
        return f"{self.bank_name} · {self.account_number}"


class BankStatement(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        IMPORTED = "imported", "Imported"
        RECONCILED = "reconciled", "Reconciled"
        CLOSED = "closed", "Closed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="bank_statements")
    bank_account = models.ForeignKey(CompanyBankAccount, on_delete=models.PROTECT, related_name="statements")
    reference = models.CharField(max_length=50)
    date_from = models.DateField()
    date_to = models.DateField()
    opening_balance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    closing_balance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    import_source = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-date_to", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "reference"], name="unique_bank_statement_per_company")]

    def clean(self):
        if self.bank_account_id and self.bank_account.company_id != self.company_id:
            raise ValidationError({"bank_account": "The bank account must belong to the statement company."})
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValidationError({"date_to": "End date must not be earlier than start date."})

    def __str__(self):
        return self.reference


class BankStatementLine(TimeStampedModel):
    statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name="lines")
    transaction_date = models.DateField()
    reference = models.CharField(max_length=80, blank=True)
    partner_name = models.CharField(max_length=160, blank=True)
    description = models.CharField(max_length=240)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    matched_journal_line = models.ForeignKey(
        JournalLine, on_delete=models.PROTECT, blank=True, null=True, related_name="bank_statement_matches"
    )
    reconciled = models.BooleanField(default=False)

    class Meta:
        ordering = ["transaction_date", "id"]

    def __str__(self):
        return f"{self.statement.reference} · {self.transaction_date} · {self.amount}"


class ReconciliationRule(TimeStampedModel):
    class MatchType(models.TextChoices):
        LABEL = "label", "Label Contains"
        AMOUNT = "amount", "Exact Amount"
        PARTNER = "partner", "Partner"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="reconciliation_rules")
    name = models.CharField(max_length=120)
    sequence = models.PositiveIntegerField(default=10)
    match_type = models.CharField(max_length=16, choices=MatchType.choices)
    match_value = models.CharField(max_length=160)
    counterpart_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="reconciliation_rules")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "name"]

    def clean(self):
        if self.counterpart_account_id and self.counterpart_account.company_id != self.company_id:
            raise ValidationError({"counterpart_account": "The account must belong to the rule company."})

    def __str__(self):
        return self.name


class AccountPayment(TimeStampedModel):
    class Direction(models.TextChoices):
        RECEIVE = "receive", "Customer Receipt"
        SEND = "send", "Vendor Payment"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        RECONCILED = "reconciled", "Reconciled"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="account_payments")
    number = models.CharField(max_length=40)
    payment_date = models.DateField(default=timezone.localdate)
    direction = models.CharField(max_length=16, choices=Direction.choices)
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="account_payments")
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="payments")
    payment_method = models.CharField(max_length=40, default="bank_transfer")
    currency = models.CharField(max_length=3, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    reference = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(
        JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="payment"
    )

    class Meta:
        ordering = ["-payment_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_payment_number_per_company")]

    def clean(self):
        if self.partner_id and self.partner.company_id != self.company_id:
            raise ValidationError({"partner": "The partner must belong to the payment company."})
        if self.journal_id and self.journal.company_id != self.company_id:
            raise ValidationError({"journal": "The journal must belong to the payment company."})

    def __str__(self):
        return self.number


class PaymentAllocation(TimeStampedModel):
    payment = models.ForeignKey(AccountPayment, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payment_allocations")
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["payment", "invoice"], name="unique_invoice_payment_allocation")]

    def clean(self):
        if self.payment_id and self.invoice_id and self.payment.company_id != self.invoice.company_id:
            raise ValidationError("Payment and invoice must belong to the same company.")

    def __str__(self):
        return f"{self.payment.number} · {self.invoice.number}"


class AccountDeposit(TimeStampedModel):
    class DepositType(models.TextChoices):
        CUSTOMER = "customer", "Customer Deposit"
        VENDOR = "vendor", "Vendor Deposit"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RECEIVED = "received", "Received"
        APPLIED = "applied", "Applied"
        REFUNDED = "refunded", "Refunded"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="account_deposits")
    number = models.CharField(max_length=40)
    deposit_type = models.CharField(max_length=16, choices=DepositType.choices, default=DepositType.CUSTOMER)
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="account_deposits")
    deposit_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    unapplied_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="deposits")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    reference = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-deposit_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_deposit_number_per_company")]

    def __str__(self):
        return self.number


class RentalAccountingSettings(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="rental_accounting_settings")
    rental_downpayment_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="rental_downpayment_settings"
    )

    class Meta:
        verbose_name_plural = "Rental accounting settings"

    def clean(self):
        account = self.rental_downpayment_account
        if account and account.company_id != self.company_id:
            raise ValidationError("Rental down-payment account must belong to the selected company.")
        if account and account.account_type != Account.AccountType.LIABILITY:
            raise ValidationError("Rental down-payment account must be a liability account.")
        if self.pk:
            previous_account_id = RentalAccountingSettings.objects.filter(pk=self.pk).values_list(
                "rental_downpayment_account_id", flat=True
            ).first()
            if previous_account_id != self.rental_downpayment_account_id and RentalInvoiceAccounting.objects.filter(
                rental_terms__order__company_id=self.company_id,
                invoice_kind=RentalInvoiceAccounting.InvoiceKind.DOWNPAYMENT,
                invoice__status__in=[Invoice.Status.POSTED, Invoice.Status.PAID],
            ).exists():
                raise ValidationError(
                    "Rental down-payment account cannot be changed after a rental down-payment invoice is posted."
                )

    def __str__(self):
        return f"Rental Accounting · {self.company}"


class RentalOrderAccounting(TimeStampedModel):
    class DepositStatus(models.TextChoices):
        NOT_REQUIRED = "not_required", "Not Required"
        INSUFFICIENT = "insufficient", "Insufficient"
        SUFFICIENT = "sufficient", "Sufficient"
        HELD = "held", "Held"
        RELEASED = "released", "Released"

    order = models.OneToOneField("core.SaleOrder", on_delete=models.CASCADE, related_name="rental_accounting")
    deposit_required = models.BooleanField(default=False)
    required_deposit_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    downpayment_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    downpayment_invoice = models.OneToOneField(
        Invoice,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="rental_downpayment_terms",
    )
    settlement_invoice = models.OneToOneField(
        Invoice,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="rental_settlement_terms",
    )
    selected_customer_deposit = models.ForeignKey(
        AccountDeposit,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="rental_order_terms",
    )
    deposit_status = models.CharField(
        max_length=20, choices=DepositStatus.choices, default=DepositStatus.NOT_REQUIRED
    )

    @property
    def available_deposit_balance(self):
        return self.selected_customer_deposit.unapplied_amount if self.selected_customer_deposit_id else Decimal("0")

    @property
    def remaining_after_deposit_hold(self):
        if self.deposit_status == self.DepositStatus.HELD:
            return self.available_deposit_balance
        return self.available_deposit_balance - self.required_deposit_amount

    def clean(self):
        if self.required_deposit_amount < 0:
            raise ValidationError({"required_deposit_amount": "Required deposit cannot be negative."})
        if self.downpayment_amount < 0:
            raise ValidationError({"downpayment_amount": "Rental down payment cannot be negative."})
        if self.order_id and self.order.status != self.order.Status.DRAFT and self.pk:
            previous = RentalOrderAccounting.objects.filter(pk=self.pk).values(
                "deposit_required", "required_deposit_amount", "selected_customer_deposit_id"
            ).first()
            current = {
                "deposit_required": self.deposit_required,
                "required_deposit_amount": self.required_deposit_amount,
                "selected_customer_deposit_id": self.selected_customer_deposit_id,
            }
            if previous and previous != current:
                raise ValidationError("Rental deposit terms can only be edited while the sales order is draft.")
        deposit = self.selected_customer_deposit
        if deposit:
            if deposit.company_id != self.order.company_id or deposit.partner_id != self.order.customer_id:
                raise ValidationError("Customer deposit must match the rental order company and customer.")
            if deposit.deposit_type != AccountDeposit.DepositType.CUSTOMER:
                raise ValidationError("Rental orders can only use customer deposits.")
            if deposit.status != AccountDeposit.Status.RECEIVED:
                raise ValidationError("Rental orders require a received customer deposit.")
        if self.deposit_status not in {self.DepositStatus.HELD, self.DepositStatus.RELEASED}:
            if not self.deposit_required or self.required_deposit_amount <= 0:
                self.deposit_status = self.DepositStatus.NOT_REQUIRED
            elif deposit and deposit.unapplied_amount >= self.required_deposit_amount:
                self.deposit_status = self.DepositStatus.SUFFICIENT
            else:
                self.deposit_status = self.DepositStatus.INSUFFICIENT

    def __str__(self):
        return f"{self.order.number} · rental deposit"


class RentalDepositHold(TimeStampedModel):
    class Status(models.TextChoices):
        HELD = "held", "Held"
        RELEASED = "released", "Released"

    rental_terms = models.OneToOneField(
        RentalOrderAccounting, on_delete=models.CASCADE, related_name="deposit_hold"
    )
    account_deposit = models.ForeignKey(AccountDeposit, on_delete=models.PROTECT, related_name="rental_deposit_holds")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.HELD)
    held_at = models.DateTimeField(default=timezone.now)
    held_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="held_rental_deposits"
    )
    released_at = models.DateTimeField(blank=True, null=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="released_rental_deposits",
    )

    class Meta:
        ordering = ["id"]

    def clean(self):
        if self.amount <= 0:
            raise ValidationError({"amount": "Held amount must be greater than zero."})
        if self.rental_terms_id and self.account_deposit_id:
            order = self.rental_terms.order
            if self.account_deposit.company_id != order.company_id or self.account_deposit.partner_id != order.customer_id:
                raise ValidationError("Deposit hold must match the rental order company and customer.")

    def __str__(self):
        return f"{self.rental_terms.order.number} · {self.amount} · {self.status}"


class RentalInvoiceAccounting(TimeStampedModel):
    class InvoiceKind(models.TextChoices):
        DOWNPAYMENT = "downpayment", "Rental Down Payment"
        SETTLEMENT = "settlement", "Rental Settlement"

    invoice = models.OneToOneField(Invoice, on_delete=models.CASCADE, related_name="rental_provenance")
    rental_terms = models.ForeignKey(
        RentalOrderAccounting,
        on_delete=models.PROTECT,
        related_name="invoice_accounting_records",
    )
    invoice_kind = models.CharField(max_length=16, choices=InvoiceKind.choices)
    source_downpayment_invoice = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="settlements",
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def clean(self):
        order = self.rental_terms.order
        if self.invoice.company_id != order.company_id or self.invoice.partner_id != order.customer_id:
            raise ValidationError("Rental invoice provenance must match the rental order company and customer.")
        if self.invoice.invoice_type != Invoice.InvoiceType.CUSTOMER:
            raise ValidationError("Rental accounting provenance requires a customer invoice.")
        if self.invoice_kind == self.InvoiceKind.DOWNPAYMENT and self.source_downpayment_invoice_id:
            raise ValidationError({"source_downpayment_invoice": "A down-payment invoice cannot reference another down payment."})
        if self.invoice_kind == self.InvoiceKind.SETTLEMENT:
            source = self.source_downpayment_invoice
            if source and (
                source.invoice_kind != self.InvoiceKind.DOWNPAYMENT
                or source.rental_terms_id != self.rental_terms_id
            ):
                raise ValidationError({"source_downpayment_invoice": "Settlement source must be a down payment for the same rental order."})

    def __str__(self):
        return f"{self.invoice.number} · {self.get_invoice_kind_display()}"


class RentalInvoiceLineAccounting(TimeStampedModel):
    invoice_line = models.OneToOneField(
        InvoiceLine,
        on_delete=models.CASCADE,
        related_name="rental_provenance",
    )
    rental_invoice = models.ForeignKey(
        RentalInvoiceAccounting,
        on_delete=models.CASCADE,
        related_name="line_accounting_records",
    )
    source_downpayment_line = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="settlement_lines",
    )

    def clean(self):
        if self.invoice_line.invoice_id != self.rental_invoice.invoice_id:
            raise ValidationError("Rental invoice line provenance must belong to the linked invoice.")
        source = self.source_downpayment_line
        if source and (
            self.rental_invoice.invoice_kind != RentalInvoiceAccounting.InvoiceKind.SETTLEMENT
            or source.rental_invoice.invoice_kind != RentalInvoiceAccounting.InvoiceKind.DOWNPAYMENT
            or source.rental_invoice.rental_terms_id != self.rental_invoice.rental_terms_id
        ):
            raise ValidationError("Settlement line source must be a down-payment line for the same rental order.")

    def __str__(self):
        return f"{self.rental_invoice} · {self.invoice_line.description}"


class ServiceWorkOrderAccounting(TimeStampedModel):
    class Status(models.TextChoices):
        READY = "ready", "Ready"
        BILL_DRAFT = "bill_draft", "Vendor Bill Draft"
        BILL_POSTED = "bill_posted", "Vendor Bill Posted"
        ACCOUNTING_ERROR = "accounting_error", "Accounting Error"

    work_order = models.OneToOneField(
        "gsshop_purchase.ServiceWorkOrder",
        on_delete=models.PROTECT,
        related_name="accounting_link",
    )
    vendor_bill = models.OneToOneField(
        Invoice,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="service_work_order_accounting",
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.READY)
    error_message = models.TextField(blank=True)

    def clean(self):
        if self.vendor_bill_id:
            if self.vendor_bill.invoice_type != Invoice.InvoiceType.VENDOR:
                raise ValidationError({"vendor_bill": "Service work orders must link to a vendor bill."})
            if (
                self.vendor_bill.company_id != self.work_order.company_id
                or self.vendor_bill.partner_id != self.work_order.vendor_id
            ):
                raise ValidationError({"vendor_bill": "Vendor bill must match the work order company and vendor."})

    def __str__(self):
        return f"{self.work_order.number} · accounting"


class MarketplaceAccountingSettings(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="marketplace_accounting_settings")
    provider = models.CharField(max_length=60, help_text="Marketplace/provider key supplied by the installed adapter.")
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="marketplace_settings")
    marketplace_receivable_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="marketplace_receivable_settings"
    )
    settlement_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="marketplace_settlement_settings"
    )
    revenue_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="marketplace_revenue_settings")
    fee_expense_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="marketplace_fee_settings"
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company", "provider"]
        constraints = [
            models.UniqueConstraint(fields=["company", "provider"], name="unique_marketplace_accounting_provider")
        ]

    def clean(self):
        records = (
            self.journal,
            self.marketplace_receivable_account,
            self.settlement_account,
            self.revenue_account,
            self.fee_expense_account,
        )
        if any(record and record.company_id != self.company_id for record in records):
            raise ValidationError("All marketplace accounting settings must belong to the selected company.")

    def __str__(self):
        return f"{self.company.code} · {self.provider}"


class MarketplaceAccountingOrder(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ORDER_POSTED = "order_posted", "Order Posted"
        SETTLED = "settled", "Settled"
        FAILED = "failed", "Failed"
        REVERSED = "reversed", "Reversed"

    accounting_settings = models.ForeignKey(
        MarketplaceAccountingSettings,
        on_delete=models.PROTECT,
        related_name="accounting_orders",
    )
    external_order_id = models.CharField(max_length=120)
    reference = models.CharField(max_length=120)
    transaction_date = models.DateField(default=timezone.localdate)
    partner = models.ForeignKey(
        Contact,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="marketplace_accounting_orders",
    )
    gross_amount = models.DecimalField(max_digits=18, decimal_places=2)
    fee_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    refund_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    settlement_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    order_event = models.OneToOneField(
        "AccountingIntegrationEvent",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="marketplace_order_posting",
    )
    settlement_event = models.OneToOneField(
        "AccountingIntegrationEvent",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="marketplace_settlement_posting",
    )
    payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="marketplace_accounting_orders",
    )

    class Meta:
        ordering = ["-transaction_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["accounting_settings", "external_order_id"], name="unique_marketplace_external_accounting_order"
            )
        ]

    def clean(self):
        if self.partner_id and self.partner.company_id != self.accounting_settings.company_id:
            raise ValidationError({"partner": "Marketplace customer must belong to the settings company."})
        amounts = (self.gross_amount, self.fee_amount, self.refund_amount, self.settlement_amount)
        if any(amount < 0 for amount in amounts):
            raise ValidationError("Marketplace accounting amounts cannot be negative.")
        if self.settlement_amount and self.settlement_amount + self.fee_amount + self.refund_amount != self.gross_amount:
            raise ValidationError("Settlement, fee, and refund amounts must add up to the gross order amount.")

    def __str__(self):
        return f"{self.accounting_settings.provider} · {self.external_order_id}"


class SalesPromotionAccounting(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="sales_promotion_accounting")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=140)
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="sales_promotion_settings")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_sales_promotion_accounting")
        ]

    def clean(self):
        if self.expense_account_id and self.expense_account.company_id != self.company_id:
            raise ValidationError({"expense_account": "Promotion expense account must belong to the promotion company."})
        if self.expense_account_id and self.expense_account.account_type != Account.AccountType.EXPENSE:
            raise ValidationError({"expense_account": "Promotion account must be an expense account."})
        if self.pk:
            previous_id = SalesPromotionAccounting.objects.filter(pk=self.pk).values_list(
                "expense_account_id", flat=True
            ).first()
            if previous_id != self.expense_account_id and SalesInvoicePromotionSplit.objects.filter(
                promotion_id=self.pk
            ).exists():
                raise ValidationError("Promotion expense account cannot change after the promotion is invoiced.")

    def __str__(self):
        return f"{self.code} - {self.name}"


class SaleOrderInvoiceAccounting(TimeStampedModel):
    class Status(models.TextChoices):
        READY = "ready", "Ready"
        DOWNPAYMENT_DRAFT = "downpayment_draft", "Down-Payment Draft"
        SETTLEMENT_DRAFT = "settlement_draft", "Settlement Draft"
        COMPLETED = "completed", "Completed"
        ACCOUNTING_ERROR = "accounting_error", "Accounting Error"

    order = models.OneToOneField("core.SaleOrder", on_delete=models.PROTECT, related_name="invoice_accounting")
    downpayment_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    downpayment_invoice = models.OneToOneField(
        Invoice,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="sale_downpayment_order_accounting",
    )
    settlement_invoice = models.OneToOneField(
        Invoice,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="sale_settlement_order_accounting",
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.READY)
    error_message = models.TextField(blank=True)

    def clean(self):
        if self.downpayment_amount < 0:
            raise ValidationError({"downpayment_amount": "Sales down payment cannot be negative."})

    def __str__(self):
        return f"{self.order.number} · invoicing"


class SalesInvoiceAccounting(TimeStampedModel):
    class InvoiceKind(models.TextChoices):
        DOWNPAYMENT = "downpayment", "Sales Down Payment"
        SETTLEMENT = "settlement", "Sales Settlement"

    invoice = models.OneToOneField(Invoice, on_delete=models.CASCADE, related_name="sales_provenance")
    order_accounting = models.ForeignKey(
        SaleOrderInvoiceAccounting,
        on_delete=models.PROTECT,
        related_name="invoice_provenance",
    )
    invoice_kind = models.CharField(max_length=16, choices=InvoiceKind.choices)
    source_downpayment_invoice = models.ForeignKey(
        "self", on_delete=models.PROTECT, blank=True, null=True, related_name="settlement_provenance"
    )

    def clean(self):
        order = self.order_accounting.order
        if self.invoice.company_id != order.company_id or self.invoice.partner_id != order.customer_id:
            raise ValidationError("Sales invoice provenance must match the sales order company and customer.")

    def __str__(self):
        return f"{self.invoice.number} · {self.get_invoice_kind_display()}"


class SalesInvoiceLineProvenance(TimeStampedModel):
    invoice_line = models.OneToOneField(
        InvoiceLine, on_delete=models.CASCADE, related_name="sales_provenance"
    )
    sales_invoice = models.ForeignKey(
        SalesInvoiceAccounting, on_delete=models.CASCADE, related_name="line_provenance"
    )
    source_downpayment_line = models.ForeignKey(
        "self", on_delete=models.PROTECT, blank=True, null=True, related_name="settlement_applications"
    )

    def clean(self):
        if self.invoice_line.invoice_id != self.sales_invoice.invoice_id:
            raise ValidationError("Sales invoice line provenance must belong to its invoice.")

    def __str__(self):
        return f"{self.sales_invoice} · {self.invoice_line.description}"


class SalesInvoicePromotionSplit(TimeStampedModel):
    invoice_line = models.ForeignKey(
        InvoiceLine, on_delete=models.CASCADE, related_name="promotion_accounting_splits"
    )
    promotion = models.ForeignKey(
        SalesPromotionAccounting, on_delete=models.PROTECT, related_name="invoice_splits"
    )
    discount_amount = models.DecimalField(
        max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    expense_account_snapshot = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, related_name="sales_promotion_invoice_splits"
    )

    class Meta:
        ordering = ["invoice_line", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["invoice_line", "promotion"], name="unique_invoice_line_promotion_split"
            )
        ]

    def clean(self):
        if not self.expense_account_snapshot_id and self.promotion_id:
            self.expense_account_snapshot = self.promotion.expense_account
        company_id = self.invoice_line.invoice.company_id
        if self.promotion.company_id != company_id or self.expense_account_snapshot.company_id != company_id:
            raise ValidationError("Promotion split, invoice, and expense account must belong to the same company.")
        if self.expense_account_snapshot.account_type != Account.AccountType.EXPENSE:
            raise ValidationError("Promotion split snapshot must use an expense account.")

    def save(self, *args, **kwargs):
        if not self.expense_account_snapshot_id and self.promotion_id:
            self.expense_account_snapshot = self.promotion.expense_account
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invoice_line} · {self.promotion.code}"


class SchoolAccountingSettings(TimeStampedModel):
    school = models.OneToOneField(
        "gsshop_education.School", on_delete=models.PROTECT, related_name="accounting_settings"
    )
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="school_settings")
    receivable_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="school_receivable_settings"
    )
    default_revenue_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="school_revenue_settings"
    )
    late_return_revenue_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="school_library_fine_settings"
    )
    late_return_fine_per_day = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    active = models.BooleanField(default=True)

    def clean(self):
        company_id = self.school.company_id
        related = {
            "journal": self.journal,
            "receivable_account": self.receivable_account,
            "default_revenue_account": self.default_revenue_account,
            "late_return_revenue_account": self.late_return_revenue_account,
        }
        errors = {
            field: "School accounting configuration must belong to the school's company."
            for field, record in related.items()
            if record.company_id != company_id
        }
        if self.receivable_account.account_type != Account.AccountType.ASSET:
            errors["receivable_account"] = "School receivable must be an asset account."
        for field in ("default_revenue_account", "late_return_revenue_account"):
            if getattr(self, field).account_type != Account.AccountType.INCOME:
                errors[field] = "School revenue accounts must be income accounts."
        if self.late_return_fine_per_day < 0:
            errors["late_return_fine_per_day"] = "Late-return fine cannot be negative."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.school} · accounting"


class StudentAccountingProfile(TimeStampedModel):
    student = models.OneToOneField(
        "gsshop_education.Student", on_delete=models.PROTECT, related_name="accounting_profile"
    )
    billing_contact = models.ForeignKey(
        Contact, on_delete=models.PROTECT, related_name="student_accounting_profiles"
    )

    def clean(self):
        if self.billing_contact_id and self.billing_contact.company_id != self.student.school.company_id:
            raise ValidationError({"billing_contact": "Student billing contact must belong to the school company."})

    def __str__(self):
        return f"{self.student} · {self.billing_contact}"


class SchoolFeeType(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="school_fee_types")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=120)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_school_fee_type")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class SchoolFeeStructure(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    class SourceType(models.TextChoices):
        ADMISSION = "admission", "Admission"
        REGISTRATION = "registration", "Registration"
        ENROLLMENT = "enrollment", "Enrollment"
        COURSE = "course", "Course"
        EXTRACURRICULAR = "extracurricular", "Extracurricular"
        MANUAL = "manual", "Manual"

    school = models.ForeignKey(
        "gsshop_education.School", on_delete=models.PROTECT, related_name="fee_structures"
    )
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    source_type = models.CharField(max_length=24, choices=SourceType.choices, default=SourceType.MANUAL)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["school", "code"]
        constraints = [
            models.UniqueConstraint(fields=["school", "code"], name="unique_school_fee_structure")
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class SchoolFeeItem(TimeStampedModel):
    class Frequency(models.TextChoices):
        ONE_TIME = "one_time", "One Time"
        TERM = "term", "Term"
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    structure = models.ForeignKey(SchoolFeeStructure, on_delete=models.CASCADE, related_name="items")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=180)
    fee_type = models.ForeignKey(SchoolFeeType, on_delete=models.PROTECT, related_name="fee_items")
    frequency = models.CharField(max_length=16, choices=Frequency.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    course = models.ForeignKey(
        "gsshop_education.Course", on_delete=models.PROTECT, blank=True, null=True, related_name="fee_items"
    )
    revenue_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="school_fee_items"
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["structure", "id"]
        constraints = [
            models.UniqueConstraint(fields=["structure", "code"], name="unique_school_fee_item")
        ]

    def clean(self):
        company_id = self.structure.school.company_id
        if self.fee_type_id and self.fee_type.company_id != company_id:
            raise ValidationError({"fee_type": "Fee type must belong to the school company."})
        if self.course_id and self.course.school_id != self.structure.school_id:
            raise ValidationError({"course": "Course must belong to the fee-structure school."})
        if self.revenue_account_id:
            if self.revenue_account.company_id != company_id:
                raise ValidationError({"revenue_account": "Revenue account must belong to the school company."})
            if self.revenue_account.account_type != Account.AccountType.INCOME:
                raise ValidationError({"revenue_account": "Fee revenue must use an income account."})

    def __str__(self):
        return f"{self.structure.code} · {self.name}"


class StudentFeeSchedule(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        CANCELLED = "cancelled", "Cancelled"
        ENDED = "ended", "Ended"

    student = models.ForeignKey(
        "gsshop_education.Student", on_delete=models.PROTECT, related_name="fee_schedules"
    )
    fee_structure = models.ForeignKey(
        SchoolFeeStructure, on_delete=models.PROTECT, related_name="student_schedules"
    )
    effective_start_date = models.DateField(default=timezone.localdate)
    effective_end_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    change_note = models.TextField(blank=True)

    class Meta:
        ordering = ["student", "effective_start_date", "id"]

    def clean(self):
        if self.student.school_id != self.fee_structure.school_id:
            raise ValidationError("Student and fee structure must belong to the same school.")
        if self.effective_end_date and self.effective_end_date < self.effective_start_date:
            raise ValidationError({"effective_end_date": "End date cannot be before start date."})

    def __str__(self):
        return f"{self.student} · {self.fee_structure.code}"


class StudentFeeScheduleLine(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        INVOICED = "invoiced", "Invoiced"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    schedule = models.ForeignKey(StudentFeeSchedule, on_delete=models.CASCADE, related_name="lines")
    fee_item = models.ForeignKey(SchoolFeeItem, on_delete=models.PROTECT, related_name="schedule_lines")
    period_key = models.CharField(max_length=128)
    invoice_date = models.DateField()
    due_date = models.DateField()
    amount_snapshot = models.DecimalField(max_digits=18, decimal_places=2)
    revenue_account_snapshot = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="school_fee_schedule_lines"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, blank=True, null=True, related_name="school_fee_schedule_lines"
    )
    failure_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["invoice_date", "id"]
        constraints = [
            models.UniqueConstraint(fields=["schedule", "period_key"], name="unique_student_fee_period")
        ]

    def clean(self):
        company_id = self.schedule.student.school.company_id
        if self.fee_item.structure_id != self.schedule.fee_structure_id:
            raise ValidationError({"fee_item": "Schedule line fee item must belong to its fee structure."})
        if self.revenue_account_snapshot.company_id != company_id:
            raise ValidationError({"revenue_account_snapshot": "Revenue snapshot must belong to the school company."})
        if self.amount_snapshot <= 0:
            raise ValidationError({"amount_snapshot": "Scheduled fee must be positive."})

    def __str__(self):
        return f"{self.schedule} · {self.period_key}"


class SchoolInvoiceAccounting(TimeStampedModel):
    class SourceKind(models.TextChoices):
        FEE_SCHEDULE = "fee_schedule", "Fee Schedule"
        LIBRARY_FINE = "library_fine", "Library Fine"
        MANUAL = "manual", "Manual"

    invoice = models.OneToOneField(Invoice, on_delete=models.CASCADE, related_name="school_accounting")
    school = models.ForeignKey(
        "gsshop_education.School", on_delete=models.PROTECT, related_name="accounting_invoices"
    )
    student = models.ForeignKey(
        "gsshop_education.Student", on_delete=models.PROTECT, related_name="accounting_invoices"
    )
    source_kind = models.CharField(max_length=20, choices=SourceKind.choices)

    def clean(self):
        if self.student.school_id != self.school_id or self.invoice.company_id != self.school.company_id:
            raise ValidationError("School invoice, student, and school must share one company boundary.")

    def __str__(self):
        return f"{self.invoice.number} · {self.student}"


class SchoolInvoiceLineAccounting(TimeStampedModel):
    invoice_line = models.OneToOneField(
        InvoiceLine, on_delete=models.CASCADE, related_name="school_accounting"
    )
    school_invoice = models.ForeignKey(
        SchoolInvoiceAccounting, on_delete=models.CASCADE, related_name="line_accounting"
    )
    schedule_line = models.OneToOneField(
        StudentFeeScheduleLine,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="invoice_line_accounting",
    )
    book_loan = models.ForeignKey(
        "gsshop_education.BookLoan",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="fine_invoice_lines",
    )
    revenue_account_snapshot = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="school_invoice_lines"
    )

    def clean(self):
        if self.invoice_line.invoice_id != self.school_invoice.invoice_id:
            raise ValidationError("School invoice line metadata must belong to its invoice.")
        if self.schedule_line_id and self.book_loan_id:
            raise ValidationError("School invoice line can reference a schedule line or book loan, not both.")

    def __str__(self):
        return f"{self.school_invoice} · {self.invoice_line.description}"


class SchoolBillingRun(TimeStampedModel):
    class RunType(models.TextChoices):
        MANUAL = "manual", "Manual"
        SCHEDULED = "scheduled", "Scheduled"

    school = models.ForeignKey(
        "gsshop_education.School", on_delete=models.PROTECT, related_name="billing_runs"
    )
    run_type = models.CharField(max_length=16, choices=RunType.choices, default=RunType.MANUAL)
    run_date = models.DateField(default=timezone.localdate)
    requested_count = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True, related_name="school_billing_runs"
    )

    class Meta:
        ordering = ["-run_date", "-id"]

    def __str__(self):
        return f"{self.school.code} · {self.run_date}"


class SchoolBillingRunLine(TimeStampedModel):
    class Result(models.TextChoices):
        CREATED = "created", "Created"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    billing_run = models.ForeignKey(SchoolBillingRun, on_delete=models.CASCADE, related_name="lines")
    schedule_line = models.ForeignKey(
        StudentFeeScheduleLine, on_delete=models.PROTECT, related_name="billing_run_lines"
    )
    result = models.CharField(max_length=16, choices=Result.choices)
    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, blank=True, null=True, related_name="school_billing_run_lines"
    )
    failure_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["billing_run", "id"]

    def __str__(self):
        return f"{self.billing_run} · {self.schedule_line.period_key}"


class SchoolInstallmentPolicy(TimeStampedModel):
    class Interval(models.TextChoices):
        WEEKLY = "weekly", "Weekly"
        BI_WEEKLY = "bi_weekly", "Bi-weekly"
        MONTHLY = "monthly", "Monthly"
        TERMLY = "termly", "Termly"
        CUSTOM = "custom", "Custom"

    school = models.OneToOneField(
        "gsshop_education.School", on_delete=models.PROTECT, related_name="installment_policy"
    )
    enabled = models.BooleanField(default=False)
    max_installment_count = models.PositiveSmallIntegerField(default=3)
    interval_type = models.CharField(max_length=16, choices=Interval.choices, default=Interval.MONTHLY)
    custom_gap_days = models.PositiveIntegerField(blank=True, null=True)
    approval_due_days = models.PositiveIntegerField(default=0)
    allow_custom_schedule = models.BooleanField(default=True)
    allow_down_payment = models.BooleanField(default=True)

    def clean(self):
        if self.max_installment_count < 1:
            raise ValidationError({"max_installment_count": "At least one installment is required."})
        if self.interval_type == self.Interval.CUSTOM and not self.custom_gap_days:
            raise ValidationError({"custom_gap_days": "Custom interval requires a positive day gap."})

    def __str__(self):
        return f"{self.school} · installment policy"


class SchoolInstallmentRequest(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="school_installment_requests")
    student = models.ForeignKey(
        "gsshop_education.Student", on_delete=models.PROTECT, related_name="installment_requests"
    )
    requested_count = models.PositiveSmallIntegerField(default=2)
    preferred_first_due_date = models.DateField(blank=True, null=True)
    down_payment_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    rejection_reason = models.TextField(blank=True)
    approved_count = models.PositiveSmallIntegerField(blank=True, null=True)
    approved_first_due_date = models.DateField(blank=True, null=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="school_installment_requests"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="approved_school_installment_requests",
    )

    def clean(self):
        try:
            billing_contact_id = self.student.accounting_profile.billing_contact_id
        except ObjectDoesNotExist:
            billing_contact_id = None
        if not billing_contact_id or self.invoice.partner_id != billing_contact_id:
            raise ValidationError("Installment invoice must belong to the student's billing contact.")
        if self.down_payment_amount < 0 or self.down_payment_amount >= self.invoice.balance:
            raise ValidationError({"down_payment_amount": "Down payment must be non-negative and below invoice balance."})

    def __str__(self):
        return f"{self.invoice.number} · installment request"


class SchoolInstallmentPlan(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        RESCHEDULING = "rescheduling", "Rescheduling"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    request = models.OneToOneField(
        SchoolInstallmentRequest, on_delete=models.PROTECT, related_name="plan"
    )
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="school_installment_plans")
    student = models.ForeignKey(
        "gsshop_education.Student", on_delete=models.PROTECT, related_name="installment_plans"
    )
    fee_schedule = models.ForeignKey(
        StudentFeeSchedule,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="installment_plans",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    installment_count = models.PositiveSmallIntegerField(default=2)
    first_due_date = models.DateField()
    interval_type = models.CharField(max_length=16, choices=SchoolInstallmentPolicy.Interval.choices)
    custom_gap_days = models.PositiveIntegerField(blank=True, null=True)
    down_payment_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    reschedule_reason = models.TextField(blank=True)

    def clean(self):
        if self.invoice_id != self.request.invoice_id or self.student_id != self.request.student_id:
            raise ValidationError("Installment plan must preserve its approved request provenance.")

    def __str__(self):
        return f"{self.invoice.number} · installment plan"


class SchoolInstallmentLine(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DUE_SOON = "due_soon", "Due Soon"
        OVERDUE = "overdue", "Overdue"
        PARTIALLY_PAID = "partially_paid", "Partially Paid"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    plan = models.ForeignKey(SchoolInstallmentPlan, on_delete=models.CASCADE, related_name="lines")
    sequence = models.PositiveSmallIntegerField()
    due_date = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ["due_date", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "sequence"], name="unique_school_installment_sequence")
        ]

    @property
    def outstanding_amount(self):
        return self.amount - self.paid_amount

    def clean(self):
        if self.amount <= 0 or self.paid_amount < 0 or self.paid_amount > self.amount:
            raise ValidationError("Installment amounts are inconsistent.")

    def __str__(self):
        return f"{self.plan.invoice.number} · {self.sequence}"


class SchoolInstallmentRescheduleLog(TimeStampedModel):
    plan = models.ForeignKey(SchoolInstallmentPlan, on_delete=models.PROTECT, related_name="reschedule_logs")
    reason = models.TextField()
    schedule_snapshot = models.JSONField(default=list)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="school_installment_reschedules"
    )

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.plan} · {self.created_at:%Y-%m-%d}"


class SchoolLibraryFineAccounting(TimeStampedModel):
    class Status(models.TextChoices):
        READY = "ready", "Ready"
        INVOICED = "invoiced", "Invoiced"
        NO_FINE = "no_fine", "No Fine"
        FAILED = "failed", "Failed"

    book_loan = models.OneToOneField(
        "gsshop_education.BookLoan", on_delete=models.PROTECT, related_name="fine_accounting"
    )
    invoice = models.OneToOneField(
        Invoice, on_delete=models.PROTECT, blank=True, null=True, related_name="school_library_fine"
    )
    late_days = models.PositiveIntegerField(default=0)
    fine_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.READY)
    error_message = models.TextField(blank=True)

    def __str__(self):
        return f"{self.book_loan} · fine"


class CustomerPortalAccess(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="accounting_customer_portal"
    )
    customer = models.ForeignKey(
        Contact, on_delete=models.PROTECT, related_name="accounting_portal_users"
    )
    active = models.BooleanField(default=True)

    def clean(self):
        if self.customer_id and self.customer.contact_type not in {
            Contact.ContactType.CUSTOMER,
            Contact.ContactType.BOTH,
        }:
            raise ValidationError({"customer": "Portal access requires a customer contact."})

    def __str__(self):
        return f"{self.user} · {self.customer}"


class PortalDocumentReference(TimeStampedModel):
    document_type = models.CharField(max_length=64)
    content_type = models.ForeignKey(
        ContentType, on_delete=models.PROTECT, related_name="accounting_portal_documents"
    )
    object_id = models.PositiveBigIntegerField()
    portal_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="portal_document_references")
    customer = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="portal_document_references")

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id"], name="unique_accounting_portal_document"
            )
        ]
        indexes = [
            models.Index(fields=["document_type", "customer"], name="fin_portal_type_customer_idx"),
        ]

    def clean(self):
        if self.customer_id and self.company_id != self.customer.company_id:
            raise ValidationError("Portal document company and customer must match.")

    def __str__(self):
        return f"{self.document_type} · {self.portal_uuid}"


class AccountCheque(TimeStampedModel):
    class ChequeType(models.TextChoices):
        RECEIPT = "receipt", "Receipt Cheque"
        PAYMENT = "payment", "Payment Cheque"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        CLEARED = "cleared", "Cleared"
        BOUNCED = "bounced", "Bounced"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="account_cheques")
    cheque_type = models.CharField(max_length=16, choices=ChequeType.choices)
    cheque_number = models.CharField(max_length=50)
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="account_cheques")
    bank_account = models.ForeignKey(CompanyBankAccount, on_delete=models.PROTECT, related_name="cheques")
    cheque_date = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-cheque_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "cheque_number"], name="unique_cheque_per_company")]

    def __str__(self):
        return self.cheque_number


class InternalBankTransfer(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="internal_bank_transfers")
    number = models.CharField(max_length=40)
    transfer_date = models.DateField(default=timezone.localdate)
    source_account = models.ForeignKey(
        CompanyBankAccount, on_delete=models.PROTECT, related_name="outgoing_transfers"
    )
    destination_account = models.ForeignKey(
        CompanyBankAccount, on_delete=models.PROTECT, related_name="incoming_transfers"
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    fee_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    reference = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(
        JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="bank_transfer"
    )

    class Meta:
        ordering = ["-transfer_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_bank_transfer_per_company")]

    def clean(self):
        if self.source_account_id and self.destination_account_id and self.source_account_id == self.destination_account_id:
            raise ValidationError("Source and destination bank accounts must be different.")
        for bank_account in (self.source_account, self.destination_account):
            if bank_account and bank_account.company_id != self.company_id:
                raise ValidationError("Both bank accounts must belong to the transfer company.")

    def __str__(self):
        return self.number


class RecurringJournal(TimeStampedModel):
    class Frequency(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        YEARLY = "yearly", "Yearly"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="recurring_journals")
    name = models.CharField(max_length=140)
    template_entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="recurring_templates")
    frequency = models.CharField(max_length=16, choices=Frequency.choices, default=Frequency.MONTHLY)
    next_run_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    auto_post = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["next_run_date", "name"]

    def __str__(self):
        return self.name


class Prepayment(TimeStampedModel):
    class PrepaymentType(models.TextChoices):
        CUSTOMER = "customer", "Customer Prepayment"
        VENDOR = "vendor", "Vendor Prepayment"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        FULLY_APPLIED = "fully_applied", "Fully Applied"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="prepayments")
    number = models.CharField(max_length=40)
    prepayment_type = models.CharField(max_length=16, choices=PrepaymentType.choices)
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="prepayments")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="prepayments")
    start_date = models.DateField(default=timezone.localdate)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    applied_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_prepayment_per_company")]

    @property
    def remaining_amount(self):
        return self.total_amount - self.applied_amount

    def __str__(self):
        return self.number


class CurrencyRate(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="currency_rates")
    currency = models.CharField(max_length=3)
    rate_date = models.DateField()
    rate = models.DecimalField(max_digits=18, decimal_places=8, validators=[MinValueValidator(Decimal("0.00000001"))])
    source = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-rate_date", "currency"]
        constraints = [models.UniqueConstraint(fields=["company", "currency", "rate_date"], name="unique_currency_rate_per_day")]

    def __str__(self):
        return f"{self.currency} · {self.rate_date}"


class CurrencyRevaluation(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="currency_revaluations")
    number = models.CharField(max_length=40)
    revaluation_date = models.DateField(default=timezone.localdate)
    currency = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=18, decimal_places=8)
    unrealized_gain = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    unrealized_loss = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    journal_entry = models.OneToOneField(
        JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="currency_revaluation"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-revaluation_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_revaluation_per_company")]

    def __str__(self):
        return self.number


class AccountingSettings(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="accounting_settings")
    fiscal_year_start_month = models.PositiveSmallIntegerField(default=1)
    tax_rounding_method = models.CharField(
        max_length=20, choices=[("per_line", "Per Line"), ("global", "Global")], default="per_line"
    )
    default_receivable_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="receivable_settings"
    )
    default_payable_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="payable_settings"
    )
    exchange_gain_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="exchange_gain_settings"
    )
    exchange_loss_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="exchange_loss_settings"
    )
    retained_earnings_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="retained_earnings_settings"
    )
    lock_date = models.DateField(blank=True, null=True)
    allow_posting_to_closed_period = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "Accounting settings"

    def clean(self):
        if not 1 <= self.fiscal_year_start_month <= 12:
            raise ValidationError({"fiscal_year_start_month": "Use a month number from 1 to 12."})
        accounts = (
            self.default_receivable_account,
            self.default_payable_account,
            self.exchange_gain_account,
            self.exchange_loss_account,
            self.retained_earnings_account,
        )
        if any(account and account.company_id != self.company_id for account in accounts):
            raise ValidationError("Every configured account must belong to the settings company.")

    def __str__(self):
        return f"Accounting Settings · {self.company}"


class CommercialAccountingSettings(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="commercial_accounting_settings")
    sales_journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="commercial_sales_settings")
    purchase_journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="commercial_purchase_settings")
    sales_downpayment_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="sales_downpayment_settings",
    )
    purchase_downpayment_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="purchase_downpayment_settings",
    )
    auto_post_sales = models.BooleanField(default=True)
    auto_post_purchase = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Commercial accounting settings"

    def clean(self):
        records = (
            self.sales_journal, self.purchase_journal,
            self.sales_downpayment_account, self.purchase_downpayment_account,
        )
        if any(record and record.company_id != self.company_id for record in records):
            raise ValidationError("Commercial journals and accounts must belong to the selected company.")

    def __str__(self):
        return f"Sales & Purchase Accounting · {self.company}"


class PartnerAccountingDefault(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="partner_accounting_defaults")
    partner = models.OneToOneField(Contact, on_delete=models.PROTECT, related_name="accounting_default")
    receivable_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="partner_receivable_defaults",
    )
    payable_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="partner_payable_defaults",
    )
    active = models.BooleanField(default=True)

    def clean(self):
        if self.partner_id and self.partner.company_id != self.company_id:
            raise ValidationError({"partner": "Partner must belong to the selected company."})
        for account in (self.receivable_account, self.payable_account):
            if account and account.company_id != self.company_id:
                raise ValidationError("Partner default accounts must belong to the selected company.")

    def __str__(self):
        return f"{self.partner} · accounting defaults"


class AccountingAuditEvent(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="accounting_audit_events")
    event_type = models.CharField(max_length=40)
    object_type = models.CharField(max_length=60)
    object_id = models.PositiveBigIntegerField()
    reference = models.CharField(max_length=100, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True, related_name="accounting_audit_events"
    )
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["company", "object_type", "object_id"])]

    def __str__(self):
        return f"{self.event_type} · {self.reference or self.object_id}"


class AccountTypeDefinition(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="account_type_definitions")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=16, choices=Account.AccountType.choices)
    normal_balance = models.CharField(max_length=8, choices=[("debit", "Debit"), ("credit", "Credit")])
    sequence = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_account_type_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class JournalEntryTemplate(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="journal_entry_templates")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=140)
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="entry_templates")
    description = models.CharField(max_length=240, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_journal_template_per_company")]

    def clean(self):
        if self.journal_id and self.journal.company_id != self.company_id:
            raise ValidationError({"journal": "Journal and template must belong to the same company."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class JournalEntryTemplateLine(TimeStampedModel):
    class Side(models.TextChoices):
        DEBIT = "debit", "Debit"
        CREDIT = "credit", "Credit"

    template = models.ForeignKey(JournalEntryTemplate, on_delete=models.CASCADE, related_name="lines")
    sequence = models.PositiveIntegerField(default=10)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="template_lines")
    side = models.CharField(max_length=8, choices=Side.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    description = models.CharField(max_length=200, blank=True)
    cost_center = models.ForeignKey(
        AccountingCostCenter, on_delete=models.PROTECT, blank=True, null=True, related_name="template_lines"
    )

    class Meta:
        ordering = ["sequence", "id"]

    def clean(self):
        if self.template_id and self.account_id and self.account.company_id != self.template.company_id:
            raise ValidationError({"account": "Account and template must belong to the same company."})
        if self.cost_center_id and self.cost_center.company_id != self.template.company_id:
            raise ValidationError({"cost_center": "Cost center and template must belong to the same company."})

    def __str__(self):
        return f"{self.template.code} · {self.account.code}"


class AccountPaymentMethod(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="account_payment_methods")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    direction = models.CharField(
        max_length=16,
        choices=[("incoming", "Incoming"), ("outgoing", "Outgoing"), ("both", "Incoming & Outgoing")],
        default="both",
    )
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, blank=True, null=True, related_name="payment_methods")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_payment_method_per_company")]

    def clean(self):
        if self.journal_id and self.journal.company_id != self.company_id:
            raise ValidationError({"journal": "Journal and payment method must belong to the same company."})

    def __str__(self):
        return self.name


class OtherIncomeExpense(TimeStampedModel):
    class TransactionType(models.TextChoices):
        INCOME = "income", "Other Income"
        EXPENSE = "expense", "Other Expense"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        POSTED = "posted", "Posted"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"
        REVERSED = "reversed", "Reversed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="other_income_expenses")
    number = models.CharField(max_length=40)
    transaction_type = models.CharField(max_length=16, choices=TransactionType.choices)
    transaction_date = models.DateField(default=timezone.localdate)
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="other_income_expenses")
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="other_income_expenses")
    reference = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(
        JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="other_income_expense"
    )

    class Meta:
        ordering = ["-transaction_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_other_transaction_per_company")]

    def clean(self):
        if self.journal_id and self.journal.company_id != self.company_id:
            raise ValidationError({"journal": "Journal and document must belong to the same company."})
        if self.partner_id and self.partner.company_id != self.company_id:
            raise ValidationError({"partner": "Partner and document must belong to the same company."})

    def __str__(self):
        return self.number


class OtherIncomeExpenseLine(TimeStampedModel):
    document = models.ForeignKey(OtherIncomeExpense, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="other_transaction_lines")
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    tax = models.ForeignKey(AccountTax, on_delete=models.PROTECT, blank=True, null=True, related_name="other_transaction_lines")
    cost_center = models.ForeignKey(
        AccountingCostCenter, on_delete=models.PROTECT, blank=True, null=True, related_name="other_transaction_lines"
    )
    budget = models.ForeignKey(
        "Budget",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="other_expense_lines",
    )
    budget_account = models.ForeignKey(
        "BudgetAccount",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="other_expense_lines",
    )

    def clean(self):
        company_id = self.document.company_id if self.document_id else None
        if company_id and self.account_id and self.account.company_id != company_id:
            raise ValidationError({"account": "Account and document must belong to the same company."})
        if company_id and self.tax_id and self.tax.company_id != company_id:
            raise ValidationError({"tax": "Tax and document must belong to the same company."})
        if company_id and self.cost_center_id and self.cost_center.company_id != company_id:
            raise ValidationError({"cost_center": "Cost center and document must belong to the same company."})
        if company_id and self.budget_id and self.budget.company_id != company_id:
            raise ValidationError({"budget": "Budget and document must belong to the same company."})
        if company_id and self.budget_account_id and self.budget_account.company_id != company_id:
            raise ValidationError({"budget_account": "Budget account and document must belong to the same company."})
        if bool(self.budget_id) != bool(self.budget_account_id):
            raise ValidationError("Budget and budget account must be selected together.")

    def __str__(self):
        return f"{self.document.number} · {self.description}"


class PaymentVoucher(TimeStampedModel):
    class VoucherType(models.TextChoices):
        RECEIPT = "receipt", "Receipt Voucher"
        PAYMENT = "payment", "Payment Voucher"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        POSTED = "posted", "Posted"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="payment_vouchers")
    number = models.CharField(max_length=40)
    voucher_type = models.CharField(max_length=16, choices=VoucherType.choices)
    voucher_date = models.DateField(default=timezone.localdate)
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="payment_vouchers")
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="payment_vouchers")
    payment_method = models.ForeignKey(
        AccountPaymentMethod, on_delete=models.PROTECT, blank=True, null=True, related_name="vouchers"
    )
    reference = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(
        JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="payment_voucher"
    )

    class Meta:
        ordering = ["-voucher_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_voucher_per_company")]

    def clean(self):
        for field_name in ("journal", "payment_method"):
            record = getattr(self, field_name, None)
            if record and record.company_id != self.company_id:
                raise ValidationError({field_name: "Selection and voucher must belong to the same company."})
        if self.partner_id and self.partner.company_id != self.company_id:
            raise ValidationError({"partner": "Partner and voucher must belong to the same company."})

    def __str__(self):
        return self.number


class PaymentVoucherLine(TimeStampedModel):
    voucher = models.ForeignKey(PaymentVoucher, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="voucher_lines")
    description = models.CharField(max_length=200, blank=True)
    debit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="voucher_lines")
    cost_center = models.ForeignKey(
        AccountingCostCenter, on_delete=models.PROTECT, blank=True, null=True, related_name="voucher_lines"
    )

    def clean(self):
        if bool(self.debit) == bool(self.credit):
            raise ValidationError("Enter either debit or credit on a voucher line.")
        company_id = self.voucher.company_id if self.voucher_id else None
        if company_id and self.account_id and self.account.company_id != company_id:
            raise ValidationError({"account": "Account and voucher must belong to the same company."})
        if company_id and self.partner_id and self.partner.company_id != company_id:
            raise ValidationError({"partner": "Partner and voucher must belong to the same company."})
        if company_id and self.cost_center_id and self.cost_center.company_id != company_id:
            raise ValidationError({"cost_center": "Cost center and voucher must belong to the same company."})

    def __str__(self):
        return f"{self.voucher.number} · {self.account.code}"


class MultiPaymentBatch(TimeStampedModel):
    class BatchType(models.TextChoices):
        CUSTOMER_RECEIPT = "customer_receipt", "Customer Multi Receipt"
        VENDOR_PAYMENT = "vendor_payment", "Vendor Multi Payment"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        POSTED = "posted", "Posted"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="multi_payment_batches")
    number = models.CharField(max_length=40)
    batch_type = models.CharField(max_length=20, choices=BatchType.choices)
    payment_date = models.DateField(default=timezone.localdate)
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="multi_payment_batches")
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="multi_payment_batches")
    payment_method = models.ForeignKey(
        AccountPaymentMethod, on_delete=models.PROTECT, blank=True, null=True, related_name="multi_payment_batches"
    )
    reference = models.CharField(max_length=100, blank=True)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-payment_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_multi_payment_batch_per_company")]

    def clean(self):
        for field_name in ("partner", "journal", "payment_method"):
            record = getattr(self, field_name, None)
            if record and record.company_id != self.company_id:
                raise ValidationError({field_name: "Selection and batch must belong to the same company."})

    def __str__(self):
        return self.number


class MultiPaymentBatchLine(TimeStampedModel):
    batch = models.ForeignKey(MultiPaymentBatch, on_delete=models.CASCADE, related_name="lines")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="multi_payment_lines")
    due_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    discount_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    withholding_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    reference = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["batch", "invoice"], name="unique_invoice_per_multi_payment_batch")]

    def clean(self):
        if self.batch_id and self.invoice_id:
            if self.invoice.company_id != self.batch.company_id or self.invoice.partner_id != self.batch.partner_id:
                raise ValidationError({"invoice": "Invoice must belong to the batch company and partner."})
            if self.amount + self.discount_amount + self.withholding_amount > self.due_amount:
                raise ValidationError("Payment, discount, and withholding cannot exceed the due amount.")

    def __str__(self):
        return f"{self.batch.number} · {self.invoice.number}"


class WithholdingTaxTransaction(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VALIDATED = "validated", "Validated"
        REVERSED = "reversed", "Reversed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="withholding_tax_transactions")
    number = models.CharField(max_length=40)
    withholding_tax = models.ForeignKey(WithholdingTax, on_delete=models.PROTECT, related_name="transactions")
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="withholding_tax_transactions")
    transaction_date = models.DateField(default=timezone.localdate)
    gross_amount = models.DecimalField(max_digits=18, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2)
    reference = models.CharField(max_length=100, blank=True)
    note = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    journal_entry = models.OneToOneField(
        JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="withholding_tax_transaction"
    )

    class Meta:
        ordering = ["-transaction_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_wht_transaction_per_company")]

    def clean(self):
        if self.withholding_tax_id and self.withholding_tax.company_id != self.company_id:
            raise ValidationError({"withholding_tax": "Withholding tax and transaction must belong to the same company."})
        if self.partner_id and self.partner.company_id != self.company_id:
            raise ValidationError({"partner": "Partner and transaction must belong to the same company."})
        if self.tax_amount < 0 or self.tax_amount > self.gross_amount:
            raise ValidationError({"tax_amount": "Tax amount must be between zero and the gross amount."})

    def __str__(self):
        return self.number


class WithholdingTaxTransactionLine(TimeStampedModel):
    transaction = models.ForeignKey(WithholdingTaxTransaction, on_delete=models.CASCADE, related_name="lines")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, blank=True, null=True, related_name="withholding_lines")
    description = models.CharField(max_length=200)
    tax_base = models.DecimalField(max_digits=18, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2)

    def clean(self):
        if self.transaction_id and self.invoice_id and self.invoice.company_id != self.transaction.company_id:
            raise ValidationError({"invoice": "Invoice and withholding transaction must belong to the same company."})
        if self.tax_amount < 0 or self.tax_amount > self.tax_base:
            raise ValidationError({"tax_amount": "Tax amount must be between zero and the tax base."})

    def __str__(self):
        return f"{self.transaction.number} · {self.description}"


class WithholdingTaxHistory(TimeStampedModel):
    transaction = models.ForeignKey(WithholdingTaxTransaction, on_delete=models.PROTECT, related_name="history")
    action = models.CharField(max_length=30)
    previous_status = models.CharField(max_length=16, blank=True)
    new_status = models.CharField(max_length=16)
    reason = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="withholding_tax_history")

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.transaction.number} · {self.action}"


class ReconciliationHistory(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="reconciliation_history")
    reference = models.CharField(max_length=60)
    reconciliation_date = models.DateField(default=timezone.localdate)
    journal_line = models.ForeignKey(JournalLine, on_delete=models.PROTECT, related_name="reconciliation_history")
    bank_statement_line = models.ForeignKey(
        BankStatementLine, on_delete=models.PROTECT, blank=True, null=True, related_name="reconciliation_history"
    )
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    match_type = models.CharField(max_length=30, default="manual")
    reversed = models.BooleanField(default=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reconciliation_history")

    class Meta:
        ordering = ["-reconciliation_date", "-id"]

    def __str__(self):
        return self.reference


class BankImportConfiguration(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="bank_import_configurations")
    bank_account = models.ForeignKey(CompanyBankAccount, on_delete=models.PROTECT, related_name="import_configurations")
    name = models.CharField(max_length=120)
    file_format = models.CharField(max_length=12, choices=[("csv", "CSV"), ("xlsx", "Excel"), ("ofx", "OFX")])
    delimiter = models.CharField(max_length=4, default=",")
    date_column = models.CharField(max_length=80, default="date")
    description_column = models.CharField(max_length=80, default="description")
    amount_column = models.CharField(max_length=80, default="amount")
    reference_column = models.CharField(max_length=80, default="reference")
    date_format = models.CharField(max_length=30, default="%Y-%m-%d")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_bank_import_config_per_company")]

    def clean(self):
        if self.bank_account_id and self.bank_account.company_id != self.company_id:
            raise ValidationError({"bank_account": "Bank account and import configuration must belong to the same company."})

    def __str__(self):
        return self.name


class AccountingImportPreview(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        VALIDATED = "validated", "Validated"
        IMPORTED = "imported", "Imported"
        FAILED = "failed", "Failed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="accounting_import_previews")
    import_type = models.CharField(max_length=30)
    filename = models.CharField(max_length=200)
    row_count = models.PositiveIntegerField(default=0)
    valid_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    preview_data = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True, related_name="accounting_import_previews"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.filename


class CurrencyRevaluationLine(TimeStampedModel):
    revaluation = models.ForeignKey(CurrencyRevaluation, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="currency_revaluation_lines")
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="currency_revaluation_lines")
    original_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    base_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    revalued_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    gain_loss_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    def clean(self):
        company_id = self.revaluation.company_id if self.revaluation_id else None
        if company_id and self.account_id and self.account.company_id != company_id:
            raise ValidationError({"account": "Account and revaluation must belong to the same company."})
        if company_id and self.partner_id and self.partner.company_id != company_id:
            raise ValidationError({"partner": "Partner and revaluation must belong to the same company."})

    def __str__(self):
        return f"{self.revaluation.number} · {self.account.code}"


class ClosingEntry(TimeStampedModel):
    class ClosingType(models.TextChoices):
        PERIOD = "period", "Period Closing"
        YEAR = "year", "Year Closing"
        OPENING = "opening", "Opening Balance"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        GENERATED = "generated", "Generated"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="closing_entries")
    number = models.CharField(max_length=40)
    closing_type = models.CharField(max_length=16, choices=ClosingType.choices)
    fiscal_period = models.ForeignKey(FiscalPeriod, on_delete=models.PROTECT, related_name="closing_entries")
    closing_date = models.DateField()
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="closing_entries")
    retained_earnings_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="closing_entries")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(
        JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="closing_record"
    )
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-closing_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_closing_entry_per_company")]

    def clean(self):
        if self.fiscal_period_id and self.fiscal_period.fiscal_year.company_id != self.company_id:
            raise ValidationError({"fiscal_period": "Fiscal period and closing entry must belong to the same company."})
        if self.journal_id and self.journal.company_id != self.company_id:
            raise ValidationError({"journal": "Journal and closing entry must belong to the same company."})
        if self.fiscal_period_id and not (self.fiscal_period.start_date <= self.closing_date <= self.fiscal_period.end_date):
            raise ValidationError({"closing_date": "Closing date must be inside the selected fiscal period."})

    def __str__(self):
        return self.number


class OpeningBalanceLine(TimeStampedModel):
    closing_entry = models.ForeignKey(ClosingEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="opening_balance_lines")
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="opening_balance_lines")
    debit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    description = models.CharField(max_length=200, blank=True)

    def clean(self):
        if bool(self.debit) == bool(self.credit):
            raise ValidationError("Enter either debit or credit on an opening balance line.")
        company_id = self.closing_entry.company_id if self.closing_entry_id else None
        if company_id and self.account_id and self.account.company_id != company_id:
            raise ValidationError({"account": "Account and opening balance must belong to the same company."})
        if company_id and self.partner_id and self.partner.company_id != company_id:
            raise ValidationError({"partner": "Partner and opening balance must belong to the same company."})

    def __str__(self):
        return f"{self.closing_entry.number} · {self.account.code}"


class CompanyAccountMapping(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        VALIDATED = "validated", "Validated"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    parent_company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="consolidation_parent_mappings")
    subsidiary_company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="consolidation_subsidiary_mappings")
    name = models.CharField(max_length=140)
    currency = models.CharField(max_length=3)
    elimination_journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="consolidation_mappings")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent_company", "subsidiary_company"], name="unique_company_consolidation_mapping"
            )
        ]

    def clean(self):
        if self.parent_company_id and self.parent_company_id == self.subsidiary_company_id:
            raise ValidationError({"subsidiary_company": "Parent and subsidiary companies must be different."})
        if self.elimination_journal_id and self.elimination_journal.company_id != self.parent_company_id:
            raise ValidationError({"elimination_journal": "Elimination journal must belong to the parent company."})

    def __str__(self):
        return self.name


class CompanyAccountMappingLine(TimeStampedModel):
    mapping = models.ForeignKey(CompanyAccountMapping, on_delete=models.CASCADE, related_name="lines")
    subsidiary_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="subsidiary_mapping_lines")
    parent_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="parent_mapping_lines")
    elimination_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="elimination_mapping_lines"
    )
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["mapping", "subsidiary_account"], name="unique_subsidiary_account_mapping")]

    def clean(self):
        if not self.mapping_id:
            return
        if self.subsidiary_account_id and self.subsidiary_account.company_id != self.mapping.subsidiary_company_id:
            raise ValidationError({"subsidiary_account": "Account must belong to the subsidiary company."})
        if self.parent_account_id and self.parent_account.company_id != self.mapping.parent_company_id:
            raise ValidationError({"parent_account": "Account must belong to the parent company."})
        if self.elimination_account_id and self.elimination_account.company_id != self.mapping.parent_company_id:
            raise ValidationError({"elimination_account": "Elimination account must belong to the parent company."})

    def __str__(self):
        return f"{self.subsidiary_account} → {self.parent_account}"


class FinancialRatioDefinition(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="financial_ratio_definitions")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    numerator_accounts = models.ManyToManyField(Account, related_name="ratio_numerators", blank=True)
    denominator_accounts = models.ManyToManyField(Account, related_name="ratio_denominators", blank=True)
    multiplier = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    sequence = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_financial_ratio_per_company")]

    def __str__(self):
        return self.name


class CostCenterRule(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="cost_center_rules")
    name = models.CharField(max_length=120)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="cost_center_rules")
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="cost_center_rules")
    cost_center = models.ForeignKey(AccountingCostCenter, on_delete=models.PROTECT, related_name="rules")
    allocation_percent = models.DecimalField(max_digits=7, decimal_places=4, default=100)
    priority = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["priority", "name"]

    def clean(self):
        for field_name in ("account", "partner", "cost_center"):
            record = getattr(self, field_name, None)
            if record and record.company_id != self.company_id:
                raise ValidationError({field_name: "Selection and cost center rule must belong to the same company."})
        if self.allocation_percent <= 0 or self.allocation_percent > 100:
            raise ValidationError({"allocation_percent": "Allocation must be greater than zero and at most 100%."})

    def __str__(self):
        return self.name


class ProductAccountMapping(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="product_account_mappings")
    product = models.ForeignKey("core.Product", on_delete=models.PROTECT, related_name="account_mappings")
    revenue_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="product_revenue_mappings")
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="product_expense_mappings")
    prepayment_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="product_prepayment_mappings"
    )
    inventory_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="product_inventory_mappings"
    )
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "product"], name="unique_product_account_mapping")]

    def clean(self):
        if self.product_id and self.product.company_id != self.company_id:
            raise ValidationError({"product": "Product and mapping must belong to the same company."})
        for field_name in ("revenue_account", "expense_account", "prepayment_account", "inventory_account"):
            account = getattr(self, field_name, None)
            if account and account.company_id != self.company_id:
                raise ValidationError({field_name: "Account and mapping must belong to the same company."})

    def __str__(self):
        return str(self.product)


class IntercompanyPartnerAccount(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="intercompany_partner_accounts")
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="intercompany_accounts")
    receivable_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="intercompany_receivables")
    payable_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="intercompany_payables")
    due_to_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="intercompany_due_to"
    )
    due_from_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="intercompany_due_from"
    )
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "partner"], name="unique_intercompany_partner_account")]

    def clean(self):
        if self.partner_id and self.partner.company_id != self.company_id:
            raise ValidationError({"partner": "Partner and mapping must belong to the same company."})
        for field_name in ("receivable_account", "payable_account", "due_to_account", "due_from_account"):
            account = getattr(self, field_name, None)
            if account and account.company_id != self.company_id:
                raise ValidationError({field_name: "Account and mapping must belong to the same company."})

    def __str__(self):
        return f"{self.company.code} · {self.partner.code}"


class SOAReminder(TimeStampedModel):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="soa_reminders")
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="soa_reminders")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, blank=True, null=True, related_name="soa_reminders")
    reminder_date = models.DateField(default=timezone.localdate)
    channel = models.CharField(max_length=16, choices=[("email", "Email"), ("in_app", "In App")], default="email")
    subject = models.CharField(max_length=180)
    message = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCHEDULED)
    sent_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["reminder_date", "id"]

    def clean(self):
        if self.partner_id and self.partner.company_id != self.company_id:
            raise ValidationError({"partner": "Partner and reminder must belong to the same company."})
        if self.invoice_id and (
            self.invoice.company_id != self.company_id or self.invoice.partner_id != self.partner_id
        ):
            raise ValidationError({"invoice": "Invoice must belong to the reminder company and partner."})

    def __str__(self):
        return f"{self.partner} · {self.reminder_date}"


class AccountReportDefinition(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="account_report_definitions")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=140)
    report_type = models.CharField(
        max_length=30,
        choices=[
            ("balance_sheet", "Balance Sheet"), ("profit_loss", "Profit and Loss"),
            ("cash_flow", "Cash Flow"), ("equity", "Changes in Equity"), ("tax", "Tax Report"),
        ],
    )
    configuration = models.JSONField(default=dict, blank=True)
    sequence = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_account_report_definition")]

    def __str__(self):
        return self.name


class AccountTaxDistribution(TimeStampedModel):
    tax = models.ForeignKey(AccountTax, on_delete=models.CASCADE, related_name="distributions")
    distribution_type = models.CharField(
        max_length=16, choices=[("base", "Tax Base"), ("tax", "Tax Amount"), ("refund", "Credit Note")]
    )
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="tax_distributions")
    percentage = models.DecimalField(max_digits=7, decimal_places=4, default=100)
    sequence = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["tax", "sequence", "id"]

    def clean(self):
        if self.tax_id and self.account_id and self.tax.company_id != self.account.company_id:
            raise ValidationError({"account": "Distribution account must belong to the tax company."})
        if self.percentage <= 0 or self.percentage > 100:
            raise ValidationError({"percentage": "Percentage must be greater than zero and at most 100%."})

    def __str__(self):
        return f"{self.tax.code} · {self.distribution_type}"


class FiscalPositionAccountMapping(TimeStampedModel):
    fiscal_position = models.ForeignKey(FiscalPosition, on_delete=models.CASCADE, related_name="account_mappings")
    source_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="fiscal_position_sources")
    destination_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="fiscal_position_destinations")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["fiscal_position", "source_account"], name="unique_fiscal_position_account_map")]

    def clean(self):
        company_id = self.fiscal_position.company_id if self.fiscal_position_id else None
        if company_id and any(account.company_id != company_id for account in (self.source_account, self.destination_account)):
            raise ValidationError("Fiscal position and mapped accounts must belong to the same company.")

    def __str__(self):
        return f"{self.source_account} → {self.destination_account}"


class FiscalPositionTaxMapping(TimeStampedModel):
    fiscal_position = models.ForeignKey(FiscalPosition, on_delete=models.CASCADE, related_name="tax_mappings")
    source_tax = models.ForeignKey(AccountTax, on_delete=models.PROTECT, related_name="fiscal_position_sources")
    destination_tax = models.ForeignKey(AccountTax, on_delete=models.PROTECT, blank=True, null=True, related_name="fiscal_position_destinations")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["fiscal_position", "source_tax"], name="unique_fiscal_position_tax_map")]

    def clean(self):
        company_id = self.fiscal_position.company_id if self.fiscal_position_id else None
        if company_id and self.source_tax_id and self.source_tax.company_id != company_id:
            raise ValidationError({"source_tax": "Source tax must belong to the fiscal position company."})
        if company_id and self.destination_tax_id and self.destination_tax.company_id != company_id:
            raise ValidationError({"destination_tax": "Destination tax must belong to the fiscal position company."})

    def __str__(self):
        return f"{self.source_tax} → {self.destination_tax or 'No Tax'}"


class ReconciliationRuleCondition(TimeStampedModel):
    rule = models.ForeignKey(ReconciliationRule, on_delete=models.CASCADE, related_name="conditions")
    field_name = models.CharField(
        max_length=30,
        choices=[("reference", "Reference"), ("partner_name", "Partner"), ("description", "Description"), ("amount", "Amount")],
    )
    operator = models.CharField(
        max_length=20,
        choices=[("contains", "Contains"), ("equals", "Equals"), ("starts_with", "Starts With"), ("greater_than", "Greater Than"), ("less_than", "Less Than")],
    )
    value = models.CharField(max_length=200)
    sequence = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["rule", "sequence", "id"]

    def __str__(self):
        return f"{self.rule} · {self.field_name}"


class ReconciliationRuleCounterpart(TimeStampedModel):
    rule = models.ForeignKey(ReconciliationRule, on_delete=models.CASCADE, related_name="counterpart_lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="reconciliation_counterparts")
    amount_type = models.CharField(max_length=16, choices=[("balance", "Remaining Balance"), ("fixed", "Fixed"), ("percent", "Percentage")], default="balance")
    amount = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    label = models.CharField(max_length=160, blank=True)
    sequence = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["rule", "sequence", "id"]

    def clean(self):
        if self.rule_id and self.account_id and self.rule.company_id != self.account.company_id:
            raise ValidationError({"account": "Counterpart account must belong to the rule company."})

    def __str__(self):
        return f"{self.rule} · {self.account}"


class ReconciliationRuleTestRun(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        MATCHED = "matched", "Matched"
        UNMATCHED = "unmatched", "Unmatched"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="reconciliation_rule_tests")
    statement_line = models.ForeignKey(
        BankStatementLine,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="rule_tests",
    )
    reference = models.CharField(max_length=80, blank=True)
    partner_name = models.CharField(max_length=160, blank=True)
    description = models.CharField(max_length=240, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    matched_rule = models.ForeignKey(
        ReconciliationRule,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="test_runs",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    result = models.JSONField(default=dict, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reconciliation_rule_tests",
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def clean(self):
        if self.statement_line_id and self.statement_line.statement.company_id != self.company_id:
            raise ValidationError({"statement_line": "Statement line must belong to the test company."})

    def __str__(self):
        return f"Rule test {self.pk or 'new'} · {self.status}"


class DepositApplication(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPLIED = "applied", "Applied"
        REVERSED = "reversed", "Reversed"

    deposit = models.ForeignKey(AccountDeposit, on_delete=models.PROTECT, related_name="applications")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="deposit_applications")
    application_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    journal_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="deposit_application")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    reversed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-application_date", "-id"]

    def clean(self):
        if self.deposit_id and self.invoice_id:
            if self.deposit.company_id != self.invoice.company_id or self.deposit.partner_id != self.invoice.partner_id:
                raise ValidationError({"invoice": "Invoice must belong to the deposit company and partner."})
            if self.amount > self.deposit.unapplied_amount:
                raise ValidationError({"amount": "Application cannot exceed the unapplied deposit amount."})

    def __str__(self):
        return f"{self.deposit.number} · {self.invoice.number}"


class PrepaymentApplication(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPLIED = "applied", "Applied"
        REVERSED = "reversed", "Reversed"

    prepayment = models.ForeignKey(Prepayment, on_delete=models.PROTECT, related_name="applications")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="prepayment_applications")
    application_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    journal_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="prepayment_application")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    reversed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-application_date", "-id"]

    def clean(self):
        if self.prepayment_id and self.invoice_id:
            if self.prepayment.company_id != self.invoice.company_id or self.prepayment.partner_id != self.invoice.partner_id:
                raise ValidationError({"invoice": "Invoice must belong to the prepayment company and partner."})
            if self.amount > self.prepayment.remaining_amount:
                raise ValidationError({"amount": "Application cannot exceed the remaining prepayment amount."})

    def __str__(self):
        return f"{self.prepayment.number} · {self.invoice.number}"


class RecurringJournalExecution(TimeStampedModel):
    schedule = models.ForeignKey(RecurringJournal, on_delete=models.PROTECT, related_name="executions")
    scheduled_date = models.DateField()
    journal_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, related_name="recurring_execution")
    status = models.CharField(max_length=16, choices=[("generated", "Generated"), ("posted", "Posted"), ("failed", "Failed")])
    message = models.TextField(blank=True)

    class Meta:
        ordering = ["-scheduled_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["schedule", "scheduled_date"], name="unique_recurring_execution_date")]

    def __str__(self):
        return f"{self.schedule} · {self.scheduled_date}"


class CurrencyRateLog(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="currency_rate_logs")
    currency = models.CharField(max_length=3)
    rate_date = models.DateField()
    rate = models.DecimalField(max_digits=18, decimal_places=8)
    rate_type = models.CharField(max_length=16, choices=[("spot", "Spot"), ("average", "Average"), ("closing", "Closing")], default="spot")
    source = models.CharField(max_length=100, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-rate_date", "currency"]

    def __str__(self):
        return f"{self.currency} · {self.rate_date}"


class AssetAccountingSettings(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="asset_accounting_settings")
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="asset_accounting_settings")
    fixed_asset_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="fixed_asset_settings")
    accumulated_depreciation_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="accumulated_depreciation_settings")
    depreciation_expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="depreciation_expense_settings")
    disposal_gain_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="asset_disposal_gain_settings")
    disposal_loss_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="asset_disposal_loss_settings")
    disposal_clearing_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="asset_disposal_clearing_settings",
    )

    class Meta:
        verbose_name_plural = "Asset accounting settings"

    def clean(self):
        records = (
            self.journal, self.fixed_asset_account, self.accumulated_depreciation_account,
            self.depreciation_expense_account, self.disposal_gain_account, self.disposal_loss_account,
            self.disposal_clearing_account,
        )
        if any(record and record.company_id != self.company_id for record in records):
            raise ValidationError("All asset accounting configuration must belong to the selected company.")

    def __str__(self):
        return f"Asset Accounting · {self.company}"


class AssetCategoryAccountingRule(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="asset_category_accounting_rules")
    category_code = models.CharField(max_length=60)
    category_name = models.CharField(max_length=140)
    fixed_asset_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="asset_category_fixed_accounts")
    accumulated_depreciation_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="asset_category_accumulated_accounts")
    depreciation_expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="asset_category_depreciation_accounts")
    disposal_journal = models.ForeignKey(
        AccountJournal,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="asset_category_disposal_rules",
    )
    disposal_gain_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="asset_category_disposal_gain_rules",
    )
    disposal_loss_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="asset_category_disposal_loss_rules",
    )
    disposal_clearing_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="asset_category_disposal_clearing_rules",
    )
    useful_life_months = models.PositiveIntegerField(default=60)
    depreciation_method = models.CharField(max_length=20, choices=[("straight_line", "Straight Line"), ("declining", "Declining Balance")], default="straight_line")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category_code"]
        constraints = [models.UniqueConstraint(fields=["company", "category_code"], name="unique_asset_category_accounting_rule")]

    def clean(self):
        records = (
            self.fixed_asset_account,
            self.accumulated_depreciation_account,
            self.depreciation_expense_account,
            self.disposal_journal,
            self.disposal_gain_account,
            self.disposal_loss_account,
            self.disposal_clearing_account,
        )
        if any(record and record.company_id != self.company_id for record in records):
            raise ValidationError("All asset category accounts must belong to the selected company.")

    def __str__(self):
        return f"{self.category_code} - {self.category_name}"


class AssetCapitalization(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        WAITING_APPROVAL = "waiting_approval", "Waiting for Approval"
        CONFIRMED = "confirmed", "Confirmed"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"
        CAPITALIZED = "capitalized", "Capitalized"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="asset_capitalizations")
    number = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    product = models.ForeignKey("core.Product", on_delete=models.PROTECT, related_name="asset_capitalizations")
    responsible_employee = models.ForeignKey(
        Employee,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="asset_capitalizations",
    )
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    estimated_budget = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cost_center = models.ForeignKey(
        AccountingCostCenter,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="asset_capitalizations",
    )
    cip_clearing_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="cip_capitalizations")
    fixed_asset_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="capitalized_assets")
    capitalization_threshold = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    asset_count = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(100)])
    allocation_mode = models.CharField(
        max_length=16,
        choices=[("equal", "Equal Split"), ("manual", "Manual")],
        default="equal",
    )
    useful_life_years = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    salvage_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    description = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="approved_asset_capitalizations",
    )
    journal_entry = models.OneToOneField(
        JournalEntry,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="asset_capitalization",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_asset_capitalization_number")]

    def clean(self):
        if self.product_id and self.product.company_id != self.company_id:
            raise ValidationError({"product": "Product must belong to the capitalization company."})
        if self.responsible_employee_id and self.responsible_employee.company_id != self.company_id:
            raise ValidationError({"responsible_employee": "Employee must belong to the capitalization company."})
        records = (self.cost_center, self.cip_clearing_account, self.fixed_asset_account)
        if any(record and record.company_id != self.company_id for record in records):
            raise ValidationError("Cost center and capitalization accounts must belong to the selected company.")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be before start date."})

    def __str__(self):
        return f"{self.number} - {self.name}"


class AssetCapitalizationLine(TimeStampedModel):
    capitalization = models.ForeignKey(AssetCapitalization, on_delete=models.CASCADE, related_name="lines")
    source_type = models.CharField(
        max_length=20,
        choices=[("vendor_bill", "Vendor Bill"), ("product_usage", "Product Usage"), ("manual", "Manual")],
        default="manual",
    )
    source_reference = models.CharField(max_length=100, blank=True)
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1, validators=[MinValueValidator(Decimal("0.001"))])
    unit_cost = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])

    class Meta:
        ordering = ["capitalization", "id"]

    @property
    def amount(self):
        return self.quantity * self.unit_cost

    def __str__(self):
        return f"{self.capitalization.number} · {self.description}"


class AssetCapitalizationAllocation(TimeStampedModel):
    capitalization = models.ForeignKey(AssetCapitalization, on_delete=models.CASCADE, related_name="allocations")
    sequence = models.PositiveSmallIntegerField(default=1)
    asset_code = models.CharField(max_length=40)
    asset_name = models.CharField(max_length=160)
    acquisition_value = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    salvage_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        ordering = ["capitalization", "sequence", "id"]
        constraints = [
            models.UniqueConstraint(fields=["capitalization", "sequence"], name="unique_capitalization_allocation_sequence"),
            models.UniqueConstraint(fields=["capitalization", "asset_code"], name="unique_capitalization_allocation_code"),
        ]

    def clean(self):
        if self.salvage_value > self.acquisition_value:
            raise ValidationError({"salvage_value": "Salvage value cannot exceed acquisition value."})

    def __str__(self):
        return f"{self.capitalization.number} · {self.asset_code}"


class AssetCapitalizationAsset(TimeStampedModel):
    capitalization = models.ForeignKey(AssetCapitalization, on_delete=models.PROTECT, related_name="created_assets")
    asset = models.OneToOneField("gsshop_operations.Asset", on_delete=models.PROTECT, related_name="capitalization_link")
    allocated_amount = models.DecimalField(max_digits=18, decimal_places=2)
    salvage_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    useful_life_years = models.DecimalField(max_digits=8, decimal_places=2, default=1)

    class Meta:
        ordering = ["capitalization", "id"]

    def __str__(self):
        return f"{self.capitalization.number} · {self.asset}"


class AssetDepreciationLine(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    asset = models.ForeignKey("gsshop_operations.Asset", on_delete=models.PROTECT, related_name="depreciation_lines")
    period_date = models.DateField()
    opening_book_value = models.DecimalField(max_digits=18, decimal_places=2)
    depreciation_amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    accumulated_depreciation = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    closing_book_value = models.DecimalField(max_digits=18, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="asset_depreciation")

    class Meta:
        ordering = ["asset", "period_date", "id"]
        constraints = [models.UniqueConstraint(fields=["asset", "period_date"], name="unique_asset_depreciation_period")]

    def clean(self):
        if self.closing_book_value != self.opening_book_value - self.depreciation_amount:
            raise ValidationError({"closing_book_value": "Closing value must equal opening value less depreciation."})

    def __str__(self):
        return f"{self.asset} · {self.period_date}"


class AssetRevaluation(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    asset = models.ForeignKey("gsshop_operations.Asset", on_delete=models.PROTECT, related_name="accounting_revaluations")
    number = models.CharField(max_length=40, unique=True)
    revaluation_date = models.DateField(default=timezone.localdate)
    current_book_value = models.DecimalField(max_digits=18, decimal_places=2)
    fair_value = models.DecimalField(max_digits=18, decimal_places=2)
    gain_loss_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="asset_revaluation")
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-revaluation_date", "-id"]

    def clean(self):
        if self.gain_loss_amount != self.fair_value - self.current_book_value:
            raise ValidationError({"gain_loss_amount": "Gain/loss must equal fair value less current book value."})

    def __str__(self):
        return self.number


class AssetDisposal(TimeStampedModel):
    class DisposalType(models.TextChoices):
        SOLD = "sold", "Sold"
        SCRAPPED = "scrapped", "Scrapped"

    class AccountingStatus(models.TextChoices):
        NOT_STARTED = "not_started", "Not Started"
        INVOICE_DRAFT = "invoice_draft", "Invoice Draft"
        JOURNAL_POSTED = "journal_posted", "Journal Posted"
        ACCOUNTING_ERROR = "accounting_error", "Accounting Error"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    asset = models.ForeignKey("gsshop_operations.Asset", on_delete=models.PROTECT, related_name="accounting_disposals")
    number = models.CharField(max_length=40, unique=True)
    disposal_date = models.DateField(default=timezone.localdate)
    disposal_type = models.CharField(max_length=16, choices=DisposalType.choices, default=DisposalType.SOLD)
    category_accounting_rule = models.ForeignKey(
        AssetCategoryAccountingRule,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="asset_disposals",
    )
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="asset_disposals")
    disposal_currency = models.CharField(max_length=3, blank=True)
    disposal_sale_rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)
    proceeds_foreign = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    proceeds = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    book_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    gain_loss_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="asset_disposal")
    customer_invoice = models.OneToOneField(
        Invoice,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="asset_sale_disposal",
    )
    accounting_status = models.CharField(
        max_length=24,
        choices=AccountingStatus.choices,
        default=AccountingStatus.NOT_STARTED,
    )
    accounting_error_message = models.TextField(blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-disposal_date", "-id"]

    def clean(self):
        if self.partner_id and self.partner.company_id != self.asset.company_id:
            raise ValidationError({"partner": "Partner and asset must belong to the same company."})
        if self.gain_loss_amount != self.proceeds - self.book_value:
            raise ValidationError({"gain_loss_amount": "Gain/loss must equal proceeds less book value."})
        company_currency = self.asset.company.currency.upper()
        disposal_currency = (self.disposal_currency or company_currency).upper()
        if self.disposal_sale_rate <= 0:
            raise ValidationError({"disposal_sale_rate": "Disposal exchange rate must be positive."})
        if disposal_currency != company_currency:
            if self.proceeds_foreign <= 0:
                raise ValidationError({"proceeds_foreign": "Foreign-currency disposal requires foreign proceeds."})
            expected = (self.proceeds_foreign * self.disposal_sale_rate).quantize(Decimal("0.01"))
            if self.proceeds != expected:
                raise ValidationError({"proceeds": "Company-currency proceeds must equal foreign proceeds times the sale rate."})
        if self.category_accounting_rule_id and self.category_accounting_rule.company_id != self.asset.company_id:
            raise ValidationError({"category_accounting_rule": "Asset category rule must belong to the asset company."})
        if self.disposal_type == self.DisposalType.SOLD and not self.partner_id:
            raise ValidationError({"partner": "A customer is required when an asset is sold."})
        if self.disposal_type == self.DisposalType.SCRAPPED and self.proceeds:
            raise ValidationError({"proceeds": "A scrapped asset cannot have sale proceeds."})
        if self.customer_invoice_id and (
            self.customer_invoice.company_id != self.asset.company_id
            or self.customer_invoice.partner_id != self.partner_id
        ):
            raise ValidationError({"customer_invoice": "Invoice, asset, and customer must belong to the same company."})

    def __str__(self):
        return self.number


class BudgetAccountGroup(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="budget_account_groups")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_budget_account_group")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class BudgetAccount(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="budget_accounts")
    group = models.ForeignKey(BudgetAccountGroup, on_delete=models.PROTECT, related_name="accounts")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    ledger_accounts = models.ManyToManyField(Account, related_name="budget_accounts", blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_budget_account")]

    def clean(self):
        if self.group_id and self.group.company_id != self.company_id:
            raise ValidationError({"group": "Budget group must belong to the budget account company."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class BudgetTransaction(TimeStampedModel):
    class TransactionType(models.TextChoices):
        ALLOCATION = "allocation", "Allocation"
        TRANSFER = "transfer", "Transfer"
        CARRY_OVER = "carry_over", "Carry Over"
        CHANGE = "change", "Change Request"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="budget_transactions")
    number = models.CharField(max_length=40)
    transaction_type = models.CharField(max_length=16, choices=TransactionType.choices)
    transaction_date = models.DateField(default=timezone.localdate)
    source_budget = models.ForeignKey("Budget", on_delete=models.PROTECT, blank=True, null=True, related_name="outgoing_transactions")
    destination_budget = models.ForeignKey("Budget", on_delete=models.PROTECT, blank=True, null=True, related_name="incoming_transactions")
    budget_account = models.ForeignKey(BudgetAccount, on_delete=models.PROTECT, related_name="transactions")
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_budget_transactions")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True, related_name="approved_budget_transactions")

    class Meta:
        ordering = ["-transaction_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_budget_transaction_number")]

    def clean(self):
        budgets = (self.source_budget, self.destination_budget)
        if any(budget and budget.company_id != self.company_id for budget in budgets):
            raise ValidationError("All budgets must belong to the transaction company.")
        if self.budget_account_id and self.budget_account.company_id != self.company_id:
            raise ValidationError({"budget_account": "Budget account must belong to the transaction company."})
        if self.transaction_type == self.TransactionType.TRANSFER and (
            not self.source_budget_id or not self.destination_budget_id or self.source_budget_id == self.destination_budget_id
        ):
            raise ValidationError("A transfer requires different source and destination budgets.")

    def __str__(self):
        return self.number


class BudgetMovementAudit(TimeStampedModel):
    transaction_record = models.ForeignKey(BudgetTransaction, on_delete=models.PROTECT, related_name="audit_events")
    action = models.CharField(max_length=30)
    previous_status = models.CharField(max_length=16, blank=True)
    new_status = models.CharField(max_length=16)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="budget_movement_audits")
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.transaction_record} · {self.action}"


class BudgetChangeReasonCategory(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="budget_change_reason_categories")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_budget_change_reason")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class BudgetChangeRequest(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="budget_change_requests")
    number = models.CharField(max_length=40)
    request_date = models.DateField(default=timezone.localdate)
    reason_category = models.ForeignKey(BudgetChangeReasonCategory, on_delete=models.PROTECT, related_name="requests")
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="budget_change_requests")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True, related_name="approved_budget_change_requests")

    class Meta:
        ordering = ["-request_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_budget_change_request")]

    def clean(self):
        if self.reason_category_id and self.reason_category.company_id != self.company_id:
            raise ValidationError({"reason_category": "Reason category must belong to the request company."})

    def __str__(self):
        return self.number


class BudgetChangeRequestLine(TimeStampedModel):
    request = models.ForeignKey(BudgetChangeRequest, on_delete=models.CASCADE, related_name="lines")
    budget = models.ForeignKey("Budget", on_delete=models.PROTECT, related_name="change_request_lines")
    budget_account = models.ForeignKey(BudgetAccount, on_delete=models.PROTECT, related_name="change_request_lines")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    description = models.CharField(max_length=200, blank=True)

    def clean(self):
        if self.request_id and self.budget_id and self.request.company_id != self.budget.company_id:
            raise ValidationError({"budget": "Budget must belong to the request company."})
        if self.request_id and self.budget_account_id and self.request.company_id != self.budget_account.company_id:
            raise ValidationError({"budget_account": "Budget account must belong to the request company."})

    def __str__(self):
        return f"{self.request} · {self.budget}"


class BudgetTransfer(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="budget_transfers")
    number = models.CharField(max_length=40)
    transfer_date = models.DateField(default=timezone.localdate)
    reason = models.TextField(blank=True)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="budget_transfers")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True, related_name="approved_budget_transfers")

    class Meta:
        ordering = ["-transfer_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_budget_transfer")]

    def __str__(self):
        return self.number


class BudgetTransferLine(TimeStampedModel):
    transfer = models.ForeignKey(BudgetTransfer, on_delete=models.CASCADE, related_name="lines")
    source_budget = models.ForeignKey("Budget", on_delete=models.PROTECT, related_name="dedicated_outgoing_transfers")
    destination_budget = models.ForeignKey("Budget", on_delete=models.PROTECT, related_name="dedicated_incoming_transfers")
    budget_account = models.ForeignKey(BudgetAccount, on_delete=models.PROTECT, related_name="transfer_lines")
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])

    def clean(self):
        if self.source_budget_id == self.destination_budget_id:
            raise ValidationError("Source and destination budgets must be different.")
        company_id = self.transfer.company_id if self.transfer_id else None
        if company_id and any(record.company_id != company_id for record in (self.source_budget, self.destination_budget, self.budget_account)):
            raise ValidationError("Transfer line records must belong to the transfer company.")

    def __str__(self):
        return f"{self.transfer} · {self.source_budget} → {self.destination_budget}"


class BudgetCarryOver(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="budget_carry_overs")
    number = models.CharField(max_length=40)
    carry_date = models.DateField(default=timezone.localdate)
    reason = models.TextField(blank=True)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="budget_carry_overs")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True, related_name="approved_budget_carry_overs")

    class Meta:
        ordering = ["-carry_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_budget_carry_over")]

    def __str__(self):
        return self.number


class BudgetCarryOverLine(TimeStampedModel):
    carry_over = models.ForeignKey(BudgetCarryOver, on_delete=models.CASCADE, related_name="lines")
    source_budget = models.ForeignKey("Budget", on_delete=models.PROTECT, related_name="carry_over_source_lines")
    destination_budget = models.ForeignKey("Budget", on_delete=models.PROTECT, related_name="carry_over_destination_lines")
    budget_account = models.ForeignKey(BudgetAccount, on_delete=models.PROTECT, related_name="carry_over_lines")
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])

    def clean(self):
        if self.source_budget_id == self.destination_budget_id:
            raise ValidationError("Source and destination budgets must be different.")
        company_id = self.carry_over.company_id if self.carry_over_id else None
        if company_id and any(record.company_id != company_id for record in (self.source_budget, self.destination_budget, self.budget_account)):
            raise ValidationError("Carry-over line records must belong to the carry-over company.")

    def __str__(self):
        return f"{self.carry_over} · {self.source_budget} → {self.destination_budget}"


class BudgetCostCenterDistribution(TimeStampedModel):
    budget_account = models.ForeignKey(BudgetAccount, on_delete=models.CASCADE, related_name="cost_center_distributions")
    cost_center = models.ForeignKey(AccountingCostCenter, on_delete=models.PROTECT, related_name="budget_distributions")
    percentage = models.DecimalField(max_digits=7, decimal_places=4, validators=[MinValueValidator(Decimal("0.0001"))])

    class Meta:
        ordering = ["budget_account", "cost_center"]
        constraints = [models.UniqueConstraint(fields=["budget_account", "cost_center"], name="unique_budget_cost_center_distribution")]

    def clean(self):
        if self.budget_account_id and self.cost_center_id and self.budget_account.company_id != self.cost_center.company_id:
            raise ValidationError({"cost_center": "Cost center must belong to the budget account company."})
        if self.percentage > Decimal("100"):
            raise ValidationError({"percentage": "Percentage cannot exceed 100."})

    def __str__(self):
        return f"{self.budget_account} · {self.cost_center}"


class PettyCashCategory(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="petty_cash_categories")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="petty_cash_categories")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_petty_cash_category")]

    def clean(self):
        if self.expense_account_id and self.expense_account.company_id != self.company_id:
            raise ValidationError({"expense_account": "Expense account must belong to the category company."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class PettyCashFund(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="petty_cash_funds")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    custodian = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="petty_cash_funds")
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="petty_cash_funds")
    cash_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="petty_cash_funds")
    imprest_limit = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    current_balance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_petty_cash_fund")]

    def clean(self):
        if self.custodian_id and self.custodian.company_id != self.company_id:
            raise ValidationError({"custodian": "Custodian must belong to the fund company."})
        if self.journal_id and self.journal.company_id != self.company_id:
            raise ValidationError({"journal": "Journal must belong to the fund company."})
        if self.cash_account_id and self.cash_account.company_id != self.company_id:
            raise ValidationError({"cash_account": "Cash account must belong to the fund company."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class PettyCashVoucher(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        POSTED = "posted", "Posted"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"
        REVERSED = "reversed", "Reversed"

    fund = models.ForeignKey(PettyCashFund, on_delete=models.PROTECT, related_name="vouchers")
    number = models.CharField(max_length=40, unique=True)
    voucher_date = models.DateField(default=timezone.localdate)
    payee = models.CharField(max_length=160)
    reference = models.CharField(max_length=100, blank=True)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="petty_cash_voucher")
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-voucher_date", "-id"]

    def __str__(self):
        return self.number


class PettyCashVoucherLine(TimeStampedModel):
    voucher = models.ForeignKey(PettyCashVoucher, on_delete=models.CASCADE, related_name="lines")
    category = models.ForeignKey(PettyCashCategory, on_delete=models.PROTECT, related_name="voucher_lines")
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    cost_center = models.ForeignKey(AccountingCostCenter, on_delete=models.PROTECT, blank=True, null=True, related_name="petty_cash_lines")

    def clean(self):
        company_id = self.voucher.fund.company_id if self.voucher_id else None
        if company_id and self.category_id and self.category.company_id != company_id:
            raise ValidationError({"category": "Category and voucher must belong to the same company."})
        if company_id and self.cost_center_id and self.cost_center.company_id != company_id:
            raise ValidationError({"cost_center": "Cost center and voucher must belong to the same company."})

    def __str__(self):
        return f"{self.voucher} · {self.description}"


class PettyCashReplenishment(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        POSTED = "posted", "Posted"
        CANCELLED = "cancelled", "Cancelled"
        REVERSED = "reversed", "Reversed"

    fund = models.ForeignKey(PettyCashFund, on_delete=models.PROTECT, related_name="replenishments")
    number = models.CharField(max_length=40, unique=True)
    replenishment_date = models.DateField(default=timezone.localdate)
    bank_account = models.ForeignKey(CompanyBankAccount, on_delete=models.PROTECT, related_name="petty_cash_replenishments")
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="petty_cash_replenishment")
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-replenishment_date", "-id"]

    def clean(self):
        if self.fund_id and self.bank_account_id and self.fund.company_id != self.bank_account.company_id:
            raise ValidationError({"bank_account": "Bank account and fund must belong to the same company."})

    def __str__(self):
        return self.number


class PettyCashReconciliation(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RECONCILED = "reconciled", "Reconciled"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    fund = models.ForeignKey(PettyCashFund, on_delete=models.PROTECT, related_name="reconciliations")
    reference = models.CharField(max_length=40, unique=True)
    reconciliation_date = models.DateField(default=timezone.localdate)
    system_balance = models.DecimalField(max_digits=18, decimal_places=2)
    counted_balance = models.DecimalField(max_digits=18, decimal_places=2)
    variance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="petty_cash_reconciliation")
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-reconciliation_date", "-id"]

    def clean(self):
        if self.variance != self.counted_balance - self.system_balance:
            raise ValidationError({"variance": "Variance must equal counted balance less system balance."})

    def __str__(self):
        return self.reference


class PettyCashReconciliationLine(TimeStampedModel):
    reconciliation = models.ForeignKey(PettyCashReconciliation, on_delete=models.CASCADE, related_name="lines")
    voucher = models.ForeignKey(PettyCashVoucher, on_delete=models.PROTECT, blank=True, null=True, related_name="reconciliation_lines")
    description = models.CharField(max_length=200)
    expected_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    counted_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    note = models.CharField(max_length=240, blank=True)

    @property
    def variance(self):
        return self.counted_amount - self.expected_amount

    def clean(self):
        if self.voucher_id and self.reconciliation_id and self.voucher.fund_id != self.reconciliation.fund_id:
            raise ValidationError({"voucher": "Voucher and reconciliation must use the same petty cash fund."})

    def __str__(self):
        return f"{self.reconciliation} · {self.description}"


class PettyCashReplenishmentJournalLog(TimeStampedModel):
    replenishment = models.ForeignKey(PettyCashReplenishment, on_delete=models.PROTECT, related_name="journal_override_logs")
    original_journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="petty_cash_original_journal_logs")
    replacement_journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="petty_cash_replacement_journal_logs")
    reason = models.TextField()
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="petty_cash_journal_override_logs")

    class Meta:
        ordering = ["-created_at", "-id"]

    def clean(self):
        company_id = self.replenishment.fund.company_id if self.replenishment_id else None
        if company_id and any(journal.company_id != company_id for journal in (self.original_journal, self.replacement_journal)):
            raise ValidationError("Both journals must belong to the replenishment company.")

    def __str__(self):
        return f"{self.replenishment} · {self.created_at:%Y-%m-%d %H:%M}"


class AccountingIntegrationEvent(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        POSTED = "posted", "Posted"
        FAILED = "failed", "Failed"
        REVERSED = "reversed", "Reversed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="accounting_integration_events")
    source_module = models.CharField(max_length=40)
    source_model = models.CharField(max_length=80)
    source_id = models.PositiveBigIntegerField()
    event_type = models.CharField(max_length=40)
    reference = models.CharField(max_length=100)
    event_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    retry_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    journal_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="integration_event")

    class Meta:
        ordering = ["-event_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "source_model", "source_id", "event_type"], name="unique_accounting_integration_event")]
        indexes = [models.Index(fields=["company", "status", "event_date"])]

    def __str__(self):
        return self.reference


class AccountingSourceAdapter(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="accounting_source_adapters")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=140)
    source_module = models.CharField(
        max_length=80,
        help_text="Installed app/provider key. New modules can register their own key without changing Finance.",
    )
    source_model = models.CharField(max_length=80)
    event_type = models.CharField(max_length=40, default="accounting_posting")
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="source_adapters")
    default_debit_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="source_adapter_debit_defaults"
    )
    default_credit_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, related_name="source_adapter_credit_defaults"
    )
    auto_post = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    configuration = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["source_module", "code"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_accounting_source_adapter_code"),
            models.UniqueConstraint(
                fields=["company", "source_module", "source_model", "event_type"],
                name="unique_accounting_source_adapter_route",
            ),
        ]

    def clean(self):
        records = (self.journal, self.default_debit_account, self.default_credit_account)
        if any(record and record.company_id != self.company_id for record in records):
            raise ValidationError("The adapter journal and accounts must belong to its company.")
        if self.default_debit_account_id == self.default_credit_account_id:
            raise ValidationError("Debit and credit defaults must use different accounts.")

    def __str__(self):
        return f"{self.code} - {self.name}"


class SourceAccountingPosting(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending"
        POSTED = "posted", "Posted"
        FAILED = "failed", "Failed"
        REVERSED = "reversed", "Reversed"

    adapter = models.ForeignKey(AccountingSourceAdapter, on_delete=models.PROTECT, related_name="posting_requests")
    source_id = models.CharField(max_length=100)
    reference = models.CharField(max_length=100)
    event_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    partner = models.ForeignKey(
        Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="source_accounting_postings"
    )
    cost_center = models.ForeignKey(
        AccountingCostCenter,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="source_accounting_postings",
    )
    description = models.CharField(max_length=240, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    retry_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    integration_event = models.OneToOneField(
        AccountingIntegrationEvent,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="source_posting_request",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="source_accounting_postings"
    )

    class Meta:
        ordering = ["-event_date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["adapter", "source_id", "reference"], name="unique_source_accounting_posting"
            )
        ]

    @property
    def company(self):
        return self.adapter.company

    def clean(self):
        if self.amount <= 0:
            raise ValidationError({"amount": "Posting amount must be greater than zero."})
        if self.partner_id and self.partner.company_id != self.adapter.company_id:
            raise ValidationError({"partner": "Partner and adapter must belong to the same company."})
        if self.cost_center_id and self.cost_center.company_id != self.adapter.company_id:
            raise ValidationError({"cost_center": "Cost center and adapter must belong to the same company."})

    def __str__(self):
        return f"{self.adapter.code} · {self.reference}"


class ExternalAccountingConnector(TimeStampedModel):
    class Status(models.TextChoices):
        DISCONNECTED = "disconnected", "Disconnected"
        CONNECTED = "connected", "Connected"
        ERROR = "error", "Error"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="external_accounting_connectors")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=140)
    backend = models.CharField(
        max_length=60,
        help_text="Registered connector backend key; installed modules may add backends without changing Finance.",
    )
    endpoint_url = models.URLField()
    credential_alias = models.CharField(max_length=100)
    timeout_seconds = models.PositiveSmallIntegerField(default=30)
    verify_tls = models.BooleanField(default=True)
    configuration = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DISCONNECTED)
    last_error = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company", "code"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_external_accounting_connector")
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ExternalInvoiceSync(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        CONFIRMED = "confirmed", "Confirmed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    connector = models.ForeignKey(
        ExternalAccountingConnector,
        on_delete=models.PROTECT,
        related_name="invoice_syncs",
    )
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="external_accounting_syncs")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    external_reference = models.CharField(max_length=160, blank=True)
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="external_invoice_syncs",
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["connector", "invoice"], name="unique_external_invoice_sync")
        ]

    def clean(self):
        if self.connector_id and self.invoice_id and self.connector.company_id != self.invoice.company_id:
            raise ValidationError("External connector and invoice must belong to the same company.")

    def __str__(self):
        return f"{self.connector.code} · {self.invoice.number}"


class POSAccountingSettings(TimeStampedModel):
    profile = models.OneToOneField("gsshop_pos.POSProfile", on_delete=models.PROTECT, related_name="accounting_settings")
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="pos_accounting_settings")
    sales_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="pos_sales_settings")
    cash_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="pos_cash_settings")
    inventory_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="pos_inventory_settings")
    cogs_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="pos_cogs_settings")
    consumption_expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="pos_consumption_expense_settings")
    variance_gain_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="pos_variance_gain_settings")
    variance_loss_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="pos_variance_loss_settings")
    auto_post_on_close = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "POS accounting settings"

    def clean(self):
        if not self.profile_id:
            return
        company_id = self.profile.company_id
        records = (
            self.journal, self.sales_account, self.cash_account, self.inventory_account,
            self.cogs_account, self.consumption_expense_account, self.variance_gain_account, self.variance_loss_account,
        )
        if any(record and record.company_id != company_id for record in records):
            raise ValidationError("All POS accounting records must belong to the profile company.")

    def __str__(self):
        return f"POS Accounting · {self.profile}"


class POSPaymentAccountMapping(TimeStampedModel):
    payment_method = models.OneToOneField("gsshop_pos.POSPaymentMethod", on_delete=models.PROTECT, related_name="accounting_mapping")
    settlement_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="pos_payment_settlement_mappings")
    fee_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="pos_payment_fee_mappings")
    active = models.BooleanField(default=True)

    def clean(self):
        if not self.payment_method_id:
            return
        company_id = self.payment_method.company_id
        if self.settlement_account_id and self.settlement_account.company_id != company_id:
            raise ValidationError({"settlement_account": "Settlement account must belong to the payment method company."})
        if self.fee_account_id and self.fee_account.company_id != company_id:
            raise ValidationError({"fee_account": "Fee account must belong to the payment method company."})

    def __str__(self):
        return f"{self.payment_method} · {self.settlement_account}"


class PayrollAccountingSettings(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="payroll_accounting_settings")
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="payroll_accounting_settings")
    salary_expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="payroll_salary_expense_settings")
    payroll_payable_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="payroll_payable_settings")
    bank_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="payroll_bank_settings")
    auto_post = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Payroll accounting settings"

    def clean(self):
        records = (self.journal, self.salary_expense_account, self.payroll_payable_account, self.bank_account)
        if any(record and record.company_id != self.company_id for record in records):
            raise ValidationError("All payroll accounting records must belong to the selected company.")

    def __str__(self):
        return f"Payroll Accounting · {self.company}"


class PayrollComponentAccountMapping(TimeStampedModel):
    salary_component = models.OneToOneField(
        "gsshop_hr.SalaryComponent",
        on_delete=models.PROTECT,
        related_name="accounting_mapping",
    )
    payroll_account_override = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="payroll_component_mappings",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["salary_component__code"]

    def clean(self):
        if (
            self.salary_component_id
            and self.payroll_account_override_id
            and self.salary_component.company_id != self.payroll_account_override.company_id
        ):
            raise ValidationError({"payroll_account_override": "Payroll account must belong to the salary component company."})

    def __str__(self):
        return f"{self.salary_component} · {self.payroll_account_override}"


class InventoryAccountingSettings(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="inventory_accounting_settings")
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="inventory_accounting_settings")
    inventory_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="inventory_valuation_settings")
    cogs_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="inventory_cogs_settings")
    variance_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="inventory_variance_settings")
    usage_expense_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="inventory_usage_expense_settings",
    )
    landed_cost_clearing_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="inventory_landed_cost_clearing_settings",
    )
    auto_post = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Inventory accounting settings"

    def clean(self):
        records = (
            self.journal, self.inventory_account, self.cogs_account, self.variance_account,
            self.usage_expense_account, self.landed_cost_clearing_account,
        )
        if any(record and record.company_id != self.company_id for record in records):
            raise ValidationError("All inventory accounting records must belong to the selected company.")

    def __str__(self):
        return f"Inventory Accounting · {self.company}"


class InventoryUsageType(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="inventory_usage_types")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=140)
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="inventory_usage_types")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_inventory_usage_type")]

    def clean(self):
        if self.expense_account_id and self.expense_account.company_id != self.company_id:
            raise ValidationError({"expense_account": "Expense account must belong to the usage type company."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class InventoryAccountingDocument(TimeStampedModel):
    class DocumentType(models.TextChoices):
        LANDED_COST = "landed_cost", "Landed Cost"
        PRODUCT_USAGE = "product_usage", "Product Usage"
        STOCK_COUNT = "stock_count", "Stock Count"
        STOCK_BLOCK = "stock_block", "Stock Block"
        TRANSFER = "transfer", "Inventory Transfer"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        WAITING_APPROVAL = "waiting_approval", "Waiting for Approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="inventory_accounting_documents")
    number = models.CharField(max_length=40)
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    document_date = models.DateField(default=timezone.localdate)
    source_warehouse = models.ForeignKey(
        "core.Warehouse",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="source_accounting_documents",
    )
    destination_warehouse = models.ForeignKey(
        "core.Warehouse",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="destination_accounting_documents",
    )
    usage_type = models.ForeignKey(
        InventoryUsageType,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="documents",
    )
    allocation_method = models.CharField(
        max_length=16,
        choices=[("equal", "Equal"), ("quantity", "By Quantity"), ("value", "By Value")],
        default="value",
    )
    source_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    posted_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="approved_inventory_accounting_documents",
    )
    journal_entry = models.OneToOneField(
        JournalEntry,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="inventory_accounting_document",
    )
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-document_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_inventory_accounting_document")]

    def clean(self):
        records = (self.source_warehouse, self.destination_warehouse, self.usage_type)
        if any(record and record.company_id != self.company_id for record in records):
            raise ValidationError("Warehouses and usage type must belong to the document company.")
        if self.document_type == self.DocumentType.TRANSFER:
            if not self.source_warehouse_id or not self.destination_warehouse_id:
                raise ValidationError("Inventory transfers require source and destination warehouses.")
            if self.source_warehouse_id == self.destination_warehouse_id:
                raise ValidationError("Source and destination warehouses must differ.")
        if self.document_type == self.DocumentType.PRODUCT_USAGE and not self.usage_type_id:
            raise ValidationError({"usage_type": "Product usage requires a usage type."})

    def __str__(self):
        return self.number


class InventoryAccountingDocumentLine(TimeStampedModel):
    document = models.ForeignKey(InventoryAccountingDocument, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("core.Product", on_delete=models.PROTECT, related_name="inventory_accounting_lines")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    book_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    counted_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    allocated_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    debit_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="inventory_document_debit_lines",
    )
    credit_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="inventory_document_credit_lines",
    )
    cost_center = models.ForeignKey(
        AccountingCostCenter,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="inventory_accounting_lines",
    )
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["document", "id"]

    def clean(self):
        if self.document_id and self.product_id and self.document.company_id != self.product.company_id:
            raise ValidationError({"product": "Product must belong to the document company."})
        records = (self.debit_account, self.credit_account, self.cost_center)
        if self.document_id and any(record and record.company_id != self.document.company_id for record in records):
            raise ValidationError("Line accounts and cost center must belong to the document company.")

    def __str__(self):
        return f"{self.document.number} · {self.product}"


class ExpenseAdvanceAccountingSettings(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="expense_advance_accounting_settings")
    journal = models.ForeignKey(AccountJournal, on_delete=models.PROTECT, related_name="expense_advance_accounting_settings")
    advance_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="cash_advance_settings")
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="employee_expense_settings")
    payable_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="expense_payable_settings")
    bank_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="expense_bank_settings")
    auto_post = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Expense and advance accounting settings"

    def clean(self):
        records = (self.journal, self.advance_account, self.expense_account, self.payable_account, self.bank_account)
        if any(record and record.company_id != self.company_id for record in records):
            raise ValidationError("All expense and advance accounting records must belong to the selected company.")

    def __str__(self):
        return f"Expense & Advance Accounting · {self.company}"


class CountryTaxProfile(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="country_tax_profiles")
    country_code = models.CharField(max_length=2)
    registration_number = models.CharField(max_length=80)
    tax_office_code = models.CharField(max_length=40, blank=True)
    filing_frequency = models.CharField(max_length=16, choices=[("monthly", "Monthly"), ("quarterly", "Quarterly"), ("annual", "Annual")], default="monthly")
    connector_type = models.CharField(max_length=30, blank=True)
    endpoint_url = models.URLField(blank=True)
    credential_alias = models.CharField(max_length=100, blank=True, help_text="Reference to an externally managed secret; no credential is stored here.")
    active = models.BooleanField(default=True)
    configuration = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["country_code", "company"]
        constraints = [models.UniqueConstraint(fields=["company", "country_code"], name="unique_company_country_tax_profile")]

    def __str__(self):
        return f"{self.country_code} · {self.company}"


class LocalizationTaxCode(TimeStampedModel):
    profile = models.ForeignKey(CountryTaxProfile, on_delete=models.CASCADE, related_name="tax_codes")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    tax_type = models.CharField(max_length=40)
    rate = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    authority_code = models.CharField(max_length=80, blank=True)
    posting_account = models.ForeignKey(
        Account, on_delete=models.PROTECT, blank=True, null=True, related_name="localization_tax_codes"
    )
    metadata = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["profile", "code"]
        constraints = [models.UniqueConstraint(fields=["profile", "code"], name="unique_localization_tax_code")]

    def clean(self):
        if self.posting_account_id and self.profile_id and self.posting_account.company_id != self.profile.company_id:
            raise ValidationError({"posting_account": "Posting account must belong to the country profile company."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class StatutoryReportTemplate(TimeStampedModel):
    profile = models.ForeignKey(CountryTaxProfile, on_delete=models.CASCADE, related_name="report_templates")
    form_code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    version = models.CharField(max_length=30, default="1.0")
    output_format = models.CharField(max_length=16, choices=[("json", "JSON"), ("xml", "XML"), ("csv", "CSV"), ("pdf", "PDF"), ("dat", "DAT")], default="json")
    field_mapping = models.JSONField(default=dict, blank=True)
    validation_rules = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["profile", "form_code", "version"]
        constraints = [models.UniqueConstraint(fields=["profile", "form_code", "version"], name="unique_statutory_report_template")]

    def __str__(self):
        return f"{self.form_code} · {self.version}"


class StatutoryReportLine(TimeStampedModel):
    report = models.ForeignKey("ComplianceReport", on_delete=models.CASCADE, related_name="lines")
    sequence = models.PositiveIntegerField(default=10)
    field_code = models.CharField(max_length=60)
    label = models.CharField(max_length=180)
    tax_base = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    value = models.CharField(max_length=240, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["report", "sequence", "id"]
        constraints = [models.UniqueConstraint(fields=["report", "field_code"], name="unique_statutory_report_field")]

    def __str__(self):
        return f"{self.report} · {self.field_code}"


class TaxAuthorityDocument(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        RECEIVED = "received", "Received"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    profile = models.ForeignKey(CountryTaxProfile, on_delete=models.PROTECT, related_name="authority_documents")
    direction = models.CharField(max_length=12, choices=[("outgoing", "Outgoing"), ("incoming", "Incoming")])
    document_type = models.CharField(max_length=50)
    internal_reference = models.CharField(max_length=100)
    external_reference = models.CharField(max_length=120, blank=True)
    issue_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["profile", "direction", "internal_reference"], name="unique_tax_authority_document")]

    def __str__(self):
        return self.internal_reference


class TaxIdentifierLookup(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        FOUND = "found", "Found"
        NOT_FOUND = "not_found", "Not Found"
        FAILED = "failed", "Failed"

    profile = models.ForeignKey(CountryTaxProfile, on_delete=models.PROTECT, related_name="identifier_lookups")
    identifier = models.CharField(max_length=100)
    identifier_type = models.CharField(max_length=30, default="TIN")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    result_name = models.CharField(max_length=200, blank=True)
    result = models.JSONField(default=dict, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tax_identifier_lookups")
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.identifier_type} · {self.identifier}"


class RegulatoryExchangeRate(TimeStampedModel):
    profile = models.ForeignKey(CountryTaxProfile, on_delete=models.PROTECT, related_name="regulatory_exchange_rates")
    rate_date = models.DateField()
    currency = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=18, decimal_places=8)
    source = models.CharField(max_length=80)
    reference = models.CharField(max_length=100, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-rate_date", "currency"]
        constraints = [models.UniqueConstraint(fields=["profile", "rate_date", "currency", "source"], name="unique_regulatory_exchange_rate")]

    def __str__(self):
        return f"{self.source} · {self.currency} · {self.rate_date}"


class TaxMiddlewareConnector(TimeStampedModel):
    class Status(models.TextChoices):
        DISABLED = "disabled", "Disabled"
        READY = "ready", "Ready"
        ERROR = "error", "Error"

    profile = models.OneToOneField(CountryTaxProfile, on_delete=models.CASCADE, related_name="middleware_connector")
    name = models.CharField(max_length=120)
    connector_type = models.CharField(max_length=40)
    endpoint_url = models.URLField()
    credential_alias = models.CharField(max_length=120)
    timeout_seconds = models.PositiveIntegerField(default=30)
    verify_tls = models.BooleanField(default=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DISABLED)
    last_health_check_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True)
    configuration = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.profile} · {self.name}"


class ComplianceAuditEvent(TimeStampedModel):
    profile = models.ForeignKey(CountryTaxProfile, on_delete=models.PROTECT, related_name="audit_events")
    event_type = models.CharField(max_length=50)
    object_type = models.CharField(max_length=100)
    object_id = models.PositiveBigIntegerField()
    reference = models.CharField(max_length=120)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True, related_name="compliance_audit_events")
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.event_type} · {self.reference}"


class ComplianceReport(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        GENERATED = "generated", "Generated"
        VALIDATED = "validated", "Validated"
        SUBMITTED = "submitted", "Submitted"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    profile = models.ForeignKey(CountryTaxProfile, on_delete=models.PROTECT, related_name="reports")
    report_type = models.CharField(max_length=40)
    period_start = models.DateField()
    period_end = models.DateField()
    reference = models.CharField(max_length=80)
    totals = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    rendered_filename = models.CharField(max_length=220, blank=True)
    rendered_content = models.TextField(blank=True)
    content_sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="generated_compliance_reports")

    class Meta:
        ordering = ["-period_end", "report_type"]
        constraints = [models.UniqueConstraint(fields=["profile", "report_type", "period_start", "period_end"], name="unique_compliance_report_period")]

    def clean(self):
        if self.period_end < self.period_start:
            raise ValidationError({"period_end": "Period end cannot be earlier than period start."})

    def __str__(self):
        return self.reference


class ExternalTaxSubmission(TimeStampedModel):
    report = models.ForeignKey(ComplianceReport, on_delete=models.PROTECT, related_name="submissions")
    submitted_at = models.DateTimeField(blank=True, null=True)
    external_reference = models.CharField(max_length=120, blank=True)
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=[("queued", "Queued"), ("submitted", "Submitted"), ("accepted", "Accepted"), ("rejected", "Rejected"), ("failed", "Failed")], default="queued")
    error_message = models.TextField(blank=True)
    retry_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.external_reference or f"Submission {self.pk or 'new'}"


class BankStatementParseJob(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PARSED = "parsed", "Parsed"
        REVIEW = "review", "Needs Review"
        IMPORTED = "imported", "Imported"
        FAILED = "failed", "Failed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="bank_statement_parse_jobs")
    bank_account = models.ForeignKey(CompanyBankAccount, on_delete=models.PROTECT, related_name="parse_jobs")
    filename = models.CharField(max_length=200)
    parser_type = models.CharField(max_length=20, choices=[("csv", "CSV"), ("ofx", "OFX"), ("ocr", "AI / OCR")])
    source_file = models.FileField(upload_to="accounting/bank-imports/%Y/%m/", blank=True)
    source_text = models.TextField(blank=True)
    parser_provider = models.CharField(max_length=60, blank=True, default="builtin")
    column_mapping = models.JSONField(default=dict, blank=True)
    provider_options = models.JSONField(default=dict, blank=True)
    raw_provider_result = models.JSONField(default=dict, blank=True)
    parsed_rows = models.JSONField(default=list, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    error_message = models.TextField(blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bank_statement_parse_jobs")
    bank_statement = models.OneToOneField(
        BankStatement,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="parse_job",
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def clean(self):
        if self.bank_account_id and self.bank_account.company_id != self.company_id:
            raise ValidationError({"bank_account": "Bank account and parse job must belong to the same company."})

    def __str__(self):
        return self.filename


class CompanyAccountingProvisioningRun(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="accounting_provisioning_runs")
    country_code = models.CharField(max_length=2, blank=True)
    status = models.CharField(max_length=16, choices=[("pending", "Pending"), ("completed", "Completed"), ("failed", "Failed")], default="pending")
    created_accounts = models.PositiveIntegerField(default=0)
    created_journals = models.PositiveIntegerField(default=0)
    created_taxes = models.PositiveIntegerField(default=0)
    details = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="accounting_provisioning_runs")

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.company} · {self.created_at:%Y-%m-%d %H:%M}"


class AccountTaxCreditNoteDistribution(TimeStampedModel):
    tax = models.ForeignKey(AccountTax, on_delete=models.CASCADE, related_name="credit_note_distributions")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="tax_credit_note_distributions")
    percentage = models.DecimalField(max_digits=7, decimal_places=4, validators=[MinValueValidator(Decimal("0.0001"))])
    sequence = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["tax", "sequence", "id"]

    def clean(self):
        if self.tax_id and self.account_id and self.tax.company_id != self.account.company_id:
            raise ValidationError({"account": "Tax and account must belong to the same company."})
        if self.percentage > Decimal("100"):
            raise ValidationError({"percentage": "Percentage cannot exceed 100."})

    def __str__(self):
        return f"{self.tax} · credit note {self.percentage}%"


class AccountDepositLine(TimeStampedModel):
    deposit = models.ForeignKey(AccountDeposit, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="deposit_lines")
    description = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])

    def clean(self):
        if self.deposit_id and self.account_id and self.deposit.company_id != self.account.company_id:
            raise ValidationError({"account": "Deposit and account must belong to the same company."})

    def __str__(self):
        return f"{self.deposit} · {self.account}"


class AccountDepositHistory(TimeStampedModel):
    deposit = models.ForeignKey(AccountDeposit, on_delete=models.PROTECT, related_name="history")
    action = models.CharField(max_length=30)
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    previous_status = models.CharField(max_length=16, blank=True)
    new_status = models.CharField(max_length=16)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="account_deposit_history")
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.deposit} · {self.action}"


class AccountDepositConversion(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"
        CANCELLED = "cancelled", "Cancelled"

    deposit = models.ForeignKey(AccountDeposit, on_delete=models.PROTECT, related_name="conversions")
    number = models.CharField(max_length=40, unique=True)
    conversion_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    journal_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="deposit_conversion")
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-conversion_date", "-id"]

    def __str__(self):
        return self.number


class AccountDepositConversionLine(TimeStampedModel):
    conversion = models.ForeignKey(AccountDepositConversion, on_delete=models.CASCADE, related_name="lines")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="deposit_conversion_lines")
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])

    def clean(self):
        if self.conversion_id and self.invoice_id:
            deposit = self.conversion.deposit
            if deposit.company_id != self.invoice.company_id or deposit.partner_id != self.invoice.partner_id:
                raise ValidationError({"invoice": "Invoice must belong to the deposit company and partner."})

    def __str__(self):
        return f"{self.conversion} · {self.invoice}"


class AccountPaymentLine(TimeStampedModel):
    payment = models.ForeignKey(AccountPayment, on_delete=models.CASCADE, related_name="payment_lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="payment_lines")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, blank=True, null=True, related_name="account_payment_lines")
    description = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])

    def clean(self):
        if self.payment_id and self.account_id and self.payment.company_id != self.account.company_id:
            raise ValidationError({"account": "Payment and account must belong to the same company."})
        if self.payment_id and self.invoice_id and self.payment.company_id != self.invoice.company_id:
            raise ValidationError({"invoice": "Payment and invoice must belong to the same company."})

    def __str__(self):
        return f"{self.payment} · {self.account}"


class PaymentUnreconcileLog(TimeStampedModel):
    payment = models.ForeignKey(AccountPayment, on_delete=models.PROTECT, related_name="unreconcile_logs")
    allocation = models.ForeignKey(PaymentAllocation, on_delete=models.PROTECT, blank=True, null=True, related_name="unreconcile_logs")
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    reason = models.TextField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payment_unreconcile_logs")

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.payment} · {self.amount}"


class AccountChequeLine(TimeStampedModel):
    cheque = models.ForeignKey(AccountCheque, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="cheque_lines")
    description = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])

    def clean(self):
        if self.cheque_id and self.account_id and self.cheque.company_id != self.account.company_id:
            raise ValidationError({"account": "Cheque and account must belong to the same company."})

    def __str__(self):
        return f"{self.cheque} · {self.account}"


class CompanyAccountMappingTaxLine(TimeStampedModel):
    mapping = models.ForeignKey(CompanyAccountMapping, on_delete=models.CASCADE, related_name="tax_lines")
    subsidiary_tax = models.ForeignKey(AccountTax, on_delete=models.PROTECT, related_name="subsidiary_mapping_tax_lines")
    parent_tax = models.ForeignKey(AccountTax, on_delete=models.PROTECT, related_name="parent_mapping_tax_lines")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["mapping", "subsidiary_tax"], name="unique_subsidiary_tax_mapping")]

    def clean(self):
        if not self.mapping_id:
            return
        if self.subsidiary_tax_id and self.subsidiary_tax.company_id != self.mapping.subsidiary_company_id:
            raise ValidationError({"subsidiary_tax": "Tax must belong to the subsidiary company."})
        if self.parent_tax_id and self.parent_tax.company_id != self.mapping.parent_company_id:
            raise ValidationError({"parent_tax": "Tax must belong to the parent company."})

    def __str__(self):
        return f"{self.subsidiary_tax} → {self.parent_tax}"


class GeneralCentralizedNotification(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="accounting_notifications")
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="accounting_notifications")
    notification_type = models.CharField(max_length=40)
    channel = models.CharField(max_length=16, choices=[("email", "Email"), ("in_app", "In App"), ("webhook", "Webhook")])
    recipient = models.CharField(max_length=200)
    subject = models.CharField(max_length=180)
    message = models.TextField()
    scheduled_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["scheduled_at", "id"]

    def clean(self):
        if self.partner_id and self.partner.company_id != self.company_id:
            raise ValidationError({"partner": "Partner must belong to the notification company."})

    def __str__(self):
        return self.subject


class SOAReminderAudit(TimeStampedModel):
    reminder = models.ForeignKey(SOAReminder, on_delete=models.PROTECT, related_name="audit_events")
    action = models.CharField(max_length=30)
    previous_status = models.CharField(max_length=16, blank=True)
    new_status = models.CharField(max_length=16)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True, related_name="soa_reminder_audits")
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.reminder} · {self.action}"


class AccountingIntelligenceSettings(TimeStampedModel):
    company = models.OneToOneField(
        Company, on_delete=models.PROTECT, related_name="accounting_intelligence_settings"
    )
    risk_thresholds = models.JSONField(default=dict, blank=True)
    health_scoring = models.JSONField(default=dict, blank=True)
    forecast_settings = models.JSONField(default=dict, blank=True)
    governance = models.JSONField(default=dict, blank=True)
    routing = models.JSONField(default=dict, blank=True)
    source_coverage = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name_plural = "Accounting intelligence settings"

    def __str__(self):
        return f"Accounting Intelligence · {self.company}"


class AccountingVisionReport(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        GENERATED = "generated", "Generated"
        REVIEWED = "reviewed", "Reviewed"
        ARCHIVED = "archived", "Archived"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="accounting_vision_reports")
    name = models.CharField(max_length=140)
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    health_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    risk_level = models.CharField(max_length=16, blank=True)
    metrics = models.JSONField(default=dict, blank=True)
    insights = models.JSONField(default=list, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    recommended_actions = models.JSONField(default=list, blank=True)
    data_quality = models.JSONField(default=dict, blank=True)
    forecast = models.JSONField(default=dict, blank=True)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="accounting_vision_reports")

    class Meta:
        ordering = ["-period_end", "-id"]

    def clean(self):
        if self.period_end < self.period_start:
            raise ValidationError({"period_end": "Period end must be on or after period start."})

    def __str__(self):
        return self.name


class BankCashValuation(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        GENERATED = "generated", "Generated"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="bank_cash_valuations")
    number = models.CharField(max_length=40)
    valuation_date = models.DateField(default=timezone.localdate)
    currency = models.CharField(max_length=3)
    rate = models.DecimalField(max_digits=18, decimal_places=8)
    total_gain_loss = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="bank_cash_valuation")

    class Meta:
        ordering = ["-valuation_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_bank_cash_valuation")]

    def __str__(self):
        return self.number


class BankCashValuationLine(TimeStampedModel):
    valuation = models.ForeignKey(BankCashValuation, on_delete=models.CASCADE, related_name="lines")
    bank_account = models.ForeignKey(CompanyBankAccount, on_delete=models.PROTECT, related_name="valuation_lines")
    foreign_balance = models.DecimalField(max_digits=18, decimal_places=2)
    book_value = models.DecimalField(max_digits=18, decimal_places=2)
    revalued_value = models.DecimalField(max_digits=18, decimal_places=2)
    gain_loss = models.DecimalField(max_digits=18, decimal_places=2)

    def clean(self):
        if self.valuation_id and self.bank_account_id and self.valuation.company_id != self.bank_account.company_id:
            raise ValidationError({"bank_account": "Bank account must belong to the valuation company."})
        if self.gain_loss != self.revalued_value - self.book_value:
            raise ValidationError({"gain_loss": "Gain/loss must equal revalued value less book value."})

    def __str__(self):
        return f"{self.valuation} · {self.bank_account}"


class SourceDocumentCancellation(TimeStampedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="source_document_cancellations")
    source_model = models.CharField(max_length=100)
    source_id = models.PositiveBigIntegerField()
    source_reference = models.CharField(max_length=100)
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_source_cancellations")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True, related_name="approved_source_cancellations")
    reversal_entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="source_document_cancellations")

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "source_model", "source_id"], name="unique_source_document_cancellation")]

    def __str__(self):
        return self.source_reference


class ProductAccountingRule(TimeStampedModel):
    class Scope(models.TextChoices):
        PRODUCT = "product", "Product"
        CATEGORY = "category", "Product Category"
        VARIANT = "variant", "Product Variant"
        PREPAYMENT = "prepayment", "Prepayment"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="product_accounting_rules")
    scope = models.CharField(max_length=16, choices=Scope.choices)
    reference = models.CharField(max_length=120)
    expense_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="product_expense_rules")
    revenue_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="product_revenue_rules")
    prepayment_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="product_prepayment_rules")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["scope", "reference"]
        constraints = [models.UniqueConstraint(fields=["company", "scope", "reference"], name="unique_product_accounting_rule")]

    def clean(self):
        accounts = (self.expense_account, self.revenue_account, self.prepayment_account)
        if any(account and account.company_id != self.company_id for account in accounts):
            raise ValidationError("All rule accounts must belong to the selected company.")

    def __str__(self):
        return f"{self.get_scope_display()} · {self.reference}"


class ARReconciliationSession(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RECONCILED = "reconciled", "Reconciled"
        REVERSED = "reversed", "Reversed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="ar_reconciliation_sessions")
    reference = models.CharField(max_length=60)
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="ar_reconciliation_sessions")
    reconciliation_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    total_debit = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    total_credit = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ar_reconciliation_sessions")

    class Meta:
        ordering = ["-reconciliation_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "reference"], name="unique_ar_reconciliation_session")]

    def clean(self):
        if self.partner_id and self.partner.company_id != self.company_id:
            raise ValidationError({"partner": "Partner must belong to the reconciliation company."})

    def __str__(self):
        return self.reference


class ARReconciliationLine(TimeStampedModel):
    session = models.ForeignKey(ARReconciliationSession, on_delete=models.CASCADE, related_name="lines")
    journal_line = models.ForeignKey(JournalLine, on_delete=models.PROTECT, related_name="ar_reconciliation_lines")
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["session", "journal_line"], name="unique_ar_reconciliation_line")]

    def clean(self):
        if self.session_id and self.journal_line_id:
            if self.journal_line.entry.company_id != self.session.company_id:
                raise ValidationError({"journal_line": "Journal line must belong to the reconciliation company."})
            if self.journal_line.partner_id != self.session.partner_id:
                raise ValidationError({"journal_line": "Journal line must belong to the reconciliation partner."})

    def __str__(self):
        return f"{self.session} · {self.journal_line}"


class ARReconciliationAllocation(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REVERSED = "reversed", "Reversed"

    session = models.ForeignKey(
        ARReconciliationSession, on_delete=models.PROTECT, related_name="allocations"
    )
    debit_line = models.ForeignKey(
        JournalLine, on_delete=models.PROTECT, related_name="debit_ar_allocations"
    )
    credit_line = models.ForeignKey(
        JournalLine, on_delete=models.PROTECT, related_name="credit_ar_allocations"
    )
    amount = models.DecimalField(
        max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    reconciliation_date = models.DateField(default=timezone.localdate)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ar_reconciliation_allocations"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    reversed_at = models.DateTimeField(blank=True, null=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="reversed_ar_reconciliation_allocations",
    )

    class Meta:
        ordering = ["reconciliation_date", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "debit_line", "credit_line"],
                condition=models.Q(status="active"),
                name="unique_active_ar_session_line_pair",
            )
        ]

    def clean(self):
        errors = {}
        if self.debit_line_id and self.credit_line_id:
            debit = self.debit_line
            credit = self.credit_line
            if debit_id := getattr(debit, "pk", None):
                if debit_id == getattr(credit, "pk", None):
                    errors["credit_line"] = "Debit and credit lines must be different."
            if debit.entry.status != JournalEntry.Status.POSTED or credit.entry.status != JournalEntry.Status.POSTED:
                errors["debit_line"] = "Only posted journal lines can be reconciled."
            if debit.debit <= 0 or credit.credit <= 0:
                errors["credit_line"] = "Allocation requires a debit line and an opposite credit line."
            if (
                debit.entry.company_id != credit.entry.company_id
                or debit.account_id != credit.account_id
                or debit.partner_id != credit.partner_id
                or (debit.currency or debit.entry.currency) != (credit.currency or credit.entry.currency)
            ):
                errors["credit_line"] = "Lines must share company, account, partner, and currency."
            if self.session_id and debit.entry.company_id != self.session.company_id:
                errors["session"] = "Allocation lines must belong to the reconciliation company."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.debit_line} ↔ {self.credit_line} · {self.amount}"


class Budget(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        CLOSED = "closed", "Closed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="budgets")
    name = models.CharField(max_length=160)
    start_date = models.DateField()
    end_date = models.DateField()
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    committed = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    actual = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-start_date", "name"]

    @property
    def remaining(self):
        return self.amount - self.committed - self.actual

    def __str__(self):
        return self.name


class ExpenseClaim(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        RETURN_DUE = "return_due", "Advance Return Due"
        PAID = "paid", "Paid"
        CLOSED = "closed", "Closed"
        REVERSED = "reversed", "Reversed"
        REJECTED = "rejected", "Rejected"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="expense_claims")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="expense_claims")
    product = models.ForeignKey(
        "core.Product",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="expense_claims",
        help_text="Optional product used to resolve the expense account before the company fallback.",
    )
    cash_advance = models.OneToOneField(
        "gsshop_hr.CashAdvance",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="expense_claim",
    )
    reference = models.CharField(max_length=40)
    claim_date = models.DateField(default=timezone.localdate)
    category = models.CharField(max_length=80)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-claim_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "reference"], name="unique_expense_ref_per_company")]

    def clean(self):
        if self.employee_id and self.company_id and self.employee.company_id != self.company_id:
            raise ValidationError({"employee": "Employee must belong to the expense company."})
        if self.cash_advance_id:
            if self.cash_advance.employee_id != self.employee_id:
                raise ValidationError({"cash_advance": "Cash advance and expense must belong to the same employee."})
            if self.cash_advance.employee.company_id != self.company_id:
                raise ValidationError({"cash_advance": "Cash advance must belong to the expense company."})
        if self.product_id and self.product.company_id != self.company_id:
            raise ValidationError({"product": "Product must belong to the expense company."})

    def __str__(self):
        return self.reference


class ExpenseAdvanceSettlement(TimeStampedModel):
    class Status(models.TextChoices):
        RECOGNIZED = "recognized", "Recognized"
        REIMBURSED = "reimbursed", "Reimbursed"
        RETURNED = "returned", "Advance Returned"
        CLOSED = "closed", "Closed"
        REVERSED = "reversed", "Reversed"

    expense = models.OneToOneField(ExpenseClaim, on_delete=models.PROTECT, related_name="advance_settlement")
    advance_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    expense_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    reimbursement_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    return_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECOGNIZED)
    recognition_event = models.OneToOneField(
        AccountingIntegrationEvent,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="expense_advance_recognition",
    )
    reimbursement_event = models.OneToOneField(
        AccountingIntegrationEvent,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="expense_advance_reimbursement",
    )
    return_event = models.OneToOneField(
        AccountingIntegrationEvent,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="expense_advance_return",
    )
    closed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @property
    def company_id(self):
        return self.expense.company_id

    def __str__(self):
        return f"{self.expense.reference} · {self.status}"


class Loan(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        ACTIVE = "active", "Active"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="loans")
    number = models.CharField(max_length=40, unique=True)
    principal = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    balance = models.DecimalField(max_digits=18, decimal_places=2)
    term_months = models.PositiveIntegerField(default=12)
    start_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-start_date", "number"]

    def __str__(self):
        return self.number


class LocalizedTaxTransactionLine(TimeStampedModel):
    """Immutable statutory source row shared by every country report engine.

    The row keeps the tax calculation snapshot separate from mutable product,
    partner, and tax masters.  This mirrors HMX's report-source contracts while
    allowing each country pack to impose its own field and payload rules.
    """

    profile = models.ForeignKey(CountryTaxProfile, on_delete=models.PROTECT, related_name="tax_transaction_lines")
    tax_code = models.ForeignKey(LocalizationTaxCode, on_delete=models.PROTECT, related_name="transaction_lines")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, blank=True, null=True, related_name="localized_tax_lines")
    invoice_line = models.ForeignKey(
        InvoiceLine, on_delete=models.PROTECT, blank=True, null=True, related_name="localized_tax_lines"
    )
    sale_order = models.ForeignKey(
        "core.SaleOrder", on_delete=models.PROTECT, blank=True, null=True, related_name="localized_tax_lines"
    )
    sale_order_line = models.ForeignKey(
        "core.SaleOrderLine", on_delete=models.PROTECT, blank=True, null=True, related_name="localized_tax_lines"
    )
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="localized_tax_lines")
    transaction_date = models.DateField(default=timezone.localdate)
    document_number = models.CharField(max_length=100)
    document_type = models.CharField(
        max_length=24,
        choices=[
            ("sale", "Sale"), ("sale_return", "Sale Return"),
            ("purchase", "Purchase"), ("purchase_return", "Purchase Return"),
            ("adjustment", "Adjustment"),
        ],
    )
    supply_category = models.CharField(max_length=60, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    tax_base = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    gross_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, blank=True)
    exchange_rate = models.DecimalField(max_digits=18, decimal_places=8, default=1)
    authority_fields = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["transaction_date", "document_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "tax_code", "invoice_line"],
                condition=models.Q(invoice_line__isnull=False),
                name="unique_localized_invoice_tax_line",
            ),
            models.UniqueConstraint(
                fields=["profile", "tax_code", "sale_order_line"],
                condition=models.Q(sale_order_line__isnull=False),
                name="unique_localized_sale_tax_line",
            ),
        ]

    def clean(self):
        errors = {}
        if self.tax_code_id and self.profile_id and self.tax_code.profile_id != self.profile_id:
            errors["tax_code"] = "Tax code must belong to the selected country profile."
        if self.partner_id and self.profile_id and self.partner.company_id != self.profile.company_id:
            errors["partner"] = "Partner must belong to the country profile company."
        if self.invoice_line_id and self.invoice_id and self.invoice_line.invoice_id != self.invoice_id:
            errors["invoice_line"] = "Invoice line must belong to the selected invoice."
        if self.sale_order_line_id and self.sale_order_id and self.sale_order_line.order_id != self.sale_order_id:
            errors["sale_order_line"] = "Sale order line must belong to the selected sale order."
        if not self.invoice_line_id and not self.sale_order_line_id and self.document_type != "adjustment":
            errors["document_type"] = "A source line is required unless this is a statutory adjustment."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.profile.country_code} · {self.document_number} · {self.tax_code.code}"


class TaxAuthorityCallbackEvent(TimeStampedModel):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        APPLIED = "applied", "Applied"
        IGNORED = "ignored", "Ignored"
        FAILED = "failed", "Failed"

    profile = models.ForeignKey(CountryTaxProfile, on_delete=models.PROTECT, related_name="callback_events")
    event_id = models.CharField(max_length=160)
    event_type = models.CharField(max_length=80)
    external_reference = models.CharField(max_length=160, blank=True)
    signature = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECEIVED)
    processed_at = models.DateTimeField(blank=True, null=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["profile", "event_id"], name="unique_tax_callback_event")]

    def __str__(self):
        return f"{self.profile.country_code} · {self.event_type} · {self.event_id}"


class TaxAuthoritySyncCursor(TimeStampedModel):
    profile = models.ForeignKey(CountryTaxProfile, on_delete=models.CASCADE, related_name="sync_cursors")
    resource_type = models.CharField(max_length=80)
    cursor = models.CharField(max_length=255, blank=True)
    last_sync_at = models.DateTimeField(blank=True, null=True)
    last_status = models.CharField(max_length=20, blank=True)
    last_error = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["profile", "resource_type"]
        constraints = [models.UniqueConstraint(fields=["profile", "resource_type"], name="unique_tax_sync_cursor")]

    def __str__(self):
        return f"{self.profile} · {self.resource_type}"


class RegulatoryRateSyncRun(TimeStampedModel):
    profile = models.ForeignKey(CountryTaxProfile, on_delete=models.PROTECT, related_name="rate_sync_runs")
    source = models.CharField(max_length=80)
    date_from = models.DateField()
    date_to = models.DateField()
    currencies = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=16,
        choices=[("pending", "Pending"), ("completed", "Completed"), ("failed", "Failed")],
        default="pending",
    )
    imported_count = models.PositiveIntegerField(default=0)
    response_payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="regulatory_rate_sync_runs"
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def clean(self):
        if self.date_to < self.date_from:
            raise ValidationError({"date_to": "End date cannot be earlier than start date."})

    def __str__(self):
        return f"{self.profile.country_code} · {self.source} · {self.date_from}"


class POSOrderAccounting(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        INVOICED = "invoiced", "Invoiced"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"
        FAILED = "failed", "Failed"

    order = models.OneToOneField("gsshop_pos.POSOrder", on_delete=models.PROTECT, related_name="order_accounting")
    invoice = models.OneToOneField(
        Invoice, on_delete=models.PROTECT, blank=True, null=True, related_name="pos_order_accounting"
    )
    journal_entry = models.OneToOneField(
        JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="pos_order_accounting"
    )
    customer_deposit = models.ForeignKey(
        AccountDeposit, on_delete=models.PROTECT, blank=True, null=True, related_name="pos_order_accounting"
    )
    invoice_requested = models.BooleanField(default=False)
    deposit_applied = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    mdr_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)
    accounting_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    @property
    def company_id(self):
        return self.order.session.register.company_id

    def clean(self):
        company_id = self.company_id if self.order_id else None
        if self.invoice_id and self.invoice.company_id != company_id:
            raise ValidationError({"invoice": "Invoice and POS order must belong to the same company."})
        if self.customer_deposit_id and self.customer_deposit.company_id != company_id:
            raise ValidationError({"customer_deposit": "Deposit and POS order must belong to the same company."})

    def __str__(self):
        return f"{self.order.number} · {self.status}"


class SaleOrderLocalization(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CALCULATED = "calculated", "Calculated"
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"

    order = models.OneToOneField("core.SaleOrder", on_delete=models.PROTECT, related_name="tax_localization")
    profile = models.ForeignKey(CountryTaxProfile, on_delete=models.PROTECT, related_name="sale_order_localizations")
    total_tax_base = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_tax = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_gross = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    authority_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-order__order_date", "-id"]

    def clean(self):
        if self.order_id and self.profile_id and self.order.company_id != self.profile.company_id:
            raise ValidationError({"profile": "Tax profile must belong to the sales order company."})

    def __str__(self):
        return f"{self.order.number} · {self.profile.country_code}"


class SaleOrderLocalizedTaxLine(TimeStampedModel):
    localization = models.ForeignKey(SaleOrderLocalization, on_delete=models.CASCADE, related_name="tax_lines")
    order_line = models.ForeignKey(
        "core.SaleOrderLine", on_delete=models.PROTECT, related_name="localized_tax_selections"
    )
    tax_code = models.ForeignKey(LocalizationTaxCode, on_delete=models.PROTECT, related_name="sale_order_tax_lines")
    tax_base = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    gross_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    authority_fields = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["localization", "order_line", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["localization", "order_line", "tax_code"], name="unique_sale_order_localized_tax"
            )
        ]

    def clean(self):
        errors = {}
        if self.order_line_id and self.localization_id and self.order_line.order_id != self.localization.order_id:
            errors["order_line"] = "Taxed line must belong to the localized sales order."
        if self.tax_code_id and self.localization_id and self.tax_code.profile_id != self.localization.profile_id:
            errors["tax_code"] = "Tax code must belong to the selected country profile."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.localization.order.number} · {self.tax_code.code}"
