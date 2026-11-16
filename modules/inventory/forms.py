from django import forms

from core.forms import StyledModelForm

from .models import InventoryLocation, InventoryLotSerial, InventoryReorderRule


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
