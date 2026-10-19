from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from core.forms import StyledModelForm
from core.models import Company

from .models import Branch, UserOrganizationAccess


class BranchForm(StyledModelForm):
    class Meta:
        model = Branch
        fields = [
            "company",
            "parent",
            "code",
            "name",
            "email",
            "phone",
            "address",
            "sequence",
            "is_main",
            "active",
        ]


class OrganizationAccessForm(StyledModelForm):
    companies = forms.ModelMultipleChoiceField(
        queryset=Company.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "organization-access-options"}),
        help_text="Choose one or more companies, or enable All companies.",
    )
    branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "organization-access-options"}),
        help_text="Choose branches inside the allowed companies, or enable All branches.",
    )

    class Meta:
        model = UserOrganizationAccess
        fields = [
            "user",
            "all_companies",
            "companies",
            "all_branches",
            "branches",
            "default_company",
            "default_branch",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active_companies = Company.objects.filter(active=True).order_by("name", "code")
        active_branches = Branch.objects.filter(active=True, company__active=True).select_related("company").order_by(
            "company__name", "sequence", "name"
        )
        self.fields["companies"].queryset = active_companies
        self.fields["branches"].queryset = active_branches
        self.fields["default_company"].queryset = active_companies
        self.fields["default_branch"].queryset = active_branches

        if "user" in self.fields:
            user_model = get_user_model()
            users = user_model.objects.filter(is_active=True)
            if self.instance.pk:
                users = user_model.objects.filter(Q(is_active=True) | Q(pk=self.instance.user_id))
                users = users.filter(Q(organization_access__isnull=True) | Q(pk=self.instance.user_id))
            else:
                users = users.filter(organization_access__isnull=True)
            self.fields["user"].queryset = users.order_by("username")
            self.fields["user"].help_text = "Each user has one organization access profile."
        self.fields["all_companies"].help_text = "Includes active companies added later."
        self.fields["all_branches"].help_text = "Includes every active branch inside the allowed companies."
        self.fields["default_company"].help_text = "Company selected automatically when the user signs in."
        self.fields["default_branch"].help_text = "Branch selected automatically inside the default company."

    def clean(self):
        cleaned = super().clean()
        all_companies = cleaned.get("all_companies")
        companies = cleaned.get("companies")
        all_branches = cleaned.get("all_branches")
        branches = cleaned.get("branches")
        default_company = cleaned.get("default_company")
        default_branch = cleaned.get("default_branch")

        company_ids = set(companies.values_list("pk", flat=True)) if companies is not None else set()
        branch_company_ids = set(branches.values_list("company_id", flat=True)) if branches is not None else set()

        if not all_companies and branch_company_ids - company_ids:
            self.add_error("branches", "Every selected branch must belong to a selected company.")
        if default_company and not all_companies and default_company.pk not in company_ids:
            self.add_error("default_company", "Default company must be included in the selected companies.")
        if default_branch and not default_company:
            self.add_error("default_company", "Choose a default company when a default branch is set.")
        elif default_branch and default_branch.company_id != default_company.pk:
            self.add_error("default_branch", "Default branch must belong to the default company.")
        elif default_branch and not all_branches and branches is not None and default_branch.pk not in {
            branch.pk for branch in branches
        }:
            self.add_error("default_branch", "Default branch must be included in the selected branches.")
        return cleaned

    class Media:
        css = {"all": ("css/organization-admin.css",)}
