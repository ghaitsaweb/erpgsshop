from decimal import Decimal
from pathlib import Path

from django import forms
from django.db import transaction
from django.db.models import Q
from django.forms import formset_factory
from django.urls import reverse
from django.utils import timezone

from core.forms import PurchaseOrderForm, StyledModelForm
from core.models import Contact, PurchaseOrderLine

from .models import (
    AwardStrategy,
    BlanketOrder,
    BlanketOrderLine,
    MAX_PURCHASE_DOCUMENT_ATTACHMENTS,
    MAX_PURCHASE_DOCUMENT_ATTACHMENT_TOTAL_SIZE,
    PurchaseDocumentAttachment,
    PurchaseOrderControl,
    PurchaseOrderInvoiceAccounting,
    PurchaseInvoiceAccounting,
    PurchaseOrderLinePricingProvenance,
    PurchaseLegalDocument,
    PurchaseMailTemplate,
    PurchaseRemarkTemplate,
    PurchaseQuotation,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseTeam,
    PurchaseTeamMember,
    PurchaseTender,
    PurchaseTenderLine,
    VendorChangeProposal,
    validate_purchase_document_attachment,
)
from .purchase_cost_center import validate_purchase_cost_center_values
from .services import (
    MAX_PURCHASE_DOCUMENT_LINES,
    apply_purchase_remark_template,
    create_vendor_change_proposal,
    ensure_purchase_mail_templates,
    parse_purchase_email_recipients,
    recalculate_blanket_order_totals,
    render_purchase_mail_template,
    snapshot_purchase_order_commercial_defaults,
    update_blanket_order_prices,
)


class PurchaseRFQMailForm(forms.Form):
    template = forms.ModelChoiceField(
        queryset=PurchaseMailTemplate.objects.none(),
        label="Mail Template",
    )
    subject = forms.CharField(max_length=255)
    email_to = forms.CharField(label="To", max_length=2048)
    email_cc = forms.CharField(label="CC (comma separated)", max_length=2048, required=False)
    body_html = forms.CharField(label="Message", widget=forms.Textarea(attrs={"rows": 14}))

    def __init__(self, *args, quotation, template_code=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.quotation = quotation
        templates = ensure_purchase_mail_templates(quotation.company_id).filter(active=True)
        self.fields["template"].queryset = templates
        if self.is_bound:
            return
        selected = None
        if template_code:
            selected = (
                templates.filter(pk=int(template_code)).first()
                if str(template_code).isdigit()
                else templates.filter(code=template_code).first()
            )
        if selected is None:
            selected = templates.filter(
                code=PurchaseMailTemplate.TemplateType.STANDARD
            ).first()
        if selected is None:
            selected = templates.first()
        if selected is not None:
            subject, body_html = render_purchase_mail_template(selected, quotation)
            self.initial.update(
                {
                    "template": selected,
                    "subject": subject,
                    "body_html": body_html,
                }
            )
        self.initial["email_to"] = quotation.vendor.email

    def clean_template(self):
        template = self.cleaned_data["template"]
        if template.company_id != self.quotation.company_id or not template.active:
            raise forms.ValidationError("Select an active Mail Template from the RFQ company.")
        return template

    def clean_email_to(self):
        value = self.cleaned_data["email_to"]
        parse_purchase_email_recipients(value, required=True)
        return value

    def clean_email_cc(self):
        value = self.cleaned_data.get("email_cc", "")
        parse_purchase_email_recipients(value)
        return value


class PurchaseTenderRFQVendorForm(forms.Form):
    vendors = forms.ModelMultipleChoiceField(
        queryset=Contact.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Eligible Vendors",
    )

    def __init__(self, *args, candidates, vendor_scope, **kwargs):
        super().__init__(*args, **kwargs)
        candidate_ids = [vendor.pk for vendor in candidates]
        self.fields["vendors"].queryset = Contact.objects.filter(
            pk__in=candidate_ids,
        ).order_by("name", "pk")
        if vendor_scope in {"invited", "open_public"}:
            self.fields["vendors"].disabled = True
            self.initial["vendors"] = candidate_ids


def _clean_purchase_cost_center_fields(form, cleaned, company_id):
    if not company_id:
        return cleaned
    try:
        values = validate_purchase_cost_center_values(
            company_id=company_id,
            cost_center=cleaned.get("cost_center", ""),
            cost_center_mode=cleaned.get("cost_center_mode") or "single",
            cost_center_distribution_ids=cleaned.get("cost_center_distribution_ids") or [],
            cost_center_distribution_data=cleaned.get("cost_center_distribution_data") or [],
            cost_center_analytic_tag_ids=cleaned.get("cost_center_analytic_tag_ids") or [],
        )
    except forms.ValidationError as exc:
        form.add_error("cost_center_distribution_data", exc)
    else:
        cleaned.update(values)
    return cleaned


def _clean_purchase_team_assignment(form, cleaned, company_id):
    team = cleaned.get("team") or cleaned.get("purchase_team")
    assigned_to = cleaned.get("assigned_to")
    if team and company_id and team.company_id != company_id:
        form.add_error(
            "team" if "team" in form.fields else "purchase_team",
            "Purchase Team must belong to the document company.",
        )
    if assigned_to and not team:
        form.add_error("assigned_to", "Select a Purchase Team before assigning a PIC.")
    elif assigned_to and not PurchaseTeamMember.objects.filter(
        team_id=team.pk,
        user_id=assigned_to.pk,
        active=True,
    ).exists():
        form.add_error(
            "assigned_to",
            "PIC must be an active member of the selected Purchase Team.",
        )
    return cleaned


def _scope_purchase_team_assignment_choices(form, team_field="team"):
    if "assigned_to" not in form.fields:
        return
    team_value = None
    if form.is_bound:
        team_value = form.data.get(form.add_prefix(team_field))
    if not team_value:
        team_value = form.initial.get(team_field)
    if hasattr(team_value, "pk"):
        team_value = team_value.pk
    assignment_filter = {"purchase_team_memberships__active": True}
    if str(team_value or "").isdigit():
        assignment_filter["purchase_team_memberships__team_id"] = int(team_value)
    form.fields["assigned_to"].queryset = (
        form.fields["assigned_to"].queryset.filter(**assignment_filter)
        .distinct()
        .order_by("username", "pk")
    )


class PurchaseMultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class PurchaseMultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data] if data else []
        clean_file = super().clean
        return [clean_file(item, initial) for item in files]


class PurchaseDocumentAttachmentFormMixin:
    attachment_parent_field = None

    def _initialize_purchase_attachments(self):
        self.fields["attachment_files"] = PurchaseMultipleFileField(
            required=False,
            label="Add attachment files",
            validators=[validate_purchase_document_attachment],
            widget=PurchaseMultipleFileInput(
                attrs={
                    "accept": ".pdf,.jpg,.jpeg,.png",
                    "data-purchase-attachment-input": "true",
                }
            ),
            help_text="PDF, JPG, or PNG. Multiple files supported; maximum 25 MB each and 100 MB per document.",
        )
        self.purchase_attachment_rows = []
        self.purchase_attachment_has_more = False
        if self.instance and self.instance.pk:
            filters = {f"{self.attachment_parent_field}_id": self.instance.pk}
            rows = list(
                PurchaseDocumentAttachment.objects.filter(**filters)
                .select_related("uploaded_by")
                .order_by("-created_at", "-pk")[: MAX_PURCHASE_DOCUMENT_ATTACHMENTS + 1]
            )
            self.purchase_attachment_rows = rows[:MAX_PURCHASE_DOCUMENT_ATTACHMENTS]
            self.purchase_attachment_has_more = len(rows) > MAX_PURCHASE_DOCUMENT_ATTACHMENTS
        self.fields["remove_attachments"] = forms.MultipleChoiceField(
            required=False,
            label="Remove existing attachments",
            choices=[
                (str(row.pk), f"{row.original_name} ({row.file_size / 1024:.1f} KB)")
                for row in self.purchase_attachment_rows
            ],
            widget=forms.CheckboxSelectMultiple,
        )

    def _clean_purchase_attachments(self, cleaned):
        files = cleaned.get("attachment_files") or []
        remove_ids = {int(value) for value in cleaned.get("remove_attachments") or []}
        retained = [row for row in self.purchase_attachment_rows if row.pk not in remove_ids]
        if self.purchase_attachment_has_more or len(retained) + len(files) > MAX_PURCHASE_DOCUMENT_ATTACHMENTS:
            self.add_error(
                "attachment_files",
                f"A Purchase document supports at most {MAX_PURCHASE_DOCUMENT_ATTACHMENTS} attachment files.",
            )
        retained_size = sum(row.file_size for row in retained)
        new_size = sum(upload.size for upload in files)
        if retained_size + new_size > MAX_PURCHASE_DOCUMENT_ATTACHMENT_TOTAL_SIZE:
            self.add_error(
                "attachment_files",
                "Purchase attachment files must total 100 MB or less per document.",
            )
        return cleaned

    def _save_purchase_attachments(self, parent):
        remove_ids = {int(value) for value in self.cleaned_data.get("remove_attachments") or []}
        removed_rows = [row for row in self.purchase_attachment_rows if row.pk in remove_ids]
        if removed_rows:
            PurchaseDocumentAttachment.objects.filter(
                pk__in=[row.pk for row in removed_rows]
            ).delete()
            for row in removed_rows:
                storage = row.file.storage
                stored_name = row.file.name
                transaction.on_commit(
                    lambda storage=storage, stored_name=stored_name: storage.delete(stored_name)
                )
        actor = getattr(self, "purchase_attachment_actor", None)
        uploads = self.cleaned_data.get("attachment_files") or []
        attachment_rows = []
        for upload in uploads:
            values = {
                self.attachment_parent_field: parent,
                "file": upload,
                "original_name": Path(upload.name).name[:255],
                "content_type": str(getattr(upload, "content_type", ""))[:100],
                "file_size": upload.size,
                "uploaded_by": actor if getattr(actor, "is_authenticated", False) else None,
            }
            attachment_rows.append(PurchaseDocumentAttachment(**values))
        if attachment_rows:
            PurchaseDocumentAttachment.objects.bulk_create(attachment_rows)


class PurchaseAttachmentManagementForm(PurchaseDocumentAttachmentFormMixin, forms.Form):
    def __init__(self, *args, parent, parent_field, actor, **kwargs):
        self.instance = parent
        self.attachment_parent_field = parent_field
        super().__init__(*args, **kwargs)
        self.purchase_attachment_actor = actor
        self._initialize_purchase_attachments()

    def clean(self):
        return self._clean_purchase_attachments(super().clean())

    def save(self):
        self._save_purchase_attachments(self.instance)
        return self.instance


def save_purchase_document_attachments_after_lines(instance, form, actor):
    if not isinstance(form, PurchaseDocumentAttachmentFormMixin):
        raise TypeError("Purchase attachment save hook requires a Purchase attachment form.")
    form.purchase_attachment_actor = actor
    form._save_purchase_attachments(instance)


class VendorChangeProposalForm(StyledModelForm):
    class Meta:
        model = VendorChangeProposal
        fields = ["vendor", "proposed_values", "requested_by"]

    def clean(self):
        cleaned = super().clean()
        proposed_values = cleaned.get("proposed_values")
        if not isinstance(proposed_values, dict) or not proposed_values:
            self.add_error("proposed_values", "Enter a JSON object containing at least one Vendor field.")
        return cleaned

    def save(self, commit=True):
        if self.instance.pk or not commit:
            return super().save(commit=commit)
        created = create_vendor_change_proposal(
            self.cleaned_data["vendor"],
            self.cleaned_data["proposed_values"],
            self.cleaned_data["requested_by"],
        )
        self.instance = created
        return created


class PurchaseLegalDocumentForm(StyledModelForm):
    class Meta:
        model = PurchaseLegalDocument
        fields = [
            "company",
            "name",
            "description",
            "document_usage",
            "requirement_type",
            "is_blocking",
            "expiry_applicable",
            "expiry_date_required",
            "default_validity_months",
            "reminder_before_expiry_days",
            "apply_to",
            "applicable_vendors",
            "applicable_categories",
            "active",
        ]

    def clean(self):
        cleaned = super().clean()
        company = cleaned.get("company")
        vendors = cleaned.get("applicable_vendors")
        categories = cleaned.get("applicable_categories")
        if company and vendors is not None and vendors.exclude(company=company).exists():
            self.add_error("applicable_vendors", "Every applicable Vendor must belong to the selected company.")
        if company and categories is not None and categories.exclude(company=company).exists():
            self.add_error(
                "applicable_categories",
                "Every applicable Vendor Category must belong to the selected company.",
            )
        return cleaned


class PurchaseTeamForm(StyledModelForm):
    class Meta:
        model = PurchaseTeam
        fields = [
            "company",
            "name",
            "manager",
            "description",
            "parameter",
            "product_categories",
            "order_types",
            "vendor_categories",
            "active",
        ]

    def clean(self):
        cleaned = super().clean()
        company = cleaned.get("company")
        product_categories = cleaned.get("product_categories")
        order_types = cleaned.get("order_types")
        vendor_categories = cleaned.get("vendor_categories")
        if company and product_categories is not None and product_categories.exclude(company=company).exists():
            self.add_error(
                "product_categories",
                "Every Product Category must belong to the Purchase Team company.",
            )
        if company and order_types is not None and order_types.exclude(
            Q(company=company) | Q(companies=company)
        ).exists():
            self.add_error(
                "order_types",
                "Every Purchase Order Type must be available to the Purchase Team company.",
            )
        if company and vendor_categories is not None and vendor_categories.exclude(company=company).exists():
            self.add_error(
                "vendor_categories",
                "Every Vendor Category must belong to the Purchase Team company.",
            )
        return cleaned


class PurchaseTeamMemberForm(StyledModelForm):
    class Meta:
        model = PurchaseTeamMember
        fields = ["team", "user", "role", "categories", "is_primary", "active"]

    def clean(self):
        cleaned = super().clean()
        team = cleaned.get("team")
        categories = cleaned.get("categories")
        if cleaned.get("is_primary") and not categories:
            self.add_error("is_primary", "A Primary Buyer must have at least one assigned category.")
        if team and categories is not None and categories.exclude(company=team.company).exists():
            self.add_error(
                "categories",
                "Assigned categories must belong to the Purchase Team company.",
            )
        return cleaned


class PurchaseRequestDocumentForm(PurchaseDocumentAttachmentFormMixin, StyledModelForm):
    attachment_parent_field = "purchase_request"

    class Meta:
        model = PurchaseRequest
        fields = [
            "company",
            "order_type",
            "team",
            "number",
            "requestor",
            "request_date",
            "required_date",
            "expiry_date",
            "destination_warehouse",
            "currency",
            "cost_center",
            "cost_center_mode",
            "cost_center_distribution_ids",
            "cost_center_distribution_data",
            "cost_center_analytic_tag_ids",
            "justification",
            "rejection_reason",
            "notes",
            "remark_template",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._initialize_purchase_attachments()
        self._previous_expected_date = (
            self.instance.required_date if self.instance and self.instance.pk else None
        )
        self._previous_expiry_date = (
            self.instance.expiry_date if self.instance and self.instance.pk else None
        )
        self._previous_destination_warehouse_id = (
            self.instance.destination_warehouse_id
            if self.instance and self.instance.pk
            else None
        )

    def clean(self):
        cleaned = super().clean()
        template = cleaned.get("remark_template")
        company = cleaned.get("company")
        if template and not template.active:
            self.add_error("remark_template", "Only an active Purchase Remark Template can be applied.")
        if template and company and template.company_id != company.pk:
            self.add_error("remark_template", "Purchase Remark Template must belong to the request company.")
        if template and template.applicable_document not in {
            PurchaseRemarkTemplate.ApplicableDocument.PURCHASE_REQUEST,
            PurchaseRemarkTemplate.ApplicableDocument.ALL,
        }:
            self.add_error("remark_template", "This template is not applicable to Purchase Requests.")
        _clean_purchase_cost_center_fields(
            self,
            cleaned,
            company.pk if company else None,
        )
        return self._clean_purchase_attachments(cleaned)

    def save(self, commit=True):
        saved = super().save(commit=commit)
        if commit:
            saved._purchase_previous_expected_date = self._previous_expected_date
            saved._purchase_previous_expiry_date = self._previous_expiry_date
            saved._purchase_previous_destination_warehouse_id = (
                self._previous_destination_warehouse_id
            )
        if commit and "remark_template" in self.changed_data and saved.remark_template_id:
            saved = apply_purchase_remark_template(saved, saved.remark_template)
            self.instance = saved
        return saved


class PurchaseRequestLineDocumentForm(StyledModelForm):
    class Meta:
        model = PurchaseRequestLine
        fields = [
            "product",
            "description",
            "specification",
            "quantity",
            "expected_date",
            "destination_warehouse",
            "expiry_date",
            "approved_quantity",
            "ordered_quantity",
            "estimated_unit_price",
            "cost_center",
            "cost_center_mode",
            "cost_center_distribution_ids",
            "cost_center_distribution_data",
            "cost_center_analytic_tag_ids",
            "approval_justification",
            "rejection_reason",
        ]

        widgets = {
            "expected_date": forms.DateInput(attrs={"type": "date"}),
            "expiry_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            return
        request = (
            self.instance.request
            if self.instance and self.instance.request_id
            else None
        )
        if request:
            self.initial.update(
                {
                    "expected_date": request.required_date,
                    "destination_warehouse": request.destination_warehouse_id,
                    "expiry_date": request.expiry_date,
                }
            )

    def clean(self):
        cleaned = super().clean()
        request = self.instance.request if self.instance and self.instance.request_id else None
        return _clean_purchase_cost_center_fields(
            self,
            cleaned,
            request.company_id if request else None,
        )


class PurchaseQuotationDocumentForm(StyledModelForm):
    class Meta:
        model = PurchaseQuotation
        fields = [
            "company",
            "order_type",
            "order_template",
            "number",
            "request",
            "vendor",
            "warehouse",
            "issue_date",
            "response_deadline",
            "expected_date",
            "currency",
            "quoted_amount",
            "team",
            "assigned_to",
            "notes",
        ]
        labels = {"team": "Purchase Team", "assigned_to": "PIC"}
        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date"}),
            "response_deadline": forms.DateInput(attrs={"type": "date"}),
            "expected_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _scope_purchase_team_assignment_choices(self)

    def clean(self):
        cleaned = super().clean()
        company = cleaned.get("company")
        return _clean_purchase_team_assignment(
            self,
            cleaned,
            company.pk if company else None,
        )


class PurchaseTenderDocumentForm(StyledModelForm):
    class Meta:
        model = PurchaseTender
        fields = [
            "company",
            "order_type",
            "order_template",
            "number",
            "title",
            "category",
            "tender_type",
            "method",
            "award_strategy",
            "warehouse",
            "currency",
            "publish_date",
            "bid_start_date",
            "close_date",
            "expiry_date",
            "validity_days",
            "budget_amount",
            "evaluation_price_weight",
            "evaluation_delivery_weight",
            "evaluation_compliance_weight",
            "evaluation_term_weight",
            "evaluation_history_weight",
            "cost_center",
            "cost_center_mode",
            "cost_center_distribution_ids",
            "cost_center_distribution_data",
            "cost_center_analytic_tag_ids",
            "team",
            "assigned_to",
            "description",
        ]

        labels = {"team": "Purchase Team", "assigned_to": "PIC"}

        widgets = {
            "publish_date": forms.DateInput(attrs={"type": "date"}),
            "bid_start_date": forms.DateInput(attrs={"type": "date"}),
            "close_date": forms.DateInput(attrs={"type": "date"}),
            "expiry_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _scope_purchase_team_assignment_choices(self)
        self.fields["order_template"].widget.attrs["data-tender-onchange-url"] = reverse(
            "purchase_tender_onchange"
        )
        self.fields["expiry_date"].widget.attrs["data-tender-header-expiry"] = "true"
        self.fields["warehouse"].widget.attrs["data-tender-header-warehouse"] = "true"

    def clean(self):
        cleaned = super().clean()
        company = cleaned.get("company")
        template = cleaned.get("order_template")
        if template and not template.active:
            self.add_error("order_template", "Only an active Purchase Order Template can be applied.")
        if template and company and template.company_id != company.pk:
            self.add_error(
                "order_template",
                "Purchase Order Template must belong to the Tender company.",
            )
        if template and template.order_type_id:
            cleaned["order_type"] = template.order_type
            self.instance.order_type = template.order_type
        _clean_purchase_team_assignment(
            self,
            cleaned,
            company.pk if company else None,
        )
        return _clean_purchase_cost_center_fields(
            self,
            cleaned,
            company.pk if company else None,
        )


class PurchaseTenderLineDocumentForm(StyledModelForm):
    class Meta:
        model = PurchaseTenderLine
        fields = [
            "request_line",
            "product",
            "description",
            "specification",
            "quantity",
            "target_unit_price",
            "delivery_date",
            "expiry_date",
            "destination_warehouse",
            "cost_center",
            "cost_center_mode",
            "cost_center_distribution_ids",
            "cost_center_distribution_data",
            "cost_center_analytic_tag_ids",
        ]

        widgets = {
            "delivery_date": forms.DateInput(attrs={"type": "date"}),
            "expiry_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].widget.attrs["data-tender-product-onchange-url"] = reverse(
            "purchase_tender_onchange"
        )

    def clean(self):
        cleaned = super().clean()
        tender = self.instance.tender if self.instance and self.instance.tender_id else None
        product = cleaned.get("product")
        if product and not cleaned.get("description"):
            cleaned["description"] = product.name
            self.instance.description = product.name
        if tender and not cleaned.get("expiry_date") and tender.expiry_date:
            cleaned["expiry_date"] = tender.expiry_date
            self.instance.expiry_date = tender.expiry_date
        if tender and not cleaned.get("destination_warehouse") and tender.warehouse_id:
            cleaned["destination_warehouse"] = tender.warehouse
            self.instance.destination_warehouse = tender.warehouse
        return _clean_purchase_cost_center_fields(
            self,
            cleaned,
            tender.company_id if tender else None,
        )


class BlanketOrderDocumentForm(StyledModelForm):
    class Meta:
        model = BlanketOrder
        fields = [
            "company",
            "order_type",
            "order_template",
            "number",
            "vendor",
            "warehouse",
            "currency",
            "date_start",
            "date_end",
            "agreement_value",
            "consumed_value",
            "agreement_policy",
            "cost_center",
            "cost_center_mode",
            "cost_center_distribution_ids",
            "cost_center_distribution_data",
            "cost_center_analytic_tag_ids",
            "terms",
        ]

    def clean(self):
        cleaned = super().clean()
        company = cleaned.get("company")
        return _clean_purchase_cost_center_fields(
            self,
            cleaned,
            company.pk if company else None,
        )


class PurchaseOrderDocumentForm(PurchaseDocumentAttachmentFormMixin, PurchaseOrderForm):
    """Expose Purchase-owned PO control fields without extending the core PO model."""

    attachment_parent_field = "purchase_order"

    expiry_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    cost_center = forms.CharField(required=False, max_length=80)
    cost_center_mode = forms.ChoiceField(
        required=False,
        choices=PurchaseOrderControl.Mode.choices,
    )
    cost_center_distribution_ids = forms.JSONField(required=False, initial=list)
    cost_center_distribution_data = forms.JSONField(required=False, initial=list)
    cost_center_analytic_tag_ids = forms.JSONField(required=False, initial=list)
    contract_terms = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    payment_terms = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    delivery_terms = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    legal_terms = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    vendor_address = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    bill_address = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    expected_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    rental_duration = forms.IntegerField(required=False, min_value=0, initial=0)
    duration_unit = forms.ChoiceField(
        required=False,
        choices=PurchaseOrderControl.DurationUnit.choices,
    )
    is_multiple_expected_date = forms.BooleanField(required=False)
    is_multiple_rental_duration = forms.BooleanField(required=False)
    remark_template = forms.ModelChoiceField(
        queryset=PurchaseRemarkTemplate.objects.all(),
        required=False,
    )
    downpayment_method = forms.ChoiceField(
        choices=PurchaseOrderControl.DownpaymentMethod.choices,
        initial=PurchaseOrderControl.DownpaymentMethod.WITH_VAT,
        widget=forms.RadioSelect,
        label="Down Payment Method",
    )
    downpayment_type = forms.ChoiceField(
        choices=PurchaseOrderInvoiceAccounting.DownpaymentType.choices,
        initial=PurchaseOrderInvoiceAccounting.DownpaymentType.FIXED_AMOUNT,
        widget=forms.RadioSelect,
        label="Down Payment Basis",
    )
    downpayment_amount = forms.DecimalField(
        required=False,
        min_value=0,
        max_digits=18,
        decimal_places=2,
        initial=0,
        label="Fixed Down Payment Amount",
    )
    downpayment_percentage = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=100,
        max_digits=5,
        decimal_places=2,
        initial=0,
        label="Down Payment Percentage",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._initialize_purchase_attachments()
        self.fields["invoicing_policy"].widget = forms.RadioSelect(
            choices=self.fields["invoicing_policy"].choices
        )
        self._previous_expected_date = None
        self._previous_expiry_date = None
        self._purchase_billing_plan = None
        self._purchase_billing_locked = False
        if self.instance and self.instance.pk:
            control = PurchaseOrderControl.objects.filter(order_id=self.instance.pk).only(
                "expiry_date",
                "cost_center",
                "contract_terms",
                "payment_terms",
                "delivery_terms",
                "legal_terms",
                "vendor_address",
                "bill_address",
                "cost_center_mode",
                "cost_center_distribution_ids",
                "cost_center_distribution_data",
                "cost_center_analytic_tag_ids",
                "expected_date",
                "rental_duration",
                "duration_unit",
                "is_multiple_expected_date",
                "is_multiple_rental_duration",
                "remark_template",
                "remark_template_code",
                "remark_template_name",
                "downpayment_method",
            ).first()
            if control:
                self._previous_expected_date = control.expected_date
                self._previous_expiry_date = control.expiry_date
                self.instance._state.fields_cache["purchase_control"] = control
                self.initial.update(
                    {
                        "expiry_date": control.expiry_date,
                        "cost_center": control.cost_center,
                        "contract_terms": control.contract_terms,
                        "payment_terms": control.payment_terms,
                        "delivery_terms": control.delivery_terms,
                        "legal_terms": control.legal_terms,
                        "vendor_address": control.vendor_address,
                        "bill_address": control.bill_address,
                        "cost_center_mode": control.cost_center_mode,
                        "cost_center_distribution_ids": control.cost_center_distribution_ids,
                        "cost_center_distribution_data": control.cost_center_distribution_data,
                        "cost_center_analytic_tag_ids": control.cost_center_analytic_tag_ids,
                        "expected_date": control.expected_date,
                        "rental_duration": control.rental_duration,
                        "duration_unit": control.duration_unit,
                        "is_multiple_expected_date": control.is_multiple_expected_date,
                        "is_multiple_rental_duration": control.is_multiple_rental_duration,
                        "remark_template": control.remark_template_id,
                        "downpayment_method": control.downpayment_method,
                    }
                )
            self._purchase_billing_plan = PurchaseOrderInvoiceAccounting.objects.filter(
                order_id=self.instance.pk
            ).first()
            if self._purchase_billing_plan:
                self.initial.update(
                    {
                        "downpayment_type": self._purchase_billing_plan.downpayment_type,
                        "downpayment_amount": self._purchase_billing_plan.downpayment_amount,
                        "downpayment_percentage": self._purchase_billing_plan.downpayment_percentage,
                    }
                )
                self._purchase_billing_locked = PurchaseInvoiceAccounting.objects.filter(
                    order_accounting_id=self._purchase_billing_plan.pk
                ).exists()
        if self._purchase_billing_locked:
            for field_name in (
                "invoicing_policy",
                "downpayment_method",
                "downpayment_type",
                "downpayment_amount",
                "downpayment_percentage",
            ):
                self.fields[field_name].disabled = True
        _scope_purchase_team_assignment_choices(self, team_field="purchase_team")

    def clean(self):
        cleaned = super().clean()
        expiry_date = cleaned.get("expiry_date")
        order_date = cleaned.get("order_date")
        if expiry_date and order_date and expiry_date < order_date:
            self.add_error("expiry_date", "Expiry Date cannot be before the Purchase Order date.")
        template = cleaned.get("remark_template")
        company = cleaned.get("company")
        if template and not template.active:
            self.add_error("remark_template", "Only an active Purchase Remark Template can be applied.")
        if template and company and template.company_id != company.pk:
            self.add_error("remark_template", "Purchase Remark Template must belong to the order company.")
        if template and template.applicable_document not in {
            PurchaseRemarkTemplate.ApplicableDocument.PURCHASE_ORDER,
            PurchaseRemarkTemplate.ApplicableDocument.ALL,
        }:
            self.add_error("remark_template", "This template is not applicable to Purchase Orders.")
        _clean_purchase_team_assignment(
            self,
            cleaned,
            company.pk if company else None,
        )
        _clean_purchase_cost_center_fields(
            self,
            cleaned,
            company.pk if company else None,
        )
        downpayment_type = cleaned.get("downpayment_type")
        downpayment_amount = cleaned.get("downpayment_amount") or Decimal("0")
        downpayment_percentage = cleaned.get("downpayment_percentage") or Decimal("0")
        has_downpayment = bool(
            downpayment_amount > 0
            or downpayment_percentage > 0
        )
        if has_downpayment:
            if (
                downpayment_type
                == PurchaseOrderInvoiceAccounting.DownpaymentType.FIXED_AMOUNT
                and downpayment_amount <= 0
            ):
                self.add_error(
                    "downpayment_amount",
                    "Set a positive fixed Purchase down-payment amount.",
                )
            if (
                downpayment_type
                == PurchaseOrderInvoiceAccounting.DownpaymentType.PERCENTAGE
                and downpayment_percentage <= 0
            ):
                self.add_error(
                    "downpayment_percentage",
                    "Set a positive Purchase down-payment percentage.",
                )
        return self._clean_purchase_attachments(cleaned)

    def save(self, commit=True):
        saved = super().save(commit=commit)
        if not commit:
            return saved
        saved._purchase_previous_expected_date = self._previous_expected_date
        saved._purchase_previous_expiry_date = self._previous_expiry_date
        PurchaseOrderControl.objects.filter(order=saved).update(
            expiry_date=self.cleaned_data.get("expiry_date"),
            cost_center=self.cleaned_data.get("cost_center", ""),
            contract_terms=self.cleaned_data.get("contract_terms", ""),
            payment_terms=self.cleaned_data.get("payment_terms", ""),
            delivery_terms=self.cleaned_data.get("delivery_terms", ""),
            legal_terms=self.cleaned_data.get("legal_terms", ""),
            vendor_address=self.cleaned_data.get("vendor_address", ""),
            bill_address=self.cleaned_data.get("bill_address", ""),
            cost_center_mode=self.cleaned_data.get("cost_center_mode") or "single",
            cost_center_distribution_ids=self.cleaned_data.get("cost_center_distribution_ids") or [],
            cost_center_distribution_data=self.cleaned_data.get("cost_center_distribution_data") or [],
            cost_center_analytic_tag_ids=self.cleaned_data.get("cost_center_analytic_tag_ids") or [],
            expected_date=self.cleaned_data.get("expected_date"),
            rental_duration=self.cleaned_data.get("rental_duration") or 0,
            duration_unit=(
                self.cleaned_data.get("duration_unit")
                or PurchaseOrderControl.DurationUnit.DAY
            ),
            is_multiple_expected_date=bool(
                self.cleaned_data.get("is_multiple_expected_date")
            ),
            is_multiple_rental_duration=bool(
                self.cleaned_data.get("is_multiple_rental_duration")
            ),
            remark_template=self.cleaned_data.get("remark_template"),
            downpayment_method=(
                self.cleaned_data.get("downpayment_method")
                or PurchaseOrderControl.DownpaymentMethod.WITH_VAT
            ),
            updated_at=timezone.now(),
        )
        downpayment_amount = self.cleaned_data.get("downpayment_amount") or Decimal("0")
        downpayment_percentage = self.cleaned_data.get("downpayment_percentage") or Decimal("0")
        has_downpayment = downpayment_amount > 0 or downpayment_percentage > 0
        if has_downpayment:
            PurchaseOrderInvoiceAccounting.objects.update_or_create(
                order=saved,
                defaults={
                    "downpayment_type": self.cleaned_data.get("downpayment_type"),
                    "downpayment_amount": downpayment_amount,
                    "downpayment_percentage": downpayment_percentage,
                },
            )
        elif self._purchase_billing_plan and not self._purchase_billing_locked:
            self._purchase_billing_plan.delete()
        snapshot_purchase_order_commercial_defaults(saved)
        template = self.cleaned_data.get("remark_template")
        if "remark_template" in self.changed_data and template:
            saved = apply_purchase_remark_template(saved, template)
            self.instance = saved
        return saved


class PurchaseOrderLineDocumentForm(StyledModelForm):
    """Edit Purchase-owned rental controls beside one core Purchase Order line."""

    rent_duration = forms.IntegerField(required=False, min_value=0, initial=0)
    rental_period = forms.ChoiceField(
        required=False,
        choices=PurchaseOrderControl.DurationUnit.choices,
    )
    expected_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    expiry_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    cost_center = forms.CharField(required=False, max_length=80)
    cost_center_mode = forms.ChoiceField(
        required=False,
        choices=PurchaseOrderControl.Mode.choices,
    )
    cost_center_distribution_ids = forms.JSONField(required=False, initial=list)
    cost_center_distribution_data = forms.JSONField(required=False, initial=list)
    cost_center_analytic_tag_ids = forms.JSONField(required=False, initial=list)

    class Meta:
        model = PurchaseOrderLine
        fields = [
            "product",
            "description",
            "warehouse",
            "quantity",
            "unit_price",
            "cost_center",
            "cost_center_mode",
            "cost_center_distribution_ids",
            "cost_center_distribution_data",
            "cost_center_analytic_tag_ids",
            "expected_date",
            "expiry_date",
            "rent_duration",
            "rental_period",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        provenance = None
        if self.instance and self.instance.pk:
            try:
                provenance = self.instance.purchase_pricing_provenance
            except PurchaseOrderLinePricingProvenance.DoesNotExist:
                provenance = None
        if provenance:
            self.initial.update(
                {
                    "rent_duration": provenance.rent_duration,
                    "rental_period": provenance.rental_period,
                    "expected_date": provenance.expected_date,
                    "expiry_date": provenance.expiry_date,
                    "cost_center": provenance.cost_center,
                    "cost_center_mode": provenance.cost_center_mode,
                    "cost_center_distribution_ids": provenance.cost_center_distribution_ids,
                    "cost_center_distribution_data": provenance.cost_center_distribution_data,
                    "cost_center_analytic_tag_ids": provenance.cost_center_analytic_tag_ids,
                }
            )
            return

        order = (
            self.instance.order
            if self.instance and self.instance.order_id
            else None
        )
        if not order:
            return
        try:
            control = order.purchase_control
        except PurchaseOrderControl.DoesNotExist:
            return
        self.initial.update(
            {
                "rent_duration": control.rental_duration,
                "rental_period": control.duration_unit,
                "expected_date": control.expected_date,
                "expiry_date": control.expiry_date,
                "cost_center": control.cost_center,
                "cost_center_mode": control.cost_center_mode,
                "cost_center_distribution_ids": control.cost_center_distribution_ids,
                "cost_center_distribution_data": control.cost_center_distribution_data,
                "cost_center_analytic_tag_ids": control.cost_center_analytic_tag_ids,
            }
        )

    def clean(self):
        cleaned = super().clean()
        order = self.instance.order if self.instance and self.instance.order_id else None
        return _clean_purchase_cost_center_fields(
            self,
            cleaned,
            order.company_id if order else None,
        )


class BlanketOrderLineForm(StyledModelForm):
    class Meta:
        model = BlanketOrderLine
        fields = [
            "blanket_order",
            "request_line",
            "product",
            "description",
            "agreed_quantity",
            "released_quantity",
            "unit_price",
            "tax_percent",
            "cost_center",
            "cost_center_mode",
            "cost_center_distribution_ids",
            "cost_center_distribution_data",
            "cost_center_analytic_tag_ids",
            "minimum_release_quantity",
        ]

    def clean(self):
        cleaned = super().clean()
        blanket = cleaned.get("blanket_order")
        if self.instance.pk and self.instance.free_product:
            raise forms.ValidationError(
                "Generated free-product lines are maintained from their paid source line."
            )
        if blanket and blanket.status != BlanketOrder.Status.DRAFT:
            self.add_error("blanket_order", "Blanket Order lines can only be edited while the agreement is Draft.")
        _clean_purchase_cost_center_fields(
            self,
            cleaned,
            blanket.company_id if blanket else None,
        )
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        explicit_manual_price = "unit_price" in self.changed_data and instance.unit_price > 0
        price_drivers_changed = bool(
            {"blanket_order", "product", "agreed_quantity"}.intersection(self.changed_data)
        )
        if explicit_manual_price or (
            not instance.price_source
            and instance.unit_price > 0
            and not {"blanket_order", "product"}.intersection(self.changed_data)
        ):
            instance.price_source = BlanketOrderLine.PriceSource.MANUAL
            instance.applied_pricelist_id = None
            instance.applied_pricelist_tier_id = None
            instance.price_source_note = "Manual price"
            instance.discount_formula = ""
        elif {"blanket_order", "product"}.intersection(self.changed_data):
            instance.price_source = ""
            instance.applied_pricelist_id = None
            instance.applied_pricelist_tier_id = None
            instance.price_source_note = ""
            instance.discount_formula = ""
        if not commit:
            return instance
        with transaction.atomic():
            instance.save()
            if explicit_manual_price or price_drivers_changed:
                update_blanket_order_prices(instance.blanket_order)
                instance.refresh_from_db()
            elif "tax_percent" in self.changed_data:
                recalculate_blanket_order_totals(instance.blanket_order)
        return instance


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

    def __init__(self, *args, quantity_controlled=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.quantity_controlled = quantity_controlled
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("include"):
            return cleaned
        quantity = cleaned.get("quantity")
        remaining = cleaned.get("remaining_quantity")
        if self.quantity_controlled and quantity is not None and remaining is not None and quantity > remaining:
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
