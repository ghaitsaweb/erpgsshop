from django import forms
from django.db.models import Q
from django.forms import BaseInlineFormSet

from core.forms import StyledModelForm

from .models import (
    InventoryLocation,
    InventoryLotSerial,
    InventoryPackage,
    InventoryPackageAllocation,
    InventoryPackageType,
    InventoryPicking,
    InventoryPickingLine,
    InventoryProofOfDelivery,
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


class InventoryPickingForm(StyledModelForm):
    class Meta:
        model = InventoryPicking
        fields = [
            "company",
            "branch",
            "warehouse",
            "scheduled_date",
            "responsible",
            "destination_name",
            "destination_address",
            "notes",
        ]
        widgets = {
            "scheduled_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.status != InventoryPicking.Status.DRAFT:
            for field in self.fields.values():
                field.disabled = True


class InventoryPickingLineForm(StyledModelForm):
    reserved_quantity = forms.DecimalField(
        label="Reserved quantity",
        decimal_places=3,
        max_digits=18,
        required=False,
        disabled=True,
    )

    class Meta:
        model = InventoryPickingLine
        fields = [
            "picking",
            "product",
            "location",
            "lot",
            "requested_quantity",
            "done_quantity",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reserved_quantity"].disabled = True
        picking = self.instance.picking if self.instance.picking_id else None
        if self.instance.pk and picking and picking.status != InventoryPicking.Status.DRAFT:
            for field_name in (
                "picking",
                "product",
                "location",
                "lot",
                "requested_quantity",
            ):
                if field_name in self.fields:
                    self.fields[field_name].disabled = True
        if not picking or picking.status not in {
            InventoryPicking.Status.PARTIAL,
            InventoryPicking.Status.READY,
        }:
            self.fields["done_quantity"].disabled = True


class InventoryPickingLineFormSet(BaseInlineFormSet):
    MAX_LINES = 250

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        active_forms = [
            form
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]
        if len(active_forms) > self.MAX_LINES:
            raise forms.ValidationError(
                f"A Picking supports at most {self.MAX_LINES} demand lines."
            )
        if self.instance.status != InventoryPicking.Status.DRAFT and any(
            form.cleaned_data.get("DELETE")
            for form in self.forms
            if form.cleaned_data
        ):
            raise forms.ValidationError("Only Draft Picking lines can be deleted.")


class InventoryPackageTypeForm(StyledModelForm):
    class Meta:
        model = InventoryPackageType
        fields = [
            "company",
            "code",
            "name",
            "reusable",
            "auto_generated",
            "strict_compatibility",
            "active",
        ]


class InventoryPackageForm(StyledModelForm):
    class Meta:
        model = InventoryPackage
        fields = [
            "company",
            "package_type",
            "code",
            "barcode",
            "pack_date",
            "owner_warehouse",
            "return_expected",
        ]
        widgets = {
            "pack_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["code"].required = False
        if self.instance.pk:
            for field_name in (
                "company",
                "package_type",
                "code",
                "owner_warehouse",
            ):
                self.fields[field_name].disabled = True
            if self.instance.status != InventoryPackage.Status.AVAILABLE:
                for field in self.fields.values():
                    field.disabled = True

    def clean(self):
        cleaned = super().clean()
        package_type = cleaned.get("package_type") or getattr(self.instance, "package_type", None)
        code = (cleaned.get("code") or "").strip()
        if not code and package_type and not package_type.auto_generated:
            self.add_error("code", "Package code is required for a manually numbered Package Type.")
        return cleaned


class InventoryPackageAllocationForm(StyledModelForm):
    class Meta:
        model = InventoryPackageAllocation
        fields = ["picking_line", "package", "quantity"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        picking = getattr(self.instance, "picking", None)
        if picking and picking.pk:
            self.fields["picking_line"].queryset = InventoryPickingLine.objects.filter(
                picking_id=picking.pk,
            ).select_related("product", "location", "lot")
            self.fields["package"].queryset = InventoryPackage.objects.filter(
                company_id=picking.company_id,
                owner_warehouse_id=picking.warehouse_id,
                active=True,
            ).filter(
                Q(status=InventoryPackage.Status.AVAILABLE)
                | Q(
                    status=InventoryPackage.Status.IN_USE,
                    current_picking_id=picking.pk,
                )
            ).select_related("package_type")
            if picking.status != InventoryPicking.Status.READY:
                for field in self.fields.values():
                    field.disabled = True


class InventoryPackageAllocationFormSet(BaseInlineFormSet):
    MAX_ALLOCATIONS = 500

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        totals = {}
        combinations = set()
        active_forms = [
            form
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]
        if len(active_forms) > self.MAX_ALLOCATIONS:
            raise forms.ValidationError(
                f"A Picking supports at most {self.MAX_ALLOCATIONS} Package allocations."
            )
        if active_forms and self.instance.status != InventoryPicking.Status.READY:
            raise forms.ValidationError("Packing is only available when the Picking is Ready.")
        for form in active_forms:
            picking_line = form.cleaned_data.get("picking_line")
            package = form.cleaned_data.get("package")
            quantity = form.cleaned_data.get("quantity")
            if not picking_line or not package or quantity is None:
                continue
            key = (picking_line.pk, package.pk)
            if key in combinations:
                raise forms.ValidationError(
                    "The same Picking Line and Package can only appear once."
                )
            combinations.add(key)
            totals[picking_line.pk] = totals.get(picking_line.pk, 0) + quantity
        line_map = {
            line.pk: line
            for line in InventoryPickingLine.objects.filter(
                picking_id=self.instance.pk,
                pk__in=sorted(totals),
            ).only("pk", "reserved_quantity")
        }
        for line_id, packed_quantity in totals.items():
            line = line_map.get(line_id)
            if not line or packed_quantity > line.reserved_quantity:
                raise forms.ValidationError(
                    "Total packed quantity cannot exceed the Picking Line reserved quantity."
                )


class InventoryProofOfDeliveryForm(StyledModelForm):
    class Meta:
        model = InventoryProofOfDelivery
        fields = [
            "picking",
            "sender_name",
            "sender_signature",
            "sender_evidence_document",
            "sender_evidence_photo",
            "sender_evidence_caption",
            "receiver_name",
            "receiver_signature",
            "receiver_evidence_document",
            "receiver_evidence_photo",
            "receiver_evidence_caption",
            "notes",
            "evidence_document",
            "evidence_photo",
            "evidence_caption",
        ]
        widgets = {
            "sender_signature": forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg"}),
            "receiver_signature": forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg"}),
            "sender_evidence_photo": forms.ClearableFileInput(
                attrs={"accept": "image/png,image/jpeg", "capture": "environment"}
            ),
            "receiver_evidence_photo": forms.ClearableFileInput(
                attrs={"accept": "image/png,image/jpeg", "capture": "environment"}
            ),
            "evidence_photo": forms.ClearableFileInput(
                attrs={"accept": "image/png,image/jpeg", "capture": "environment"}
            ),
            "sender_evidence_document": forms.ClearableFileInput(
                attrs={"accept": ".pdf,.doc,.docx,.xls,.xlsx,.csv,.png,.jpg,.jpeg"}
            ),
            "receiver_evidence_document": forms.ClearableFileInput(
                attrs={"accept": ".pdf,.doc,.docx,.xls,.xlsx,.csv,.png,.jpg,.jpeg"}
            ),
            "evidence_document": forms.ClearableFileInput(
                attrs={"accept": ".pdf,.doc,.docx,.xls,.xlsx,.csv,.png,.jpg,.jpeg"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["picking"].disabled = True
        if self.instance.sender_signature:
            self.fields["sender_signature"].disabled = True
        if self.instance.receiver_signature:
            self.fields["receiver_signature"].disabled = True


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
