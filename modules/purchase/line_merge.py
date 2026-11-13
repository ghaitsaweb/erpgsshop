"""HMX-compatible, bounded line folding for Purchase documents."""

import json
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import post_save
from django.utils import timezone

from core.document_registry import register_document_option
from core.models import PurchaseOrder, PurchaseOrderLine, recalculate_total
from core.total_controls import suppress_document_total_recalculation

from .models import (
    BlanketOrder,
    BlanketOrderLine,
    PurchaseOrderLinePricingProvenance,
    PurchaseQuotation,
    PurchaseQuotationLine,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseTender,
    PurchaseTenderLine,
)
from .services import (
    MAX_PURCHASE_DOCUMENT_LINES,
    recalculate_blanket_order_totals,
    recalculate_purchase_order_total,
    recalculate_purchase_quotation_total,
    sync_purchase_request_line_timeline,
    sync_purchase_order_rental_controls,
    update_purchase_order_prices,
    update_purchase_quotation_prices,
)


_line_merge_suspended = ContextVar("purchase_line_merge_suspended", default=False)


@dataclass(frozen=True)
class PurchaseLineMergeConfig:
    line_model: type[models.Model]
    parent_model: type[models.Model]
    parent_field: str
    quantity_field: str
    key_fields: tuple[str, ...]
    editable_statuses: frozenset[str]
    zero_progress_fields: tuple[str, ...] = ()


LINE_MERGE_CONFIGS = (
    PurchaseLineMergeConfig(
        line_model=PurchaseRequestLine,
        parent_model=PurchaseRequest,
        parent_field="request",
        quantity_field="quantity",
        key_fields=(
            "product_id",
            "description",
            "specification",
            "estimated_unit_price",
            "expected_date",
            "destination_warehouse_id",
            "expiry_date",
            "origin_request_line_id",
            "cost_center",
            "cost_center_mode",
            "cost_center_distribution_ids",
            "cost_center_distribution_data",
            "cost_center_analytic_tag_ids",
        ),
        editable_statuses=frozenset({PurchaseRequest.Status.DRAFT}),
        zero_progress_fields=(
            "approved_quantity",
            "rfq_quantity",
            "blanket_quantity",
            "tender_quantity",
            "direct_purchase_quantity",
            "re_request_quantity",
            "ordered_quantity",
        ),
    ),
    PurchaseLineMergeConfig(
        line_model=PurchaseQuotationLine,
        parent_model=PurchaseQuotation,
        parent_field="quotation",
        quantity_field="quantity",
        key_fields=(
            "request_line_id",
            "blanket_order_line_id",
            "product_id",
            "description",
            "specification",
            "unit_price",
            "tax_percent",
            "discount_percent",
            "promised_date",
            "expiry_date",
            "free_product",
            "free_product_source_line_id",
            "price_source",
            "applied_pricelist_id",
            "applied_pricelist_tier_id",
            "price_source_note",
            "discount_formula",
        ),
        editable_statuses=frozenset({PurchaseQuotation.Status.DRAFT, PurchaseQuotation.Status.RECEIVED}),
    ),
    PurchaseLineMergeConfig(
        line_model=PurchaseTenderLine,
        parent_model=PurchaseTender,
        parent_field="tender",
        quantity_field="quantity",
        key_fields=(
            "request_line_id",
            "product_id",
            "description",
            "specification",
            "target_unit_price",
            "delivery_date",
            "cost_center",
            "cost_center_mode",
            "cost_center_distribution_ids",
            "cost_center_distribution_data",
            "cost_center_analytic_tag_ids",
        ),
        editable_statuses=frozenset({PurchaseTender.Status.DRAFT}),
    ),
    PurchaseLineMergeConfig(
        line_model=BlanketOrderLine,
        parent_model=BlanketOrder,
        parent_field="blanket_order",
        quantity_field="agreed_quantity",
        key_fields=(
            "request_line_id",
            "product_id",
            "description",
            "unit_price",
            "tax_percent",
            "minimum_release_quantity",
            "free_product",
            "free_product_source_line_id",
            "price_source",
            "applied_pricelist_id",
            "applied_pricelist_tier_id",
            "price_source_note",
            "discount_formula",
            "cost_center",
            "cost_center_mode",
            "cost_center_distribution_ids",
            "cost_center_distribution_data",
            "cost_center_analytic_tag_ids",
        ),
        editable_statuses=frozenset({BlanketOrder.Status.DRAFT}),
        zero_progress_fields=("released_quantity",),
    ),
    PurchaseLineMergeConfig(
        line_model=PurchaseOrderLine,
        parent_model=PurchaseOrder,
        parent_field="order",
        quantity_field="quantity",
        key_fields=("product_id", "description", "warehouse_id", "unit_price"),
        editable_statuses=frozenset({PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.REVISED}),
        zero_progress_fields=("received_quantity", "billed_quantity"),
    ),
)

_CONFIG_BY_LINE_MODEL = {config.line_model: config for config in LINE_MERGE_CONFIGS}
_CONFIG_BY_PARENT_MODEL = {config.parent_model: config for config in LINE_MERGE_CONFIGS}


@contextmanager
def suspend_purchase_line_merge():
    token = _line_merge_suspended.set(True)
    try:
        yield
    finally:
        _line_merge_suspended.reset(token)


def _normalized_value(value):
    if isinstance(value, Decimal):
        return value.normalize()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if value is None:
        return ""
    return value


def _merge_key(line, config):
    key = tuple(_normalized_value(getattr(line, field)) for field in config.key_fields)
    if config.line_model is PurchaseOrderLine:
        provenance = getattr(line, "purchase_pricing_provenance", None)
        if provenance:
            key += (
                provenance.price_source,
                provenance.applied_pricelist_id,
                provenance.applied_pricelist_tier_id,
                provenance.price_source_note,
                provenance.discount_formula,
                provenance.applicable_vendor_moq,
                provenance.is_below_vendor_moq,
                provenance.moq_warning_message,
                provenance.is_rental,
                provenance.rent_duration,
                provenance.rental_period,
                provenance.expected_date,
                provenance.expiry_date,
                provenance.rental_end_date,
                provenance.total_rental_duration,
                provenance.cost_center,
                provenance.cost_center_mode,
                _normalized_value(provenance.cost_center_distribution_ids),
                _normalized_value(provenance.cost_center_distribution_data),
                _normalized_value(provenance.cost_center_analytic_tag_ids),
                provenance.tax_percent,
                provenance.discount_percent,
            )
    return key


def _line_is_eligible(line, config):
    if config.line_model is PurchaseOrderLine:
        provenance = getattr(line, "purchase_pricing_provenance", None)
        if provenance and (provenance.quotation_line_id or provenance.free_product):
            return False
    return not any(Decimal(getattr(line, field) or 0) != 0 for field in config.zero_progress_fields)


def merge_purchase_document_lines(parent, *, preferred_line_id=None):
    """Fold identical eligible lines using fixed-query parent and line locks."""
    config = _CONFIG_BY_PARENT_MODEL.get(type(parent))
    if config is None:
        raise ValidationError(f"No Purchase line-merge configuration exists for {type(parent).__name__}.")

    with transaction.atomic():
        locked_parent = config.parent_model.objects.select_for_update().get(pk=parent.pk)
        if getattr(locked_parent, "status", None) not in config.editable_statuses:
            return 0

        parent_lookup = {f"{config.parent_field}_id": locked_parent.pk}
        line_queryset = config.line_model.objects.select_for_update(of=("self",)).filter(**parent_lookup)
        if config.line_model is PurchaseOrderLine:
            line_queryset = line_queryset.select_related("purchase_pricing_provenance")
        lines = list(line_queryset.order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1])
        if len(lines) > MAX_PURCHASE_DOCUMENT_LINES:
            raise ValidationError(
                f"Purchase document line merge exceeds the operational limit of {MAX_PURCHASE_DOCUMENT_LINES} lines."
            )

        groups = {}
        for line in lines:
            if _line_is_eligible(line, config):
                groups.setdefault(_merge_key(line, config), []).append(line)

        keepers = []
        duplicate_ids = []
        now = timezone.now()
        for matching_lines in groups.values():
            if len(matching_lines) < 2:
                continue
            keeper = next(
                (line for line in matching_lines if line.pk == preferred_line_id),
                matching_lines[0],
            )
            setattr(
                keeper,
                config.quantity_field,
                sum((getattr(line, config.quantity_field) for line in matching_lines), Decimal("0")),
            )
            keeper.updated_at = now
            keepers.append(keeper)
            duplicate_ids.extend(line.pk for line in matching_lines if line.pk != keeper.pk)

        if not duplicate_ids:
            return 0

        total_context = (
            suppress_document_total_recalculation("purchase_order")
            if config.line_model is PurchaseOrderLine
            else nullcontext()
        )
        with suspend_purchase_line_merge(), total_context:
            config.line_model.objects.filter(pk__in=duplicate_ids).delete()
            config.line_model.objects.bulk_update(
                keepers,
                [config.quantity_field, "updated_at"],
                batch_size=MAX_PURCHASE_DOCUMENT_LINES,
            )

        if config.line_model is PurchaseOrderLine:
            recalculate_total(locked_parent)
        return len(duplicate_ids)


def save_and_merge_purchase_document_lines(parent, formset):
    """Persist one bounded formset, then reconcile duplicate lines once."""
    config = _CONFIG_BY_PARENT_MODEL.get(type(parent))
    purchase_request_forms = []
    if isinstance(parent, PurchaseRequest):
        for form in formset.forms:
            cleaned_data = getattr(form, "cleaned_data", None)
            if not cleaned_data:
                continue
            purchase_request_forms.append(
                (form, form.instance.pk is None, set(form.changed_data))
            )
    quotation_forms = []
    if isinstance(parent, PurchaseQuotation):
        for form in formset.forms:
            cleaned_data = getattr(form, "cleaned_data", None)
            if not cleaned_data:
                continue
            quotation_forms.append(
                (
                    form,
                    form.instance.pk is None,
                    set(form.changed_data),
                    form.instance.free_product_source_line_id,
                )
            )
    purchase_order_forms = []
    if isinstance(parent, PurchaseOrder):
        existing_line_ids = [
            form.instance.pk
            for form in formset.forms
            if form.instance.pk
        ]
        existing_provenance = {
            row.order_line_id: row
            for row in PurchaseOrderLinePricingProvenance.objects.filter(
                order_line_id__in=existing_line_ids
            ).only(
                "order_line_id",
                "free_product",
                "free_product_source_order_line_id",
            )
        }
        for form in formset.forms:
            cleaned_data = getattr(form, "cleaned_data", None)
            if not cleaned_data:
                continue
            provenance = existing_provenance.get(form.instance.pk)
            purchase_order_forms.append(
                (
                    form,
                    form.instance.pk is None,
                    set(form.changed_data),
                    bool(provenance and provenance.free_product),
                    provenance.free_product_source_order_line_id if provenance else None,
                )
            )
    total_context = (
        suppress_document_total_recalculation("purchase_order")
        if config and config.line_model is PurchaseOrderLine
        else nullcontext()
    )
    with suspend_purchase_line_merge(), total_context:
        formset.save()
    if isinstance(parent, PurchaseRequest):
        request_line_values = {}
        for form, is_new, changed_fields in purchase_request_forms:
            cleaned_data = form.cleaned_data
            if cleaned_data.get("DELETE") or not form.instance.pk:
                continue
            request_line_values[form.instance.pk] = {
                field: cleaned_data.get(field)
                for field in ("expected_date", "expiry_date", "destination_warehouse")
                if field in cleaned_data and (is_new or field in changed_fields)
            }
        sync_purchase_request_line_timeline(parent, request_line_values)
    if isinstance(parent, PurchaseQuotation):
        selected_line_ids = set()
        manual_price_line_ids = set()
        reset_manual_line_ids = set()
        price_driver_fields = {"request_line", "product", "quantity", "unit_price"}
        reset_fields = {"request_line", "product"}
        for form, is_new, changed_fields, original_source_line_id in quotation_forms:
            if form.cleaned_data.get("DELETE"):
                if original_source_line_id:
                    selected_line_ids.add(original_source_line_id)
                continue
            line = form.instance
            if line.free_product:
                if changed_fields and line.free_product_source_line_id:
                    selected_line_ids.add(line.free_product_source_line_id)
                continue
            if not is_new and not changed_fields.intersection(price_driver_fields):
                continue
            selected_line_ids.add(line.pk)
            if is_new:
                if "unit_price" in changed_fields and line.unit_price > 0:
                    manual_price_line_ids.add(line.pk)
            elif changed_fields.intersection(reset_fields):
                reset_manual_line_ids.add(line.pk)
            elif "unit_price" in changed_fields:
                manual_price_line_ids.add(line.pk)
        update_purchase_quotation_prices(
            parent,
            line_ids=selected_line_ids,
            manual_price_line_ids=manual_price_line_ids,
            reset_manual_line_ids=reset_manual_line_ids,
        )
    purchase_order_price_args = None
    purchase_order_line_values = {}
    if isinstance(parent, PurchaseOrder):
        selected_line_ids = set()
        manual_price_line_ids = set()
        reset_manual_line_ids = set()
        price_driver_fields = {"product", "quantity", "unit_price"}
        for form, is_new, changed_fields, was_free_product, original_source_line_id in purchase_order_forms:
            if form.cleaned_data.get("DELETE"):
                if was_free_product and original_source_line_id:
                    selected_line_ids.add(original_source_line_id)
                continue
            line = form.instance
            if was_free_product:
                if changed_fields and original_source_line_id:
                    selected_line_ids.add(original_source_line_id)
                continue
            purchase_order_line_values[line.pk] = {
                field: form.cleaned_data.get(field)
                for field in (
                    "rent_duration",
                    "rental_period",
                    "expected_date",
                    "expiry_date",
                    "cost_center",
                    "cost_center_mode",
                    "cost_center_distribution_ids",
                    "cost_center_distribution_data",
                    "cost_center_analytic_tag_ids",
                )
                if field in form.cleaned_data and (is_new or field in changed_fields)
            }
            if not is_new and not changed_fields.intersection(price_driver_fields):
                continue
            selected_line_ids.add(line.pk)
            if is_new:
                if "unit_price" in changed_fields and line.unit_price > 0:
                    manual_price_line_ids.add(line.pk)
            elif "product" in changed_fields:
                reset_manual_line_ids.add(line.pk)
            elif "unit_price" in changed_fields:
                manual_price_line_ids.add(line.pk)
        purchase_order_price_args = {
            "line_ids": selected_line_ids,
            "manual_price_line_ids": manual_price_line_ids,
            "reset_manual_line_ids": reset_manual_line_ids,
        }
        update_purchase_order_prices(parent, **purchase_order_price_args)
        sync_purchase_order_rental_controls(parent, purchase_order_line_values)
    merged_count = merge_purchase_document_lines(parent)
    if isinstance(parent, PurchaseOrder) and merged_count:
        update_purchase_order_prices(parent, **purchase_order_price_args)
        sync_purchase_order_rental_controls(parent)
    if isinstance(parent, BlanketOrder):
        recalculate_blanket_order_totals(parent)
    elif isinstance(parent, PurchaseQuotation):
        recalculate_purchase_quotation_total(parent)
    elif isinstance(parent, PurchaseOrder):
        recalculate_purchase_order_total(parent)


def _merge_saved_purchase_line(sender, instance, raw=False, **kwargs):
    if raw or _line_merge_suspended.get():
        return
    config = _CONFIG_BY_LINE_MODEL[sender]
    parent_id = getattr(instance, f"{config.parent_field}_id")
    parent = config.parent_model(pk=parent_id)
    merge_purchase_document_lines(parent, preferred_line_id=instance.pk)


def register_purchase_line_merge():
    for config in LINE_MERGE_CONFIGS:
        post_save.connect(
            _merge_saved_purchase_line,
            sender=config.line_model,
            dispatch_uid=f"gsshop_purchase.merge_{config.line_model._meta.label_lower}",
        )

    for entity in (
        "purchase-requests",
        "purchase-quotations",
        "purchase-tenders",
        "blanket-orders",
        "purchases",
    ):
        register_document_option(entity, "line_limit", MAX_PURCHASE_DOCUMENT_LINES)
        register_document_option(entity, "line_saver", save_and_merge_purchase_document_lines)
