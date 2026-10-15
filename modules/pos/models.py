from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Company, Contact, Product, TimeStampedModel, Warehouse


class POSRegister(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="pos_registers")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="pos_registers")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_pos_register_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class POSSession(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    register = models.ForeignKey(POSRegister, on_delete=models.PROTECT, related_name="sessions")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="opened_pos_sessions")
    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(blank=True, null=True)
    opening_cash = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    closing_cash = models.DecimalField(max_digits=18, decimal_places=2, default=0)
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
    seats = models.PositiveSmallIntegerField(default=2)
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

    session = models.ForeignKey(POSSession, on_delete=models.PROTECT, related_name="orders")
    number = models.CharField(max_length=40, unique=True)
    customer = models.ForeignKey(Contact, on_delete=models.SET_NULL, blank=True, null=True, related_name="pos_orders")
    order_type = models.CharField(max_length=16, choices=OrderType.choices, default=OrderType.RETAIL)
    restaurant_table = models.ForeignKey(RestaurantTable, on_delete=models.SET_NULL, blank=True, null=True, related_name="orders")
    total = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    paid_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number


class POSOrderLine(TimeStampedModel):
    order = models.ForeignKey(POSOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_order_lines")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    notes = models.CharField(max_length=200, blank=True)

    @property
    def subtotal(self):
        return self.quantity * self.unit_price

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
    station = models.CharField(max_length=80, default="Main Kitchen")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["status", "created_at"]

    def __str__(self):
        return f"{self.order.number} · {self.station}"

