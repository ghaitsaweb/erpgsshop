from decimal import Decimal

from django import forms
from django.urls import reverse

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
    StockMove,
    Warehouse,
)


class SearchableMasterWidget(forms.HiddenInput):
    """Keep a master-record id hidden while exposing its searchable control."""

    @property
    def is_hidden(self):
        return False


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
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
        if "discount_percent" in self.fields and cleaned_data.get("discount_percent") in (None, ""):
            cleaned_data["discount_percent"] = Decimal("0")
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
    class Meta:
        model = Company
        fields = ["code", "name", "email", "phone", "address", "currency", "active"]


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
            "number",
            "customer",
            "validity_date",
            "order_date",
            "price_list",
            "payment_terms",
            "salesperson_name",
            "customer_reference",
            "notes",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        customer_field = self.fields["customer"]
        customer_field.widget.attrs.update(
            {
                "data-master-search-url": reverse("customer_search"),
                "data-master-search-placeholder": "Cari kode atau nama customer...",
            }
        )
        if self.instance and self.instance.customer_id:
            customer = self.instance.customer
            customer_field.widget.attrs["data-initial-label"] = f"{customer.code} — {customer.name}"


class SaleOrderLineForm(StyledModelForm):
    product = forms.ModelChoiceField(
        label="Produk",
        queryset=Product.objects.all(),
        required=False,
        widget=SearchableMasterWidget(),
    )

    class Meta:
        model = SaleOrderLine
        fields = [
            "product",
            "description",
            "quantity",
            "unit_price",
            "discount_percent",
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


class PurchaseOrderForm(StyledModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ["company", "number", "vendor", "order_date", "status", "notes"]


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
