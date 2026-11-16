from datetime import timedelta
from decimal import Decimal

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
