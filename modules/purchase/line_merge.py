"""HMX-compatible, bounded line folding for Purchase documents."""

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
    PurchaseQuotation,
    PurchaseQuotationLine,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseTender,
    PurchaseTenderLine,
)
from .services import MAX_PURCHASE_DOCUMENT_LINES


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
            "expiry_date",
            "origin_request_line_id",
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
            "minimum_release_quantity",
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
    if value is None:
        return ""
    return value


def _merge_key(line, config):
    return tuple(_normalized_value(getattr(line, field)) for field in config.key_fields)


def _line_is_eligible(line, config):
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
        lines = list(
            config.line_model.objects.select_for_update()
            .filter(**parent_lookup)
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        )
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
    total_context = (
        suppress_document_total_recalculation("purchase_order")
        if config and config.line_model is PurchaseOrderLine
        else nullcontext()
    )
    with suspend_purchase_line_merge(), total_context:
        formset.save()
    merge_purchase_document_lines(parent)


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
