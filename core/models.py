from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models.functions import Coalesce
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Company(TimeStampedModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=160)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    currency = models.CharField(max_length=3, default="IDR")
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Companies"
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Contact(TimeStampedModel):
    class ContactType(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        VENDOR = "vendor", "Vendor"
        BOTH = "both", "Customer & Vendor"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="contacts")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=160)
    contact_type = models.CharField(max_length=16, choices=ContactType.choices, default=ContactType.CUSTOMER)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    tax_id = models.CharField(max_length=64, blank=True)
    address = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_contact_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Product(TimeStampedModel):
    class ProductType(models.TextChoices):
        STOCKABLE = "stockable", "Stockable Product"
        CONSUMABLE = "consumable", "Consumable"
        SERVICE = "service", "Service"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="products")
    sku = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    product_type = models.CharField(max_length=16, choices=ProductType.choices, default=ProductType.STOCKABLE)
    unit = models.CharField(max_length=24, default="Unit")
    sale_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    purchase_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    current_stock = models.DecimalField(max_digits=18, decimal_places=3, default=0, editable=False)
    reorder_level = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "sku"], name="unique_product_sku_per_company")]

    def __str__(self):
        return f"{self.sku} - {self.name}"


class Warehouse(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="warehouses")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    address = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_warehouse_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class StockMove(TimeStampedModel):
    class Direction(models.TextChoices):
        IN = "in", "Stock In"
        OUT = "out", "Stock Out"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="stock_moves")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="stock_moves")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="stock_moves")
    direction = models.CharField(max_length=3, choices=Direction.choices)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    reference = models.CharField(max_length=80, blank=True)
    movement_date = models.DateField(default=timezone.localdate)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="stock_moves")

    class Meta:
        ordering = ["-movement_date", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gt=0), name="stock_move_quantity_positive")]

    def clean(self):
        errors = {}
        if self.product_id and self.company_id and self.product.company_id != self.company_id:
            errors["product"] = "Product must belong to the selected company."
        if self.warehouse_id and self.company_id and self.warehouse.company_id != self.company_id:
            errors["warehouse"] = "Warehouse must belong to the selected company."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Posted stock movements are immutable. Create a reversing movement instead.")
        self.full_clean()
        with transaction.atomic():
            product = Product.objects.select_for_update().get(pk=self.product_id)
            delta = self.quantity if self.direction == self.Direction.IN else -self.quantity
            new_balance = product.current_stock + delta
            if new_balance < 0:
                raise ValidationError({"quantity": f"Insufficient stock. Available: {product.current_stock}."})
            super().save(*args, **kwargs)
            product.current_stock = new_balance
            product.save(update_fields=["current_stock", "updated_at"])

    def delete(self, *args, **kwargs):
        raise ValidationError("Posted stock movements cannot be deleted. Create a reversing movement instead.")

    def __str__(self):
        return f"{self.get_direction_display()} {self.quantity} {self.product.sku}"


class OrderBase(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    number = models.CharField(max_length=40)
    order_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    total = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    notes = models.TextField(blank=True)

    class Meta:
        abstract = True
        ordering = ["-order_date", "-id"]


class SaleOrder(OrderBase):
    customer = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="sale_orders")
    validity_date = models.DateField("Expiration Date", blank=True, null=True)
    customer_reference = models.CharField("Customer Reference", max_length=120, blank=True)
    salesperson_name = models.CharField("Salesperson", max_length=160, blank=True)
    price_list = models.CharField("Pricelist", max_length=120, default="IDR", blank=True)
    payment_terms = models.CharField("Payment Terms", max_length=120, blank=True)
    odoo_source_id = models.PositiveIntegerField(blank=True, null=True, unique=True, editable=False)

    class Meta(OrderBase.Meta):
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_sale_number_per_company")]

    def __str__(self):
        return self.number


class SaleOrderLine(TimeStampedModel):
    class DisplayType(models.TextChoices):
        SECTION = "line_section", "Section"
        NOTE = "line_note", "Note"

    class RentalStatus(models.TextChoices):
        RETURNED = "returned", "Sudah Kembali"
        AT_CUSTOMER = "at_customer", "Di Customer"

    order = models.ForeignKey(SaleOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True)
    display_type = models.CharField(max_length=20, choices=DisplayType.choices, blank=True, editable=False)
    description = models.CharField(max_length=200, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    discount_percent = models.DecimalField(
        "Discount (%)",
        max_digits=5,
        decimal_places=2,
        default=0,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    rental_start_date = models.DateField("Tanggal Sewa", blank=True, null=True)
    rental_return_date = models.DateField("Tanggal Kembali", blank=True, null=True)
    rental_status = models.CharField("Status Barang", max_length=20, choices=RentalStatus.choices, blank=True)
    odoo_source_id = models.PositiveIntegerField(blank=True, null=True, unique=True, editable=False)

    @property
    def subtotal(self):
        return self.quantity * self.unit_price * (Decimal("1") - self.discount_percent / Decimal("100"))

    def clean(self):
        super().clean()
        if not self.display_type and not self.product_id:
            raise ValidationError({"product": "Product wajib diisi untuk baris Sales Order."})
        if self.rental_start_date and self.rental_return_date and self.rental_return_date < self.rental_start_date:
            raise ValidationError({"rental_return_date": "Tanggal Kembali tidak boleh sebelum Tanggal Sewa."})

    def __str__(self):
        return f"{self.order.number} / {self.product.sku if self.product_id else self.get_display_type_display()}"


class PurchaseOrder(OrderBase):
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="purchase_orders")

    class Meta(OrderBase.Meta):
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_purchase_number_per_company")]

    def __str__(self):
        return self.number


class PurchaseOrderLine(TimeStampedModel):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    description = models.CharField(max_length=200, blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    @property
    def subtotal(self):
        return self.quantity * self.unit_price

    def __str__(self):
        return f"{self.order.number} / {self.product.sku}"


class Invoice(TimeStampedModel):
    class InvoiceType(models.TextChoices):
        CUSTOMER = "customer", "Customer Invoice"
        VENDOR = "vendor", "Vendor Bill"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="invoices")
    number = models.CharField(max_length=40)
    invoice_type = models.CharField(max_length=16, choices=InvoiceType.choices, default=InvoiceType.CUSTOMER)
    partner = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="invoices")
    invoice_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    total = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    paid_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-invoice_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_invoice_number_per_company")]

    @property
    def balance(self):
        return self.total - self.paid_amount

    def __str__(self):
        return self.number


class InvoiceLine(TimeStampedModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True)
    description = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    @property
    def subtotal(self):
        return self.quantity * self.unit_price

    def __str__(self):
        return f"{self.invoice.number} / {self.description}"


class Employee(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employees")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="employee_profiles",
    )
    supervisor = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="direct_reports",
    )
    employee_id = models.CharField(max_length=30)
    name = models.CharField(max_length=160)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    department = models.CharField(max_length=100, blank=True)
    position = models.CharField(max_length=100, blank=True)
    join_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "employee_id"], name="unique_employee_id_per_company")]

    def __str__(self):
        return f"{self.employee_id} - {self.name}"


class Attendance(TimeStampedModel):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        LEAVE = "leave", "Leave"
        SICK = "sick", "Sick"
        ABSENT = "absent", "Absent"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="attendances")
    attendance_date = models.DateField(default=timezone.localdate)
    check_in = models.TimeField(blank=True, null=True)
    check_out = models.TimeField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PRESENT)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-attendance_date", "employee__name"]
        constraints = [models.UniqueConstraint(fields=["employee", "attendance_date"], name="unique_attendance_per_day")]

    def __str__(self):
        return f"{self.employee.name} - {self.attendance_date}"


def recalculate_total(parent, relation_name="lines"):
    line_queryset = getattr(parent, relation_name)
    line_total = models.F("quantity") * models.F("unit_price")
    if "discount_percent" in {field.name for field in line_queryset.model._meta.concrete_fields}:
        line_total = line_total * (Decimal("100") - models.F("discount_percent")) / Decimal("100")
    line_total = models.ExpressionWrapper(line_total, output_field=models.DecimalField(max_digits=18, decimal_places=2))
    total = line_queryset.aggregate(
        total=Coalesce(
            models.Sum(line_total),
            Decimal("0"),
            output_field=models.DecimalField(max_digits=18, decimal_places=2),
        )
    )["total"]
    parent.__class__.objects.filter(pk=parent.pk).update(total=total)
