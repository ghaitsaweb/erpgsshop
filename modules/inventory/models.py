from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import Company, Contact, Product, StockMove, TimeStampedModel, Warehouse
from modules.organization.models import Branch


ZERO = Decimal("0")
MAX_LOCATION_DEPTH = 32


class InventoryLocation(TimeStampedModel):
    """Warehouse location/bin adapted from HMX ``core_inventory.stocklocation``."""

    class Usage(models.TextChoices):
        INTERNAL = "internal", "Internal"
        VIEW = "view", "View"
        VIRTUAL = "virtual", "Virtual"

    class RemovalStrategy(models.TextChoices):
        FIFO = "fifo", "FIFO"
        FEFO = "fefo", "FEFO"

    class CapacityStatus(models.TextChoices):
        NOT_CONFIGURED = "not_configured", "Not Configured"
        EMPTY = "empty", "Empty"
        AVAILABLE = "available", "Available"
        FULL = "full", "Full"
        OVER_CAPACITY = "over_capacity", "Over Capacity"
        INCOMPLETE = "incomplete", "Incomplete"

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="inventory_locations",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="inventory_locations",
        blank=True,
        null=True,
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="inventory_locations",
        blank=True,
        null=True,
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        blank=True,
        null=True,
    )
    name = models.CharField(max_length=100)
    complete_name = models.CharField(max_length=255, editable=False)
    ancestor_ids = models.JSONField(default=list, blank=True, editable=False)
    usage = models.CharField(max_length=16, choices=Usage.choices, default=Usage.INTERNAL)
    barcode = models.CharField(max_length=100, unique=True, blank=True, null=True)
    capacity_length = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        blank=True,
        null=True,
    )
    capacity_width = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        blank=True,
        null=True,
    )
    capacity_height = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        blank=True,
        null=True,
    )
    dimension_unit = models.CharField(max_length=12, default="m")
    capacity_volume = models.DecimalField(
        max_digits=24,
        decimal_places=12,
        default=0,
        editable=False,
    )
    occupied_volume = models.DecimalField(
        max_digits=24,
        decimal_places=12,
        default=0,
        editable=False,
    )
    available_volume = models.DecimalField(
        max_digits=24,
        decimal_places=12,
        default=0,
        editable=False,
    )
    fulfilment_percent = models.DecimalField(
        max_digits=16,
        decimal_places=2,
        default=0,
        editable=False,
    )
    capacity_status = models.CharField(
        max_length=20,
        choices=CapacityStatus.choices,
        default=CapacityStatus.NOT_CONFIGURED,
        editable=False,
    )
    removal_strategy = models.CharField(
        max_length=8,
        choices=RemovalStrategy.choices,
        default=RemovalStrategy.FIFO,
    )
    is_expired_location = models.BooleanField(default=False)
    is_packing_zone = models.BooleanField(default=False)
    is_delivery_zone = models.BooleanField(default=False)
    sequence = models.PositiveIntegerField(default=0, db_index=True)
    active = models.BooleanField(default=True)

    organization_branch_lookup = "branch"

    class Meta:
        ordering = ["warehouse__name", "sequence", "complete_name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["warehouse", "parent", "name"],
                name="unique_inventory_location_in_parent",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=models.Q(capacity_length__gte=0)
                | models.Q(capacity_length__isnull=True),
                name="inventory_location_length_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity_width__gte=0)
                | models.Q(capacity_width__isnull=True),
                name="inventory_location_width_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(capacity_height__gte=0)
                | models.Q(capacity_height__isnull=True),
                name="inventory_location_height_nonnegative",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "branch", "warehouse", "sequence"],
                name="inventory_loc_scope_idx",
            ),
        ]

    def _capacity_values(self):
        return (self.capacity_length, self.capacity_width, self.capacity_height)

    def _refresh_capacity_fields(self):
        dimensions = self._capacity_values()
        configured = [value is not None for value in dimensions]
        if any(configured) and not all(configured):
            self.capacity_volume = ZERO
            self.available_volume = ZERO
            self.fulfilment_percent = ZERO
            self.capacity_status = self.CapacityStatus.INCOMPLETE
            return
        if not all(configured) or any(value <= 0 for value in dimensions):
            self.capacity_volume = ZERO
            self.available_volume = ZERO
            self.fulfilment_percent = ZERO
            self.capacity_status = self.CapacityStatus.NOT_CONFIGURED
            return
        self.capacity_volume = dimensions[0] * dimensions[1] * dimensions[2]
        self.available_volume = max(self.capacity_volume - self.occupied_volume, ZERO)
        self.fulfilment_percent = (
            self.occupied_volume * Decimal("100") / self.capacity_volume
        ).quantize(Decimal("0.01"))
        if self.occupied_volume <= 0:
            self.capacity_status = self.CapacityStatus.EMPTY
        elif self.occupied_volume > self.capacity_volume:
            self.capacity_status = self.CapacityStatus.OVER_CAPACITY
        elif self.occupied_volume == self.capacity_volume:
            self.capacity_status = self.CapacityStatus.FULL
        else:
            self.capacity_status = self.CapacityStatus.AVAILABLE

    def clean(self):
        super().clean()
        errors = {}
        if self.warehouse_id:
            if self.company_id != self.warehouse.company_id:
                errors["company"] = "Location company must match the selected warehouse company."
            if self.branch_id != self.warehouse.branch_id:
                errors["branch"] = "Location branch must match the selected warehouse branch."
        elif self.usage != self.Usage.VIRTUAL:
            errors["warehouse"] = "A non-virtual location must belong to a warehouse."
        elif self.branch_id:
            errors["branch"] = "A warehouse-less virtual location cannot belong to a branch."
        if self.parent_id:
            if (
                self.parent.warehouse_id != self.warehouse_id
                or self.parent.company_id != self.company_id
                or self.parent.branch_id != self.branch_id
            ):
                errors["parent"] = "Parent location must use the same company, branch, and warehouse."
            if self.pk and (
                self.parent_id == self.pk or self.pk in (self.parent.ancestor_ids or [])
            ):
                errors["parent"] = "Cannot create a recursive location hierarchy."
            if len(self.parent.ancestor_ids or []) + 1 > MAX_LOCATION_DEPTH:
                errors["parent"] = f"Location hierarchy supports at most {MAX_LOCATION_DEPTH} levels."
        if self.is_packing_zone and self.is_delivery_zone:
            errors["is_delivery_zone"] = "A location cannot be both a Packing Zone and Delivery Zone."
        for field_name in ("capacity_length", "capacity_width", "capacity_height"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                errors[field_name] = "Location dimensions cannot be negative."
        if self.pk and self.usage != self.Usage.VIEW and self.children.exists():
            errors["usage"] = "A location with children must remain a View location."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.warehouse_id:
            self.company_id = self.warehouse.company_id
            self.branch_id = self.warehouse.branch_id
        self.ancestor_ids = (
            [*(self.parent.ancestor_ids or []), self.parent_id]
            if self.parent_id
            else []
        )
        self.complete_name = (
            f"{self.parent.complete_name} / {self.name}" if self.parent_id else self.name
        )
        self._refresh_capacity_fields()
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.complete_name or self.name


class InventoryLotSerial(TimeStampedModel):
    """Lot/serial identity with expiry and dimensional snapshot."""

    class TrackingType(models.TextChoices):
        LOT = "lot", "Lot"
        SERIAL = "serial", "Serial Number"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        OUT = "out", "Out"

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="inventory_lots",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="inventory_lots",
    )
    name = models.CharField(max_length=255, db_index=True)
    barcode = models.CharField(max_length=255, unique=True, blank=True, null=True)
    tracking_type = models.CharField(
        max_length=16,
        choices=TrackingType.choices,
        default=TrackingType.LOT,
        db_index=True,
    )
    expiration_date = models.DateField(blank=True, null=True, db_index=True)
    alert_date = models.DateField(blank=True, null=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        editable=False,
    )
    length = models.DecimalField(max_digits=16, decimal_places=4, blank=True, null=True)
    width = models.DecimalField(max_digits=16, decimal_places=4, blank=True, null=True)
    height = models.DecimalField(max_digits=16, decimal_places=4, blank=True, null=True)
    dimension_unit = models.CharField(max_length=12, default="m")
    unit_volume = models.DecimalField(
        max_digits=24,
        decimal_places=12,
        blank=True,
        null=True,
        editable=False,
    )

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "product", "name"],
                name="unique_inventory_lot_product_company",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "product", "status"],
                name="inventory_lot_scope_idx",
            ),
        ]

    @property
    def is_expired(self):
        return bool(self.expiration_date and self.expiration_date < timezone.localdate())

    @property
    def is_near_expiry(self):
        if not self.expiration_date or self.status != self.Status.ACTIVE:
            return False
        today = timezone.localdate()
        return today <= self.expiration_date <= today + timedelta(days=30)

    def clean(self):
        super().clean()
        errors = {}
        if self.product_id and self.company_id != self.product.company_id:
            errors["product"] = "Lot/serial product must belong to the selected company."
        if self.alert_date and self.expiration_date and self.alert_date > self.expiration_date:
            errors["alert_date"] = "Alert Date cannot be later than Expiration Date."
        dimensions = (self.length, self.width, self.height)
        if any(value is not None for value in dimensions):
            if not all(value is not None and value > 0 for value in dimensions):
                errors["length"] = "Length, width, and height must all be positive when dimensions are used."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        dimensions = (self.length, self.width, self.height)
        self.unit_volume = (
            dimensions[0] * dimensions[1] * dimensions[2]
            if all(value is not None and value > 0 for value in dimensions)
            else None
        )
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} · {self.product}"


class InventoryStockBalance(TimeStampedModel):
    """Real-time stock position per product, location, lot, and ownership pool."""

    class OwnershipType(models.TextChoices):
        COMPANY = "company", "Company Owned"
        VENDOR = "vendor", "Vendor Owned"

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="inventory_stock_balances",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="inventory_stock_balances",
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="inventory_stock_balances",
        blank=True,
        null=True,
    )
    location = models.ForeignKey(
        InventoryLocation,
        on_delete=models.PROTECT,
        related_name="stock_balances",
    )
    lot = models.ForeignKey(
        InventoryLotSerial,
        on_delete=models.PROTECT,
        related_name="stock_balances",
        blank=True,
        null=True,
    )
    ownership_type = models.CharField(
        max_length=16,
        choices=OwnershipType.choices,
        default=OwnershipType.COMPANY,
        db_index=True,
    )
    owner_vendor = models.ForeignKey(
        Contact,
        on_delete=models.PROTECT,
        related_name="owned_inventory_balances",
        blank=True,
        null=True,
    )
    qty_on_hand = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    qty_reserved = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    qty_available = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        editable=False,
    )
    unit_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    incoming_date = models.DateField(blank=True, null=True, db_index=True)
    last_move = models.ForeignKey(
        StockMove,
        on_delete=models.SET_NULL,
        related_name="latest_inventory_balances",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["product__name", "warehouse__name", "location__complete_name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "product",
                    "location",
                    "lot",
                    "ownership_type",
                    "owner_vendor",
                ],
                name="unique_inventory_stock_position",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=models.Q(qty_reserved__gte=0),
                name="inventory_reserved_nonnegative",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "warehouse", "location"],
                name="inventory_balance_scope_idx",
            ),
            models.Index(
                fields=["company", "product", "warehouse"],
                name="inventory_balance_product_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.product_id and self.product.company_id != self.company_id:
            errors["product"] = "Stock-balance product must belong to the selected company."
        if self.location_id:
            if self.location.company_id != self.company_id:
                errors["location"] = "Stock-balance location must belong to the selected company."
            if self.location.warehouse_id != self.warehouse_id:
                errors["warehouse"] = "Stock-balance warehouse must match its location warehouse."
            if self.location.usage != InventoryLocation.Usage.INTERNAL:
                errors["location"] = "Stock can only be held in an Internal location."
        if self.lot_id:
            if self.lot.company_id != self.company_id or self.lot.product_id != self.product_id:
                errors["lot"] = "Lot/serial must belong to the same company and product."
            if self.lot.tracking_type == InventoryLotSerial.TrackingType.SERIAL and self.qty_on_hand > 1:
                errors["qty_on_hand"] = "A serial-number stock position cannot exceed one unit."
        if self.qty_reserved < 0:
            errors["qty_reserved"] = "Reserved quantity cannot be negative."
        if self.qty_reserved > self.qty_on_hand:
            errors["qty_reserved"] = "Reserved quantity cannot exceed on-hand quantity."
        if self.ownership_type == self.OwnershipType.VENDOR and not self.owner_vendor_id:
            errors["owner_vendor"] = "Vendor-owned stock requires an owner vendor."
        if self.ownership_type == self.OwnershipType.COMPANY and self.owner_vendor_id:
            errors["owner_vendor"] = "Company-owned stock cannot have an owner vendor."
        if self.owner_vendor_id and self.owner_vendor.company_id != self.company_id:
            errors["owner_vendor"] = "Owner vendor must belong to the selected company."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.qty_available = self.qty_on_hand - self.qty_reserved
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product} · {self.location} · {self.qty_available} available"


class InventoryBalanceEvent(models.Model):
    """Idempotency marker proving one immutable core StockMove reached balances."""

    stock_move = models.OneToOneField(
        StockMove,
        on_delete=models.PROTECT,
        related_name="inventory_balance_event",
    )
    balance = models.ForeignKey(
        InventoryStockBalance,
        on_delete=models.PROTECT,
        related_name="movement_events",
    )
    direction = models.CharField(max_length=3, choices=StockMove.Direction.choices)
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["stock_move_id"]
        indexes = [models.Index(fields=["processed_at", "stock_move"], name="inventory_event_time_idx")]

    def __str__(self):
        return f"Stock Move {self.stock_move_id} → Balance {self.balance_id}"


class InventoryReorderRule(TimeStampedModel):
    """Minimum/maximum stock policy for one product and destination warehouse."""

    class Route(models.TextChoices):
        BUY = "buy", "Buy"
        INTERNAL = "internal", "Internal Transfer"

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="inventory_reorder_rules",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="inventory_reorder_rules",
        blank=True,
        null=True,
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="inventory_reorder_rules",
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="inventory_reorder_rules",
    )
    route = models.CharField(max_length=16, choices=Route.choices, default=Route.BUY)
    source_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="inventory_source_reorder_rules",
        blank=True,
        null=True,
    )
    vendor = models.ForeignKey(
        Contact,
        on_delete=models.PROTECT,
        related_name="inventory_reorder_rules",
        blank=True,
        null=True,
    )
    minimum_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    maximum_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    reorder_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        help_text="Fixed quantity to replenish; leave zero to replenish up to Maximum Quantity.",
    )
    lead_time_days = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True, db_index=True)

    organization_branch_lookup = "branch"

    class Meta:
        ordering = ["warehouse__name", "product__name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "warehouse"],
                name="unique_inventory_reorder_product_warehouse",
            ),
            models.CheckConstraint(
                condition=models.Q(minimum_quantity__gte=0),
                name="inventory_reorder_min_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(maximum_quantity__gte=0),
                name="inventory_reorder_max_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(reorder_quantity__gte=0),
                name="inventory_reorder_qty_nonnegative",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "branch", "warehouse", "active"],
                name="inventory_reorder_scope_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.warehouse_id:
            if self.warehouse.company_id != self.company_id:
                errors["warehouse"] = "Reorder-rule warehouse must belong to the selected company."
            if self.warehouse.branch_id != self.branch_id:
                errors["branch"] = "Reorder-rule branch must match the destination warehouse."
        if self.product_id and self.product.company_id != self.company_id:
            errors["product"] = "Reorder-rule product must belong to the selected company."
        if self.maximum_quantity < self.minimum_quantity:
            errors["maximum_quantity"] = "Maximum Quantity cannot be lower than Minimum Quantity."
        if self.route == self.Route.INTERNAL:
            if not self.source_warehouse_id:
                errors["source_warehouse"] = "Internal Transfer requires a source warehouse."
            elif self.source_warehouse_id == self.warehouse_id:
                errors["source_warehouse"] = "Source warehouse must differ from destination warehouse."
            elif self.source_warehouse.company_id != self.company_id:
                errors["source_warehouse"] = "Source warehouse must belong to the selected company."
        elif self.source_warehouse_id:
            errors["source_warehouse"] = "A source warehouse is only used by the Internal Transfer route."
        if self.vendor_id:
            if self.vendor.company_id != self.company_id:
                errors["vendor"] = "Vendor must belong to the selected company."
            elif self.vendor.contact_type not in {
                Contact.ContactType.VENDOR,
                Contact.ContactType.BOTH,
            }:
                errors["vendor"] = "The selected contact must be a vendor."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.warehouse_id:
            self.company_id = self.warehouse.company_id
            self.branch_id = self.warehouse.branch_id
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product} · {self.warehouse}"


class InventoryReplenishmentSnapshot(TimeStampedModel):
    """Latest bounded evaluation of a reorder rule, kept read-only for operators."""

    class Status(models.TextChoices):
        DUE = "due", "Replenishment Due"
        HEALTHY = "healthy", "Stock Healthy"
        INACTIVE = "inactive", "Rule Inactive"

    rule = models.OneToOneField(
        InventoryReorderRule,
        on_delete=models.CASCADE,
        related_name="replenishment_snapshot",
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="inventory_replenishment_snapshots",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="inventory_replenishment_snapshots",
        blank=True,
        null=True,
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="inventory_replenishment_snapshots",
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="inventory_replenishment_snapshots",
    )
    route = models.CharField(max_length=16, choices=InventoryReorderRule.Route.choices)
    source_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="inventory_source_replenishment_snapshots",
        blank=True,
        null=True,
    )
    vendor = models.ForeignKey(
        Contact,
        on_delete=models.PROTECT,
        related_name="inventory_replenishment_snapshots",
        blank=True,
        null=True,
    )
    quantity_available = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    minimum_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    maximum_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    suggested_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    need_by_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)
    evaluated_at = models.DateTimeField(default=timezone.now, db_index=True)

    organization_branch_lookup = "branch"

    class Meta:
        ordering = ["status", "need_by_date", "warehouse__name", "product__name", "pk"]
        indexes = [
            models.Index(
                fields=["company", "branch", "status", "need_by_date"],
                name="inventory_replenish_scope_idx",
            ),
        ]

    def __str__(self):
        return f"{self.product} · {self.warehouse} · {self.get_status_display()}"


class InventoryStockCount(TimeStampedModel):
    """Physical-count document with a frozen warehouse snapshot."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        VALIDATED = "validated", "Validated"
        CANCELLED = "cancelled", "Cancelled"

    class ProductScope(models.TextChoices):
        ALL = "all_product", "All Products"
        SELECTED = "product", "Specific Products"

    class CountedDefault(models.TextChoices):
        ZERO = "zero", "Default To Zero"
        SYSTEM = "system_qty", "Based on System Quantity"

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="inventory_stock_counts",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="inventory_stock_counts",
        blank=True,
        null=True,
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="inventory_stock_counts",
    )
    name = models.CharField(max_length=80)
    locations = models.ManyToManyField(
        InventoryLocation,
        related_name="stock_counts",
        blank=True,
    )
    inventoried_product = models.CharField(
        max_length=20,
        choices=ProductScope.choices,
        default=ProductScope.ALL,
    )
    products = models.ManyToManyField(
        Product,
        related_name="inventory_stock_counts",
        blank=True,
    )
    include_zero_stock_product = models.BooleanField(default=False)
    counted_qty_default = models.CharField(
        max_length=20,
        choices=CountedDefault.choices,
        default=CountedDefault.SYSTEM,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    notes = models.TextField(blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_inventory_stock_counts",
        blank=True,
        null=True,
        editable=False,
    )
    confirmed_at = models.DateTimeField(blank=True, null=True, editable=False)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="completed_inventory_stock_counts",
        blank=True,
        null=True,
        editable=False,
    )
    completed_at = models.DateTimeField(blank=True, null=True, editable=False)
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="validated_inventory_stock_counts",
        blank=True,
        null=True,
        editable=False,
    )
    validated_at = models.DateTimeField(blank=True, null=True, editable=False)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cancelled_inventory_stock_counts",
        blank=True,
        null=True,
        editable=False,
    )
    cancelled_at = models.DateTimeField(blank=True, null=True, editable=False)

    organization_branch_lookup = "branch"

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"],
                name="unique_inventory_stock_count_name",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "branch", "warehouse", "status"],
                name="inventory_count_scope_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.warehouse_id:
            if self.warehouse.company_id != self.company_id:
                errors["warehouse"] = "Stock Count warehouse must belong to the selected company."
            if self.warehouse.branch_id != self.branch_id:
                errors["branch"] = "Stock Count branch must match its warehouse branch."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.warehouse_id:
            self.company_id = self.warehouse.company_id
            self.branch_id = self.warehouse.branch_id
        if not self.name:
            self.name = f"SC-{timezone.localdate():%Y%m%d}-{uuid4().hex[:8].upper()}"
        self.full_clean(exclude={"locations", "products"})
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ValidationError("Only Draft Stock Counts can be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return self.name


class InventoryStockCountLine(TimeStampedModel):
    class LineSource(models.TextChoices):
        BALANCE = "balance", "Balance"
        NO_STOCK = "no_stock", "No Stock"
        MANUAL = "manual", "Manual"

    stock_count = models.ForeignKey(
        InventoryStockCount,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="inventory_stock_count_lines",
    )
    location = models.ForeignKey(
        InventoryLocation,
        on_delete=models.PROTECT,
        related_name="stock_count_lines",
    )
    lot = models.ForeignKey(
        InventoryLotSerial,
        on_delete=models.PROTECT,
        related_name="stock_count_lines",
        blank=True,
        null=True,
    )
    system_qty = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    counted_qty = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    difference_qty = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        editable=False,
    )
    line_source = models.CharField(
        max_length=16,
        choices=LineSource.choices,
        default=LineSource.BALANCE,
        editable=False,
    )
    adjustment_stock_move = models.OneToOneField(
        StockMove,
        on_delete=models.PROTECT,
        related_name="inventory_stock_count_line",
        blank=True,
        null=True,
        editable=False,
    )

    organization_branch_lookup = "stock_count__branch"

    class Meta:
        ordering = ["product__name", "location__complete_name", "lot__name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["stock_count", "product", "location", "lot"],
                name="unique_inventory_stock_count_position",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=models.Q(counted_qty__gte=0),
                name="inventory_counted_qty_nonnegative",
            ),
        ]
        indexes = [
            models.Index(
                fields=["stock_count", "product", "location"],
                name="inventory_count_line_scope_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        stock_count = self.stock_count if self.stock_count_id else None
        stock_count_status = (
            InventoryStockCount.objects.filter(pk=self.stock_count_id)
            .values_list("status", flat=True)
            .first()
            if self.stock_count_id
            else None
        )
        if stock_count and stock_count_status != InventoryStockCount.Status.IN_PROGRESS:
            errors["stock_count"] = (
                "Counting lines can only be edited while the Stock Count is In Progress."
            )
        if self.product_id and self.stock_count_id:
            if self.product.company_id != self.stock_count.company_id:
                errors["product"] = "Counting-line product must belong to the Stock Count company."
        if self.location_id and self.stock_count_id:
            if self.location.warehouse_id != self.stock_count.warehouse_id:
                errors["location"] = "Counting-line location must belong to the Stock Count warehouse."
            if self.location.usage != InventoryLocation.Usage.INTERNAL:
                errors["location"] = "Counting lines require an Internal location."
        if self.lot_id:
            if self.lot.product_id != self.product_id or self.lot.company_id != self.stock_count.company_id:
                errors["lot"] = "Lot/serial must belong to the same company and product."
            if (
                self.lot.tracking_type == InventoryLotSerial.TrackingType.SERIAL
                and self.counted_qty > 1
            ):
                errors["counted_qty"] = "A serial-number count cannot exceed one unit."
        if self.counted_qty < 0:
            errors["counted_qty"] = "Counted quantity cannot be negative."
        if stock_count and self.location_id and self.product_id:
            freeze_scope = (
                InventoryStockFreeze.objects.filter(stock_count_id=stock_count.pk)
                .values("location_ids", "product_ids")
                .first()
            )
            if not freeze_scope:
                errors["stock_count"] = "The Stock Count warehouse freeze is missing."
            else:
                location_ids = set(freeze_scope["location_ids"] or [])
                if location_ids and not (
                    self.location_id in location_ids
                    or bool(location_ids & set(self.location.ancestor_ids or []))
                ):
                    errors["location"] = "Counting-line location is outside the Stock Count scope."
                product_ids = set(freeze_scope["product_ids"] or [])
                if product_ids and self.product_id not in product_ids:
                    errors["product"] = "Counting-line product is outside the Stock Count scope."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            persisted = type(self).objects.filter(pk=self.pk).values(
                "stock_count_id",
                "product_id",
                "location_id",
                "lot_id",
                "system_qty",
                "line_source",
            ).first()
            immutable_fields = (
                "stock_count_id",
                "product_id",
                "location_id",
                "lot_id",
                "system_qty",
                "line_source",
            )
            if persisted and any(
                persisted[field_name] != getattr(self, field_name)
                for field_name in immutable_fields
            ):
                raise ValidationError(
                    "Counting-line snapshot identity and System Qty are immutable."
                )
        self.difference_qty = self.counted_qty - self.system_qty
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        stock_count_status = InventoryStockCount.objects.filter(
            pk=self.stock_count_id
        ).values_list("status", flat=True).first()
        if stock_count_status != InventoryStockCount.Status.IN_PROGRESS:
            raise ValidationError(
                "Counting lines can only be deleted while the Stock Count is In Progress."
            )
        if self.adjustment_stock_move_id:
            raise ValidationError("A posted counting line cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.stock_count} · {self.product} · {self.location}"


class InventoryStockFreeze(models.Model):
    """Conservative warehouse lock while one Stock Count is being processed."""

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="inventory_stock_freezes",
    )
    warehouse = models.OneToOneField(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="inventory_stock_freeze",
    )
    stock_count = models.OneToOneField(
        InventoryStockCount,
        on_delete=models.CASCADE,
        related_name="freeze",
    )
    location_ids = models.JSONField(default=list, blank=True)
    product_ids = models.JSONField(default=list, blank=True)
    frozen_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["warehouse_id"]

    def __str__(self):
        return f"{self.warehouse} locked by {self.stock_count}"


class InventoryReservation(TimeStampedModel):
    """Outbound stock reservation with immutable balance allocations."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RESERVED = "reserved", "Reserved"
        PARTIAL = "partially_reserved", "Partially Reserved"
        RELEASED = "released", "Released"
        CONSUMED = "consumed", "Consumed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="inventory_reservations",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="inventory_reservations",
        blank=True,
        null=True,
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="inventory_reservations",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="inventory_reservations",
    )
    location = models.ForeignKey(
        InventoryLocation,
        on_delete=models.PROTECT,
        related_name="inventory_reservations",
        blank=True,
        null=True,
        help_text="Leave blank to allocate across active internal locations by priority.",
    )
    lot = models.ForeignKey(
        InventoryLotSerial,
        on_delete=models.PROTECT,
        related_name="inventory_reservations",
        blank=True,
        null=True,
    )
    reference = models.CharField(max_length=80)
    requested_quantity = models.DecimalField(max_digits=18, decimal_places=3)
    reserved_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        editable=False,
    )
    allow_partial = models.BooleanField(default=False)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    notes = models.TextField(blank=True)
    reserved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reserved_inventory_reservations",
        blank=True,
        null=True,
        editable=False,
    )
    reserved_at = models.DateTimeField(blank=True, null=True, editable=False)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="released_inventory_reservations",
        blank=True,
        null=True,
        editable=False,
    )
    released_at = models.DateTimeField(blank=True, null=True, editable=False)
    consumed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="consumed_inventory_reservations",
        blank=True,
        null=True,
        editable=False,
    )
    consumed_at = models.DateTimeField(blank=True, null=True, editable=False)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cancelled_inventory_reservations",
        blank=True,
        null=True,
        editable=False,
    )
    cancelled_at = models.DateTimeField(blank=True, null=True, editable=False)

    organization_branch_lookup = "branch"

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "reference"],
                name="unique_inventory_reservation_reference",
            ),
            models.CheckConstraint(
                condition=models.Q(requested_quantity__gt=0),
                name="inventory_reservation_requested_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_quantity__gte=0),
                name="inventory_reservation_reserved_nonnegative",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "branch", "warehouse", "status"],
                name="inv_reservation_scope_idx",
            ),
            models.Index(
                fields=["company", "product", "status"],
                name="inv_reservation_product_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.warehouse_id:
            if self.warehouse.company_id != self.company_id:
                errors["warehouse"] = "Reservation warehouse must belong to the selected company."
            if self.warehouse.branch_id != self.branch_id:
                errors["branch"] = "Reservation branch must match its warehouse branch."
        if self.product_id and self.product.company_id != self.company_id:
            errors["product"] = "Reservation product must belong to the selected company."
        elif self.product_id and self.product.product_type != Product.ProductType.STOCKABLE:
            errors["product"] = "Only stockable products can be reserved."
        if self.location_id:
            if self.location.warehouse_id != self.warehouse_id:
                errors["location"] = "Reservation location must belong to the selected warehouse."
            if self.location.usage != InventoryLocation.Usage.INTERNAL:
                errors["location"] = "Reservations require an Internal location."
            if (
                self.status == self.Status.DRAFT
                and (not self.location.active or self.location.is_expired_location)
            ):
                errors["location"] = "Reservations require an active, non-expired location."
        if self.lot_id:
            if self.lot.company_id != self.company_id or self.lot.product_id != self.product_id:
                errors["lot"] = "Reservation lot/serial must match the selected company and product."
            if (
                self.status == self.Status.DRAFT
                and (
                    self.lot.status != InventoryLotSerial.Status.ACTIVE
                    or self.lot.is_expired
                )
            ):
                errors["lot"] = "Reservations require an active, non-expired lot/serial."
            if (
                self.lot.tracking_type == InventoryLotSerial.TrackingType.SERIAL
                and self.requested_quantity > 1
            ):
                errors["requested_quantity"] = "A serial-number reservation cannot exceed one unit."
        if self.requested_quantity is not None and self.requested_quantity <= ZERO:
            errors["requested_quantity"] = "Requested quantity must be greater than zero."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.warehouse_id:
            self.company_id = self.warehouse.company_id
            self.branch_id = self.warehouse.branch_id
        if not self.reference:
            self.reference = f"RSV-{timezone.localdate():%Y%m%d}-{uuid4().hex[:8].upper()}"
        if self.pk:
            persisted = type(self).objects.filter(pk=self.pk).values(
                "company_id",
                "branch_id",
                "warehouse_id",
                "product_id",
                "location_id",
                "lot_id",
                "reference",
                "requested_quantity",
                "allow_partial",
            ).first()
            immutable_fields = (
                "company_id",
                "branch_id",
                "warehouse_id",
                "product_id",
                "location_id",
                "lot_id",
                "reference",
                "requested_quantity",
                "allow_partial",
            )
            if persisted and self.status != self.Status.DRAFT and any(
                persisted[field_name] != getattr(self, field_name)
                for field_name in immutable_fields
            ):
                raise ValidationError("Reserved request scope and quantity are immutable.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ValidationError("Only Draft Reservations can be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return self.reference


class InventoryReservationAllocation(models.Model):
    reservation = models.ForeignKey(
        InventoryReservation,
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    balance = models.ForeignKey(
        InventoryStockBalance,
        on_delete=models.PROTECT,
        related_name="reservation_allocations",
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    stock_move = models.OneToOneField(
        StockMove,
        on_delete=models.PROTECT,
        related_name="inventory_reservation_allocation",
        blank=True,
        null=True,
    )
    allocated_at = models.DateTimeField(default=timezone.now)

    organization_branch_lookup = "reservation__branch"

    class Meta:
        ordering = ["reservation_id", "balance_id", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["reservation", "balance"],
                name="unique_inventory_reservation_balance",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="inventory_reservation_allocation_positive",
            ),
        ]

    def delete(self, *args, **kwargs):
        raise ValidationError("Reservation allocations are immutable.")

    def __str__(self):
        return f"{self.reservation} · {self.balance} · {self.quantity}"


class InventoryPicking(TimeStampedModel):
    """Outbound picking adapted from the HMX Transfer/Demand workflow."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Waiting Availability"
        PARTIAL = "partially_available", "Partially Available"
        READY = "ready", "Ready"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="inventory_pickings",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="inventory_pickings",
        blank=True,
        null=True,
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="inventory_pickings",
    )
    reference = models.CharField(max_length=80)
    scheduled_date = models.DateField(default=timezone.localdate, db_index=True)
    responsible = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="responsible_inventory_pickings",
        blank=True,
        null=True,
    )
    destination_name = models.CharField(max_length=160, blank=True)
    destination_address = models.TextField(blank=True)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    backorder_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="backorders",
        blank=True,
        null=True,
        editable=False,
    )
    notes = models.TextField(blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_inventory_pickings",
        blank=True,
        null=True,
        editable=False,
    )
    confirmed_at = models.DateTimeField(blank=True, null=True, editable=False)
    availability_checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="availability_checked_inventory_pickings",
        blank=True,
        null=True,
        editable=False,
    )
    availability_checked_at = models.DateTimeField(
        blank=True,
        null=True,
        editable=False,
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="completed_inventory_pickings",
        blank=True,
        null=True,
        editable=False,
    )
    completed_at = models.DateTimeField(blank=True, null=True, editable=False)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cancelled_inventory_pickings",
        blank=True,
        null=True,
        editable=False,
    )
    cancelled_at = models.DateTimeField(blank=True, null=True, editable=False)

    organization_branch_lookup = "branch"

    class Meta:
        ordering = ["-scheduled_date", "-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "reference"],
                name="unique_inventory_picking_reference",
            ),
        ]
        indexes = [
            models.Index(
                fields=["company", "branch", "warehouse", "status"],
                name="inv_picking_scope_idx",
            ),
            models.Index(
                fields=["company", "scheduled_date", "status"],
                name="inv_picking_schedule_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.warehouse_id:
            if self.warehouse.company_id != self.company_id:
                errors["warehouse"] = "Picking warehouse must belong to the selected company."
            if self.warehouse.branch_id != self.branch_id:
                errors["branch"] = "Picking branch must match its warehouse branch."
        if self.backorder_of_id and (
            self.backorder_of.company_id != self.company_id
            or self.backorder_of.warehouse_id != self.warehouse_id
        ):
            errors["backorder_of"] = "Backorder must keep the original company and warehouse."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.warehouse_id:
            self.company_id = self.warehouse.company_id
            self.branch_id = self.warehouse.branch_id
        if not self.reference:
            self.reference = f"PICK-{timezone.localdate():%Y%m%d}-{uuid4().hex[:8].upper()}"
        if self.pk:
            persisted = type(self).objects.filter(pk=self.pk).values(
                "company_id",
                "branch_id",
                "warehouse_id",
                "reference",
                "backorder_of_id",
                "status",
            ).first()
            immutable_fields = (
                "company_id",
                "branch_id",
                "warehouse_id",
                "reference",
                "backorder_of_id",
            )
            if persisted and persisted["status"] != self.Status.DRAFT and any(
                persisted[field_name] != getattr(self, field_name)
                for field_name in immutable_fields
            ):
                raise ValidationError("Confirmed Picking scope is immutable.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ValidationError("Only Draft Pickings can be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return self.reference


class InventoryPickingLine(TimeStampedModel):
    picking = models.ForeignKey(
        InventoryPicking,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="inventory_picking_lines",
    )
    location = models.ForeignKey(
        InventoryLocation,
        on_delete=models.PROTECT,
        related_name="inventory_picking_lines",
        blank=True,
        null=True,
        help_text="Optional exact source location; blank uses location priority.",
    )
    lot = models.ForeignKey(
        InventoryLotSerial,
        on_delete=models.PROTECT,
        related_name="inventory_picking_lines",
        blank=True,
        null=True,
    )
    requested_quantity = models.DecimalField(max_digits=18, decimal_places=3)
    reserved_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        editable=False,
    )
    done_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    current_reservation = models.ForeignKey(
        InventoryReservation,
        on_delete=models.PROTECT,
        related_name="current_picking_lines",
        blank=True,
        null=True,
        editable=False,
    )

    organization_branch_lookup = "picking__branch"

    class Meta:
        ordering = ["picking_id", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["picking", "product", "location", "lot"],
                name="unique_inventory_picking_position",
                nulls_distinct=False,
            ),
            models.CheckConstraint(
                condition=models.Q(requested_quantity__gt=0),
                name="inventory_picking_requested_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_quantity__gte=0),
                name="inventory_picking_reserved_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(done_quantity__gte=0),
                name="inventory_picking_done_nonnegative",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.product_id:
            if self.product.company_id != self.picking.company_id:
                errors["product"] = "Picking product must belong to the Picking company."
            elif self.product.product_type != Product.ProductType.STOCKABLE:
                errors["product"] = "Only stockable products can be picked."
        if self.location_id:
            if self.location.warehouse_id != self.picking.warehouse_id:
                errors["location"] = "Source location must belong to the Picking warehouse."
            if self.location.usage != InventoryLocation.Usage.INTERNAL:
                errors["location"] = "Picking requires an Internal source location."
        if self.lot_id and (
            self.lot.company_id != self.picking.company_id
            or self.lot.product_id != self.product_id
        ):
            errors["lot"] = "Lot/serial must match the Picking company and product."
        if self.requested_quantity is not None and self.requested_quantity <= ZERO:
            errors["requested_quantity"] = "Requested quantity must be greater than zero."
        if self.done_quantity is not None and self.done_quantity < ZERO:
            errors["done_quantity"] = "Done quantity cannot be negative."
        if self.done_quantity is not None and self.done_quantity > self.reserved_quantity:
            errors["done_quantity"] = "Done quantity cannot exceed reserved quantity."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            persisted = type(self).objects.filter(pk=self.pk).values(
                "picking_id",
                "product_id",
                "location_id",
                "lot_id",
                "requested_quantity",
                "reserved_quantity",
                "current_reservation_id",
            ).first()
            immutable_fields = (
                "picking_id",
                "product_id",
                "location_id",
                "lot_id",
                "requested_quantity",
                "current_reservation_id",
            )
            picking_status = InventoryPicking.objects.filter(
                pk=self.picking_id
            ).values_list("status", flat=True).first()
            if persisted and (
                persisted["reserved_quantity"] != self.reserved_quantity
                or persisted["current_reservation_id"] != self.current_reservation_id
            ):
                raise ValidationError("Picking reservation fields are system managed.")
            if (
                persisted
                and picking_status != InventoryPicking.Status.DRAFT
                and any(
                    persisted[field_name] != getattr(self, field_name)
                    for field_name in immutable_fields
                )
            ):
                raise ValidationError("Confirmed Picking line demand is immutable.")
        else:
            picking_status = self.picking.status
        if picking_status not in {
            InventoryPicking.Status.DRAFT,
            InventoryPicking.Status.CONFIRMED,
            InventoryPicking.Status.PARTIAL,
            InventoryPicking.Status.READY,
        }:
            raise ValidationError("Picking lines cannot be edited in this status.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        status = InventoryPicking.objects.filter(pk=self.picking_id).values_list(
            "status", flat=True
        ).first()
        if status != InventoryPicking.Status.DRAFT:
            raise ValidationError("Only Draft Picking lines can be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.picking} · {self.product} · {self.requested_quantity}"
