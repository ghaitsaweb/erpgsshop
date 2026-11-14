from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import (
    Attendance,
    Company,
    Contact,
    Employee,
    Invoice,
    Product,
    PurchaseOrder,
    SaleOrder,
    SaleOrderLine,
    SaleOrderType,
    StockMove,
    Warehouse,
)

from modules.purchase.models import (
    PurchaseMilestoneTemplate,
    PurchaseOrderControl,
    PurchaseOrderTemplate,
    PurchaseOrderType,
    PurchaseTeam,
)


class SearchableMasterWidget(forms.HiddenInput):
    """Keep a master-record id hidden while exposing its searchable control."""

    @property
    def is_hidden(self):
        return False


class ImagePreviewFileInput(forms.ClearableFileInput):
    """Render image uploads without exposing the storage path as a text link."""

    template_name = "core/widgets/image_preview_file_input.html"
    clear_checkbox_label = "Remove current image"


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field, forms.ImageField):
                if not isinstance(field.widget, ImagePreviewFileInput):
                    field.widget = ImagePreviewFileInput(attrs=field.widget.attrs)
                field.widget.is_image_preview = True
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"form-control {existing}".strip()
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("rows", 3)
            if isinstance(field, forms.DateField) and isinstance(field.widget, forms.DateInput):
                field.widget.input_type = "date"
                field.widget.format = "%Y-%m-%d"
                field.widget.attrs.pop("type", None)

    def clean(self):
        cleaned_data = super().clean()
        for percent_field in ("discount_percent", "global_discount_percent", "tax_percent"):
            if percent_field in self.fields and cleaned_data.get(percent_field) in (None, ""):
                cleaned_data[percent_field] = Decimal("0")
        branch = cleaned_data.get("organization_branch")
        company = cleaned_data.get("company")
        company_id = getattr(company, "pk", None) or getattr(self.instance, "company_id", None)
        if branch and company_id and branch.company_id != company_id:
            self.add_error("organization_branch", "Branch must belong to the selected company.")
        return cleaned_data

    def save(self, commit=True):
        branch = self.cleaned_data.get("organization_branch")
        if branch:
            self.instance._gsshop_organization_branch_id = branch.pk
        return super().save(commit=commit)


class CompanyForm(StyledModelForm):
    MAX_LOGO_SIZE = 5 * 1024 * 1024

    class Meta:
        model = Company
        fields = ["code", "name", "logo", "email", "phone", "address", "currency", "active"]
        widgets = {
            "logo": forms.ClearableFileInput(
                attrs={"accept": "image/png,image/jpeg,image/webp"}
            )
        }

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if logo and getattr(logo, "size", 0) > self.MAX_LOGO_SIZE:
            raise forms.ValidationError("Company logo must be 5 MB or smaller.")
        return logo


class ContactForm(StyledModelForm):
    class Meta:
        model = Contact
        fields = ["company", "code", "name", "contact_type", "email", "phone", "tax_id", "address", "active"]


class ProductForm(StyledModelForm):
    class Meta:
        model = Product
        fields = ["company", "sku", "name", "product_type", "unit", "sale_price", "purchase_price", "reorder_level", "active"]


class WarehouseForm(StyledModelForm):
    class Meta:
        model = Warehouse
        fields = ["company", "branch", "code", "name", "address", "active"]


class StockMoveForm(StyledModelForm):
    class Meta:
        model = StockMove
        fields = ["company", "product", "warehouse", "direction", "quantity", "reference", "movement_date", "note"]


class SaleOrderTypeForm(StyledModelForm):
    class Meta:
        model = SaleOrderType
        fields = ["name", "companies", "description", "origin"]

    def clean(self):
        cleaned_data = super().clean()
        if self.instance.pk and "companies" in self.changed_data and self.instance.sale_orders.exists():
            self.add_error(
                "companies",
                "The Companies field cannot be modified because this Order Type is already used in transactions.",
            )
        return cleaned_data


class SaleOrderForm(StyledModelForm):
    class Meta:
        model = SaleOrder
        fields = ["company", "number", "customer", "order_date", "status", "notes"]


class SaleOrderDocumentForm(StyledModelForm):
    customer = forms.ModelChoiceField(
        label="Customer",
        queryset=Contact.objects.filter(
            active=True,
            contact_type__in=[Contact.ContactType.CUSTOMER, Contact.ContactType.BOTH],
        ),
        widget=SearchableMasterWidget(),
    )

    class Meta:
        model = SaleOrder
        fields = [
            "company",
            "order_type",
            "number",
            "customer",
            "validity_date",
            "order_date",
            "currency",
            "price_list",
            "payment_terms",
            "invoicing_policy",
            "salesperson_name",
            "customer_reference",
            "delivery_address",
            "invoice_address",
            "delivery_date",
            "global_discount_percent",
            "terms_conditions",
            "notes",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and not self.instance.pk:
            from .sales import DEFAULT_SALES_QUOTATION_VALIDITY_DAYS

            self.fields["validity_date"].initial = timezone.localdate() + timedelta(
                days=DEFAULT_SALES_QUOTATION_VALIDITY_DAYS
            )
        self.fields["number"].required = False
        self.fields["order_type"].required = False
        from modules.organization.context import get_current_context

        organization_context = get_current_context()
        if organization_context and organization_context.active_company_ids:
            self.fields["order_type"].queryset = (
                SaleOrderType.objects.filter(origin=SaleOrderType.Origin.SALES)
                .filter(
                    Q(companies__isnull=True)
                    | Q(companies__pk__in=organization_context.active_company_ids)
                )
                .order_by("name", "pk")
                .distinct()
            )
        self.fields["currency"].required = False
        self.fields["invoicing_policy"].required = False
        customer_field = self.fields["customer"]
        customer_field.widget.attrs.update(
            {
                "data-master-search-url": reverse("customer_search"),
                "data-master-search-placeholder": "Cari kode atau nama customer...",
            }
        )
        posted_label = ""
        if self.is_bound:
            posted_label = str(self.data.get(f"{self.add_prefix('customer')}_display", "")).strip()
        if posted_label:
            customer_field.widget.attrs["data-initial-label"] = posted_label
        elif self.instance and self.instance.customer_id:
            customer = self.instance.customer
            customer_field.widget.attrs["data-initial-label"] = f"{customer.code} — {customer.name}"

    def clean(self):
        cleaned_data = super().clean()
        company = cleaned_data.get("company")
        order_type = cleaned_data.get("order_type")
        customer = cleaned_data.get("customer")
        if not customer and self.is_bound:
            display_value = str(
                self.data.get(f"{self.add_prefix('customer')}_display", "")
            ).strip()[:200]
            if display_value:
                search_value = display_value
                exact_code = ""
                if "—" in display_value:
                    exact_code, search_value = (
                        value.strip() for value in display_value.split("—", 1)
                    )
                candidate_filter = (
                    Q(code__iexact=exact_code)
                    if exact_code
                    else Q(code__icontains=search_value) | Q(name__icontains=search_value)
                )
                candidates = list(
                    self.fields["customer"].queryset.filter(candidate_filter).order_by("name", "pk")[:2]
                )
                if len(candidates) == 1:
                    customer = candidates[0]
                    cleaned_data["customer"] = customer
                    self.instance.customer = customer
                    self._errors.pop("customer", None)
        if company and not cleaned_data.get("currency"):
            cleaned_data["currency"] = company.currency
            self.instance.currency = company.currency
        if not cleaned_data.get("invoicing_policy"):
            cleaned_data["invoicing_policy"] = SaleOrder.InvoicingPolicy.ORDERED
            self.instance.invoicing_policy = SaleOrder.InvoicingPolicy.ORDERED
        if company and not order_type:
            from .sales import get_default_sale_order_type

            order_type = get_default_sale_order_type(company.pk)
            if order_type:
                cleaned_data["order_type"] = order_type
                self.instance.order_type = order_type
                self._errors.pop("order_type", None)
        if order_type and company:
            scoped_company_ids = list(
                order_type.companies.order_by().values_list("pk", flat=True)[:2]
            )
            if scoped_company_ids and company.pk not in scoped_company_ids:
                self.add_error(
                    "order_type",
                    "Order Type must be global or available to the Sales Order company.",
                )
        if company and not cleaned_data.get("validity_date") and not self.instance.pk:
            from .sales import get_sale_quotation_validity_days

            cleaned_data["validity_date"] = timezone.localdate() + timedelta(
                days=get_sale_quotation_validity_days(company.pk)
            )
            self.instance.validity_date = cleaned_data["validity_date"]
        if company and not cleaned_data.get("delivery_date") and not self.instance.pk:
            from .sales import get_sale_default_delivery_days

            delivery_days = get_sale_default_delivery_days(company.pk)
            if delivery_days and cleaned_data.get("order_date"):
                cleaned_data["delivery_date"] = cleaned_data.get("order_date") + timedelta(
                    days=delivery_days
                )
                self.instance.delivery_date = cleaned_data["delivery_date"]
        if customer:
            if not cleaned_data.get("delivery_address"):
                cleaned_data["delivery_address"] = customer.address
                self.instance.delivery_address = customer.address
            if not cleaned_data.get("invoice_address"):
                cleaned_data["invoice_address"] = customer.address
                self.instance.invoice_address = customer.address
        return cleaned_data


class SaleOrderLineForm(StyledModelForm):
    product = forms.ModelChoiceField(
        label="Produk",
        queryset=Product.objects.all(),
        required=False,
        widget=SearchableMasterWidget(),
    )
    delivered_quantity_display = forms.DecimalField(
        label="Delivered Qty",
        required=False,
        disabled=True,
        decimal_places=3,
        max_digits=18,
    )
    invoiced_quantity_display = forms.DecimalField(
        label="Invoiced Qty",
        required=False,
        disabled=True,
        decimal_places=3,
        max_digits=18,
    )
    gross_amount_display = forms.DecimalField(
        label="Subtotal",
        required=False,
        disabled=True,
        decimal_places=2,
        max_digits=18,
    )
    line_discount_amount_display = forms.DecimalField(
        label="Discount Amount",
        required=False,
        disabled=True,
        decimal_places=2,
        max_digits=18,
    )
    untaxed_amount_display = forms.DecimalField(
        label="Untaxed",
        required=False,
        disabled=True,
        decimal_places=2,
        max_digits=18,
    )
    tax_amount_display = forms.DecimalField(
        label="Tax Amount",
        required=False,
        disabled=True,
        decimal_places=2,
        max_digits=18,
    )
    total_amount_display = forms.DecimalField(
        label="Total",
        required=False,
        disabled=True,
        decimal_places=2,
        max_digits=18,
    )

    class Meta:
        model = SaleOrderLine
        fields = [
            "product",
            "description",
            "delivery_address",
            "delivery_date",
            "quantity",
            "uom",
            "delivered_quantity_display",
            "invoiced_quantity_display",
            "unit_price",
            "discount_percent",
            "gross_amount_display",
            "line_discount_amount_display",
            "untaxed_amount_display",
            "tax_percent",
            "tax_amount_display",
            "total_amount_display",
            "rental_start_date",
            "rental_return_date",
            "rental_status",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        product_field = self.fields["product"]
        product_field.widget.attrs.update(
            {
                "data-master-search-url": reverse("product_search"),
                "data-master-search-placeholder": "Cari SKU atau nama produk...",
            }
        )
        if self.instance and self.instance.product_id:
            product = self.instance.product
            product_field.widget.attrs["data-initial-label"] = f"{product.sku} — {product.name}"
        if self.instance and self.instance.pk:
            self.initial.update(
                {
                    "delivered_quantity_display": self.instance.delivered_quantity,
                    "invoiced_quantity_display": self.instance.invoiced_quantity,
                    "gross_amount_display": self.instance.gross_amount,
                    "line_discount_amount_display": self.instance.line_discount_amount,
                    "untaxed_amount_display": self.instance.untaxed_amount,
                    "tax_amount_display": self.instance.tax_amount,
                    "total_amount_display": self.instance.total_amount,
                }
            )
        for field_name in (
            "gross_amount_display",
            "line_discount_amount_display",
            "untaxed_amount_display",
            "tax_amount_display",
            "total_amount_display",
        ):
            self.fields[field_name].widget.attrs["data-sales-amount-output"] = field_name


class PurchaseOrderForm(StyledModelForm):
    order_type = forms.ModelChoiceField(queryset=PurchaseOrderType.objects.filter(active=True), required=False)
    order_template = forms.ModelChoiceField(queryset=PurchaseOrderTemplate.objects.filter(active=True), required=False)
    milestone_template = forms.ModelChoiceField(queryset=PurchaseMilestoneTemplate.objects.all(), required=False)
    purchase_team = forms.ModelChoiceField(queryset=PurchaseTeam.objects.filter(active=True), required=False)
    assigned_to = forms.ModelChoiceField(queryset=get_user_model().objects.filter(is_active=True), required=False)

    class Meta:
        model = PurchaseOrder
        fields = [
            "company",
            "number",
            "vendor",
            "warehouse",
            "invoicing_policy",
            "order_date",
            "notes",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            control = PurchaseOrderControl.objects.filter(order_id=self.instance.pk).first()
            if control:
                self.initial.update(
                    {
                        "order_type": control.order_type_id,
                        "order_template": control.order_template_id,
                        "milestone_template": control.milestone_template_id,
                        "purchase_team": control.team_id,
                        "assigned_to": control.assigned_to_id,
                    }
                )

    def clean(self):
        cleaned_data = super().clean()
        company = cleaned_data.get("company")
        order_type = cleaned_data.get("order_type")
        order_template = cleaned_data.get("order_template")
        milestone_template = cleaned_data.get("milestone_template")
        purchase_team = cleaned_data.get("purchase_team")
        if order_template and order_template.order_type_id and not order_type:
            order_type = order_template.order_type
            cleaned_data["order_type"] = order_type
        if order_template and order_template.vendor_id and not cleaned_data.get("vendor"):
            cleaned_data["vendor"] = order_template.vendor
        if not cleaned_data.get("number") and not order_type:
            self.add_error("order_type", "Order Type is required when the Purchase Order number is generated.")
        if company and order_type:
            if order_type.company_id != company.pk and not order_type.companies.filter(pk=company.pk).exists():
                self.add_error("order_type", "Order Type must be available to the Purchase Order company.")
        if company and order_template and order_template.company_id != company.pk:
            self.add_error("order_template", "Order Template must belong to the Purchase Order company.")
        if company and milestone_template and milestone_template.company_id != company.pk:
            self.add_error("milestone_template", "Milestone Template must belong to the Purchase Order company.")
        if company and purchase_team and purchase_team.company_id != company.pk:
            self.add_error("purchase_team", "Purchase Team must belong to the Purchase Order company.")
        return cleaned_data

    def save(self, commit=True):
        old_control = None
        if self.instance.pk:
            old_control = PurchaseOrderControl.objects.filter(order_id=self.instance.pk).values(
                "order_template_id", "milestone_template_id"
            ).first()
        order_type = self.cleaned_data.get("order_type")
        if not self.instance.number and order_type:
            from modules.purchase.services import allocate_purchase_document_number

            self.instance.number = allocate_purchase_document_number(
                order_type.pk,
                self.cleaned_data["company"].pk,
                "purchase_order_draft",
            )
        saved = super().save(commit=commit)
        if not commit:
            return saved
        order_template = self.cleaned_data.get("order_template")
        milestone_template = self.cleaned_data.get("milestone_template")
        PurchaseOrderControl.objects.update_or_create(
            order=saved,
            defaults={
                "order_type": order_type,
                "order_template": order_template,
                "milestone_template": milestone_template,
                "team": self.cleaned_data.get("purchase_team"),
                "assigned_to": self.cleaned_data.get("assigned_to"),
            },
        )
        if order_template and (
            old_control is None or old_control["order_template_id"] != order_template.pk
        ):
            saved._apply_purchase_order_template_id = order_template.pk
        if milestone_template and (
            old_control is None or old_control["milestone_template_id"] != milestone_template.pk
        ):
            saved._apply_purchase_milestone_template_id = milestone_template.pk
        return saved


class InvoiceForm(StyledModelForm):
    class Meta:
        model = Invoice
        fields = ["company", "number", "invoice_type", "partner", "invoice_date", "due_date", "status", "paid_amount", "notes"]


class EmployeeForm(StyledModelForm):
    class Meta:
        model = Employee
        fields = [
            "company",
            "branch",
            "user",
            "supervisor",
            "employee_id",
            "name",
            "email",
            "phone",
            "gender",
            "commute_distance_km",
            "religion",
            "race",
            "marital_status",
            "nationality",
            "province",
            "domicile_code",
            "identification",
            "passport",
            "birth_date",
            "place_of_birth",
            "blood_type",
            "height_cm",
            "weight_kg",
            "visa_number",
            "work_permit_number",
            "visa_expiry_date",
            "department_record",
            "job_position",
            "work_location",
            "grade",
            "join_date",
        ]

    def clean(self):
        cleaned = super().clean()
        department = cleaned.get("department_record")
        job_position = cleaned.get("job_position")
        self.instance.department = department.name if department else ""
        self.instance.position = job_position.name if job_position else ""
        return cleaned


class AttendanceForm(StyledModelForm):
    class Meta:
        model = Attendance
        fields = ["employee", "attendance_date", "check_in", "check_out", "status", "note"]
        widgets = {
            "check_in": forms.TimeInput(attrs={"type": "time"}),
            "check_out": forms.TimeInput(attrs={"type": "time"}),
        }
