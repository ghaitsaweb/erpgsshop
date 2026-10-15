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
        return cleaned_data


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
        fields = ["company", "code", "name", "address", "active"]


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
        fields = ["company", "user", "supervisor", "employee_id", "name", "email", "phone", "department", "position", "join_date", "status"]


class AttendanceForm(StyledModelForm):
    class Meta:
        model = Attendance
        fields = ["employee", "attendance_date", "check_in", "check_out", "status", "note"]
        widgets = {
            "check_in": forms.TimeInput(attrs={"type": "time"}),
            "check_out": forms.TimeInput(attrs={"type": "time"}),
        }
