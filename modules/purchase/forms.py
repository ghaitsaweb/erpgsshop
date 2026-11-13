from decimal import Decimal

from django import forms
from django.db import transaction
from django.db.models import Q
from django.forms import formset_factory
from django.utils import timezone

from core.forms import PurchaseOrderForm, StyledModelForm
from core.models import Contact, PurchaseOrderLine

from .models import (
    AwardStrategy,
    BlanketOrder,
    BlanketOrderLine,
    PurchaseOrderControl,
    PurchaseOrderLinePricingProvenance,
    PurchaseLegalDocument,
    PurchaseMailTemplate,
    PurchaseRemarkTemplate,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseTeam,
    PurchaseTeamMember,
    PurchaseTender,
    PurchaseTenderLine,
    VendorChangeProposal,
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


class PurchaseRequestDocumentForm(StyledModelForm):
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
        return cleaned

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
            "validity_days",
            "budget_amount",
            "cost_center",
            "cost_center_mode",
            "cost_center_distribution_ids",
            "cost_center_distribution_data",
            "cost_center_analytic_tag_ids",
            "description",
        ]

    def clean(self):
        cleaned = super().clean()
        company = cleaned.get("company")
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
            "cost_center",
            "cost_center_mode",
            "cost_center_distribution_ids",
            "cost_center_distribution_data",
            "cost_center_analytic_tag_ids",
        ]

    def clean(self):
        cleaned = super().clean()
        tender = self.instance.tender if self.instance and self.instance.tender_id else None
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


class PurchaseOrderDocumentForm(PurchaseOrderForm):
    """Expose Purchase-owned PO control fields without extending the core PO model."""

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._previous_expected_date = None
        self._previous_expiry_date = None
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
                    }
                )

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
        _clean_purchase_cost_center_fields(
            self,
            cleaned,
            company.pk if company else None,
        )
        return cleaned

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
            updated_at=timezone.now(),
        )
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
