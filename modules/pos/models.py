from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import Company, Contact, Product, TimeStampedModel, Warehouse


class POSRegister(TimeStampedModel):
    class RegisterType(models.TextChoices):
        RETAIL = "pos_retail", "Retail"
        FNB = "pos_fnb", "Food & Beverage"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_registers")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="pos_registers")
    profile = models.ForeignKey(
        "POSProfile", on_delete=models.SET_NULL, blank=True, null=True, related_name="registers"
    )
    register_type = models.CharField(max_length=16, choices=RegisterType.choices, default=RegisterType.RETAIL)
    branch = models.CharField(max_length=120, blank=True)
    device_identifier = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_pos_register_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def clean(self):
        if self.warehouse_id and self.company_id and self.warehouse.company_id != self.company_id:
            raise ValidationError({"warehouse": "Warehouse must belong to the selected company."})
        if self.profile_id and self.company_id and self.profile.company_id != self.company_id:
            raise ValidationError({"profile": "POS profile must belong to the selected company."})


class POSSession(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    register = models.ForeignKey(POSRegister, on_delete=models.PROTECT, related_name="sessions")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="opened_pos_sessions")
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="closed_pos_sessions",
    )
    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(blank=True, null=True)
    opening_cash = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    closing_cash = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    expected_closing_cash = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cash_sales_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cash_in_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cash_out_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    variance_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    variance_reason = models.TextField(blank=True)
    closing_summary = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["-opened_at"]

    def __str__(self):
        return f"{self.register.code} · {self.opened_at:%Y-%m-%d %H:%M}"


class RestaurantTable(TimeStampedModel):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        OCCUPIED = "occupied", "Occupied"
        RESERVED = "reserved", "Reserved"
        CLEANING = "cleaning", "Cleaning"

    register = models.ForeignKey(POSRegister, on_delete=models.PROTECT, related_name="restaurant_tables")
    name = models.CharField(max_length=60)
    area = models.CharField(max_length=80, blank=True)
    pos_area = models.ForeignKey(
        "POSArea", on_delete=models.SET_NULL, blank=True, null=True, related_name="tables"
    )
    sync_uuid = models.CharField(max_length=64, blank=True, null=True, unique=True)
    seats = models.PositiveSmallIntegerField(default=2)
    shape = models.CharField(max_length=16, choices=(("square", "Square"), ("round", "Round")), default="square")
    x_position = models.PositiveIntegerField(default=0)
    y_position = models.PositiveIntegerField(default=0)
    width = models.DecimalField(max_digits=8, decimal_places=2, default=100)
    height = models.DecimalField(max_digits=8, decimal_places=2, default=100)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.AVAILABLE)

    class Meta:
        ordering = ["area", "name"]
        constraints = [models.UniqueConstraint(fields=["register", "name"], name="unique_restaurant_table_per_register")]

    def __str__(self):
        return self.name


class POSOrder(TimeStampedModel):
    class OrderType(models.TextChoices):
        RETAIL = "retail", "Retail"
        DINE_IN = "dine_in", "Dine In"
        TAKEAWAY = "takeaway", "Takeaway"
        DELIVERY = "delivery", "Delivery"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"
        REFUNDED = "refunded", "Refunded"

    PREORDER_STATUS_CHOICES = (
        ("ordered", "Ordered"),
        ("ready", "Ready"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    )
    RETURN_TYPE_CHOICES = (("void", "Void"), ("return", "Return"), ("exchange", "Exchange"))
    RETURN_STATE_CHOICES = (
        ("partially_exchanged", "Partially Exchanged"),
        ("partially_returned", "Partially Returned"),
        ("voided", "Voided"),
        ("returned", "Returned"),
        ("exchanged", "Exchanged"),
    )

    session = models.ForeignKey(POSSession, on_delete=models.PROTECT, related_name="orders")
    profile = models.ForeignKey(
        "POSProfile", on_delete=models.SET_NULL, blank=True, null=True, related_name="orders"
    )
    device = models.ForeignKey(
        "POSDevice", on_delete=models.SET_NULL, blank=True, null=True, related_name="orders"
    )
    number = models.CharField(max_length=40, unique=True)
    customer = models.ForeignKey(Contact, on_delete=models.SET_NULL, blank=True, null=True, related_name="pos_orders")
    order_type = models.CharField(max_length=16, choices=OrderType.choices, default=OrderType.RETAIL)
    restaurant_table = models.ForeignKey(RestaurantTable, on_delete=models.SET_NULL, blank=True, null=True, related_name="orders")
    queue_number = models.CharField(max_length=32, blank=True)
    guest_display_name = models.CharField(max_length=160, blank=True)
    pax = models.PositiveIntegerField(default=1)
    fnb_state = models.CharField(
        max_length=20,
        choices=(("draft", "Draft"), ("sent", "Sent to Kitchen"), ("preparing", "Preparing"), ("ready", "Ready"), ("served", "Served")),
        default="draft",
    )
    is_preorder = models.BooleanField(default=False)
    fulfillment_date = models.DateField(blank=True, null=True)
    preorder_status = models.CharField(
        max_length=20, choices=PREORDER_STATUS_CHOICES, default="ordered", blank=True
    )
    ready_date = models.DateTimeField(blank=True, null=True)
    return_type = models.CharField(max_length=20, choices=RETURN_TYPE_CHOICES, blank=True)
    return_state = models.CharField(max_length=20, choices=RETURN_STATE_CHOICES, blank=True)
    source_order = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="return_orders",
    )
    subtotal_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    discount_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    service_charge_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    takeaway_fee_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    paid_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number

    def clean(self):
        if self.order_type == self.OrderType.DINE_IN and not self.restaurant_table_id:
            raise ValidationError({"restaurant_table": "A table is required for dine-in orders."})
        if self.restaurant_table_id and self.restaurant_table.register_id != self.session.register_id:
            raise ValidationError({"restaurant_table": "Table must belong to the session register."})
        if self.is_preorder and not self.fulfillment_date:
            raise ValidationError({"fulfillment_date": "Fulfillment date is required for a pre-order."})
        if self.return_type and not self.source_order_id:
            raise ValidationError({"source_order": "Source order is required when return type is set."})
        if self.source_order_id and not self.return_type:
            raise ValidationError({"return_type": "Return type is required when source order is set."})
        if self.pk and self.source_order_id == self.pk:
            raise ValidationError({"source_order": "Source order cannot reference the same order."})


class POSOrderLine(TimeStampedModel):
    order = models.ForeignKey(POSOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_order_lines")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    fnb_state = models.CharField(
        max_length=20,
        choices=(("draft", "Draft"), ("queued", "Queued"), ("preparing", "Preparing"), ("ready", "Ready"), ("served", "Served"), ("cancelled", "Cancelled")),
        default="draft",
    )
    cancel_reason = models.TextField(blank=True)
    kitchen_station = models.CharField(max_length=80, blank=True)
    combo_snapshot = models.JSONField(default=dict, blank=True)
    notes = models.CharField(max_length=200, blank=True)

    @property
    def subtotal(self):
        gross = self.quantity * self.unit_price
        percentage_discount = gross * self.discount_percent / Decimal("100")
        return gross - percentage_discount - self.discount_amount + self.tax_amount

    def __str__(self):
        return f"{self.order.number} · {self.product.sku}"


class KitchenTicket(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PREPARING = "preparing", "Preparing"
        READY = "ready", "Ready"
        SERVED = "served", "Served"
        CANCELLED = "cancelled", "Cancelled"

    order = models.ForeignKey(POSOrder, on_delete=models.CASCADE, related_name="kitchen_tickets")
    reference = models.CharField(max_length=64, blank=True)
    device = models.ForeignKey(
        "POSDevice", on_delete=models.SET_NULL, blank=True, null=True, related_name="kitchen_tickets"
    )
    station = models.CharField(max_length=80, default="Main Kitchen")
    sequence = models.PositiveIntegerField(default=10)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    sent_at = models.DateTimeField(default=timezone.now)
    firing_started_at = models.DateTimeField(blank=True, null=True)
    ready_at = models.DateTimeField(blank=True, null=True)
    served_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["status", "created_at"]

    def __str__(self):
        return f"{self.order.number} · {self.station}"


# Importing the extended retail/F&B model catalog registers it under this
# application while keeping the original compact POS models import-compatible.
from .retail_models import *  # noqa: E402,F403
