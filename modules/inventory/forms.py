from django import forms

from core.forms import StyledModelForm

from .models import (
    InventoryLocation,
    InventoryLotSerial,
    InventoryReorderRule,
    InventoryReservation,
    InventoryStockBalance,
    InventoryStockCount,
    InventoryStockCountLine,
)


class InventoryLocationForm(StyledModelForm):
    class Meta:
        model = InventoryLocation
        fields = [
            "company",
            "branch",
            "warehouse",
            "parent",
            "name",
            "usage",
            "barcode",
            "capacity_length",
            "capacity_width",
            "capacity_height",
            "dimension_unit",
            "removal_strategy",
            "is_expired_location",
            "is_packing_zone",
            "is_delivery_zone",
            "sequence",
            "active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["usage"].choices = [
            (InventoryLocation.Usage.INTERNAL, InventoryLocation.Usage.INTERNAL.label),
            (InventoryLocation.Usage.VIEW, InventoryLocation.Usage.VIEW.label),
        ]
        self.fields["parent"].queryset = self.fields["parent"].queryset.exclude(
            usage=InventoryLocation.Usage.VIRTUAL
        )


class InventoryLotSerialForm(StyledModelForm):
    class Meta:
        model = InventoryLotSerial
        fields = [
            "company",
            "product",
            "name",
            "barcode",
            "tracking_type",
            "expiration_date",
            "alert_date",
            "status",
            "length",
            "width",
            "height",
            "dimension_unit",
        ]
        widgets = {
            "expiration_date": forms.DateInput(attrs={"type": "date"}),
            "alert_date": forms.DateInput(attrs={"type": "date"}),
        }


class InventoryReorderRuleForm(StyledModelForm):
    class Meta:
        model = InventoryReorderRule
        fields = [
            "company",
            "branch",
            "product",
            "warehouse",
            "route",
            "source_warehouse",
            "vendor",
            "minimum_quantity",
            "maximum_quantity",
            "reorder_quantity",
            "lead_time_days",
            "active",
        ]


class InventoryReservationForm(StyledModelForm):
    class Meta:
        model = InventoryReservation
        fields = [
            "company",
            "branch",
            "warehouse",
            "product",
            "location",
            "lot",
            "requested_quantity",
            "allow_partial",
            "notes",
        ]


class InventoryStockCountForm(StyledModelForm):
    class Meta:
        model = InventoryStockCount
        fields = [
            "company",
            "branch",
            "warehouse",
            "locations",
            "inventoried_product",
            "products",
            "include_zero_stock_product",
            "counted_qty_default",
            "notes",
        ]

    def clean(self):
        cleaned = super().clean()
        scope = cleaned.get("inventoried_product")
        products = cleaned.get("products")
        if scope == InventoryStockCount.ProductScope.SELECTED and not products:
            self.add_error("products", "Select at least one product.")
        return cleaned


class InventoryStockCountLineForm(StyledModelForm):
    class Meta:
        model = InventoryStockCountLine
        fields = [
            "stock_count",
            "product",
            "location",
            "lot",
            "system_qty",
            "counted_qty",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["system_qty"].disabled = True
        if self.instance.pk:
            for field_name in ("stock_count", "product", "location", "lot"):
                self.fields[field_name].disabled = True

    def clean(self):
        cleaned = super().clean()
        stock_count = cleaned.get("stock_count") or getattr(
            self.instance, "stock_count", None
        )
        if stock_count and stock_count.status != InventoryStockCount.Status.IN_PROGRESS:
            raise forms.ValidationError(
                "Counting lines can only be edited while the Stock Count is In Progress."
            )
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if not instance.pk:
            balance = (
                InventoryStockBalance.objects.filter(
                    company_id=instance.stock_count.company_id,
                    warehouse_id=instance.stock_count.warehouse_id,
                    product_id=instance.product_id,
                    location_id=instance.location_id,
                    lot_id=instance.lot_id,
                    ownership_type=InventoryStockBalance.OwnershipType.COMPANY,
                    owner_vendor__isnull=True,
                )
                .only("qty_on_hand")
                .first()
            )
            instance.system_qty = balance.qty_on_hand if balance else 0
            instance.line_source = InventoryStockCountLine.LineSource.MANUAL
        instance.difference_qty = instance.counted_qty - instance.system_qty
        if commit:
            instance.save()
            self.save_m2m()
        return instance
