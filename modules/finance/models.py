from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import Company, Employee, TimeStampedModel


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
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_account_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class JournalEntry(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="journal_entries")
    number = models.CharField(max_length=40)
    entry_date = models.DateField(default=timezone.localdate)
    reference = models.CharField(max_length=80, blank=True)
    description = models.CharField(max_length=240, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    total_debit = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    total_credit = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)

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
    description = models.CharField(max_length=200, blank=True)
    debit = models.DecimalField(max_digits=18, decimal_places=2, default=0, validators=[MinValueValidator(Decimal("0"))])
    credit = models.DecimalField(max_digits=18, decimal_places=2, default=0, validators=[MinValueValidator(Decimal("0"))])

    def clean(self):
        if self.debit and self.credit:
            raise ValidationError("A journal line cannot contain both debit and credit.")
        if not self.debit and not self.credit:
            raise ValidationError("A journal line requires a debit or credit value.")
        if self.account_id and self.entry_id and self.account.company_id != self.entry.company_id:
            raise ValidationError("Account and journal entry must belong to the same company.")

    def __str__(self):
        return f"{self.entry.number} · {self.account.code}"


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
        PAID = "paid", "Paid"
        REJECTED = "rejected", "Rejected"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="expense_claims")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="expense_claims")
    reference = models.CharField(max_length=40)
    claim_date = models.DateField(default=timezone.localdate)
    category = models.CharField(max_length=80)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-claim_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "reference"], name="unique_expense_ref_per_company")]

    def __str__(self):
        return self.reference


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

