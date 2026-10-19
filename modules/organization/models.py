from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from core.models import Company, TimeStampedModel


class Branch(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="branches")
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="children",
        blank=True,
        null=True,
    )
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=160)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    sequence = models.PositiveIntegerField(default=10)
    is_main = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__name", "sequence", "name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_branch_code_per_company"),
            models.UniqueConstraint(
                fields=["company"],
                condition=Q(is_main=True),
                name="one_main_branch_per_company",
            ),
        ]

    def clean(self):
        super().clean()
        if self.parent_id and self.parent.company_id != self.company_id:
            raise ValidationError({"parent": "Parent branch must belong to the same company."})

    def __str__(self):
        return f"{self.company.code} / {self.code} - {self.name}"


class UserOrganizationAccess(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organization_access",
    )
    companies = models.ManyToManyField(Company, blank=True, related_name="organization_users")
    branches = models.ManyToManyField(Branch, blank=True, related_name="organization_users")
    all_companies = models.BooleanField(
        default=False,
        help_text="Allow every active company, including companies created later.",
    )
    all_branches = models.BooleanField(
        default=False,
        help_text="Allow every active branch inside the allowed companies.",
    )
    default_company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        related_name="default_for_users",
        blank=True,
        null=True,
    )
    default_branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        related_name="default_for_users",
        blank=True,
        null=True,
    )

    class Meta:
        verbose_name = "User organization access"
        verbose_name_plural = "User organization access"

    def clean(self):
        super().clean()
        if self.default_branch_id and not self.default_company_id:
            raise ValidationError({"default_company": "Choose a default company when a default branch is set."})
        if self.default_branch_id and self.default_branch.company_id != self.default_company_id:
            raise ValidationError({"default_branch": "Default branch must belong to the default company."})

    def __str__(self):
        return f"Organization access · {self.user}"
