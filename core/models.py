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
    branch = models.ForeignKey(
        "organization.Branch",
        on_delete=models.PROTECT,
        related_name="warehouses",
        blank=True,
        null=True,
    )
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    address = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_warehouse_code_per_company")]

    def clean(self):
        super().clean()
        if self.branch_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": "Warehouse branch must belong to the selected company."})

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


class CompanySaleSetting(TimeStampedModel):
    class RevisionSequenceRule(models.TextChoices):
        APPEND_SUFFIX = "append_suffix", "Append Revision Suffix"
        NEW_SEQUENCE = "new_sequence", "Follow Sequence Master Data"

    company = models.OneToOneField(
        Company,
        on_delete=models.PROTECT,
        related_name="sale_setting",
    )
    quotation_expiry_days = models.PositiveIntegerField(
        "Quotation Expiry Period",
        default=7,
        validators=[MinValueValidator(1)],
        help_text="Default number of days before a new Sales quotation expires.",
    )
    default_delivery_date = models.PositiveIntegerField(
        "Default Delivery Date",
        default=0,
        help_text="Number of days added to the order date for new Sales Orders.",
    )
    allow_order_revision = models.BooleanField(default=True)
    revised_sequence_rule = models.CharField(
        max_length=50,
        choices=RevisionSequenceRule.choices,
        default=RevisionSequenceRule.APPEND_SUFFIX,
    )

    class Meta:
        ordering = ["company__name"]

    def __str__(self):
        return f"Sales Settings - {self.company}"


class SaleOrderType(TimeStampedModel):
    class Origin(models.TextChoices):
        SALES = "sale", "Sales"
        POS = "pos", "POS"
        MARKETPLACE = "mp", "Marketplace"

    name = models.CharField("Order Type Name", max_length=255)
    companies = models.ManyToManyField(
        Company,
        related_name="sale_order_types",
        blank=True,
        help_text="Leave empty to make this Order Type global.",
    )
    description = models.TextField(blank=True)
    prefix_sequence = models.CharField(max_length=10, unique=True, editable=False)
    origin = models.CharField(max_length=10, choices=Origin.choices, default=Origin.SALES)

    class Meta:
        ordering = ["name", "pk"]

    def _generate_prefix(self):
        words = [word for word in (self.name or "").split() if word]
        base = ("".join(word[0] for word in words).upper() or "SOT")[:10]
        candidates = [base]
        for index in range(2, 1000):
            suffix = str(index)
            candidates.append(f"{base[: 10 - len(suffix)]}{suffix}")
        existing = set(
            SaleOrderType.objects.exclude(pk=self.pk)
            .exclude(prefix_sequence="")
            .filter(prefix_sequence__in=candidates)
            .values_list("prefix_sequence", flat=True)[:999]
        )
        for candidate in candidates:
            if candidate not in existing:
                return candidate
        raise ValidationError("Could not allocate a unique Sales Order Type prefix.")

    def save(self, *args, **kwargs):
        if not self.prefix_sequence or (
            self.pk
            and SaleOrderType.objects.filter(pk=self.pk)
            .exclude(name=self.name)
            .exists()
        ):
            self.prefix_sequence = self._generate_prefix()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.sale_orders.exists():
            raise ValidationError("Record can't be deleted, as it is already used in Sale Order.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return self.name


class SaleOrderTypeSequence(TimeStampedModel):
    order_type = models.ForeignKey(
        SaleOrderType,
        on_delete=models.CASCADE,
        related_name="sequence_lines",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="sale_order_type_sequences",
        blank=True,
        null=True,
    )
    quotation_period = models.CharField(max_length=7, blank=True, editable=False)
    quotation_next = models.PositiveIntegerField(default=1, editable=False)
    order_period = models.CharField(max_length=7, blank=True, editable=False)
    order_next = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["order_type__name", "company__name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["order_type", "company"],
                condition=models.Q(company__isnull=False),
                name="unique_sale_type_company_sequence",
            ),
            models.UniqueConstraint(
                fields=["order_type"],
                condition=models.Q(company__isnull=True),
                name="unique_global_sale_type_sequence",
            ),
        ]

    def __str__(self):
        return f"{self.order_type} / {self.company or 'Global'}"


class SaleOrder(OrderBase):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        TO_APPROVE = "to_approve", "Waiting for Approval"
        SENT = "sent", "Sent"
        SALE = "sale", "Sale Order"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"
        REJECTED = "rejected", "Rejected"
        REVISED = "revised", "Revised"
        EXPIRED = "expired", "Expired"

    class InvoicingPolicy(models.TextChoices):
        ORDERED = "ordered", "Ordered Quantities"
        DELIVERED = "delivered", "Delivered Quantities"

    class CloseReason(models.TextChoices):
        COMPLETED = "completed", "Completed in full"
        CUSTOMER_CANCELLED = "customer_cancelled", "Cancelled by customer"
        DUPLICATE = "duplicate", "Duplicate / incorrect entry"
        NOT_BILLED = "not_billed", "Not billed (sample / internal)"
        PARTIAL_RELEASE = "partial_release", "Partially closed, remainder released"
        LEGACY_MIGRATION = "legacy_migration", "Legacy data migration"

    number = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    order_type = models.ForeignKey(
        SaleOrderType,
        on_delete=models.PROTECT,
        related_name="sale_orders",
        blank=True,
        null=True,
    )
    customer = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="sale_orders")
    validity_date = models.DateField("Expiration Date", blank=True, null=True)
    customer_reference = models.CharField("Customer Reference", max_length=120, blank=True)
    salesperson_name = models.CharField("Salesperson", max_length=160, blank=True)
    price_list = models.CharField("Pricelist", max_length=120, default="IDR", blank=True)
    payment_terms = models.CharField("Payment Terms", max_length=120, blank=True)
    currency = models.CharField(max_length=3, default="IDR")
    delivery_address = models.TextField(blank=True)
    invoice_address = models.TextField(blank=True)
    delivery_date = models.DateField(blank=True, null=True)
    invoicing_policy = models.CharField(
        max_length=16,
        choices=InvoicingPolicy.choices,
        default=InvoicingPolicy.ORDERED,
    )
    global_discount_percent = models.DecimalField(
        "Global Discount (%)",
        max_digits=5,
        decimal_places=2,
        default=0,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    subtotal_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    discount_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    untaxed_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    terms_conditions = models.TextField("Terms & Conditions", blank=True)
    quotation_reference = models.CharField(max_length=40, blank=True, editable=False)
    confirmation_date = models.DateTimeField(blank=True, null=True, editable=False)
    cancel_reason = models.TextField(blank=True, editable=False)
    reject_reason = models.TextField(blank=True, editable=False)
    close_reason = models.CharField(
        max_length=32,
        choices=CloseReason.choices,
        blank=True,
        editable=False,
    )
    close_note = models.TextField(blank=True, editable=False)
    revision_root = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="revision_documents",
        blank=True,
        null=True,
        editable=False,
    )
    previous_revision = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        related_name="revised_document",
        blank=True,
        null=True,
        editable=False,
    )
    revision_number = models.PositiveIntegerField(default=0, editable=False)
    odoo_source_id = models.PositiveIntegerField(blank=True, null=True, unique=True, editable=False)

    class Meta(OrderBase.Meta):
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_sale_number_per_company")]

    def __str__(self):
        return self.number

    def clean(self):
        super().clean()
        errors = {}
        if self.customer_id and self.company_id and self.customer.company_id != self.company_id:
            errors["customer"] = "Customer must belong to the sales order company."
        if self.order_type_id and self.company_id:
            scoped_companies = list(
                self.order_type.companies.order_by().values_list("pk", flat=True)[:2]
            )
            if scoped_companies and self.company_id not in scoped_companies:
                errors["order_type"] = "Order Type must be global or available to the Sales Order company."
        if self.validity_date and self.validity_date < self.order_date:
            errors["validity_date"] = "Expiration date cannot be before the order date."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self._state.adding:
            from .sales import allocate_sale_document_number, get_default_sale_order_type

            with transaction.atomic():
                if not self.order_type_id:
                    self.order_type = get_default_sale_order_type(self.company_id)
                if not self.order_type_id:
                    raise ValidationError({"order_type": "Order Type is required."})
                if not self.number:
                    self.number = allocate_sale_document_number(
                        self.order_type_id,
                        self.company_id,
                        document_kind="quotation",
                    )
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ValidationError(
                "This Sales Order cannot be deleted because it is no longer in Draft status."
            )
        return super().delete(*args, **kwargs)


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
    uom = models.CharField("Unit of Measure", max_length=24, blank=True)
    delivery_address = models.CharField(max_length=255, blank=True)
    delivery_date = models.DateField(blank=True, null=True)
    delivered_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        editable=False,
        validators=[MinValueValidator(0)],
    )
    invoiced_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        editable=False,
        validators=[MinValueValidator(0)],
    )
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    discount_percent = models.DecimalField(
        "Discount (%)",
        max_digits=5,
        decimal_places=2,
        default=0,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    tax_percent = models.DecimalField(
        "Tax (%)",
        max_digits=7,
        decimal_places=4,
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

    @property
    def gross_amount(self):
        return self.quantity * self.unit_price

    @property
    def line_discount_amount(self):
        return self.gross_amount - self.subtotal

    @property
    def untaxed_amount(self):
        return self.subtotal

    @property
    def tax_amount(self):
        return self.untaxed_amount * self.tax_percent / Decimal("100")

    @property
    def total_amount(self):
        return self.untaxed_amount + self.tax_amount

    def clean(self):
        super().clean()
        if not self.display_type and not self.product_id:
            raise ValidationError({"product": "Product wajib diisi untuk baris Sales Order."})
        if self.product_id and self.order_id and self.product.company_id != self.order.company_id:
            raise ValidationError({"product": "Product must belong to the sales order company."})
        if not self.display_type and self.quantity <= 0:
            raise ValidationError({"quantity": "Quantity must be greater than zero."})
        if self.delivered_quantity > self.quantity:
            raise ValidationError({"delivered_quantity": "Delivered quantity cannot exceed ordered quantity."})
        if self.invoiced_quantity > self.quantity:
            raise ValidationError({"invoiced_quantity": "Invoiced quantity cannot exceed ordered quantity."})
        if self.rental_start_date and self.rental_return_date and self.rental_return_date < self.rental_start_date:
            raise ValidationError({"rental_return_date": "Tanggal Kembali tidak boleh sebelum Tanggal Sewa."})

    def __str__(self):
        return f"{self.order.number} / {self.product.sku if self.product_id else self.get_display_type_display()}"


class SaleQuotationEmail(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    order = models.ForeignKey(
        SaleOrder,
        on_delete=models.PROTECT,
        related_name="email_attempts",
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sent_sales_quotations",
    )
    email_to = models.CharField(max_length=512)
    email_cc = models.CharField(max_length=512, blank=True)
    subject = models.CharField(max_length=255)
    body_html = models.TextField(blank=True)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    failure_reason = models.TextField(blank=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["order", "status", "-created_at"], name="sale_email_order_status_idx")]

    def __str__(self):
        return f"{self.order.number} · {self.subject}"


class PurchaseOrder(OrderBase):
    class InvoicingPolicy(models.TextChoices):
        ORDERED = "on_ordered", "Ordered quantities"
        RECEIVED = "on_delivered", "Received quantities"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        WAITING_APPROVAL = "waiting_approval", "Waiting Approval"
        REJECTED = "rejected", "Rejected"
        CONFIRMED = "confirmed", "Approved"
        COMPLETED = "completed", "Completed"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"
        REVISED = "revised", "Revised"
        EXPIRED = "expired", "Expired"

    number = models.CharField(max_length=40, blank=True)
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="purchase_orders")
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="purchase_orders",
        blank=True,
        null=True,
        help_text="Default destination warehouse for stockable order lines.",
    )
    invoicing_policy = models.CharField(
        max_length=16,
        choices=InvoicingPolicy.choices,
        default=InvoicingPolicy.ORDERED,
    )
    received_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        editable=False,
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    class Meta(OrderBase.Meta):
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_purchase_number_per_company")]

    def __str__(self):
        return self.number

    def clean(self):
        super().clean()
        errors = {}
        if self.vendor_id and self.company_id and self.vendor.company_id != self.company_id:
            errors["vendor"] = "Vendor must belong to the purchase order company."
        if self.warehouse_id and self.company_id and self.warehouse.company_id != self.company_id:
            errors["warehouse"] = "Destination warehouse must belong to the purchase order company."
        if errors:
            raise ValidationError(errors)


class PurchaseOrderLine(TimeStampedModel):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    description = models.CharField(max_length=200, blank=True)
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="purchase_order_lines",
        blank=True,
        null=True,
        help_text="Overrides the purchase order destination warehouse for this line.",
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    received_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0, editable=False)
    billed_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0, editable=False)

    @property
    def subtotal(self):
        return self.quantity * self.unit_price

    @property
    def remaining_to_receive(self):
        return max(self.quantity - self.received_quantity, Decimal("0"))

    @property
    def remaining_to_bill(self):
        basis = (
            self.received_quantity
            if self.order.invoicing_policy == PurchaseOrder.InvoicingPolicy.RECEIVED
            and self.product.product_type == Product.ProductType.STOCKABLE
            else self.quantity
        )
        return max(basis - self.billed_quantity, Decimal("0"))

    def clean(self):
        super().clean()
        errors = {}
        if self.quantity <= 0:
            errors["quantity"] = "Quantity must be greater than zero."
        if self.unit_price < 0:
            errors["unit_price"] = "Unit price cannot be negative."
        if self.product_id and self.order_id and self.product.company_id != self.order.company_id:
            errors["product"] = "Product must belong to the purchase order company."
        if self.warehouse_id and self.order_id and self.warehouse.company_id != self.order.company_id:
            errors["warehouse"] = "Line warehouse must belong to the purchase order company."
        if self.received_quantity < 0 or self.received_quantity > self.quantity:
            errors["received_quantity"] = "Received quantity must be between zero and the ordered quantity."
        if self.billed_quantity < 0:
            errors["billed_quantity"] = "Billed quantity cannot be negative."
        if errors:
            raise ValidationError(errors)

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

    class PaymentState(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PARTIAL = "partial", "Partial"
        PAID = "paid", "Paid"

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

    @property
    def payment_state(self):
        if self.total > 0 and self.paid_amount >= self.total:
            return self.PaymentState.PAID
        if self.paid_amount > 0:
            return self.PaymentState.PARTIAL
        return self.PaymentState.UNPAID

    def get_payment_state_display(self):
        return self.PaymentState(self.payment_state).label

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

    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        OTHER = "other", "Other"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employees")
    branch = models.ForeignKey(
        "organization.Branch",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="employees",
    )
    department_record = models.ForeignKey(
        "gsshop_hr.Department",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="employees",
    )
    job_position = models.ForeignKey(
        "gsshop_hr.JobPosition",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="employees",
    )
    work_location = models.ForeignKey(
        "gsshop_hr.WorkLocation",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="employees",
    )
    grade = models.ForeignKey(
        "gsshop_hr.EmployeeGrade",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="employees",
    )
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
    gender = models.CharField(max_length=16, choices=Gender.choices, blank=True)
    commute_distance_km = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)
    religion = models.CharField(max_length=80, blank=True)
    race = models.CharField(max_length=80, blank=True)
    marital_status = models.CharField(max_length=40, blank=True)
    nationality = models.CharField(max_length=80, blank=True)
    province = models.CharField(max_length=100, blank=True)
    domicile_code = models.CharField(max_length=20, blank=True)
    identification = models.CharField(max_length=100, blank=True)
    passport = models.CharField(max_length=100, blank=True)
    birth_date = models.DateField(blank=True, null=True)
    place_of_birth = models.CharField(max_length=160, blank=True)
    blood_type = models.CharField(max_length=3, blank=True)
    height_cm = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    weight_kg = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    visa_number = models.CharField(max_length=120, blank=True)
    work_permit_number = models.CharField(max_length=120, blank=True)
    visa_expiry_date = models.DateField(blank=True, null=True)
    department = models.CharField(max_length=100, blank=True)
    position = models.CharField(max_length=100, blank=True)
    join_date = models.DateField(default=timezone.localdate)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        editable=False,
    )
    inactive_date = models.DateField(blank=True, null=True, editable=False)
    inactive_reason = models.TextField(blank=True, editable=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "employee_id"], name="unique_employee_id_per_company"),
            models.UniqueConstraint(
                fields=["company", "identification"],
                condition=~models.Q(identification=""),
                name="unique_employee_identification_per_company",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.supervisor_id:
            if self.pk and self.supervisor_id == self.pk:
                errors["supervisor"] = "An employee cannot supervise themselves."
            elif self.company_id and self.supervisor.company_id != self.company_id:
                errors["supervisor"] = "Supervisor must belong to the employee company."
        for field_name, label in (
            ("branch", "Branch"),
            ("department_record", "Department"),
            ("job_position", "Job position"),
            ("work_location", "Work location"),
            ("grade", "Employee grade"),
        ):
            related_id = getattr(self, f"{field_name}_id")
            if not related_id:
                continue
            related = getattr(self, field_name)
            if related.company_id != self.company_id:
                errors[field_name] = f"{label} must belong to the employee company."
            elif hasattr(related, "active") and not related.active:
                errors[field_name] = f"Select an active {label.lower()}."
        if self.job_position_id and self.department_record_id:
            position_department_id = self.job_position.department_id
            if position_department_id and position_department_id != self.department_record_id:
                errors["job_position"] = "Job position must belong to the selected department."
        if self.status == self.Status.ACTIVE and self.inactive_date:
            errors["inactive_date"] = "An active employee cannot have an inactive date."
        if self.birth_date and self.birth_date > timezone.localdate():
            errors["birth_date"] = "Birth date cannot be in the future."
        for field_name in ("commute_distance_km", "height_cm", "weight_kg"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                errors[field_name] = "Enter a value greater than or equal to zero."
        if errors:
            raise ValidationError(errors)

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
    check_in_at = models.DateTimeField(blank=True, null=True)
    check_out_at = models.DateTimeField(blank=True, null=True)
    from_raw_attendance = models.BooleanField(default=False)
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


def recalculate_sale_order_totals(order):
    """Recalculate commercial totals with one aggregate and one bounded update."""
    gross = models.ExpressionWrapper(
        models.F("quantity") * models.F("unit_price"),
        output_field=models.DecimalField(max_digits=30, decimal_places=8),
    )
    untaxed = models.ExpressionWrapper(
        gross * (Decimal("100") - models.F("discount_percent")) / Decimal("100"),
        output_field=models.DecimalField(max_digits=30, decimal_places=8),
    )
    tax = models.ExpressionWrapper(
        untaxed * models.F("tax_percent") / Decimal("100"),
        output_field=models.DecimalField(max_digits=30, decimal_places=8),
    )
    zero = Decimal("0")
    amount_field = models.DecimalField(max_digits=30, decimal_places=8)
    summary = order.lines.filter(display_type="").aggregate(
        gross=Coalesce(models.Sum(gross), zero, output_field=amount_field),
        line_untaxed=Coalesce(models.Sum(untaxed), zero, output_field=amount_field),
        line_tax=Coalesce(models.Sum(tax), zero, output_field=amount_field),
    )
    factor = Decimal("1") - order.global_discount_percent / Decimal("100")
    subtotal_amount = summary["gross"].quantize(Decimal("0.01"))
    untaxed_amount = (summary["line_untaxed"] * factor).quantize(Decimal("0.01"))
    tax_amount = (summary["line_tax"] * factor).quantize(Decimal("0.01"))
    discount_amount = (subtotal_amount - untaxed_amount).quantize(Decimal("0.01"))
    total = (untaxed_amount + tax_amount).quantize(Decimal("0.01"))
    order.__class__.objects.filter(pk=order.pk).update(
        subtotal_amount=subtotal_amount,
        discount_amount=discount_amount,
        untaxed_amount=untaxed_amount,
        tax_amount=tax_amount,
        total=total,
    )
