from django.core.validators import MinValueValidator
from django.db import models

from core.models import TimeStampedModel


class LocalizationPack(TimeStampedModel):
    country_code = models.CharField(max_length=2, unique=True)
    country_name = models.CharField(max_length=100)
    currency = models.CharField(max_length=3)
    tax_label = models.CharField(max_length=40)
    language_code = models.CharField(max_length=12, default="en")
    version = models.CharField(max_length=20, default="0.1.0")
    enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ["country_name"]

    def __str__(self):
        return f"{self.country_code} - {self.country_name}"


class TaxRule(TimeStampedModel):
    class TaxType(models.TextChoices):
        SALES = "sales", "Sales Tax"
        PURCHASE = "purchase", "Purchase Tax"
        WITHHOLDING = "withholding", "Withholding Tax"
        ZERO = "zero", "Zero Rated"
        EXEMPT = "exempt", "Exempt"

    localization = models.ForeignKey(LocalizationPack, on_delete=models.CASCADE, related_name="tax_rules")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=140)
    tax_type = models.CharField(max_length=16, choices=TaxType.choices)
    rate = models.DecimalField(max_digits=7, decimal_places=4, default=0, validators=[MinValueValidator(0)])
    effective_from = models.DateField(blank=True, null=True)
    effective_until = models.DateField(blank=True, null=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["localization", "code"]
        constraints = [models.UniqueConstraint(fields=["localization", "code"], name="unique_tax_rule_per_localization")]

    def __str__(self):
        return f"{self.localization.country_code} · {self.code}"


class PayrollParameter(TimeStampedModel):
    localization = models.ForeignKey(LocalizationPack, on_delete=models.CASCADE, related_name="payroll_parameters")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=140)
    value = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    unit = models.CharField(max_length=24, default="percent")
    effective_from = models.DateField(blank=True, null=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["localization", "code"]
        constraints = [models.UniqueConstraint(fields=["localization", "code"], name="unique_payroll_parameter_per_localization")]

    def __str__(self):
        return f"{self.localization.country_code} · {self.code}"


class DocumentSequence(TimeStampedModel):
    localization = models.ForeignKey(LocalizationPack, on_delete=models.CASCADE, related_name="document_sequences")
    document_type = models.CharField(max_length=80)
    prefix = models.CharField(max_length=30)
    padding = models.PositiveSmallIntegerField(default=5)
    next_number = models.PositiveBigIntegerField(default=1)
    reset_annually = models.BooleanField(default=True)

    class Meta:
        ordering = ["localization", "document_type"]
        constraints = [models.UniqueConstraint(fields=["localization", "document_type"], name="unique_document_sequence_per_localization")]

    def __str__(self):
        return f"{self.localization.country_code} · {self.document_type}"

