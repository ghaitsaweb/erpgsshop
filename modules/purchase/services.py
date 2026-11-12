"""Transactional business effects for procurement workflows."""

import csv
import io
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import reduce
from itertools import chain
from operator import or_
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Avg, Case, Count, DecimalField, DurationField, ExpressionWrapper, F, Max, Prefetch, Q, Sum, Value, When
from django.utils import timezone

from core.models import Contact, Invoice, InvoiceLine, Product, PurchaseOrder, PurchaseOrderLine, StockMove

from .access import PURCHASE_MANAGER
from .models import (
    ApprovalStatus,
    AwardStrategy,
    BlanketOrder,
    BlanketOrderLine,
    DirectPurchase,
    DirectPurchaseLine,
    GoodsReceipt,
    GoodsReceiptLine,
    ProductBudget,
    ProductBudgetMovement,
    PurchaseAccountingDocument,
    PurchaseBillLineProvenance,
    PurchaseInventoryTransfer,
    PurchaseInventoryTransferLine,
    PurchaseInvoiceAccounting,
    PurchaseInvoiceLineProvenance,
    PurchaseKPITarget,
    PurchaseKPITargetLine,
    PurchaseLegalDocument,
    PurchaseMilestoneTemplate,
    PurchaseOrderControl,
    PurchaseOrderInvoiceAccounting,
    PurchaseOrderMilestone,
    PurchaseOrderTemplate,
    PurchaseOrderTemplateLine,
    PurchaseOrderType,
    PurchaseOrderTypeSequenceLine,
    PurchaseQuotation,
    PurchaseQuotationLine,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseReturnRequest,
    PurchaseSetting,
    PurchaseTender,
    PurchaseTenderLine,
    ServiceWorkOrder,
    ServiceWorkOrderLine,
    TenderAwardAllocation,
    TenderAwardResponse,
    TenderBid,
    TenderDocument,
    TenderDocumentCompletionLine,
    TenderDocumentCompletionMatrix,
    VendorDocumentReview,
    VendorLegalDocument,
    VendorPricelist,
    VendorPricelistAmendment,
    VendorPricelistAmendmentLine,
    VendorPricelistImportLog,
    VendorPricelistImportPreview,
    VendorPricelistItem,
    VendorPricelistTier,
    VendorPerformanceEvaluation,
    VendorPerformanceEvaluationLine,
    VendorPerformanceScoreHistory,
    VendorPerformanceTemplate,
    VendorPerformanceTemplateLine,
    VendorPrequalification,
    VendorProfile,
)
from .three_way_match import sync_purchase_three_way_match
from .vendor_credit import enforce_vendor_purchase_limit
from .vendor_pricelist_eligibility import validate_vendor_pricelist_product_eligibility
from .purchase_revision import revise_purchase_order


ZERO = Decimal("0")
HUNDRED = Decimal("100")
MAX_PURCHASE_DOCUMENT_LINES = 500
MAX_GOODS_RECEIPTS_PER_ORDER = 500
MAX_PURCHASE_RECEIPT_HISTORY_LINES = 5000
MAX_RFQ_VENDORS = 50
MAX_TENDER_AWARD_ALLOCATIONS = 5000
MAX_PURCHASE_DOWNPAYMENT_INVOICES = 100
MAX_PURCHASE_DOWNPAYMENT_LINES = 5000
MAX_VENDOR_PRICELIST_MATCHES = 5000
MAX_VENDOR_PRICELIST_EXPIRY_BATCH = 500
MAX_VENDOR_PERFORMANCE_LINES = 500
MAX_VENDOR_PERFORMANCE_EVIDENCE_LINES = 5000
MAX_VENDOR_LEGAL_DOCUMENTS = 500
MAX_VENDOR_LEGAL_DOCUMENT_REQUIREMENTS = 500
MAX_VENDOR_LEGAL_DOCUMENT_EXPIRY_BATCH = 500
MAX_PREQUALIFICATION_DOCUMENT_LINES = 500
MAX_TENDER_COMPLETION_VENDORS = 500
MAX_TENDER_COMPLETION_DOCUMENTS = 100
MAX_TENDER_COMPLETION_CELLS = 10000
MAX_VENDOR_PRICELIST_IMPORT_ROWS = 5000
MAX_VENDOR_PRICELIST_PREVIEW_ROWS = 500
MAX_PURCHASE_KPI_LINES = 100


_PR_LINE_STATUS_RANK = {
    PurchaseRequestLine.Status.DRAFT: 0,
    PurchaseRequestLine.Status.PENDING: 1,
    PurchaseRequestLine.Status.REJECTED: 2,
    PurchaseRequestLine.Status.PARTIALLY_APPROVED: 3,
    PurchaseRequestLine.Status.APPROVED: 4,
    PurchaseRequestLine.Status.PARTIALLY_RFQ: 5,
    PurchaseRequestLine.Status.IN_PROGRESS: 5,
    PurchaseRequestLine.Status.RFQ: 6,
    PurchaseRequestLine.Status.PARTIALLY_ORDERED: 7,
    PurchaseRequestLine.Status.FULLY_ORDERED: 8,
    PurchaseRequestLine.Status.CLOSED: 11,
    PurchaseRequestLine.Status.EXPIRED: 11,
}


def _pk(record_or_pk):
    return getattr(record_or_pk, "pk", record_or_pk)


def _purchase_kpi_order_queryset(target):
    queryset = PurchaseOrder.objects.filter(
        company_id=target.company_id,
        order_date__gte=target.period_start,
        order_date__lte=target.period_end,
        status__in=[
            PurchaseOrder.Status.CONFIRMED,
            PurchaseOrder.Status.COMPLETED,
            PurchaseOrder.Status.CLOSED,
        ],
    )
    if target.target_scope == PurchaseKPITarget.Scope.TEAM:
        return queryset.filter(purchase_control__team_id=target.team_id)
    return queryset.filter(purchase_control__assigned_to_id=target.buyer_id)


def _duration_days(value):
    if not value:
        return ZERO
    return (Decimal(str(value.total_seconds())) / Decimal("86400")).quantize(Decimal("0.01"))


PURCHASE_SEQUENCE_FIELDS = {
    "purchase_request": ("PR", "purchase_request_next"),
    "purchase_tender": ("PT", "purchase_tender_next"),
    "blanket_order": ("BO", "blanket_order_next"),
    "rfq": ("RFQ", "rfq_next"),
    "purchase_order_draft": ("POD", "purchase_order_draft_next"),
    "direct_purchase_order": ("DPO", "direct_purchase_order_next"),
    "purchase_order": ("PO", "purchase_order_next"),
}


@transaction.atomic
def allocate_purchase_document_numbers(order_type_id, company_id, document_kind, count=1):
    sequence_definition = PURCHASE_SEQUENCE_FIELDS.get(document_kind)
    if not sequence_definition:
        raise ValidationError("Unsupported Purchase document sequence kind.")
    if count < 1 or count > MAX_RFQ_VENDORS:
        raise ValidationError(f"Purchase sequence allocation supports between 1 and {MAX_RFQ_VENDORS} numbers.")
    order_type = PurchaseOrderType.objects.select_for_update().get(pk=order_type_id)
    if order_type.company_id != company_id and not order_type.companies.filter(pk=company_id).exists():
        raise ValidationError("Purchase Order Type is not available to the selected company.")
    company = order_type.company if order_type.company_id == company_id else order_type.companies.get(pk=company_id)
    sequence, _created = PurchaseOrderTypeSequenceLine.objects.select_for_update().get_or_create(
        order_type=order_type,
        company=company,
    )
    period = timezone.localdate().strftime("%Y-%m")
    if sequence.period != period:
        sequence.period = period
        for _prefix, field_name in PURCHASE_SEQUENCE_FIELDS.values():
            setattr(sequence, field_name, 1)
    document_prefix, next_field = sequence_definition
    next_number = getattr(sequence, next_field)
    setattr(sequence, next_field, next_number + count)
    sequence.save(update_fields=["period", next_field, "updated_at"])
    return [
        (
            f"{document_prefix}/{order_type.prefix_sequence}/{company.code}/"
            f"{timezone.localdate():%Y/%m}/{number:03d}"
        )
        for number in range(next_number, next_number + count)
    ]


def allocate_purchase_document_number(order_type_id, company_id, document_kind):
    return allocate_purchase_document_numbers(order_type_id, company_id, document_kind, count=1)[0]


@transaction.atomic
def apply_purchase_order_template(order, template, actor=None):
    del actor
    locked = PurchaseOrder.objects.select_for_update(of=("self",)).get(pk=_pk(order))
    if locked.status not in {PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.REVISED}:
        raise ValidationError("Order Template can only be applied while the Purchase Order is Draft.")
    template = PurchaseOrderTemplate.objects.select_related("vendor", "order_type").get(pk=_pk(template))
    if template.company_id != locked.company_id:
        raise ValidationError("Order Template must belong to the Purchase Order company.")
    template_lines = _require_bounded(
        list(
            PurchaseOrderTemplateLine.objects.filter(order_template_id=template.pk)
            .select_related("product")
            .order_by("sequence", "pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase Order Template",
    )
    if not template_lines:
        raise ValidationError("Order Template must contain at least one product line.")
    PurchaseOrderLine.objects.filter(order_id=locked.pk).delete()
    PurchaseOrderLine.objects.bulk_create(
        [
            PurchaseOrderLine(
                order=locked,
                product_id=line.product_id,
                description=line.description or line.product.name,
                warehouse_id=locked.warehouse_id,
                quantity=line.quantity,
                unit_price=line.price,
            )
            for line in template_lines
        ],
        batch_size=500,
    )
    if template.vendor_id:
        locked.vendor_id = template.vendor_id
    locked.total = sum((line.quantity * line.price for line in template_lines), ZERO)
    locked.save(update_fields=["vendor", "total", "updated_at"])
    control, _created = PurchaseOrderControl.objects.get_or_create(order_id=locked.pk)
    control.order_template = template
    if template.order_type_id:
        control.order_type_id = template.order_type_id
    control.save(update_fields=["order_template", "order_type", "updated_at"])
    return locked


@transaction.atomic
def apply_purchase_milestone_template(order, template, actor=None):
    del actor
    locked = PurchaseOrder.objects.select_for_update(of=("self",)).get(pk=_pk(order))
    if locked.status not in {PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.REVISED}:
        raise ValidationError("Milestone lines can only be changed while the Purchase Order is Draft.")
    template = PurchaseMilestoneTemplate.objects.get(pk=_pk(template))
    if template.company_id != locked.company_id:
        raise ValidationError("Milestone Template must belong to the Purchase Order company.")
    template.validate_ready()
    template_lines = list(template.lines.order_by("sequence", "pk")[:101])
    PurchaseOrderMilestone.objects.filter(order_id=locked.pk).delete()
    PurchaseOrderMilestone.objects.bulk_create(
        [
            PurchaseOrderMilestone(
                order=locked,
                sequence=line.sequence,
                name=line.milestone_name,
                due_date=locked.order_date + timedelta(days=line.due_days),
                percentage=line.contract_term_percentage,
            )
            for line in template_lines
        ],
        batch_size=100,
    )
    control, _created = PurchaseOrderControl.objects.get_or_create(order_id=locked.pk)
    control.milestone_template = template
    control.save(update_fields=["milestone_template", "updated_at"])
    order_lines = list(
        PurchaseOrderLine.objects.filter(order_id=locked.pk)
        .select_related("product")
        .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
    )
    if len(order_lines) <= MAX_PURCHASE_DOCUMENT_LINES:
        _sync_service_work_orders_locked(locked, order_lines, activate=False)
    return locked


@transaction.atomic
def recalculate_purchase_kpi_target(target, actor=None):
    locked = PurchaseKPITarget.objects.select_for_update(of=("self",)).get(pk=_pk(target))
    if locked.status not in {PurchaseKPITarget.Status.ACTIVE, PurchaseKPITarget.Status.CLOSED}:
        raise ValidationError("Only an Active or Closed KPI Target can calculate results.")
    lines = _require_bounded(
        list(
            PurchaseKPITargetLine.objects.select_for_update(of=("self",))
            .filter(target_id=locked.pk)
            .order_by("sequence", "pk")[: MAX_PURCHASE_KPI_LINES + 1]
        ),
        limit=MAX_PURCHASE_KPI_LINES,
        label="Purchase KPI Target",
    )
    if not lines:
        raise ValidationError("A KPI Target must contain at least one KPI line.")

    orders = _purchase_kpi_order_queryset(locked)
    order_count = orders.order_by().count()
    control_filter = PurchaseOrderControl.objects.filter(order_id__in=orders.order_by().values("pk"))
    line_filter = PurchaseOrderLine.objects.filter(order_id__in=orders.order_by().values("pk"))
    receipt_summary = line_filter.order_by().aggregate(
        received=Count("pk", filter=Q(received_quantity__gt=0)),
        compliant=Count("pk", filter=Q(received_quantity__gt=0, received_quantity__gte=F("quantity"))),
    )
    timing_summary = control_filter.order_by().aggregate(
        on_time=Count(
            "pk",
            filter=Q(
                confirmed_at__isnull=False,
                quotation__expected_date__isnull=False,
                confirmed_at__date__lte=F("quotation__expected_date"),
            ),
        ),
        timed=Count("pk", filter=Q(confirmed_at__isnull=False)),
        cycle=Avg(
            ExpressionWrapper(F("confirmed_at") - F("order__created_at"), output_field=DurationField()),
            filter=Q(confirmed_at__isnull=False),
        ),
        pr_cycle=Avg(
            ExpressionWrapper(
                F("order__created_at") - F("quotation__request__updated_at"),
                output_field=DurationField(),
            ),
            filter=Q(quotation__request__isnull=False),
        ),
    )
    quote_lines = PurchaseQuotationLine.objects.filter(
        quotation__orders__order_id__in=orders.order_by().values("pk"),
        request_line__isnull=False,
    )
    money_field = DecimalField(max_digits=24, decimal_places=4)
    price_summary = quote_lines.order_by().aggregate(
        budget=Sum(
            ExpressionWrapper(F("quantity") * F("request_line__estimated_unit_price"), output_field=money_field)
        ),
        actual=Sum(ExpressionWrapper(F("quantity") * F("unit_price"), output_field=money_field)),
    )
    budget = price_summary["budget"] or ZERO
    actual = price_summary["actual"] or ZERO
    savings = ((budget - actual) / budget * HUNDRED).quantize(Decimal("0.01")) if budget else ZERO
    received = receipt_summary["received"] or 0
    values = {
        "po_count": Decimal(order_count),
        "supplier_compliance": (
            Decimal(receipt_summary["compliant"] or 0) / Decimal(received) * HUNDRED
        ).quantize(Decimal("0.01")) if received else ZERO,
        "savings_percent": savings,
        "purchase_price_variance": -savings,
        "on_time_po_rate": (
            Decimal(timing_summary["on_time"] or 0) / Decimal(timing_summary["timed"] or 1) * HUNDRED
        ).quantize(Decimal("0.01")) if timing_summary["timed"] else ZERO,
        "cycle_time": _duration_days(timing_summary["cycle"]),
        "pr_to_po_cycle_time": max(_duration_days(timing_summary["pr_cycle"]), ZERO),
    }
    now = timezone.now()
    for line in lines:
        line.actual_value = values.get(line.metric, ZERO)
        line.calculation_snapshot = {
            "metric": line.metric,
            "order_count": order_count,
            "received_line_count": received,
            "calculated_at": now.isoformat(),
        }
        line.last_calculated_by = actor
        line.last_calculated_at = now
        line.updated_at = now
    PurchaseKPITargetLine.objects.bulk_update(
        lines,
        [
            "actual_value",
            "calculation_snapshot",
            "last_calculated_by",
            "last_calculated_at",
            "updated_at",
        ],
        batch_size=100,
    )
    locked._kpi_lines = lines
    return locked


@transaction.atomic
def transition_purchase_kpi_target(target, action, actor=None):
    locked = PurchaseKPITarget.objects.select_for_update(of=("self",)).select_related(
        "company", "team", "template"
    ).get(pk=_pk(target))
    transitions = {
        "submit": (PurchaseKPITarget.Status.DRAFT, PurchaseKPITarget.Status.WAITING_APPROVAL),
        "approve": (PurchaseKPITarget.Status.WAITING_APPROVAL, PurchaseKPITarget.Status.ACTIVE),
        "reject": (PurchaseKPITarget.Status.WAITING_APPROVAL, PurchaseKPITarget.Status.REJECTED),
        "close": (PurchaseKPITarget.Status.ACTIVE, PurchaseKPITarget.Status.CLOSED),
    }
    if action == "calculate":
        return recalculate_purchase_kpi_target(locked, actor)
    if action not in transitions:
        raise ValidationError("Unsupported Purchase KPI Target workflow action.")
    source, destination = transitions[action]
    if locked.status != source:
        raise ValidationError("That KPI Target action is not available in the current state.")
    if action in {"submit", "approve"}:
        locked.validate_ready()
    if action == "close":
        recalculate_purchase_kpi_target(locked, actor)
        locked.closed_by = actor
        locked.closed_at = timezone.now()
    elif action == "approve":
        locked.activated_by = actor
        locked.activated_at = timezone.now()
    locked.status = destination
    update_fields = ["status", "updated_at"]
    if action == "close":
        update_fields.extend(["closed_by", "closed_at"])
    elif action == "approve":
        update_fields.extend(["activated_by", "activated_at"])
    locked.save(update_fields=update_fields)
    if action == "approve":
        PurchaseKPITargetLine.objects.filter(
            target_id=locked.pk,
            activated_at__isnull=True,
        ).update(
            activated_by=actor,
            activated_at=locked.activated_at,
            updated_at=locked.activated_at,
        )
    return locked


def _stockable(line):
    return line.product.product_type == Product.ProductType.STOCKABLE


def _require_bounded(rows, *, limit, label):
    if len(rows) > limit:
        raise ValidationError(f"{label} exceeds the operational limit of {limit} rows.")
    return rows


def _request_line_progress_status(line):
    base = line.approved_quantity if line.approved_quantity > 0 else line.quantity
    if line.line_status in {PurchaseRequestLine.Status.REJECTED, PurchaseRequestLine.Status.CLOSED}:
        return line.line_status
    if line.ordered_quantity > 0:
        return (
            PurchaseRequestLine.Status.FULLY_ORDERED
            if line.ordered_quantity >= base
            else PurchaseRequestLine.Status.PARTIALLY_ORDERED
        )
    if line.direct_purchase_quantity > 0:
        return (
            PurchaseRequestLine.Status.FULLY_ORDERED
            if line.direct_purchase_quantity >= base
            else PurchaseRequestLine.Status.PARTIALLY_ORDERED
        )
    if line.rfq_quantity > 0:
        return (
            PurchaseRequestLine.Status.RFQ
            if line.rfq_quantity >= base
            else PurchaseRequestLine.Status.PARTIALLY_RFQ
        )
    if line.tender_quantity > 0:
        return PurchaseRequestLine.Status.IN_PROGRESS
    if line.approved_quantity > 0:
        return (
            PurchaseRequestLine.Status.APPROVED
            if line.approved_quantity >= line.quantity
            else PurchaseRequestLine.Status.PARTIALLY_APPROVED
        )
    return line.line_status


def _advance_request_line_status(line):
    target = _request_line_progress_status(line)
    if _PR_LINE_STATUS_RANK.get(target, 0) >= _PR_LINE_STATUS_RANK.get(line.line_status, 0):
        line.line_status = target
    return line


def purchase_request_before_workflow(*, instance, action, actor):
    """Mirror HMX line-level state changes around the generic PR approval shell."""
    del actor
    line_queryset = PurchaseRequestLine.objects.filter(request_id=instance.pk)
    line_rows = _require_bounded(
        list(
            line_queryset.order_by("pk").values(
                "pk",
                "quantity",
                "estimated_unit_price",
            )[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase Request",
    )
    if not line_rows:
        raise ValidationError("Add at least one product line before continuing.")
    if action == "submit":
        total_amount = sum(
            (row["quantity"] * row["estimated_unit_price"] for row in line_rows),
            ZERO,
        )
        if total_amount < Decimal("1"):
            raise ValidationError("Purchase Request total estimated cost must be at least 1 before approval.")
    line_queryset = line_queryset.filter(pk__in=[row["pk"] for row in line_rows])
    now = timezone.now()
    if action == "submit":
        line_queryset.update(
            line_status=PurchaseRequestLine.Status.PENDING,
            approved_quantity=ZERO,
            updated_at=now,
        )
    elif action == "approve":
        line_queryset.update(
            approved_quantity=Case(
                When(approved_quantity__lte=0, then=F("quantity")),
                default=F("approved_quantity"),
                output_field=DecimalField(max_digits=18, decimal_places=3),
            ),
            line_status=Case(
                When(
                    approved_quantity__gt=0,
                    approved_quantity__lt=F("quantity"),
                    then=Value(PurchaseRequestLine.Status.PARTIALLY_APPROVED),
                ),
                default=Value(PurchaseRequestLine.Status.APPROVED),
                output_field=models.CharField(max_length=24),
            ),
            updated_at=now,
        )
    elif action == "reject":
        line_queryset.update(
            line_status=PurchaseRequestLine.Status.REJECTED,
            approved_quantity=ZERO,
            updated_at=now,
        )
    elif action == "reset":
        line_queryset.update(
            line_status=PurchaseRequestLine.Status.DRAFT,
            approved_quantity=ZERO,
            rfq_quantity=ZERO,
            blanket_quantity=ZERO,
            tender_quantity=ZERO,
            direct_purchase_quantity=ZERO,
            ordered_quantity=ZERO,
            updated_at=now,
        )
    elif action == "close":
        line_queryset.exclude(
            line_status__in=[PurchaseRequestLine.Status.REJECTED, PurchaseRequestLine.Status.EXPIRED]
        ).update(line_status=PurchaseRequestLine.Status.CLOSED, updated_at=now)


def register_purchase_workflow_hooks():
    from modules.apphub.workflow_hooks import register_before_workflow_hook

    for action in ("submit", "approve", "reject", "reset", "close"):
        register_before_workflow_hook(
            "gsshop_purchase.purchaserequest",
            action,
            purchase_request_before_workflow,
        )
    register_before_workflow_hook(
        "core.purchaseorder",
        "request-approval",
        purchase_order_before_workflow,
    )


def purchase_order_before_workflow(*, instance, action, actor=None):
    del actor
    if action != "request-approval":
        return
    lines = _require_bounded(
        list(
            PurchaseOrderLine.objects.filter(order_id=instance.pk)
            .select_related("product")
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase order",
    )
    validate_vendor_pricelist_product_eligibility(
        document=instance,
        lines=lines,
        transaction_date=instance.order_date,
    )
    order_total = sum((line.subtotal for line in lines), ZERO)
    enforce_vendor_purchase_limit(order=instance, order_total=order_total)
    instance.total = order_total
    instance.save(update_fields=["total", "updated_at"])


@transaction.atomic
def approve_purchase_request_lines(purchase_request, *, decisions, actor=None, reason=""):
    from modules.approval_workflow.adapters import apply_approval_result
    from modules.approval_workflow.services import (
        ensure_legacy_pending_approval,
        pending_approval_for,
        perform_approval_action,
    )

    locked = PurchaseRequest.objects.select_for_update(of=("self",)).get(pk=_pk(purchase_request))
    if locked.status not in {PurchaseRequest.Status.PENDING, PurchaseRequest.Status.PARTIALLY_APPROVED}:
        raise ValidationError("Only a pending Purchase Request can be approved.")
    lines = _require_bounded(
        list(
            PurchaseRequestLine.objects.select_for_update(of=("self",))
            .filter(request_id=locked.pk)
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase Request approval",
    )
    decision_map = {int(row["line_id"]): row for row in decisions}
    if not lines or set(decision_map) != {line.pk for line in lines}:
        raise ValidationError("Approval decisions are required for every Purchase Request line.")
    now = timezone.now()
    for line in lines:
        decision = decision_map[line.pk]
        action = decision.get("action")
        reason = str(decision.get("reason") or "").strip()
        if action == "approve":
            approved_quantity = Decimal(str(decision.get("approved_quantity") or 0))
            if approved_quantity <= 0:
                raise ValidationError("Approved quantity must be greater than zero for approved lines.")
            if approved_quantity != line.quantity and len(reason) < 10:
                raise ValidationError("Reason must be at least 10 characters when approved quantity is amended.")
            line.approved_quantity = approved_quantity
            line.approval_action = "approve"
            line.approval_justification = reason
            line.rejection_reason = ""
            line.line_status = (
                PurchaseRequestLine.Status.APPROVED
                if approved_quantity >= line.quantity
                else PurchaseRequestLine.Status.PARTIALLY_APPROVED
            )
        elif action == "reject":
            if len(reason) < 10:
                raise ValidationError("Reason must be at least 10 characters for rejected lines.")
            line.approved_quantity = ZERO
            line.approval_action = "reject"
            line.approval_justification = reason
            line.rejection_reason = ""
            line.line_status = PurchaseRequestLine.Status.REJECTED
        else:
            raise ValidationError("Each Purchase Request line must be approved or rejected.")
        line.updated_at = now
    approval = pending_approval_for(locked)
    if approval is None:
        approval = ensure_legacy_pending_approval(
            locked,
            actor,
            entity="purchase-requests",
        )
    outcome = perform_approval_action(
        approval,
        actor,
        "approve",
        reason=reason,
        apply_result=lambda target, result_action: apply_approval_result(
            target,
            result_action,
            actor,
        ),
    )
    PurchaseRequestLine.objects.bulk_update(
        lines,
        [
            "approved_quantity",
            "approval_action",
            "approval_justification",
            "rejection_reason",
            "line_status",
            "updated_at",
        ],
        batch_size=500,
    )
    locked.refresh_from_db(fields=["status", "updated_at"])
    locked._approval_advanced = not outcome.completed
    locked._approval_instance_id = outcome.instance.pk
    return locked


@transaction.atomic
def create_purchase_request_re_request(purchase_request, *, allocations, actor=None):
    locked = (
        PurchaseRequest.objects.select_for_update(of=("self",))
        .select_related("requestor")
        .get(pk=_pk(purchase_request))
    )
    if locked.status not in {
        PurchaseRequest.Status.APPROVED,
        PurchaseRequest.Status.PARTIALLY_RFQ,
        PurchaseRequest.Status.RFQ,
        PurchaseRequest.Status.PARTIALLY_ORDERED,
        PurchaseRequest.Status.IN_PROGRESS,
    }:
        raise ValidationError("This Purchase Request is not available for Re-Request.")
    allocation_map = {
        int(row["line_id"]): Decimal(str(row["quantity"]))
        for row in allocations
        if Decimal(str(row.get("quantity") or 0)) > 0
    }
    if not allocation_map:
        raise ValidationError("Select at least one Purchase Request line to Re-Request.")
    lines = list(
        PurchaseRequestLine.objects.select_for_update(of=("self",))
        .filter(request_id=locked.pk, pk__in=allocation_map)
        .select_related("product")
        .order_by("pk")
    )
    if len(lines) != len(allocation_map):
        raise ValidationError("Re-Request contains a line outside this Purchase Request.")
    for line in lines:
        if allocation_map[line.pk] > line.remaining_to_re_request:
            raise ValidationError(
                f"Re-Request quantity cannot exceed remaining quantity ({line.remaining_to_re_request}) "
                f"for {line.product or line.description}."
            )
    suffix = uuid4().hex[:8].upper()
    new_request = PurchaseRequest.objects.create(
        company_id=locked.company_id,
        number=f"PR-RR-{timezone.localdate():%Y%m%d}-{suffix}",
        requestor=actor or locked.requestor,
        request_date=timezone.localdate(),
        required_date=locked.required_date,
        expiry_date=locked.expiry_date,
        destination_warehouse_id=locked.destination_warehouse_id,
        currency=locked.currency,
        justification=f"Re-Request from {locked.number}: {locked.justification}",
        notes=f"Created from Purchase Request {locked.number}.",
        status=PurchaseRequest.Status.DRAFT,
    )
    PurchaseRequestLine.objects.bulk_create(
        [
            PurchaseRequestLine(
                request=new_request,
                origin_request_line=line,
                product_id=line.product_id,
                description=line.description,
                specification=line.specification,
                quantity=allocation_map[line.pk],
                estimated_unit_price=line.estimated_unit_price,
                expiry_date=new_request.expiry_date,
                line_status=PurchaseRequestLine.Status.DRAFT,
            )
            for line in lines
        ],
        batch_size=500,
    )
    now = timezone.now()
    for line in lines:
        line.re_request_quantity += allocation_map[line.pk]
        line.updated_at = now
    PurchaseRequestLine.objects.bulk_update(
        lines,
        ["re_request_quantity", "updated_at"],
        batch_size=500,
    )
    return new_request


def _validate_rfq_vendors(company_id, vendor_ids):
    vendor_ids = list(dict.fromkeys(vendor_ids))
    if not vendor_ids:
        raise ValidationError("Vendor is required to create RFQ.")
    if len(vendor_ids) > MAX_RFQ_VENDORS:
        raise ValidationError(f"RFQ vendor selection exceeds the operational limit of {MAX_RFQ_VENDORS} vendors.")
    vendors = Contact.objects.filter(pk__in=vendor_ids, company_id=company_id).in_bulk()
    if set(vendors) != set(vendor_ids):
        raise ValidationError("Every RFQ vendor must belong to the purchase request company.")

    profiles = VendorProfile.objects.filter(
        company_id=company_id,
        partner_id__in=vendor_ids,
    ).in_bulk(field_name="partner_id")
    setting = PurchaseSetting.objects.filter(company_id=company_id).only(
        "require_vendor_prequalification",
        "rfq_vendor_eligibility",
    ).first()
    policy = (
        setting.rfq_vendor_eligibility
        if setting
        else PurchaseSetting.RFQVendorEligibility.APPROVED_ONLY
    )
    eligible_statuses = (
        {
            VendorProfile.Status.DRAFT,
            VendorProfile.Status.WAITING_APPROVAL,
            VendorProfile.Status.ACTIVE,
        }
        if policy == PurchaseSetting.RFQVendorEligibility.ALLOW_DRAFT
        else {VendorProfile.Status.ACTIVE}
    )
    blocked = [
        vendors[vendor_id].name
        for vendor_id in vendor_ids
        if vendor_id in profiles
        and (
            not profiles[vendor_id].active
            or profiles[vendor_id].status not in eligible_statuses
        )
    ]
    if blocked:
        raise ValidationError(
            f"RFQ vendor is not eligible under the company approval policy: {', '.join(blocked)}."
        )

    if setting and setting.require_vendor_prequalification:
        active_profile_ids = {
            profile.pk
            for profile in profiles.values()
            if profile.status == VendorProfile.Status.ACTIVE
        }
        approved_partner_ids = set(
            VendorProfile.objects.filter(
                pk__in=active_profile_ids,
                prequalifications__status="approved",
            ).values_list("partner_id", flat=True)
        )
        missing = [vendors[vendor_id].name for vendor_id in vendor_ids if vendor_id not in approved_partner_ids]
        if missing:
            raise ValidationError(
                f"Vendor prequalification is required before RFQ conversion: {', '.join(missing)}."
            )
    return [vendors[vendor_id] for vendor_id in vendor_ids]


def _validate_purchase_vendor_eligibility(company_id, vendor_id):
    """Require a confirmed, active profile at commitment time when a profile exists."""
    profile = (
        VendorProfile.objects.filter(company_id=company_id, partner_id=vendor_id)
        .only("active", "status", "partner_id")
        .first()
    )
    if profile and (not profile.active or profile.status != VendorProfile.Status.ACTIVE):
        raise ValidationError(
            "Vendor must be active and Confirmed before creating a purchasing commitment."
        )
    return profile


def vendor_legal_document_expiry_status(document, *, today=None):
    today = today or timezone.localdate()
    if document.review_status == VendorLegalDocument.ReviewStatus.REJECTED:
        return VendorLegalDocument.Status.REJECTED
    if document.review_status == VendorLegalDocument.ReviewStatus.SUPERSEDED:
        return VendorLegalDocument.Status.SUPERSEDED
    if not document.expiry_date:
        return (
            VendorLegalDocument.Status.MISSING_REQUIRED_EXPIRY
            if document.expiry_required
            else VendorLegalDocument.Status.NO_EXPIRY
        )
    if document.expiry_date < today:
        return VendorLegalDocument.Status.EXPIRED
    if (
        document.reminder_before_expiry_days
        and (document.expiry_date - today).days <= document.reminder_before_expiry_days
    ):
        return VendorLegalDocument.Status.EXPIRING_SOON
    return VendorLegalDocument.Status.VALID


def applicable_purchase_legal_documents(profile):
    """Return the active HMX legal-document contract for one exact vendor."""
    additional_match = Q(
        apply_to__in=[PurchaseLegalDocument.ApplyTo.VENDOR, PurchaseLegalDocument.ApplyTo.BOTH],
        applicable_vendors=profile,
    )
    if profile.category_id:
        additional_match |= Q(
            apply_to__in=[PurchaseLegalDocument.ApplyTo.CATEGORY, PurchaseLegalDocument.ApplyTo.BOTH],
            applicable_categories=profile.category_id,
        )
    additional_scope = Q(
        requirement_type=PurchaseLegalDocument.RequirementType.ADDITIONAL
    ) & additional_match
    return (
        PurchaseLegalDocument.objects.filter(
            company_id=profile.company_id,
            active=True,
            document_usage=PurchaseLegalDocument.DocumentUsage.STANDARD_VENDOR,
        )
        .filter(
            Q(
                requirement_type__in=[
                    PurchaseLegalDocument.RequirementType.REQUIRED,
                    PurchaseLegalDocument.RequirementType.OPTIONAL,
                ]
            )
            | additional_scope
        )
        .distinct()
        .order_by("name", "pk")
    )


def _bounded_vendor_legal_document_requirements(profile):
    return _require_bounded(
        list(
            applicable_purchase_legal_documents(profile).select_related("company")[
                : MAX_VENDOR_LEGAL_DOCUMENT_REQUIREMENTS + 1
            ]
        ),
        limit=MAX_VENDOR_LEGAL_DOCUMENT_REQUIREMENTS,
        label="Applicable Vendor legal-document requirements",
    )


def _vendor_legal_document_defaults(profile, requirement):
    return {
        "vendor": profile,
        "legal_document": requirement,
        "document_type": requirement.name,
        "mandatory": requirement.requirement_type == PurchaseLegalDocument.RequirementType.REQUIRED,
        "requirement_type": requirement.requirement_type,
        "is_blocking": requirement.is_blocking,
        "expiry_required": requirement.expiry_date_required,
        "reminder_before_expiry_days": requirement.reminder_before_expiry_days or 0,
    }


@transaction.atomic
def sync_vendor_legal_document_requirements(profile):
    """Materialize one vendor's applicable master requirements without global fan-out."""
    requirements = _bounded_vendor_legal_document_requirements(profile)
    existing = _require_bounded(
        list(
            VendorLegalDocument.objects.filter(vendor_id=profile.pk)
            .exclude(review_status=VendorLegalDocument.ReviewStatus.SUPERSEDED)
            .select_for_update(of=("self",))
            .select_related("legal_document")
            .order_by("-pk")[: MAX_VENDOR_LEGAL_DOCUMENTS + 1]
        ),
        limit=MAX_VENDOR_LEGAL_DOCUMENTS,
        label="Vendor legal documents",
    )
    existing_requirement_ids = {
        document.legal_document_id for document in existing if document.legal_document_id
    }
    legacy_by_name = {}
    for document in existing:
        if document.legal_document_id or not document.document_type:
            continue
        legacy_by_name.setdefault(document.document_type.casefold(), document)

    linked = []
    created = []
    now = timezone.now()
    for requirement in requirements:
        if requirement.pk in existing_requirement_ids:
            continue
        legacy = legacy_by_name.get(requirement.name.casefold())
        defaults = _vendor_legal_document_defaults(profile, requirement)
        if legacy:
            legacy.legal_document = requirement
            legacy.document_type = defaults["document_type"]
            legacy.mandatory = defaults["mandatory"]
            legacy.requirement_type = defaults["requirement_type"]
            legacy.is_blocking = defaults["is_blocking"]
            legacy.expiry_required = defaults["expiry_required"]
            legacy.reminder_before_expiry_days = defaults["reminder_before_expiry_days"]
            legacy.updated_at = now
            linked.append(legacy)
            continue
        created.append(VendorLegalDocument(**defaults))

    if linked:
        VendorLegalDocument.objects.bulk_update(
            linked,
            [
                "legal_document",
                "document_type",
                "mandatory",
                "requirement_type",
                "is_blocking",
                "expiry_required",
                "reminder_before_expiry_days",
                "updated_at",
            ],
            batch_size=500,
        )
    if created:
        VendorLegalDocument.objects.bulk_create(created, batch_size=500, ignore_conflicts=True)
    return {
        "requirements": len(requirements),
        "linked": len(linked),
        "created": len(created),
    }


def _vendor_blocking_legal_documents(profile_id, *, lock=False):
    queryset = VendorLegalDocument.objects.filter(vendor_id=profile_id).filter(
        Q(is_blocking=True) | Q(mandatory=True)
    )
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return _require_bounded(
        list(queryset.order_by("pk")[: MAX_VENDOR_LEGAL_DOCUMENTS + 1]),
        limit=MAX_VENDOR_LEGAL_DOCUMENTS,
        label="Vendor legal documents",
    )


def _validate_vendor_blocking_legal_documents(profile_id, *, lock=False):
    profile = VendorProfile.objects.select_related("category").get(pk=profile_id)
    sync_vendor_legal_document_requirements(profile)
    invalid = []
    today = timezone.localdate()
    for document in _vendor_blocking_legal_documents(profile_id, lock=lock):
        status = vendor_legal_document_expiry_status(document, today=today)
        if (
            not document.attachment_url
            or document.review_status != VendorLegalDocument.ReviewStatus.APPROVED
            or status
            in {
                VendorLegalDocument.Status.EXPIRED,
                VendorLegalDocument.Status.MISSING_REQUIRED_EXPIRY,
                VendorLegalDocument.Status.REJECTED,
                VendorLegalDocument.Status.SUPERSEDED,
            }
        ):
            invalid.append(document.document_type)
    if invalid:
        raise ValidationError(
            "Upload and approve non-expired blocking legal documents before continuing: "
            + ", ".join(sorted(set(invalid)))
            + "."
        )


@transaction.atomic
def transition_vendor_profile(profile, action, actor=None, *, reason=""):
    locked = VendorProfile.objects.select_for_update(of=("self",)).get(pk=_pk(profile))
    reason = str(reason or "").strip()
    now = timezone.now()
    if action == "submit":
        if locked.status != VendorProfile.Status.DRAFT:
            raise ValidationError("Only a Draft vendor can be submitted for approval.")
        _validate_vendor_blocking_legal_documents(locked.pk, lock=True)
        locked.status = VendorProfile.Status.WAITING_APPROVAL
        locked.rejection_reason = ""
        update_fields = ["status", "rejection_reason", "updated_at"]
    elif action == "approve":
        if locked.status != VendorProfile.Status.WAITING_APPROVAL:
            raise ValidationError("Only a vendor Waiting For Approval can be confirmed.")
        _validate_vendor_blocking_legal_documents(locked.pk, lock=True)
        locked.status = VendorProfile.Status.ACTIVE
        locked.approved_by = actor
        locked.approved_at = now
        locked.rejection_reason = ""
        update_fields = ["status", "approved_by", "approved_at", "rejection_reason", "updated_at"]
    elif action == "reject":
        if locked.status != VendorProfile.Status.WAITING_APPROVAL:
            raise ValidationError("Only a vendor Waiting For Approval can be rejected.")
        if not reason:
            raise ValidationError("A rejection reason is required.")
        locked.status = VendorProfile.Status.REJECTED
        locked.rejection_reason = reason
        update_fields = ["status", "rejection_reason", "updated_at"]
    elif action == "reset":
        if locked.status != VendorProfile.Status.REJECTED:
            raise ValidationError("Only a Rejected vendor can be reset to Draft.")
        locked.status = VendorProfile.Status.DRAFT
        locked.rejection_reason = ""
        update_fields = ["status", "rejection_reason", "updated_at"]
    elif action == "blacklist":
        if locked.status == VendorProfile.Status.BLACKLISTED:
            raise ValidationError("Vendor is already Blacklisted.")
        if not reason:
            raise ValidationError("A blacklist reason is required.")
        locked.status_before_blacklist = locked.status
        locked.status = VendorProfile.Status.BLACKLISTED
        locked.vendor_blacklist_date = now
        locked.vendor_blacklist_reason = reason
        locked.vendor_blacklist_user = actor
        update_fields = [
            "status_before_blacklist",
            "status",
            "vendor_blacklist_date",
            "vendor_blacklist_reason",
            "vendor_blacklist_user",
            "updated_at",
        ]
    elif action == "unblacklist":
        if locked.status != VendorProfile.Status.BLACKLISTED:
            raise ValidationError("Only a Blacklisted vendor can be reinstated.")
        if not reason:
            raise ValidationError("An unblacklist reason is required.")
        valid_statuses = {value for value, _label in VendorProfile.Status.choices}
        restore_status = locked.status_before_blacklist
        if restore_status not in valid_statuses or restore_status == VendorProfile.Status.BLACKLISTED:
            restore_status = VendorProfile.Status.DRAFT
        locked.status = restore_status
        locked.vendor_blacklist_reason = reason
        update_fields = ["status", "vendor_blacklist_reason", "updated_at"]
    else:
        raise ValidationError("Unsupported Vendor workflow action.")
    locked.save(update_fields=update_fields)
    return locked


@transaction.atomic
def transition_vendor_legal_document(document, action, actor=None, *, reason=""):
    locked = (
        VendorLegalDocument.objects.select_for_update(of=("self",))
        .select_related("vendor")
        .get(pk=_pk(document))
    )
    reason = str(reason or "").strip()
    now = timezone.now()
    if action == "submit":
        if locked.review_status not in {
            VendorLegalDocument.ReviewStatus.DRAFT,
            VendorLegalDocument.ReviewStatus.REJECTED,
        }:
            raise ValidationError("Only a Draft or Rejected renewal can be submitted.")
        if not locked.attachment_url:
            raise ValidationError("A legal document attachment is required before submission.")
        locked.full_clean(exclude=["status"])
        locked.review_status = VendorLegalDocument.ReviewStatus.PENDING_REVIEW
        locked.renewal_source = locked.renewal_source or "vendor"
        locked.rejection_reason = ""
        locked.reviewed_by = None
        locked.reviewed_at = None
        update_fields = [
            "review_status",
            "renewal_source",
            "rejection_reason",
            "reviewed_by",
            "reviewed_at",
            "status",
            "updated_at",
        ]
    elif action == "approve":
        if locked.review_status != VendorLegalDocument.ReviewStatus.PENDING_REVIEW:
            raise ValidationError("Only a Pending Review legal document can be approved.")
        locked.full_clean(exclude=["status"])
        previous = (
            VendorLegalDocument.objects.select_for_update(of=("self",))
            .filter(
                vendor_id=locked.vendor_id,
                document_type__iexact=locked.document_type,
                review_status=VendorLegalDocument.ReviewStatus.APPROVED,
            )
            .exclude(pk=locked.pk)
            .order_by("-pk")
            .first()
        )
        if previous:
            previous.review_status = VendorLegalDocument.ReviewStatus.SUPERSEDED
            previous.status = VendorLegalDocument.Status.SUPERSEDED
            previous.save(update_fields=["review_status", "status", "updated_at"])
            locked.previous_version = previous
        locked.review_status = VendorLegalDocument.ReviewStatus.APPROVED
        locked.rejection_reason = ""
        locked.reviewed_by = actor
        locked.reviewed_at = now
        update_fields = [
            "previous_version",
            "review_status",
            "rejection_reason",
            "reviewed_by",
            "reviewed_at",
            "status",
            "updated_at",
        ]
    elif action == "reject":
        if locked.review_status != VendorLegalDocument.ReviewStatus.PENDING_REVIEW:
            raise ValidationError("Only a Pending Review legal document can be rejected.")
        if not reason:
            raise ValidationError("A rejection reason is required.")
        locked.review_status = VendorLegalDocument.ReviewStatus.REJECTED
        locked.rejection_reason = reason
        locked.reviewed_by = actor
        locked.reviewed_at = now
        update_fields = [
            "review_status",
            "rejection_reason",
            "reviewed_by",
            "reviewed_at",
            "status",
            "updated_at",
        ]
    else:
        raise ValidationError("Unsupported Vendor Legal Document workflow action.")
    locked.status = vendor_legal_document_expiry_status(locked)
    locked.save(update_fields=update_fields)
    return locked


def refresh_vendor_legal_document_expiry_statuses(*, after_id=0, limit=MAX_VENDOR_LEGAL_DOCUMENT_EXPIRY_BATCH):
    rows = list(
        VendorLegalDocument.objects.filter(pk__gt=after_id)
        .order_by("pk")[:limit]
    )
    if not rows:
        return {"updated": 0, "last_id": after_id, "has_more": False}
    today = timezone.localdate()
    changed = []
    now = timezone.now()
    for row in rows:
        target = vendor_legal_document_expiry_status(row, today=today)
        if row.status != target:
            row.status = target
            row.updated_at = now
            changed.append(row)
    if changed:
        VendorLegalDocument.objects.bulk_update(changed, ["status", "updated_at"], batch_size=500)
    return {
        "updated": len(changed),
        "last_id": rows[-1].pk,
        "has_more": len(rows) == limit,
    }


def _prequalification_fulfillment(lines):
    if not lines:
        return ZERO
    approved = sum(1 for line in lines if line.status == VendorDocumentReview.Status.ACCEPTED)
    return (Decimal(approved) * HUNDRED / Decimal(len(lines))).quantize(Decimal("0.01"))


def _sync_prequalification_status_locked(prequalification, lines):
    statuses = {line.status for line in lines}
    if not lines or statuses == {VendorDocumentReview.Status.DRAFT}:
        status = VendorPrequalification.Status.DRAFT
    elif VendorDocumentReview.Status.REJECTED in statuses:
        status = VendorPrequalification.Status.REJECTED
    elif VendorDocumentReview.Status.REVISION in statuses:
        status = VendorPrequalification.Status.UNDER_REVIEW
    elif statuses == {VendorDocumentReview.Status.ACCEPTED}:
        status = VendorPrequalification.Status.APPROVED
    elif VendorDocumentReview.Status.ACCEPTED in statuses:
        status = VendorPrequalification.Status.UNDER_REVIEW
    else:
        status = VendorPrequalification.Status.PENDING
    prequalification.status = status
    prequalification.document_fulfillment_percentage = _prequalification_fulfillment(lines)
    prequalification.save(
        update_fields=["status", "document_fulfillment_percentage", "updated_at"]
    )
    return prequalification


def _prequalification_document_lines(prequalification_id, *, lock=False):
    queryset = VendorDocumentReview.objects.filter(prequalification_id=prequalification_id)
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    return _require_bounded(
        list(queryset.order_by("sequence", "pk")[: MAX_PREQUALIFICATION_DOCUMENT_LINES + 1]),
        limit=MAX_PREQUALIFICATION_DOCUMENT_LINES,
        label="Prequalification document requirements",
    )


def _sync_prequalification_requirements_locked(prequalification):
    lines = _prequalification_document_lines(prequalification.pk, lock=True)
    if lines or not prequalification.tender_id:
        return lines
    requirements = _require_bounded(
        list(
            TenderDocument.objects.filter(
                tender_id=prequalification.tender_id,
                vendor__isnull=True,
                bid__isnull=True,
            )
            .order_by("pk")[: MAX_PREQUALIFICATION_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PREQUALIFICATION_DOCUMENT_LINES,
        label="Tender prequalification requirements",
    )
    if not requirements:
        return []
    type_names = [requirement.document_type for requirement in requirements]
    latest_approved_ids = (
        VendorLegalDocument.objects.filter(
            vendor_id=prequalification.vendor_id,
            document_type__in=type_names,
            review_status=VendorLegalDocument.ReviewStatus.APPROVED,
        )
        .values("document_type")
        .annotate(latest_id=Max("pk"))
        .values_list("latest_id", flat=True)
    )
    approved_by_type = {}
    approved_documents = _require_bounded(
        list(
            VendorLegalDocument.objects.filter(pk__in=latest_approved_ids)
            .order_by("pk")[: MAX_PREQUALIFICATION_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PREQUALIFICATION_DOCUMENT_LINES,
        label="Approved Vendor legal documents",
    )
    for row in approved_documents:
        approved_by_type.setdefault(row.document_type.casefold(), row)
    created = VendorDocumentReview.objects.bulk_create(
        [
            VendorDocumentReview(
                prequalification=prequalification,
                sequence=index * 10,
                document_type=requirement.document_type,
                document=approved_by_type.get(requirement.document_type.casefold()),
                attachment_url=(
                    approved_by_type[requirement.document_type.casefold()].attachment_url
                    if requirement.document_type.casefold() in approved_by_type
                    else ""
                ),
                requirement_type=(
                    VendorLegalDocument.RequirementType.REQUIRED
                    if requirement.mandatory
                    else VendorLegalDocument.RequirementType.OPTIONAL
                ),
                status=VendorDocumentReview.Status.DRAFT,
            )
            for index, requirement in enumerate(requirements, start=1)
        ],
        batch_size=500,
    )
    return created


def _tender_completion_line_is_qualified(line):
    document_cells = [cell for cell in (line.document_cells or []) if not cell.get("is_action")]
    return bool(document_cells) and all(
        str(cell.get("line_status") or cell.get("status") or "").lower()
        in {"approved", "accepted", "valid"}
        for cell in document_cells
    )


@transaction.atomic
def sync_tender_document_completion(tender):
    locked = PurchaseTender.objects.select_for_update(of=("self",)).get(pk=_pk(tender))
    if locked.method != PurchaseTender.Method.PQTE:
        TenderDocumentCompletionMatrix.objects.filter(tender_id=locked.pk).delete()
        return None

    requirements = _require_bounded(
        list(
            TenderDocument.objects.filter(
                tender_id=locked.pk,
                vendor__isnull=True,
                bid__isnull=True,
            )
            .select_related("legal_document")
            .order_by("pk")[: MAX_TENDER_COMPLETION_DOCUMENTS + 1]
        ),
        limit=MAX_TENDER_COMPLETION_DOCUMENTS,
        label="Tender Document Completion requirements",
    )
    prequalifications = _require_bounded(
        list(
            VendorPrequalification.objects.filter(tender_id=locked.pk)
            .select_related("vendor__partner")
            .order_by("pk")[: MAX_TENDER_COMPLETION_VENDORS + 1]
        ),
        limit=MAX_TENDER_COMPLETION_VENDORS,
        label="Tender Document Completion vendors",
    )
    if len(requirements) * len(prequalifications) > MAX_TENDER_COMPLETION_CELLS:
        raise ValidationError(
            f"Tender Document Completion exceeds the interactive limit of {MAX_TENDER_COMPLETION_CELLS} cells."
        )

    prequalification_ids = [row.pk for row in prequalifications]
    reviews = _require_bounded(
        list(
            VendorDocumentReview.objects.filter(prequalification_id__in=prequalification_ids)
            .select_related("document")
            .order_by("prequalification_id", "sequence", "pk")[: MAX_TENDER_COMPLETION_CELLS + 1]
        ),
        limit=MAX_TENDER_COMPLETION_CELLS,
        label="Tender Document Completion review lines",
    )
    review_by_key = {}
    for review in reviews:
        review_by_key.setdefault(
            (review.prequalification_id, review.document_type.casefold()),
            review,
        )

    columns = [
        {
            "vendor_key": f"doc_{requirement.pk}",
            "column_key": f"doc_{requirement.pk}",
            "vendor_name": requirement.document_type,
            "column_name": requirement.document_type,
            "vendor_code": "tender_document",
            "column_code": "tender_document",
            "status": "required" if requirement.mandatory else "optional",
        }
        for requirement in requirements
    ]
    columns.append(
        {
            "vendor_key": "action_review",
            "column_key": "action_review",
            "vendor_name": "Pre-Qualification",
            "column_name": "Pre-Qualification",
            "vendor_code": "Action",
            "column_code": "Action",
            "status": "info",
        }
    )
    matrix, _created = TenderDocumentCompletionMatrix.objects.select_for_update().get_or_create(
        tender=locked,
        defaults={"name": f"{locked.number} Document Completion"},
    )
    matrix.name = f"{locked.number} Document Completion"
    matrix.document_columns = columns
    matrix.footer_totals = {}
    matrix.save(update_fields=["name", "document_columns", "footer_totals", "updated_at"])

    existing_lines = _require_bounded(
        list(
            TenderDocumentCompletionLine.objects.select_for_update(of=("self",))
            .filter(tender_id=locked.pk)
            .order_by("sequence", "pk")[: MAX_TENDER_COMPLETION_VENDORS + 1]
        ),
        limit=MAX_TENDER_COMPLETION_VENDORS,
        label="Tender Document Completion rows",
    )
    existing_by_vendor = {line.vendor_id: line for line in existing_lines}
    active_vendor_ids = {prequalification.vendor_id for prequalification in prequalifications}
    stale_ids = [line.pk for line in existing_lines if line.vendor_id not in active_vendor_ids]
    if stale_ids:
        TenderDocumentCompletionLine.objects.filter(pk__in=stale_ids).delete()

    now = timezone.now()
    created_lines = []
    changed_lines = []
    for index, prequalification in enumerate(prequalifications, start=1):
        cells = []
        valid_count = 0
        for requirement in requirements:
            review = review_by_key.get(
                (prequalification.pk, requirement.document_type.casefold())
            )
            line_status = review.status if review else VendorDocumentReview.Status.DRAFT
            is_valid = line_status == VendorDocumentReview.Status.ACCEPTED
            if is_valid:
                valid_count += 1
            display_status = "valid" if is_valid else "draft" if not review else "missing"
            outward_line_status = "approved" if is_valid else line_status
            cells.append(
                {
                    "vendor_key": f"doc_{requirement.pk}",
                    "column_key": f"doc_{requirement.pk}",
                    "vendor_name": requirement.document_type,
                    "column_name": requirement.document_type,
                    "vendor_code": "Document",
                    "column_code": "Document",
                    "document_id": requirement.pk,
                    "document_line_id": review.pk if review else None,
                    "line_status": outward_line_status,
                    "description": review.notes if review else requirement.title,
                    "file_name": (
                        review.document.attachment_url.rsplit("/", 1)[-1]
                        if review and review.document_id and review.document.attachment_url
                        else review.attachment_url.rsplit("/", 1)[-1]
                        if review and review.attachment_url
                        else ""
                    ),
                    "status": display_status,
                    "price_display": "Valid" if is_valid else "Draft" if not review else "Missing",
                    "subline_1": "" if is_valid else "Required" if requirement.mandatory and review else "",
                    "subline_2": "",
                    "tone": "success" if is_valid else "neutral" if not review else "danger",
                }
            )
        cells.append(
            {
                "vendor_key": "action_review",
                "column_key": "action_review",
                "vendor_name": "Pre-Qualification",
                "column_name": "Pre-Qualification",
                "vendor_code": "Action",
                "column_code": "Action",
                "status": "review",
                "price_display": "Review",
                "subline_1": str(prequalification.pk),
                "subline_2": "",
                "tone": "info",
                "action": "review_prequalification",
                "action_model": "vendor-prequalifications",
                "action_res_id": prequalification.pk,
                "prequalification_id": prequalification.pk,
                "is_action": True,
            }
        )
        total_count = len(requirements)
        values = {
            "matrix": matrix,
            "tender": locked,
            "vendor": prequalification.vendor,
            "sequence": index * 10,
            "vendor_name": str(prequalification.vendor.partner),
            "vendor_subtitle": prequalification.vendor.registration_number,
            "completion_summary": (
                f"{valid_count}/{total_count} Valid" if total_count else "No Documents"
            ),
            "document_cells": cells,
        }
        line = existing_by_vendor.get(prequalification.vendor_id)
        if line is None:
            created_lines.append(TenderDocumentCompletionLine(**values))
            continue
        for field_name, value in values.items():
            setattr(line, field_name, value)
        line.updated_at = now
        changed_lines.append(line)
    if created_lines:
        TenderDocumentCompletionLine.objects.bulk_create(created_lines, batch_size=500)
    if changed_lines:
        TenderDocumentCompletionLine.objects.bulk_update(
            changed_lines,
            [
                "matrix",
                "tender",
                "vendor",
                "sequence",
                "vendor_name",
                "vendor_subtitle",
                "completion_summary",
                "document_cells",
                "updated_at",
            ],
            batch_size=500,
        )
    return matrix


def qualified_tender_document_completion_vendor_ids(tender):
    matrix = sync_tender_document_completion(tender)
    if matrix is None:
        return set()
    lines = _require_bounded(
        list(
            TenderDocumentCompletionLine.objects.filter(matrix_id=matrix.pk)
            .order_by("sequence", "pk")[: MAX_TENDER_COMPLETION_VENDORS + 1]
        ),
        limit=MAX_TENDER_COMPLETION_VENDORS,
        label="Tender Document Completion rows",
    )
    return {line.vendor_id for line in lines if _tender_completion_line_is_qualified(line)}


@transaction.atomic
def transition_vendor_prequalification(prequalification, action, actor=None, *, reason=""):
    locked = (
        VendorPrequalification.objects.select_for_update(of=("self",))
        .select_related("tender", "vendor")
        .get(pk=_pk(prequalification))
    )
    if not locked.tender_id:
        raise ValidationError("Tender is required for Vendor Prequalification.")
    if locked.vendor.company_id != locked.tender.company_id or not locked.vendor.active:
        raise ValidationError("Prequalification vendor must be active in the Tender company.")
    if locked.vendor.status in {VendorProfile.Status.BLACKLISTED, VendorProfile.Status.REJECTED}:
        raise ValidationError("Rejected or Blacklisted vendors cannot be prequalified.")
    reason = str(reason or "").strip()
    lines = _sync_prequalification_requirements_locked(locked)
    if action == "submit":
        if locked.status not in {VendorPrequalification.Status.DRAFT, VendorPrequalification.Status.REJECTED}:
            raise ValidationError("Only a Draft or Rejected prequalification can be submitted.")
        if not lines:
            raise ValidationError("Add at least one Tender document requirement before submission.")
        locked.status = VendorPrequalification.Status.PENDING
        locked.submitted_date = timezone.localdate()
        locked.rejection_reason = ""
        locked.document_fulfillment_percentage = _prequalification_fulfillment(lines)
        locked.save(
            update_fields=[
                "status",
                "submitted_date",
                "rejection_reason",
                "document_fulfillment_percentage",
                "updated_at",
            ]
        )
    elif action == "review":
        if locked.status != VendorPrequalification.Status.PENDING:
            raise ValidationError("Only a Pending prequalification can enter review.")
        locked.status = VendorPrequalification.Status.UNDER_REVIEW
        locked.reviewer = actor
        locked.save(update_fields=["status", "reviewer", "updated_at"])
    elif action == "approve":
        if locked.status not in {
            VendorPrequalification.Status.PENDING,
            VendorPrequalification.Status.UNDER_REVIEW,
        }:
            raise ValidationError("Only a Pending or Under Review prequalification can be approved.")
        if not lines or any(line.status != VendorDocumentReview.Status.ACCEPTED for line in lines):
            raise ValidationError("Every prequalification document requirement must be Approved first.")
        locked.status = VendorPrequalification.Status.APPROVED
        locked.reviewer = actor
        locked.rejection_reason = ""
        locked.document_fulfillment_percentage = HUNDRED
        locked.save(
            update_fields=[
                "status",
                "reviewer",
                "rejection_reason",
                "document_fulfillment_percentage",
                "updated_at",
            ]
        )
    elif action == "reject":
        if locked.status not in {
            VendorPrequalification.Status.PENDING,
            VendorPrequalification.Status.UNDER_REVIEW,
        }:
            raise ValidationError("Only a Pending or Under Review prequalification can be rejected.")
        if not reason:
            raise ValidationError("A prequalification rejection reason is required.")
        locked.status = VendorPrequalification.Status.REJECTED
        locked.reviewer = actor
        locked.rejection_reason = reason
        locked.save(update_fields=["status", "reviewer", "rejection_reason", "updated_at"])
    else:
        raise ValidationError("Unsupported Vendor Prequalification workflow action.")
    sync_tender_document_completion(locked.tender_id)
    return locked


@transaction.atomic
def transition_vendor_document_review(review, action, actor=None, *, reason=""):
    locked = (
        VendorDocumentReview.objects.select_for_update(of=("self",))
        .select_related("prequalification__tender", "document")
        .get(pk=_pk(review))
    )
    prequalification = VendorPrequalification.objects.select_for_update(of=("self",)).get(
        pk=locked.prequalification_id
    )
    tender = locked.prequalification.tender
    if action in {"approve", "reject", "revision"} and not (
        actor
        and (
            actor.is_superuser
            or actor.groups.filter(name=PURCHASE_MANAGER).exists()
        )
    ):
        raise ValidationError(
            "Only Purchase Managers can approve, reject, or request revision of prequalification documents."
        )
    if tender and tender.method == PurchaseTender.Method.PQTE and tender.status != PurchaseTender.Status.DOCUMENT_SUBMISSION:
        raise ValidationError(
            "Prequalification documents can only be reviewed during Tender Document Submission."
        )
    reason = str(reason or "").strip()
    if action == "submit":
        sources, target = {VendorDocumentReview.Status.DRAFT}, VendorDocumentReview.Status.PENDING
        if locked.requirement_type == VendorLegalDocument.RequirementType.REQUIRED and not (
            locked.attachment_url or (locked.document_id and locked.document.attachment_url)
        ):
            raise ValidationError("An attachment is required for this prequalification document.")
    elif action == "approve":
        sources, target = {
            VendorDocumentReview.Status.PENDING,
            VendorDocumentReview.Status.REVISION,
        }, VendorDocumentReview.Status.ACCEPTED
    elif action == "reject":
        sources, target = {VendorDocumentReview.Status.PENDING}, VendorDocumentReview.Status.REJECTED
        if not reason:
            raise ValidationError("A document rejection reason is required.")
    elif action == "revision":
        sources, target = {VendorDocumentReview.Status.PENDING}, VendorDocumentReview.Status.REVISION
        if not reason:
            raise ValidationError("A revision reason is required.")
    elif action == "reset":
        sources, target = {
            VendorDocumentReview.Status.REJECTED,
            VendorDocumentReview.Status.REVISION,
        }, VendorDocumentReview.Status.DRAFT
    else:
        raise ValidationError("Unsupported Prequalification Document workflow action.")
    if locked.status not in sources:
        raise ValidationError("That document review action is not available in the current state.")
    locked.status = target
    locked.reviewer = actor if action in {"approve", "reject", "revision"} else None
    locked.reviewed_at = timezone.now() if locked.reviewer_id else None
    locked.notes = reason if action in {"reject", "revision"} else ""
    locked.save(update_fields=["status", "reviewer", "reviewed_at", "notes", "updated_at"])
    lines = _prequalification_document_lines(prequalification.pk, lock=True)
    _sync_prequalification_status_locked(prequalification, lines)
    if tender:
        sync_tender_document_completion(tender.pk)
    return locked


@transaction.atomic
def create_purchase_request_rfqs(
    purchase_request,
    *,
    allocations,
    response_deadline,
    actor=None,
):
    """Create bounded draft RFQs from eligible PR lines, grouped by vendor."""
    del actor
    locked = (
        PurchaseRequest.objects.select_for_update(of=("self",))
        .select_related("destination_warehouse")
        .get(pk=_pk(purchase_request))
    )
    if locked.status not in {
        PurchaseRequest.Status.APPROVED,
        PurchaseRequest.Status.PARTIALLY_RFQ,
        PurchaseRequest.Status.RFQ,
        PurchaseRequest.Status.IN_PROGRESS,
    }:
        raise ValidationError("Only an approved or in-progress Purchase Request can be converted to RFQ.")
    if not locked.destination_warehouse_id:
        raise ValidationError("Destination Warehouse is required.")
    if response_deadline < timezone.localdate():
        raise ValidationError("Response deadline cannot be in the past.")
    if not allocations:
        raise ValidationError("No remaining quantity available to convert.")
    if len(allocations) > MAX_PURCHASE_DOCUMENT_LINES:
        raise ValidationError(
            f"Purchase Request conversion exceeds the operational limit of {MAX_PURCHASE_DOCUMENT_LINES} lines."
        )

    allocation_by_line = {}
    all_vendor_ids = []
    for allocation in allocations:
        line_id = int(allocation["line_id"])
        if line_id in allocation_by_line:
            raise ValidationError("Each Purchase Request line may appear only once in the RFQ conversion.")
        quantity = Decimal(str(allocation["quantity"]))
        vendor_ids = [int(value) for value in allocation["vendor_ids"]]
        if quantity <= 0:
            raise ValidationError("RFQ quantity must be greater than 0.")
        if not vendor_ids:
            raise ValidationError("Vendor is required for each RFQ line.")
        allocation_by_line[line_id] = (quantity, list(dict.fromkeys(vendor_ids)))
        all_vendor_ids.extend(vendor_ids)

    vendors = _validate_rfq_vendors(locked.company_id, all_vendor_ids)
    vendors_by_id = {vendor.pk: vendor for vendor in vendors}
    lines = _require_bounded(
        list(
            PurchaseRequestLine.objects.select_for_update(of=("self",))
            .filter(request_id=locked.pk, pk__in=allocation_by_line)
            .select_related("product")
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase Request",
    )
    if {line.pk for line in lines} != set(allocation_by_line):
        raise ValidationError("RFQ conversion contains a line outside this Purchase Request.")

    quote_lines_by_vendor = defaultdict(list)
    for line in lines:
        quantity, vendor_ids = allocation_by_line[line.pk]
        if line.line_status not in {
            PurchaseRequestLine.Status.APPROVED,
            PurchaseRequestLine.Status.PARTIALLY_APPROVED,
            PurchaseRequestLine.Status.PARTIALLY_RFQ,
            PurchaseRequestLine.Status.RFQ,
            PurchaseRequestLine.Status.PARTIALLY_ORDERED,
            PurchaseRequestLine.Status.FULLY_ORDERED,
            PurchaseRequestLine.Status.IN_PROGRESS,
        }:
            raise ValidationError(f"Purchase Request line {line.pk} is not eligible for RFQ conversion.")
        remaining = line.remaining_to_source
        if quantity > remaining:
            raise ValidationError(
                f"RFQ quantity cannot exceed remaining quantity ({remaining}) for {line.product or line.description}."
            )
        for vendor_id in vendor_ids:
            quote_lines_by_vendor[vendor_id].append((line, quantity))

    today = timezone.localdate()
    quotations = []
    sorted_vendor_ids = sorted(quote_lines_by_vendor)
    allocated_numbers = (
        allocate_purchase_document_numbers(
            locked.order_type_id,
            locked.company_id,
            "rfq",
            count=len(sorted_vendor_ids),
        )
        if locked.order_type_id
        else [f"RFQ-{today:%Y%m%d}-{uuid4().hex[:8].upper()}" for _vendor_id in sorted_vendor_ids]
    )
    for vendor_id, number in zip(sorted_vendor_ids, allocated_numbers):
        quotation = PurchaseQuotation(
            company_id=locked.company_id,
            order_type_id=locked.order_type_id,
            number=number,
            request=locked,
            vendor=vendors_by_id[vendor_id],
            warehouse_id=locked.destination_warehouse_id,
            issue_date=today,
            response_deadline=response_deadline,
            expected_date=locked.required_date,
            currency=locked.currency,
            status=PurchaseQuotation.Status.DRAFT,
        )
        quotations.append(quotation)
    PurchaseQuotation.objects.bulk_create(quotations, batch_size=100)

    quotation_by_vendor = {quotation.vendor_id: quotation for quotation in quotations}
    quotation_lines = []
    totals = defaultdict(lambda: ZERO)
    for vendor_id, planned_lines in quote_lines_by_vendor.items():
        quotation = quotation_by_vendor[vendor_id]
        for line, quantity in planned_lines:
            quotation_lines.append(
                PurchaseQuotationLine(
                    quotation=quotation,
                    request_line=line,
                    product_id=line.product_id,
                    description=line.description,
                    specification=line.specification,
                    quantity=quantity,
                    unit_price=line.estimated_unit_price,
                    promised_date=locked.required_date,
                    expiry_date=response_deadline,
                )
            )
            totals[quotation.pk] += quantity * line.estimated_unit_price
    PurchaseQuotationLine.objects.bulk_create(quotation_lines, batch_size=500)
    for quotation in quotations:
        quotation.quoted_amount = totals[quotation.pk]
        quotation.updated_at = timezone.now()
    PurchaseQuotation.objects.bulk_update(quotations, ["quoted_amount", "updated_at"], batch_size=100)

    vendor_count_by_line = {
        line_id: len(vendor_ids)
        for line_id, (_quantity, vendor_ids) in allocation_by_line.items()
    }
    now = timezone.now()
    for line in lines:
        quantity, _vendor_ids = allocation_by_line[line.pk]
        line.rfq_quantity += quantity * vendor_count_by_line[line.pk]
        _advance_request_line_status(line)
        line.updated_at = now
    PurchaseRequestLine.objects.bulk_update(
        lines,
        ["rfq_quantity", "line_status", "updated_at"],
        batch_size=500,
    )
    locked.status = PurchaseRequest.Status.IN_PROGRESS
    locked.save(update_fields=["status", "updated_at"])
    return quotations


def _lock_request_conversion(purchase_request, allocations, *, quantity_label):
    locked = (
        PurchaseRequest.objects.select_for_update(of=("self",))
        .select_related("destination_warehouse")
        .get(pk=_pk(purchase_request))
    )
    if locked.status not in {
        PurchaseRequest.Status.APPROVED,
        PurchaseRequest.Status.PARTIALLY_RFQ,
        PurchaseRequest.Status.RFQ,
        PurchaseRequest.Status.IN_PROGRESS,
    }:
        raise ValidationError(
            f"Only an approved or in-progress Purchase Request can be converted to {quantity_label}."
        )
    if not locked.destination_warehouse_id:
        raise ValidationError("Destination Warehouse is required.")
    if not allocations:
        raise ValidationError(f"Select at least one line to create {quantity_label}.")
    if len(allocations) > MAX_PURCHASE_DOCUMENT_LINES:
        raise ValidationError(
            f"Purchase Request conversion exceeds the operational limit of {MAX_PURCHASE_DOCUMENT_LINES} lines."
        )
    quantities = {}
    for allocation in allocations:
        line_id = int(allocation["line_id"])
        if line_id in quantities:
            raise ValidationError("Each Purchase Request line may appear only once in a conversion.")
        quantity = Decimal(str(allocation["quantity"]))
        if quantity <= 0:
            raise ValidationError(f"{quantity_label} quantity must be greater than 0.")
        quantities[line_id] = quantity
    lines = _require_bounded(
        list(
            PurchaseRequestLine.objects.select_for_update(of=("self",))
            .filter(request_id=locked.pk, pk__in=quantities)
            .select_related("product")
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase Request",
    )
    if {line.pk for line in lines} != set(quantities):
        raise ValidationError("Conversion contains a line outside this Purchase Request.")
    for line in lines:
        if quantities[line.pk] > line.remaining_to_source:
            raise ValidationError(
                f"{quantity_label} quantity cannot exceed remaining quantity ({line.remaining_to_source}) "
                f"for {line.product or line.description}."
            )
    return locked, lines, quantities


def _vendor_pricelist_prices(company_id, vendor_id, line_quantities):
    profile = VendorProfile.objects.filter(company_id=company_id, partner_id=vendor_id).only("pk").first()
    if profile is None:
        return {}
    today = timezone.localdate()
    product_ids = {line.product_id for line in line_quantities}
    quantity_by_product = defaultdict(lambda: ZERO)
    for line, quantity in line_quantities.items():
        quantity_by_product[line.product_id] += quantity
    item_rows = _require_bounded(
        list(
            VendorPricelistItem.objects.filter(
                pricelist__company_id=company_id,
                pricelist__vendor_id=profile.pk,
                pricelist__active=True,
                pricelist__status=VendorPricelist.Status.ACTIVE,
                pricelist__date_start__lte=today,
                product_id__in=product_ids,
                active=True,
            )
            .filter(models.Q(pricelist__date_end__isnull=True) | models.Q(pricelist__date_end__gte=today))
            .order_by("product_id", "-pricelist__date_start", "-pricelist_id", "-pk")
            .values("pk", "product_id", "unit_price")[: MAX_VENDOR_PRICELIST_MATCHES + 1]
        ),
        limit=MAX_VENDOR_PRICELIST_MATCHES,
        label="Vendor pricelist item matches",
    )
    item_by_product = {}
    for row in item_rows:
        item_by_product.setdefault(row["product_id"], row)
    tier_rows = _require_bounded(
        list(
            VendorPricelistTier.objects.filter(item_id__in=[row["pk"] for row in item_by_product.values()])
            .order_by("item__product_id", "-minimum_quantity", "-sequence", "-pk")
            .values(
                "pk",
                "item__product_id",
                "minimum_quantity",
                "maximum_quantity",
                "tier_price",
                "discount_formula",
                "discount_from_tier_id",
            )[: MAX_VENDOR_PRICELIST_MATCHES + 1]
        ),
        limit=MAX_VENDOR_PRICELIST_MATCHES,
        label="Vendor pricelist tier matches",
    )
    tier_by_id = {row["pk"]: row for row in tier_rows}
    effective_price_cache = {}

    def effective_tier_price(row, trail=None):
        cached = effective_price_cache.get(row["pk"])
        if cached is not None:
            return cached
        trail = set(trail or ())
        if row["pk"] in trail:
            raise ValidationError("Vendor pricelist tier discount sources cannot be recursive.")
        trail.add(row["pk"])
        source = tier_by_id.get(row["discount_from_tier_id"])
        base_price = effective_tier_price(source, trail) if source else row["tier_price"]
        value = VendorPricelistTier.apply_discount_formula(base_price, row["discount_formula"])
        effective_price_cache[row["pk"]] = value
        return value

    prices = {}
    for row in tier_rows:
        product_id = row["item__product_id"]
        if product_id in prices:
            continue
        quantity = quantity_by_product[product_id]
        if row["minimum_quantity"] > quantity:
            continue
        if row["maximum_quantity"] and row["maximum_quantity"] < quantity:
            continue
        prices[product_id] = effective_tier_price(row)
    for product_id, row in item_by_product.items():
        prices.setdefault(product_id, row["unit_price"])
    rows = _require_bounded(
        list(
            VendorPricelist.objects.filter(
                company_id=company_id,
                vendor_id=profile.pk,
                product_id__in=product_ids - set(prices),
                active=True,
                date_start__lte=today,
            )
            .filter(models.Q(date_end__isnull=True) | models.Q(date_end__gte=today))
            .order_by("product_id", "-minimum_quantity", "-date_start", "-pk")
            .values("product_id", "minimum_quantity", "unit_price")[: MAX_VENDOR_PRICELIST_MATCHES + 1]
        ),
        limit=MAX_VENDOR_PRICELIST_MATCHES,
        label="Legacy vendor pricelist matches",
    )
    for row in rows:
        product_id = row["product_id"]
        if product_id in prices:
            continue
        if row["minimum_quantity"] <= quantity_by_product[product_id]:
            prices[product_id] = row["unit_price"]
    return prices


@transaction.atomic
def expire_due_vendor_pricelists(*, batch_size=MAX_VENDOR_PRICELIST_EXPIRY_BATCH):
    batch_size = max(1, min(int(batch_size), MAX_VENDOR_PRICELIST_EXPIRY_BATCH))
    due_ids = list(
        VendorPricelist.objects.select_for_update(skip_locked=True)
        .filter(date_end__lt=timezone.localdate())
        .exclude(status__in=[VendorPricelist.Status.EXPIRED, VendorPricelist.Status.INACTIVE])
        .order_by("date_end", "pk")
        .values_list("pk", flat=True)[:batch_size]
    )
    if not due_ids:
        return 0
    return VendorPricelist.objects.filter(pk__in=due_ids).update(
        status=VendorPricelist.Status.EXPIRED,
        active=False,
        updated_at=timezone.now(),
    )


@transaction.atomic
def create_purchase_request_blanket_order(
    purchase_request,
    *,
    vendor_id,
    allocations,
    date_end,
    actor=None,
):
    del actor
    locked, lines, quantities = _lock_request_conversion(
        purchase_request,
        allocations,
        quantity_label="Blanket Order",
    )
    vendor = _validate_rfq_vendors(locked.company_id, [vendor_id])[0]
    if date_end < timezone.localdate():
        raise ValidationError("Blanket Order end date cannot be in the past.")
    prices = _vendor_pricelist_prices(
        locked.company_id,
        vendor.pk,
        {line: quantities[line.pk] for line in lines},
    )
    number = (
        allocate_purchase_document_number(locked.order_type_id, locked.company_id, "blanket_order")
        if locked.order_type_id
        else f"BO-{timezone.localdate():%Y%m%d}-{uuid4().hex[:8].upper()}"
    )
    blanket = BlanketOrder.objects.create(
        company_id=locked.company_id,
        order_type_id=locked.order_type_id,
        number=number,
        vendor=vendor,
        warehouse_id=locked.destination_warehouse_id,
        currency=locked.currency,
        date_start=timezone.localdate(),
        date_end=date_end,
        status=BlanketOrder.Status.DRAFT,
    )
    blanket_lines = []
    total = ZERO
    for line in lines:
        quantity = quantities[line.pk]
        unit_price = prices.get(line.product_id) or line.estimated_unit_price or Decimal("1")
        blanket_lines.append(
            BlanketOrderLine(
                blanket_order=blanket,
                request_line=line,
                product_id=line.product_id,
                description=line.description,
                agreed_quantity=quantity,
                unit_price=unit_price,
            )
        )
        total += quantity * unit_price
    BlanketOrderLine.objects.bulk_create(blanket_lines, batch_size=500)
    blanket.agreement_value = total
    blanket.save(update_fields=["agreement_value", "updated_at"])
    now = timezone.now()
    for line in lines:
        line.blanket_quantity += quantities[line.pk]
        if _PR_LINE_STATUS_RANK.get(line.line_status, 0) < _PR_LINE_STATUS_RANK[PurchaseRequestLine.Status.IN_PROGRESS]:
            line.line_status = PurchaseRequestLine.Status.IN_PROGRESS
        line.updated_at = now
    PurchaseRequestLine.objects.bulk_update(
        lines,
        ["blanket_quantity", "line_status", "updated_at"],
        batch_size=500,
    )
    locked.status = PurchaseRequest.Status.IN_PROGRESS
    locked.save(update_fields=["status", "updated_at"])
    return blanket


@transaction.atomic
def create_purchase_request_tender(
    purchase_request,
    *,
    allocations,
    title,
    tender_type,
    method,
    award_strategy_id,
    close_date,
    actor=None,
):
    del actor
    locked, lines, quantities = _lock_request_conversion(
        purchase_request,
        allocations,
        quantity_label="Tender",
    )
    strategy = AwardStrategy.objects.filter(
        pk=award_strategy_id,
        company_id=locked.company_id,
        active=True,
    ).first()
    if strategy is None:
        raise ValidationError("Select an active award strategy from the Purchase Request company.")
    if tender_type not in PurchaseTender.TenderType.values or method not in PurchaseTender.Method.values:
        raise ValidationError("Tender type or method is invalid.")
    if close_date < timezone.localdate():
        raise ValidationError("Tender close date cannot be in the past.")
    number = (
        allocate_purchase_document_number(locked.order_type_id, locked.company_id, "purchase_tender")
        if locked.order_type_id
        else f"TNR-{locked.company.code}-{timezone.localdate():%Y}-{uuid4().hex[:8].upper()}"
    )
    tender = PurchaseTender.objects.create(
        company_id=locked.company_id,
        order_type_id=locked.order_type_id,
        number=number,
        title=title,
        category=PurchaseTender.Category.OPEX,
        tender_type=tender_type,
        method=method,
        award_strategy=strategy,
        warehouse_id=locked.destination_warehouse_id,
        currency=locked.currency,
        bid_start_date=timezone.localdate(),
        close_date=close_date,
        budget_amount=sum((quantities[line.pk] * line.estimated_unit_price for line in lines), ZERO),
        status=PurchaseTender.Status.DRAFT,
    )
    PurchaseTenderLine.objects.bulk_create(
        [
            PurchaseTenderLine(
                tender=tender,
                request_line=line,
                product_id=line.product_id,
                description=line.description,
                specification=line.specification,
                quantity=quantities[line.pk],
                target_unit_price=line.estimated_unit_price,
                delivery_date=locked.required_date,
            )
            for line in lines
        ],
        batch_size=500,
    )
    now = timezone.now()
    for line in lines:
        line.tender_quantity += quantities[line.pk]
        _advance_request_line_status(line)
        line.updated_at = now
    PurchaseRequestLine.objects.bulk_update(
        lines,
        ["tender_quantity", "line_status", "updated_at"],
        batch_size=500,
    )
    locked.status = PurchaseRequest.Status.IN_PROGRESS
    locked.save(update_fields=["status", "updated_at"])
    return tender


@transaction.atomic
def create_purchase_request_direct_purchase(
    purchase_request,
    *,
    vendor_id,
    allocations,
    justification,
    actor=None,
):
    del actor
    locked, lines, quantities = _lock_request_conversion(
        purchase_request,
        allocations,
        quantity_label="Direct Purchase",
    )
    vendor = _validate_rfq_vendors(locked.company_id, [vendor_id])[0]
    prices = _vendor_pricelist_prices(
        locked.company_id,
        vendor.pk,
        {line: quantities[line.pk] for line in lines},
    )
    number = (
        allocate_purchase_document_number(locked.order_type_id, locked.company_id, "direct_purchase_order")
        if locked.order_type_id
        else f"DPO-{timezone.localdate():%Y%m%d}-{uuid4().hex[:8].upper()}"
    )
    direct = DirectPurchase.objects.create(
        company_id=locked.company_id,
        order_type_id=locked.order_type_id,
        number=number,
        request=locked,
        vendor=vendor,
        warehouse_id=locked.destination_warehouse_id,
        purchase_date=timezone.localdate(),
        description=f"Direct Purchase from {locked.number}",
        amount=ZERO,
        justification=justification,
        status="draft",
    )
    direct_lines = []
    total = ZERO
    for line in lines:
        quantity = quantities[line.pk]
        unit_price = prices.get(line.product_id) or line.estimated_unit_price or Decimal("1")
        direct_lines.append(
            DirectPurchaseLine(
                direct_purchase=direct,
                request_line=line,
                product_id=line.product_id,
                description=line.description,
                quantity=quantity,
                unit_price=unit_price,
            )
        )
        total += quantity * unit_price
    DirectPurchaseLine.objects.bulk_create(direct_lines, batch_size=500)
    direct.amount = total
    direct.save(update_fields=["amount", "updated_at"])
    now = timezone.now()
    for line in lines:
        line.direct_purchase_quantity += quantities[line.pk]
        _advance_request_line_status(line)
        line.updated_at = now
    PurchaseRequestLine.objects.bulk_update(
        lines,
        ["direct_purchase_quantity", "line_status", "updated_at"],
        batch_size=500,
    )
    locked.status = PurchaseRequest.Status.IN_PROGRESS
    locked.save(update_fields=["status", "updated_at"])
    return direct


def _recompute_request_blanket_progress(request_line_ids):
    request_line_ids = list(set(request_line_ids))
    if not request_line_ids:
        return
    lines = list(PurchaseRequestLine.objects.select_for_update().filter(pk__in=request_line_ids).order_by("pk"))
    quantities = {
        row["request_line_id"]: (row["agreed"] or ZERO) - (row["released"] or ZERO)
        for row in BlanketOrderLine.objects.filter(request_line_id__in=request_line_ids)
        .exclude(blanket_order__status=BlanketOrder.Status.CANCELLED)
        .values("request_line_id")
        .annotate(agreed=Sum("agreed_quantity"), released=Sum("released_quantity"))
    }
    now = timezone.now()
    for line in lines:
        line.blanket_quantity = max(quantities.get(line.pk, ZERO), ZERO)
        _advance_request_line_status(line)
        line.updated_at = now
    PurchaseRequestLine.objects.bulk_update(
        lines,
        ["blanket_quantity", "line_status", "updated_at"],
        batch_size=500,
    )


@transaction.atomic
def create_blanket_order_rfq(blanket_order, *, releases=None, actor=None):
    del actor
    blanket = (
        BlanketOrder.objects.select_for_update(of=("self",))
        .select_related("vendor", "warehouse")
        .get(pk=_pk(blanket_order))
    )
    if blanket.status not in {BlanketOrder.Status.CONFIRMED, BlanketOrder.Status.RUNNING}:
        raise ValidationError("RFQ can only be created from a confirmed or running Blanket Order.")
    lines = _require_bounded(
        list(
            BlanketOrderLine.objects.select_for_update(of=("self",))
            .filter(blanket_order_id=blanket.pk)
            .select_related("product", "request_line", "request_line__request")
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Blanket Order",
    )
    release_map = {int(key): Decimal(str(value)) for key, value in (releases or {}).items()}
    selected = []
    for line in lines:
        remaining = line.agreed_quantity - line.released_quantity
        quantity = release_map.get(line.pk, remaining)
        if quantity < 0:
            raise ValidationError("Release quantity cannot be negative.")
        if quantity == 0:
            continue
        if quantity > remaining:
            raise ValidationError(
                f"Release quantity cannot exceed remaining quantity ({remaining}) for {line.product}."
            )
        selected.append((line, quantity))
    if not selected:
        raise ValidationError("No remaining Blanket Order quantity is available to release.")
    request_ids = {
        line.request_line.request_id
        for line, _quantity in selected
        if line.request_line_id
    }
    request_id = next(iter(request_ids)) if len(request_ids) == 1 else None
    suffix = uuid4().hex[:8].upper()
    quotation = PurchaseQuotation.objects.create(
        company_id=blanket.company_id,
        number=f"RFQ-{timezone.localdate():%Y%m%d}-{suffix}",
        request_id=request_id,
        blanket_order=blanket,
        vendor_id=blanket.vendor_id,
        warehouse_id=blanket.warehouse_id,
        issue_date=timezone.localdate(),
        response_deadline=blanket.date_end,
        expected_date=blanket.date_end,
        currency=blanket.currency,
        status=PurchaseQuotation.Status.DRAFT,
    )
    quote_lines = [
        PurchaseQuotationLine(
            quotation=quotation,
            request_line=line.request_line,
            blanket_order_line=line,
            product_id=line.product_id,
            description=line.description or line.product.name,
            quantity=quantity,
            unit_price=line.unit_price,
            promised_date=blanket.date_end,
            expiry_date=quotation.response_deadline,
        )
        for line, quantity in selected
    ]
    PurchaseQuotationLine.objects.bulk_create(quote_lines, batch_size=500)
    quotation.quoted_amount = sum((line.quantity * line.unit_price for line in quote_lines), ZERO)
    quotation.save(update_fields=["quoted_amount", "updated_at"])
    now = timezone.now()
    request_lines = {}
    for line, quantity in selected:
        line.released_quantity += quantity
        line.updated_at = now
        if line.request_line_id:
            request_line = line.request_line
            request_line.blanket_quantity = max(request_line.blanket_quantity - quantity, ZERO)
            request_line.rfq_quantity += quantity
            _advance_request_line_status(request_line)
            request_line.updated_at = now
            request_lines[request_line.pk] = request_line
    BlanketOrderLine.objects.bulk_update(
        [line for line, _quantity in selected],
        ["released_quantity", "updated_at"],
        batch_size=500,
    )
    if request_lines:
        PurchaseRequestLine.objects.bulk_update(
            list(request_lines.values()),
            ["blanket_quantity", "rfq_quantity", "line_status", "updated_at"],
            batch_size=500,
        )
    if blanket.status == BlanketOrder.Status.CONFIRMED:
        blanket.status = BlanketOrder.Status.RUNNING
        blanket.auto_closed = False
        blanket.save(update_fields=["status", "auto_closed", "updated_at"])
    return quotation


def _cancel_blanket_rfq_release(quotation):
    if not quotation.blanket_order_id:
        return
    released_rows = _require_bounded(list(
        PurchaseQuotationLine.objects.filter(
            quotation_id=quotation.pk,
            blanket_order_line__isnull=False,
        )
        .values("blanket_order_line_id", "request_line_id")
        .annotate(quantity=Sum("quantity"))
        .order_by("blanket_order_line_id")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
    ), limit=MAX_PURCHASE_DOCUMENT_LINES, label="Blanket RFQ release")
    if not released_rows:
        return
    blanket_lines = BlanketOrderLine.objects.select_for_update().in_bulk(
        row["blanket_order_line_id"] for row in released_rows
    )
    now = timezone.now()
    for row in released_rows:
        blanket_line = blanket_lines[row["blanket_order_line_id"]]
        blanket_line.released_quantity = max(
            blanket_line.released_quantity - (row["quantity"] or ZERO),
            ZERO,
        )
        blanket_line.updated_at = now
    BlanketOrderLine.objects.bulk_update(
        list(blanket_lines.values()),
        ["released_quantity", "updated_at"],
        batch_size=500,
    )
    request_line_ids = [row["request_line_id"] for row in released_rows if row["request_line_id"]]
    _recompute_request_blanket_progress(request_line_ids)
    blanket = BlanketOrder.objects.select_for_update().get(pk=quotation.blanket_order_id)
    if blanket.status == BlanketOrder.Status.CLOSED and blanket.auto_closed:
        blanket.status = BlanketOrder.Status.RUNNING
        blanket.auto_closed = False
        blanket.save(update_fields=["status", "auto_closed", "updated_at"])


def _auto_close_blanket_after_rfq_selection(blanket_order_id):
    if not blanket_order_id:
        return
    blanket = BlanketOrder.objects.select_for_update().get(pk=blanket_order_id)
    if blanket.status not in {BlanketOrder.Status.CONFIRMED, BlanketOrder.Status.RUNNING}:
        return
    has_remaining = BlanketOrderLine.objects.filter(
        blanket_order_id=blanket.pk,
        released_quantity__lt=F("agreed_quantity"),
    ).exists()
    has_unselected_release = PurchaseQuotation.objects.filter(
        blanket_order_id=blanket.pk,
        status__in=[
            PurchaseQuotation.Status.DRAFT,
            PurchaseQuotation.Status.SENT,
            PurchaseQuotation.Status.RECEIVED,
        ],
    ).exists()
    if not has_remaining and not has_unselected_release:
        blanket.status = BlanketOrder.Status.CLOSED
        blanket.auto_closed = True
        blanket.save(update_fields=["status", "auto_closed", "updated_at"])


@transaction.atomic
def transition_blanket_order(blanket_order, action, actor=None):
    del actor
    locked = BlanketOrder.objects.select_for_update(of=("self",)).get(pk=_pk(blanket_order))
    transitions = {
        "submit": ({BlanketOrder.Status.DRAFT}, BlanketOrder.Status.WAITING_APPROVAL),
        "approve": ({BlanketOrder.Status.WAITING_APPROVAL}, BlanketOrder.Status.CONFIRMED),
        "reject": ({BlanketOrder.Status.WAITING_APPROVAL}, BlanketOrder.Status.REJECTED),
        "run": ({BlanketOrder.Status.CONFIRMED}, BlanketOrder.Status.RUNNING),
        "close": ({BlanketOrder.Status.CONFIRMED, BlanketOrder.Status.RUNNING}, BlanketOrder.Status.CLOSED),
        "reopen": ({BlanketOrder.Status.CLOSED}, BlanketOrder.Status.RUNNING),
        "cancel": (
            {
                BlanketOrder.Status.DRAFT,
                BlanketOrder.Status.WAITING_APPROVAL,
                BlanketOrder.Status.CONFIRMED,
                BlanketOrder.Status.RUNNING,
            },
            BlanketOrder.Status.CANCELLED,
        ),
    }
    if action == "create-rfq":
        quotation = create_blanket_order_rfq(locked)
        locked._created_rfq_id = quotation.pk
        return locked
    if action not in transitions:
        raise ValidationError("Unsupported Blanket Order workflow action.")
    sources, target = transitions[action]
    if locked.status not in sources:
        raise ValidationError("That Blanket Order action is not available in the current state.")
    if action == "approve":
        _validate_purchase_vendor_eligibility(locked.company_id, locked.vendor_id)
    if action in {"submit", "approve"}:
        blanket_lines = _require_bounded(
            list(
                BlanketOrderLine.objects.filter(blanket_order_id=locked.pk)
                .select_related("product")
                .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
            ),
            limit=MAX_PURCHASE_DOCUMENT_LINES,
            label="Blanket order",
        )
        validate_vendor_pricelist_product_eligibility(
            document=locked,
            lines=blanket_lines,
            transaction_date=locked.date_start,
        )
    locked.status = target
    update_fields = ["status", "updated_at"]
    if action in {"close", "reopen"}:
        locked.auto_closed = False
        update_fields.append("auto_closed")
    locked.save(update_fields=update_fields)
    if action == "cancel":
        request_line_ids = list(locked.lines.values_list("request_line_id", flat=True))
        _recompute_request_blanket_progress([value for value in request_line_ids if value])
    return locked


@transaction.atomic
def transition_vendor_pricelist(pricelist, action, actor=None):
    del actor
    locked = VendorPricelist.objects.select_for_update(of=("self",)).get(pk=_pk(pricelist))
    transitions = {
        "submit": ({VendorPricelist.Status.DRAFT}, VendorPricelist.Status.WAITING_APPROVAL),
        "approve": ({VendorPricelist.Status.WAITING_APPROVAL}, VendorPricelist.Status.ACTIVE),
        "reject": ({VendorPricelist.Status.WAITING_APPROVAL}, VendorPricelist.Status.REJECTED),
        "archive": (
            {VendorPricelist.Status.ACTIVE, VendorPricelist.Status.EXPIRED},
            VendorPricelist.Status.INACTIVE,
        ),
        "reset": (
            {VendorPricelist.Status.REJECTED, VendorPricelist.Status.INACTIVE},
            VendorPricelist.Status.DRAFT,
        ),
    }
    if action not in transitions:
        raise ValidationError("Unsupported Vendor Pricelist workflow action.")
    sources, target = transitions[action]
    if locked.status not in sources:
        raise ValidationError("That Vendor Pricelist action is not available in the current state.")
    if action == "submit":
        has_items = VendorPricelistItem.objects.filter(pricelist_id=locked.pk, active=True).exists()
        if not has_items and not locked.product_id:
            raise ValidationError("Add at least one Vendor Pricelist item before submitting.")
        total_price = (
            VendorPricelistItem.objects.filter(pricelist_id=locked.pk, active=True)
            .aggregate(total=Sum("unit_price"))["total"]
            or ZERO
        )
        if locked.product_id:
            total_price += locked.unit_price
        if total_price < Decimal("1"):
            raise ValidationError("Vendor Pricelist total price must be at least 1 before approval.")
    locked.status = target
    locked.active = target not in {VendorPricelist.Status.INACTIVE, VendorPricelist.Status.EXPIRED}
    locked.save(update_fields=["status", "active", "updated_at"])
    return locked


_VENDOR_PRICELIST_IMPORT_HEADERS = {
    "id": "parent_id",
    "code": "parent_code",
    "name": "parent_name",
    "vendor": "vendor",
    "vendor_code": "vendor",
    "currency": "currency",
    "currency_id": "currency",
    "date_start": "date_start",
    "effective_date": "date_start",
    "date_end": "date_end",
    "expiry_date": "date_end",
    "active": "active",
    "pricelist_item_ids/product_id": "item_product",
    "pricelist_item_ids/product_code": "item_product",
    "pricelist_item_ids/variant": "item_variant",
    "pricelist_item_ids/uom": "item_uom",
    "pricelist_item_ids/vendor_uom": "item_vendor_uom",
    "pricelist_item_ids/unit_price": "item_unit_price",
    "pricelist_item_ids/enforce_moq": "item_enforce_moq",
    "pricelist_item_ids/lead_time": "item_lead_time",
    "pricelist_item_ids/vendor_product_code": "item_vendor_product_code",
    "pricelist_item_ids/vendor_product": "item_vendor_product_name",
    "pricelist_item_ids/vendor_variant": "item_variant",
    "pricelist_item_ids/pricelist_tier_ids/tier": "tier_sequence",
    "pricelist_item_ids/pricelist_tier_ids/min_qty": "tier_minimum_quantity",
    "pricelist_item_ids/pricelist_tier_ids/max_qty": "tier_maximum_quantity",
    "pricelist_item_ids/pricelist_tier_ids/tier_price": "tier_price",
    "pricelist_item_ids/pricelist_tier_ids/discount": "tier_discount_formula",
    "pricelist_item_ids/pricelist_tier_ids/free_qty": "tier_free_quantity",
    "pricelist_item_ids/pricelist_tier_ids/free_uom": "tier_free_uom",
}


def _import_header_key(value):
    return " ".join(str(value or "").strip().rstrip("*").strip().split()).casefold()


_VENDOR_PRICELIST_IMPORT_HEADER_LOOKUP = {
    _import_header_key(header): field
    for header, field in _VENDOR_PRICELIST_IMPORT_HEADERS.items()
}


def _import_decimal(value, *, field, positive=False, allow_zero=True):
    try:
        parsed = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"{field} must be a valid number.")
    if positive and (parsed < 0 or (not allow_zero and parsed == 0)):
        qualifier = "greater than zero" if not allow_zero else "zero or greater"
        raise ValidationError(f"{field} must be {qualifier}.")
    return str(parsed)


def _import_boolean(value, *, field):
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"true", "1", "yes", "y", "t"}:
        return True
    if normalized in {"false", "0", "no", "n", "f"}:
        return False
    raise ValidationError(f"{field} must be TRUE/FALSE, 1/0, or YES/NO.")


def _import_date(value, *, field):
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must use YYYY-MM-DD format.")


def _read_vendor_pricelist_csv(import_log):
    if not import_log.source_file:
        raise ValidationError("Choose a CSV file before validation.")
    import_log.source_file.open("rb")
    try:
        text_stream = io.TextIOWrapper(import_log.source_file.file, encoding="utf-8-sig", newline="")
        reader = csv.reader(text_stream)
        first_row = next(reader, None)
        if first_row is None:
            return [], []
        if import_log.has_header:
            headers = [str(value or "").strip() for value in first_row]
            rows = reader
        else:
            headers = [f"column_{index}" for index in range(1, len(first_row) + 1)]
            rows = chain([first_row], reader)
        result = []
        for index, values in enumerate(rows, start=2 if import_log.has_header else 1):
            if len(result) >= MAX_VENDOR_PRICELIST_IMPORT_ROWS + 1:
                break
            padded = list(values) + [""] * max(0, len(headers) - len(values))
            result.append((index, dict(zip(headers, padded))))
        if len(result) > MAX_VENDOR_PRICELIST_IMPORT_ROWS:
            raise ValidationError(
                f"Vendor pricelist import exceeds the operational limit of {MAX_VENDOR_PRICELIST_IMPORT_ROWS} rows. "
                "Split the file into bounded import jobs."
            )
        return headers, result
    finally:
        import_log.source_file.close()


def _normalize_vendor_pricelist_import_row(raw_row, field_mapping):
    normalized = {}
    unknown_targets = []
    for source_header, value in raw_row.items():
        mapped_header = field_mapping.get(source_header, source_header)
        if mapped_header in {None, "", "__ignore__"}:
            continue
        target = _VENDOR_PRICELIST_IMPORT_HEADER_LOOKUP.get(_import_header_key(mapped_header))
        if target is None:
            if source_header in field_mapping:
                unknown_targets.append(str(mapped_header))
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        normalized[target] = value.strip() if isinstance(value, str) else value
    if unknown_targets:
        raise ValidationError(
            "Unknown target field(s) in field mapping: " + ", ".join(sorted(set(unknown_targets))) + "."
        )
    return normalized


def _vendor_import_reference_maps(company_id, rows):
    vendor_tokens = {
        str(row["parent"].get("vendor") or "").strip()
        for row in rows
        if row.get("parent", {}).get("vendor")
    }
    product_tokens = {
        str(
            (row.get("item") or {}).get("product")
            or (row.get("tier") or {}).get("item_product")
            or ""
        ).strip()
        for row in rows
        if (row.get("item") or {}).get("product") or (row.get("tier") or {}).get("item_product")
    }
    vendor_ids = [int(token) for token in vendor_tokens if token.isdigit()]
    product_ids = [int(token) for token in product_tokens if token.isdigit()]
    profiles = _require_bounded(
        list(
            VendorProfile.objects.filter(company_id=company_id)
            .filter(
                Q(pk__in=vendor_ids)
                | Q(registration_number__in=vendor_tokens)
                | Q(partner__code__in=vendor_tokens)
            )
            .select_related("partner")
            .order_by("pk")[: MAX_VENDOR_PRICELIST_IMPORT_ROWS + 1]
        ),
        limit=MAX_VENDOR_PRICELIST_IMPORT_ROWS,
        label="Vendor pricelist import vendors",
    )
    products = _require_bounded(
        list(
            Product.objects.filter(company_id=company_id)
            .filter(Q(pk__in=product_ids) | Q(sku__in=product_tokens))
            .order_by("pk")[: MAX_VENDOR_PRICELIST_IMPORT_ROWS + 1]
        ),
        limit=MAX_VENDOR_PRICELIST_IMPORT_ROWS,
        label="Vendor pricelist import products",
    )
    vendor_map = {}
    for profile in profiles:
        vendor_map[str(profile.pk)] = profile
        vendor_map[profile.registration_number.casefold()] = profile
        vendor_map[profile.partner.code.casefold()] = profile
    product_map = {}
    for product in products:
        product_map[str(product.pk)] = product
        product_map[product.sku.casefold()] = product
    return vendor_map, product_map


@transaction.atomic
def validate_vendor_pricelist_import(import_log, actor=None):
    locked = VendorPricelistImportLog.objects.select_for_update(of=("self",)).get(pk=_pk(import_log))
    if locked.status not in {VendorPricelistImportLog.Status.DRAFT, VendorPricelistImportLog.Status.FAILED}:
        raise ValidationError("Only a Draft or Failed vendor pricelist import can be validated.")
    headers, raw_rows = _read_vendor_pricelist_csv(locked)
    candidates = []
    errors = []
    skipped = 0
    current_parent = {}
    current_item_product = ""
    for row_number, raw_row in raw_rows:
        try:
            values = _normalize_vendor_pricelist_import_row(raw_row, locked.field_mapping or {})
            if not values:
                skipped += 1
                continue
            parent_updates = {
                "id": values.get("parent_id"),
                "code": values.get("parent_code"),
                "name": values.get("parent_name"),
                "vendor": values.get("vendor"),
                "currency": values.get("currency"),
                "date_start": values.get("date_start"),
                "date_end": values.get("date_end"),
                "active": values.get("active"),
            }
            parent_updates = {key: value for key, value in parent_updates.items() if value not in {None, ""}}
            incoming_identity = parent_updates.get("id") or parent_updates.get("code") or parent_updates.get("name")
            current_identity = current_parent.get("id") or current_parent.get("code") or current_parent.get("name")
            if incoming_identity and current_identity and str(incoming_identity).casefold() != str(current_identity).casefold():
                current_parent = {}
                current_item_product = ""
            current_parent.update(parent_updates)
            if not (current_parent.get("id") or current_parent.get("code") or current_parent.get("name")):
                raise ValidationError("Pricelist item/tier row must follow a row with parent data.")
            if not current_parent.get("vendor"):
                raise ValidationError("Vendor Pricelist parent is missing Vendor.")
            if "date_start" in current_parent:
                current_parent["date_start"] = _import_date(current_parent["date_start"], field="Effective Date")
            else:
                current_parent["date_start"] = timezone.localdate().isoformat()
            if current_parent.get("date_end"):
                current_parent["date_end"] = _import_date(current_parent["date_end"], field="Expiry Date")
                if current_parent["date_end"] < current_parent["date_start"]:
                    raise ValidationError("Expiry Date cannot be earlier than Effective Date.")
            if "active" in current_parent:
                current_parent["active"] = _import_boolean(current_parent["active"], field="Active")
            else:
                current_parent["active"] = True
            current_parent.setdefault("currency", "IDR")

            item_fields_present = any(key.startswith("item_") for key in values)
            item = None
            if item_fields_present:
                current_item_product = str(values.get("item_product") or current_item_product).strip()
                if not current_item_product:
                    raise ValidationError("Vendor pricelist item is missing Product.")
                item = {
                    "product": current_item_product,
                    "uom": str(values.get("item_uom") or "").strip(),
                    "vendor_uom": str(values.get("item_vendor_uom") or "").strip(),
                    "variant": str(values.get("item_variant") or "standard").strip(),
                    "vendor_product_code": str(values.get("item_vendor_product_code") or "").strip(),
                    "vendor_product_name": str(values.get("item_vendor_product_name") or "").strip(),
                    "unit_price": _import_decimal(
                        values.get("item_unit_price", 0),
                        field="Item Unit Price",
                        positive=True,
                        allow_zero=False,
                    ),
                    "lead_time_days": _import_decimal(
                        values.get("item_lead_time", 1),
                        field="Item Lead Time",
                        positive=True,
                        allow_zero=False,
                    ),
                    "enforce_moq": _import_boolean(
                        values.get("item_enforce_moq", False),
                        field="Enforce MOQ",
                    ),
                }
                if not item["uom"]:
                    raise ValidationError("Vendor pricelist item is missing UoM.")

            tier_fields_present = any(key.startswith("tier_") for key in values)
            tier = None
            if tier_fields_present:
                if not current_item_product:
                    raise ValidationError("Pricelist tier row must follow a row with item data.")
                tier = {
                    "sequence": int(_import_decimal(
                        values.get("tier_sequence", 0),
                        field="Tier Number",
                        positive=True,
                        allow_zero=False,
                    )),
                    "minimum_quantity": _import_decimal(
                        values.get("tier_minimum_quantity", 0),
                        field="Tier Minimum Quantity",
                        positive=True,
                    ),
                    "maximum_quantity": _import_decimal(
                        values.get("tier_maximum_quantity", 0),
                        field="Tier Maximum Quantity",
                        positive=True,
                    ),
                    "tier_price": _import_decimal(
                        values.get("tier_price", 0),
                        field="Tier Price",
                        positive=True,
                        allow_zero=False,
                    ),
                    "discount_formula": str(values.get("tier_discount_formula") or "").strip(),
                    "free_quantity": _import_decimal(
                        values.get("tier_free_quantity", 0),
                        field="Free Quantity",
                        positive=True,
                    ),
                    "free_uom": str(values.get("tier_free_uom") or "").strip(),
                    "item_product": current_item_product,
                }
                if Decimal(tier["maximum_quantity"]) and Decimal(tier["maximum_quantity"]) < Decimal(tier["minimum_quantity"]):
                    raise ValidationError("Tier Maximum Quantity must be zero or at least the minimum quantity.")
            candidates.append(
                {
                    "row": row_number,
                    "parent": dict(current_parent),
                    "item": item,
                    "tier": tier,
                }
            )
        except (ValidationError, ValueError) as exc:
            message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            errors.append({"row": row_number, "data": raw_row, "error": message})

    vendor_map, product_map = _vendor_import_reference_maps(locked.company_id, candidates)
    valid_rows = []
    for row in candidates:
        vendor_token = str(row["parent"]["vendor"]).strip()
        vendor = vendor_map.get(vendor_token.casefold())
        product_token = str((row.get("item") or {}).get("product") or "").strip()
        if not product_token:
            product_token = str((row.get("tier") or {}).get("item_product") or "").strip()
        product = product_map.get(product_token.casefold()) if product_token else None
        row_errors = []
        if not vendor or not vendor.active or vendor.status in {
            VendorProfile.Status.REJECTED,
            VendorProfile.Status.BLACKLISTED,
        }:
            row_errors.append(f"Vendor '{vendor_token}' was not found or is not eligible.")
        if product_token and not product:
            row_errors.append(f"Product '{product_token}' was not found in the import company.")
        if row_errors:
            errors.append({"row": row["row"], "data": row, "error": " ".join(row_errors)})
            continue
        row["parent"]["vendor_id"] = vendor.pk
        row["parent"].pop("vendor", None)
        if row.get("item"):
            row["item"]["product_id"] = product.pk
            row["item"].pop("product", None)
        if row.get("tier"):
            row["tier"]["item_product_id"] = product.pk
        valid_rows.append(row)

    if not valid_rows:
        locked.status = VendorPricelistImportLog.Status.FAILED
    else:
        locked.status = VendorPricelistImportLog.Status.VALIDATED
    locked.total_rows = len(valid_rows) + len(errors)
    locked.valid_rows = len(valid_rows)
    locked.error_rows = len(errors)
    locked.skipped_blank_rows = skipped
    locked.normalized_data = valid_rows
    locked.errors = errors
    locked.requested_by = actor
    locked.started_at = None
    locked.completed_at = None
    locked.inserted_count = 0
    locked.updated_count = 0
    locked.save(
        update_fields=[
            "status",
            "total_rows",
            "valid_rows",
            "error_rows",
            "skipped_blank_rows",
            "normalized_data",
            "errors",
            "requested_by",
            "started_at",
            "completed_at",
            "inserted_count",
            "updated_count",
            "updated_at",
        ]
    )
    VendorPricelistImportPreview.objects.update_or_create(
        import_log=locked,
        defaults={
            "valid_data": valid_rows[:MAX_VENDOR_PRICELIST_PREVIEW_ROWS],
            "error_data": errors[:MAX_VENDOR_PRICELIST_PREVIEW_ROWS],
            "available_columns": headers,
            "validated_at": timezone.now(),
        },
    )
    return locked


def _vendor_import_parent_key(parent):
    return str(parent.get("id") or parent.get("code") or parent.get("name") or "").strip().casefold()


@transaction.atomic
def process_vendor_pricelist_import(import_log):
    locked = VendorPricelistImportLog.objects.select_for_update(of=("self",)).get(pk=_pk(import_log))
    if locked.status != VendorPricelistImportLog.Status.PROCESSING:
        raise ValidationError("Only a Processing vendor pricelist import can be executed.")
    rows = _require_bounded(
        list(locked.normalized_data or []),
        limit=MAX_VENDOR_PRICELIST_IMPORT_ROWS,
        label="Vendor pricelist import",
    )
    if not rows:
        raise ValidationError("The import has no validated rows.")

    grouped = {}
    for row in rows:
        parent = row["parent"]
        parent_key = _vendor_import_parent_key(parent)
        group = grouped.setdefault(parent_key, {"parent": dict(parent), "items": {}})
        group["parent"].update(parent)
        item = row.get("item")
        tier = row.get("tier")
        product_id = (item or {}).get("product_id") or (tier or {}).get("item_product_id")
        if item:
            product_id = item["product_id"]
            item_group = group["items"].setdefault(product_id, {"item": dict(item), "tiers": {}})
            item_group["item"].update(item)
        if tier:
            if product_id not in group["items"]:
                raise ValidationError("Validated tier lost its parent item; validate the import again.")
            clean_tier = dict(tier)
            clean_tier.pop("item_product", None)
            clean_tier.pop("item_product_id", None)
            group["items"][product_id]["tiers"][clean_tier["sequence"]] = clean_tier

    parent_ids = [int(group["parent"]["id"]) for group in grouped.values() if str(group["parent"].get("id") or "").isdigit()]
    parent_codes = [group["parent"].get("code") for group in grouped.values() if group["parent"].get("code")]
    parent_names = [group["parent"].get("name") for group in grouped.values() if group["parent"].get("name")]
    existing_parents = _require_bounded(
        list(
            VendorPricelist.objects.filter(company_id=locked.company_id)
            .filter(Q(pk__in=parent_ids) | Q(code__in=parent_codes) | Q(name__in=parent_names))
            .order_by("pk")[: MAX_VENDOR_PRICELIST_IMPORT_ROWS + 1]
        ),
        limit=MAX_VENDOR_PRICELIST_IMPORT_ROWS,
        label="Existing Vendor Pricelists",
    )
    existing_map = {}
    for parent in existing_parents:
        existing_map[str(parent.pk).casefold()] = parent
        if parent.code:
            existing_map[parent.code.casefold()] = parent
        if parent.name:
            existing_map[parent.name.casefold()] = parent

    now = timezone.now()
    new_parents = []
    changed_parents = []
    parent_by_key = {}
    for key, group in grouped.items():
        values = group["parent"]
        parent = existing_map.get(key)
        if parent is None:
            parent = VendorPricelist(company_id=locked.company_id, status=VendorPricelist.Status.DRAFT)
            new_parents.append(parent)
        else:
            changed_parents.append(parent)
        parent.code = values.get("code") or parent.code or f"VPL-{uuid4().hex[:10].upper()}"
        parent.name = values.get("name") or parent.name or parent.code
        parent.vendor_id = values["vendor_id"]
        parent.currency = str(values.get("currency") or "IDR")[:3].upper()
        parent.date_start = date.fromisoformat(values["date_start"])
        parent.date_end = date.fromisoformat(values["date_end"]) if values.get("date_end") else None
        parent.active = bool(values.get("active", True))
        parent.source_import = locked
        parent.updated_at = now
        parent_by_key[key] = parent
    if new_parents:
        VendorPricelist.objects.bulk_create(new_parents, batch_size=500)
    if changed_parents:
        VendorPricelist.objects.bulk_update(
            changed_parents,
            ["code", "name", "vendor", "currency", "date_start", "date_end", "active", "source_import", "updated_at"],
            batch_size=500,
        )

    imported_pairs = []
    for key, group in grouped.items():
        parent = parent_by_key[key]
        imported_pairs.extend((parent.pk, int(product_id)) for product_id in group["items"])
    item_query = reduce(or_, (Q(pricelist_id=parent_id, product_id=product_id) for parent_id, product_id in imported_pairs), Q(pk__in=[]))
    existing_items = _require_bounded(
        list(
            VendorPricelistItem.objects.filter(item_query)
            .order_by("pk")[: MAX_VENDOR_PRICELIST_IMPORT_ROWS + 1]
        ),
        limit=MAX_VENDOR_PRICELIST_IMPORT_ROWS,
        label="Existing Vendor Pricelist Items",
    )
    item_map = {(item.pricelist_id, item.product_id): item for item in existing_items}
    new_items = []
    changed_items = []
    for key, group in grouped.items():
        parent = parent_by_key[key]
        for product_id, state in group["items"].items():
            values = state["item"]
            item = item_map.get((parent.pk, int(product_id)))
            if item is None:
                item = VendorPricelistItem(pricelist=parent, product_id=product_id)
                new_items.append(item)
            else:
                changed_items.append(item)
            item.uom = values["uom"]
            item.vendor_uom = values.get("vendor_uom", "")
            item.variant = values.get("variant", "standard")
            item.vendor_product_code = values.get("vendor_product_code", "")
            item.vendor_product_name = values.get("vendor_product_name", "")
            item.unit_price = Decimal(values["unit_price"])
            item.lead_time_days = Decimal(values["lead_time_days"])
            item.enforce_moq = bool(values.get("enforce_moq"))
            item.active = True
            item.updated_at = now
            item_map[(parent.pk, int(product_id))] = item
    if new_items:
        VendorPricelistItem.objects.bulk_create(new_items, batch_size=500)
    if changed_items:
        VendorPricelistItem.objects.bulk_update(
            changed_items,
            [
                "uom",
                "vendor_uom",
                "variant",
                "vendor_product_code",
                "vendor_product_name",
                "unit_price",
                "lead_time_days",
                "enforce_moq",
                "active",
                "updated_at",
            ],
            batch_size=500,
        )

    tier_pairs = []
    tier_states = {}
    for key, group in grouped.items():
        parent = parent_by_key[key]
        for product_id, state in group["items"].items():
            item = item_map[(parent.pk, int(product_id))]
            for sequence, tier_values in state["tiers"].items():
                tier_pairs.append((item.pk, int(sequence)))
                tier_states[(item.pk, int(sequence))] = tier_values
    tier_query = reduce(or_, (Q(item_id=item_id, sequence=sequence) for item_id, sequence in tier_pairs), Q(pk__in=[]))
    existing_tiers = _require_bounded(
        list(
            VendorPricelistTier.objects.filter(tier_query)
            .order_by("pk")[: MAX_VENDOR_PRICELIST_IMPORT_ROWS + 1]
        ),
        limit=MAX_VENDOR_PRICELIST_IMPORT_ROWS,
        label="Existing Vendor Pricelist Tiers",
    )
    tier_map = {(tier.item_id, tier.sequence): tier for tier in existing_tiers}
    new_tiers = []
    changed_tiers = []
    for pair, values in tier_states.items():
        tier = tier_map.get(pair)
        if tier is None:
            tier = VendorPricelistTier(item_id=pair[0], sequence=pair[1])
            new_tiers.append(tier)
        else:
            changed_tiers.append(tier)
        tier.minimum_quantity = Decimal(values["minimum_quantity"])
        tier.maximum_quantity = Decimal(values["maximum_quantity"])
        tier.tier_price = Decimal(values["tier_price"])
        tier.discount_formula = values.get("discount_formula", "")
        tier.free_quantity = Decimal(values.get("free_quantity", "0"))
        tier.free_uom = values.get("free_uom", "")
        tier.updated_at = now
    if new_tiers:
        VendorPricelistTier.objects.bulk_create(new_tiers, batch_size=500)
    if changed_tiers:
        VendorPricelistTier.objects.bulk_update(
            changed_tiers,
            [
                "minimum_quantity",
                "maximum_quantity",
                "tier_price",
                "discount_formula",
                "free_quantity",
                "free_uom",
                "updated_at",
            ],
            batch_size=500,
        )

    locked.inserted_count = len(new_parents)
    locked.updated_count = len(changed_parents)
    locked.status = (
        VendorPricelistImportLog.Status.PARTIAL
        if locked.error_rows
        else VendorPricelistImportLog.Status.COMPLETED
    )
    locked.completed_at = timezone.now()
    locked.save(
        update_fields=["inserted_count", "updated_count", "status", "completed_at", "updated_at"]
    )
    return locked


@transaction.atomic
def transition_vendor_pricelist_import(import_log, action, actor=None):
    locked = VendorPricelistImportLog.objects.select_for_update(of=("self",)).get(pk=_pk(import_log))
    if action == "validate":
        return validate_vendor_pricelist_import(locked, actor)
    if action != "import":
        raise ValidationError("Unsupported Vendor Pricelist Import action.")
    if locked.status != VendorPricelistImportLog.Status.VALIDATED:
        raise ValidationError("Validate the Vendor Pricelist CSV before importing it.")
    locked.status = VendorPricelistImportLog.Status.PROCESSING
    locked.requested_by = actor
    locked.started_at = timezone.now()
    locked.completed_at = None
    locked.save(update_fields=["status", "requested_by", "started_at", "completed_at", "updated_at"])

    from .tasks import process_vendor_pricelist_import_task

    transaction.on_commit(
        lambda: process_vendor_pricelist_import_task.apply_async(kwargs={"import_log_id": locked.pk})
    )
    return locked


def _amendment_decimal(value, field_label):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"{field_label} amendment value must be numeric.")


def _amendment_boolean(value):
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValidationError("MOQ amendment value must be true or false.")


def _assert_amendment_old_value(target, line):
    if line.old_value == "":
        return
    current = getattr(target, line.field_key)
    expected = line.old_value
    if isinstance(current, Decimal):
        matches = current == _amendment_decimal(expected, line.field_key)
    elif isinstance(current, bool):
        matches = current == _amendment_boolean(expected)
    else:
        matches = str(current or "") == expected
    if not matches:
        raise ValidationError(
            f"Vendor Pricelist amendment is stale for {line.field_key}; create a new amendment."
        )


@transaction.atomic
def transition_vendor_pricelist_amendment(amendment, action, actor=None):
    locked = (
        VendorPricelistAmendment.objects.select_for_update(of=("self",))
        .select_related("pricelist")
        .get(pk=_pk(amendment))
    )
    transitions = {
        "submit": (
            {VendorPricelistAmendment.Status.DRAFT, VendorPricelistAmendment.Status.SUBMITTED},
            VendorPricelistAmendment.Status.WAITING_APPROVAL,
        ),
        "approve": (
            {VendorPricelistAmendment.Status.WAITING_APPROVAL},
            VendorPricelistAmendment.Status.APPROVED,
        ),
        "reject": (
            {VendorPricelistAmendment.Status.WAITING_APPROVAL},
            VendorPricelistAmendment.Status.REJECTED,
        ),
        "cancel": (
            {
                VendorPricelistAmendment.Status.DRAFT,
                VendorPricelistAmendment.Status.SUBMITTED,
                VendorPricelistAmendment.Status.WAITING_APPROVAL,
            },
            VendorPricelistAmendment.Status.CANCELLED,
        ),
        "reset": (
            {
                VendorPricelistAmendment.Status.REJECTED,
                VendorPricelistAmendment.Status.CANCELLED,
            },
            VendorPricelistAmendment.Status.DRAFT,
        ),
    }
    if action not in transitions:
        raise ValidationError("Unsupported Vendor Pricelist amendment action.")
    sources, target_status = transitions[action]
    if locked.status not in sources:
        raise ValidationError("That Vendor Pricelist amendment action is not available in the current state.")
    lines = _require_bounded(
        list(
            VendorPricelistAmendmentLine.objects.select_for_update(of=("self",))
            .filter(amendment_id=locked.pk)
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Vendor Pricelist amendment",
    )
    if action == "submit":
        if not lines and locked.new_price <= 0:
            raise ValidationError("Add at least one amendment line before submitting.")
        if VendorPricelistAmendment.objects.filter(
            pricelist_id=locked.pricelist_id,
            status=VendorPricelistAmendment.Status.WAITING_APPROVAL,
        ).exclude(pk=locked.pk).exists():
            raise ValidationError("This Vendor Pricelist already has a pending amendment.")
    if action == "approve":
        item_ids = {line.item_id for line in lines if line.item_id}
        tier_ids = {line.tier_id for line in lines if line.tier_id}
        items = VendorPricelistItem.objects.select_for_update().in_bulk(item_ids)
        tiers = VendorPricelistTier.objects.select_for_update().select_related("item").in_bulk(tier_ids)
        item_fields = set()
        tier_fields = set()
        now = timezone.now()
        for line in lines:
            target = items.get(line.item_id) if line.item_id else tiers.get(line.tier_id)
            if target is None:
                raise ValidationError("Vendor Pricelist amendment target no longer exists.")
            target_pricelist_id = target.pricelist_id if line.item_id else target.item.pricelist_id
            if target_pricelist_id != locked.pricelist_id:
                raise ValidationError("Vendor Pricelist amendment target belongs to another pricelist.")
            _assert_amendment_old_value(target, line)
            if line.field_key in {"unit_price", "lead_time_days", "minimum_quantity", "maximum_quantity", "tier_price", "free_quantity"}:
                value = _amendment_decimal(line.new_value, line.field_key)
            elif line.field_key == "enforce_moq":
                value = _amendment_boolean(line.new_value)
            else:
                value = line.new_value.strip()
            if line.field_key in {"unit_price", "lead_time_days", "tier_price"} and value <= 0:
                raise ValidationError(f"{line.field_key} must be greater than zero.")
            if line.field_key in {"minimum_quantity", "maximum_quantity", "free_quantity"} and value < 0:
                raise ValidationError(f"{line.field_key} cannot be negative.")
            if line.field_key == "discount_formula":
                VendorPricelistTier.apply_discount_formula(target.tier_price, value)
            setattr(target, line.field_key, value)
            target.updated_at = now
            (item_fields if line.item_id else tier_fields).add(line.field_key)
        for tier in tiers.values():
            if tier.maximum_quantity and tier.maximum_quantity < tier.minimum_quantity:
                raise ValidationError("Amended tier maximum quantity cannot be below its minimum quantity.")
        enforced_item_ids = {item.pk for item in items.values() if item.enforce_moq}
        enforced_item_ids.update(tier.item_id for tier in tiers.values() if tier.item.enforce_moq)
        if enforced_item_ids:
            moq_tiers = _require_bounded(
                list(
                    VendorPricelistTier.objects.filter(item_id__in=enforced_item_ids)
                    .order_by("item_id", "sequence", "pk")
                    .values("pk", "item_id", "minimum_quantity")[: MAX_VENDOR_PRICELIST_MATCHES + 1]
                ),
                limit=MAX_VENDOR_PRICELIST_MATCHES,
                label="Amended Vendor Pricelist MOQ tiers",
            )
            first_minimums = {}
            for row in moq_tiers:
                changed_tier = tiers.get(row["pk"])
                minimum = changed_tier.minimum_quantity if changed_tier else row["minimum_quantity"]
                first_minimums.setdefault(row["item_id"], minimum)
            if any(first_minimums.get(item_id, ZERO) <= 0 for item_id in enforced_item_ids):
                raise ValidationError("Every MOQ-enforced pricelist item requires a positive first tier.")
        if item_fields:
            VendorPricelistItem.objects.bulk_update(
                list(items.values()),
                sorted(item_fields | {"updated_at"}),
                batch_size=500,
            )
        if tier_fields:
            VendorPricelistTier.objects.bulk_update(
                list(tiers.values()),
                sorted(tier_fields | {"updated_at"}),
                batch_size=500,
            )
        if not lines and locked.new_price > 0:
            locked.pricelist.unit_price = locked.new_price
            locked.pricelist.save(update_fields=["unit_price", "updated_at"])
        locked.approver = actor
    locked.status = target_status
    update_fields = ["status", "updated_at"]
    if action == "approve":
        update_fields.append("approver")
    locked.save(update_fields=update_fields)
    return locked


def _vendor_performance_period_start(period_end, months):
    """Return the same day N months earlier without requiring a calendar loop."""
    month_index = (period_end.year * 12 + period_end.month - 1) - max(int(months or 0), 0)
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    if month == 12:
        next_month = period_end.replace(year=year + 1, month=1, day=1)
    else:
        next_month = period_end.replace(year=year, month=month + 1, day=1)
    last_day = (next_month - timedelta(days=1)).day
    return period_end.replace(year=year, month=month, day=min(period_end.day, last_day))


def _validate_vendor_performance_template(template, template_lines):
    if not template.active:
        raise ValidationError("The Vendor Performance template must be active.")
    active_lines = [line for line in template_lines if line.active]
    if not active_lines:
        raise ValidationError("Add at least one active evaluation parameter to the template.")
    if sum((line.weight for line in active_lines), ZERO) != HUNDRED:
        raise ValidationError("Active Vendor Performance parameter weights must total 100%.")
    if template.qualitative_weight + template.quantitative_weight != HUNDRED:
        raise ValidationError("Qualitative and quantitative weights must total 100%.")
    if template.delivery_accuracy_weight + template.rejection_rate_weight != HUNDRED:
        raise ValidationError("Delivery accuracy and rejection rate weights must total 100%.")


def _vendor_performance_qualitative_score(lines, score_scale):
    assessment_values = {
        VendorPerformanceEvaluationLine.Assessment.VERY_POOR: Decimal("1"),
        VendorPerformanceEvaluationLine.Assessment.POOR: Decimal("2"),
        VendorPerformanceEvaluationLine.Assessment.FAIR: Decimal("3"),
        VendorPerformanceEvaluationLine.Assessment.GOOD: Decimal("4"),
        VendorPerformanceEvaluationLine.Assessment.EXCELLENT: Decimal("5"),
    }
    weighted = ZERO
    total_weight = ZERO
    fallback = []
    for line in lines:
        if not line.active:
            continue
        value = line.score if line.score is not None else assessment_values.get(line.assessment)
        if value is None:
            continue
        fallback.append(value)
        if line.maximum_score > 0 and line.weight > 0:
            weighted += value / line.maximum_score * score_scale * line.weight / HUNDRED
            total_weight += line.weight
    if total_weight > 0:
        return weighted.quantize(Decimal("0.01"))
    if fallback:
        return (sum(fallback, ZERO) / len(fallback)).quantize(Decimal("0.01"))
    return ZERO


def _clamped_score(value, score_scale):
    return min(max(value, ZERO), max(score_scale, ZERO)).quantize(Decimal("0.01"))


@transaction.atomic
def recalculate_vendor_performance(
    vendor,
    actor=None,
    *,
    template=None,
    period_start=None,
    period_end=None,
):
    locked_vendor = (
        VendorProfile.objects.select_for_update(of=("self",))
        .select_related("company", "partner")
        .get(pk=_pk(vendor))
    )
    setting = (
        PurchaseSetting.objects.select_related("vendor_performance_default_template")
        .filter(company_id=locked_vendor.company_id)
        .first()
    )
    if setting is None or not setting.vendor_performance_scoring_enabled:
        raise ValidationError("Vendor Performance Scoring is disabled in Purchase Settings.")
    template = template or setting.vendor_performance_default_template
    if template is None:
        raise ValidationError("A default Vendor Performance Scoring Template is required.")
    template = VendorPerformanceTemplate.objects.select_related("company").get(pk=_pk(template))
    if template.company_id != locked_vendor.company_id:
        raise ValidationError("The scoring template company must match the vendor company.")
    template_lines = _require_bounded(
        list(
            VendorPerformanceTemplateLine.objects.filter(template_id=template.pk)
            .order_by("sequence", "pk")[: MAX_VENDOR_PERFORMANCE_LINES + 1]
        ),
        limit=MAX_VENDOR_PERFORMANCE_LINES,
        label="Vendor Performance template",
    )
    _validate_vendor_performance_template(template, template_lines)

    period_end = period_end or timezone.localdate()
    period_start = period_start or _vendor_performance_period_start(period_end, template.evaluation_period)
    if period_start >= period_end:
        raise ValidationError("Vendor Performance period start must be earlier than period end.")

    evidence_rows = _require_bounded(
        list(
            GoodsReceiptLine.objects.filter(
                receipt__company_id=locked_vendor.company_id,
                receipt__order__vendor_id=locked_vendor.partner_id,
                receipt__status=GoodsReceipt.Status.RECEIVED,
                receipt__receipt_date__range=(period_start, period_end),
            )
            .values("order_line_id", "order_line__quantity")
            .annotate(
                received_quantity=Sum("received_quantity"),
                accepted_quantity=Sum("accepted_quantity"),
                rejected_quantity=Sum("rejected_quantity"),
                actual_receipt_date=Max("receipt__receipt_date"),
                late_receipt_count=Count(
                    "receipt_id",
                    filter=Q(receipt__delivery_status=GoodsReceipt.DeliveryStatus.LATE),
                    distinct=True,
                ),
            )
            .order_by("order_line_id")[: MAX_VENDOR_PERFORMANCE_EVIDENCE_LINES + 1]
        ),
        limit=MAX_VENDOR_PERFORMANCE_EVIDENCE_LINES,
        label="Vendor Performance receipt evidence",
    )
    completed_rows = [
        row
        for row in evidence_rows
        if (row["accepted_quantity"] or ZERO) >= (row["order_line__quantity"] or ZERO) > ZERO
    ]
    completed_count = len(completed_rows)
    on_time_count = sum(1 for row in completed_rows if not row["late_receipt_count"])
    received_quantity = sum((row["received_quantity"] or ZERO for row in evidence_rows), ZERO)
    rejected_quantity = sum((row["rejected_quantity"] or ZERO for row in evidence_rows), ZERO)
    score_scale = template.score_scale
    delivery_accuracy_score = (
        _clamped_score(Decimal(on_time_count) / Decimal(completed_count) * score_scale, score_scale)
        if completed_count
        else ZERO
    )
    rejection_rate_score = (
        _clamped_score((Decimal("1") - min(rejected_quantity / received_quantity, Decimal("1"))) * score_scale, score_scale)
        if received_quantity > 0
        else score_scale.quantize(Decimal("0.01"))
    )
    quantitative_score = (
        delivery_accuracy_score * template.delivery_accuracy_weight / HUNDRED
        + rejection_rate_score * template.rejection_rate_weight / HUNDRED
    ).quantize(Decimal("0.01"))

    qualitative_evaluation = (
        VendorPerformanceEvaluation.objects.filter(
            vendor_id=locked_vendor.pk,
            status=VendorPerformanceEvaluation.Status.CONFIRMED,
            period_start__gte=period_start,
            period_end__lte=period_end,
        )
        .prefetch_related(
            Prefetch(
                "lines",
                queryset=VendorPerformanceEvaluationLine.objects.order_by("sequence", "pk"),
                to_attr="scoring_lines",
            )
        )
        .order_by("-confirmed_at", "-pk")
        .first()
    )
    qualitative_score = (
        qualitative_evaluation.qualitative_score if qualitative_evaluation is not None else ZERO
    )
    final_score = (
        qualitative_score * template.qualitative_weight / HUNDRED
        + quantitative_score * template.quantitative_weight / HUNDRED
    ).quantize(Decimal("0.01"))
    score_status = VendorPerformanceScoreHistory.Status.CALCULATED
    if completed_count < template.minimum_transaction_threshold:
        if template.partial_scoring_rule == VendorPerformanceTemplate.PartialScoringRule.QUALITATIVE_ONLY:
            final_score = qualitative_score.quantize(Decimal("0.01"))
            score_status = VendorPerformanceScoreHistory.Status.PARTIAL
        elif template.partial_scoring_rule == VendorPerformanceTemplate.PartialScoringRule.BLOCK_FINAL_SCORE:
            final_score = ZERO
            score_status = VendorPerformanceScoreHistory.Status.BLOCKED
        else:
            score_status = VendorPerformanceScoreHistory.Status.INSUFFICIENT

    history = VendorPerformanceScoreHistory.objects.create(
        vendor=locked_vendor,
        template=template,
        period_start=period_start,
        period_end=period_end,
        qualitative_score=qualitative_score,
        delivery_accuracy_score=delivery_accuracy_score,
        rejection_rate_score=rejection_rate_score,
        quantitative_score=quantitative_score,
        final_score=final_score,
        status=score_status,
        source_transaction_summary={
            "completed_count": completed_count,
            "on_time_count": on_time_count,
            "received_quantity": str(received_quantity),
            "rejected_quantity": str(rejected_quantity),
            "source_order_line_ids": [row["order_line_id"] for row in evidence_rows[:100]],
            "source_order_line_ids_truncated": len(evidence_rows) > 100,
        },
        template_snapshot={
            "id": template.pk,
            "code": template.code,
            "evaluation_period": template.evaluation_period,
            "score_scale": str(template.score_scale),
            "qualitative_weight": str(template.qualitative_weight),
            "quantitative_weight": str(template.quantitative_weight),
            "delivery_accuracy_weight": str(template.delivery_accuracy_weight),
            "rejection_rate_weight": str(template.rejection_rate_weight),
            "minimum_transaction_threshold": template.minimum_transaction_threshold,
            "partial_scoring_rule": template.partial_scoring_rule,
        },
        qualitative_evaluation=qualitative_evaluation,
        evaluator=actor,
    )
    VendorProfile.objects.filter(pk=locked_vendor.pk).update(
        latest_performance_score=final_score,
        latest_performance_score_date=period_end,
        latest_performance_score_status=score_status,
        latest_performance_score_period_start=period_start,
        latest_performance_score_period_end=period_end,
        latest_performance_score_template=template,
        updated_at=timezone.now(),
    )
    locked_vendor.latest_performance_score = final_score
    locked_vendor.latest_performance_score_date = period_end
    locked_vendor.latest_performance_score_status = score_status
    locked_vendor.latest_performance_score_period_start = period_start
    locked_vendor.latest_performance_score_period_end = period_end
    locked_vendor.latest_performance_score_template = template
    history._vendor = locked_vendor
    return history


@transaction.atomic
def transition_vendor_performance_evaluation(evaluation, action, actor=None, *, reason=""):
    locked = (
        VendorPerformanceEvaluation.objects.select_for_update(of=("self",))
        .select_related("vendor", "template")
        .get(pk=_pk(evaluation))
    )
    transitions = {
        "submit": ({VendorPerformanceEvaluation.Status.DRAFT}, VendorPerformanceEvaluation.Status.WAITING_APPROVAL),
        "approve": (
            {VendorPerformanceEvaluation.Status.WAITING_APPROVAL},
            VendorPerformanceEvaluation.Status.CONFIRMED,
        ),
        "reject": (
            {VendorPerformanceEvaluation.Status.WAITING_APPROVAL},
            VendorPerformanceEvaluation.Status.REJECTED,
        ),
    }
    if action not in transitions:
        raise ValidationError("Unsupported Vendor Performance Evaluation action.")
    sources, target = transitions[action]
    if locked.status not in sources:
        raise ValidationError("That Vendor Performance Evaluation action is not available in the current state.")
    lines = _require_bounded(
        list(
            VendorPerformanceEvaluationLine.objects.select_for_update(of=("self",))
            .filter(evaluation_id=locked.pk)
            .order_by("sequence", "pk")[: MAX_VENDOR_PERFORMANCE_LINES + 1]
        ),
        limit=MAX_VENDOR_PERFORMANCE_LINES,
        label="Vendor Performance Evaluation",
    )
    if action == "submit":
        template_lines = _require_bounded(
            list(
                VendorPerformanceTemplateLine.objects.filter(template_id=locked.template_id)
                .order_by("sequence", "pk")[: MAX_VENDOR_PERFORMANCE_LINES + 1]
            ),
            limit=MAX_VENDOR_PERFORMANCE_LINES,
            label="Vendor Performance template",
        )
        _validate_vendor_performance_template(locked.template, template_lines)
        if locked.period_start >= locked.period_end:
            raise ValidationError("Vendor Performance period start must be earlier than period end.")
        if locked.vendor.company_id != locked.company_id or locked.template.company_id != locked.company_id:
            raise ValidationError("Vendor, template, and evaluation must belong to the same company.")
        if not lines:
            raise ValidationError("At least one evaluation line is required before submission.")
        missing = [line.parameter_name for line in lines if line.active and line.score is None and not line.assessment]
        if missing:
            raise ValidationError("All active evaluation lines must have a score or assessment before submission.")
        locked.qualitative_score = _vendor_performance_qualitative_score(lines, locked.template.score_scale)
    elif action == "reject":
        reason = " ".join((reason or "").split())
        if len(reason) < 10:
            raise ValidationError("Vendor Performance rejection reason must contain at least 10 characters.")
        locked.rejection_reason = reason
    elif action == "approve":
        if not lines:
            raise ValidationError("Vendor Performance Evaluation has no snapshot lines.")
        locked.qualitative_score = _vendor_performance_qualitative_score(lines, locked.template.score_scale)
        locked.confirmed_by = actor
        locked.confirmed_at = timezone.now()
    locked.status = target
    update_fields = ["status", "updated_at"]
    if action in {"submit", "approve"}:
        update_fields.append("qualitative_score")
    if action == "approve":
        update_fields.extend(["confirmed_by", "confirmed_at"])
    if action == "reject":
        update_fields.append("rejection_reason")
    locked.save(update_fields=update_fields)

    if action == "approve":
        setting = PurchaseSetting.objects.filter(company_id=locked.company_id).only(
            "vendor_performance_scoring_enabled",
            "vendor_performance_default_template",
        ).first()
        if setting and setting.vendor_performance_scoring_enabled:
            recalculate_vendor_performance(
                locked.vendor,
                actor,
                template=setting.vendor_performance_default_template_id or locked.template_id,
                period_start=locked.period_start,
                period_end=locked.period_end,
            )
    return locked


@transaction.atomic
def transition_direct_purchase(direct_purchase, action, actor=None):
    locked = (
        DirectPurchase.objects.select_for_update(of=("self",))
        .select_related("vendor", "warehouse", "request")
        .get(pk=_pk(direct_purchase))
    )
    if action in {"submit", "reject", "cancel", "reset"}:
        transitions = {
            "submit": ({"draft"}, "submitted"),
            "reject": ({"submitted"}, "rejected"),
            "cancel": ({"draft", "submitted", "rejected"}, "cancelled"),
            "reset": ({"rejected", "cancelled"}, "draft"),
        }
        sources, target = transitions[action]
        if locked.status not in sources:
            raise ValidationError("That Direct Purchase action is not available in the current state.")
        locked.status = target
        locked.approver = actor if action == "reject" else locked.approver
        update_fields = ["status", "updated_at"]
        if action == "reject":
            update_fields.append("approver")
        locked.save(update_fields=update_fields)
        if action in {"reject", "cancel", "reset"}:
            request_line_ids = list(locked.lines.values_list("request_line_id", flat=True))
            line_totals = {
                row["request_line_id"]: row["quantity"] or ZERO
                for row in DirectPurchaseLine.objects.filter(
                    request_line_id__in=[value for value in request_line_ids if value]
                )
                .exclude(direct_purchase__status__in=["rejected", "cancelled"])
                .values("request_line_id")
                .annotate(quantity=Sum("quantity"))
            }
            request_lines = list(
                PurchaseRequestLine.objects.select_for_update().filter(
                    pk__in=[value for value in request_line_ids if value]
                )
            )
            now = timezone.now()
            for line in request_lines:
                line.direct_purchase_quantity = line_totals.get(line.pk, ZERO)
                _advance_request_line_status(line)
                line.updated_at = now
            PurchaseRequestLine.objects.bulk_update(
                request_lines,
                ["direct_purchase_quantity", "line_status", "updated_at"],
                batch_size=500,
            )
        return locked
    if action != "approve":
        raise ValidationError("Unsupported Direct Purchase workflow action.")
    if locked.status != "submitted":
        raise ValidationError("Only a submitted Direct Purchase can be approved.")
    if not locked.warehouse_id:
        raise ValidationError("Warehouse is required before approving Direct Purchase.")
    direct_lines = _require_bounded(
        list(
            DirectPurchaseLine.objects.select_for_update(of=("self",))
            .filter(direct_purchase_id=locked.pk)
            .select_related("product")
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Direct Purchase",
    )
    if not direct_lines:
        raise ValidationError("Direct Purchase requires at least one product line.")
    if locked.purchase_order_id:
        locked.status = "approved"
        locked.approver = actor
        locked.save(update_fields=["status", "approver", "updated_at"])
        return locked
    number = (
        allocate_purchase_document_number(
            locked.order_type_id,
            locked.company_id,
            "direct_purchase_order",
        )
        if locked.order_type_id
        else f"DPO-{timezone.localdate():%Y%m%d}-{uuid4().hex[:8].upper()}"
    )
    order = PurchaseOrder.objects.create(
        company_id=locked.company_id,
        number=number,
        vendor_id=locked.vendor_id,
        warehouse_id=locked.warehouse_id,
        order_date=locked.purchase_date,
        total=locked.amount,
        notes=f"Created from Direct Purchase {locked.number}.",
    )
    PurchaseOrderLine.objects.bulk_create(
        [
            PurchaseOrderLine(
                order=order,
                product_id=line.product_id,
                description=line.description,
                warehouse_id=locked.warehouse_id,
                quantity=line.quantity,
                unit_price=line.unit_price,
            )
            for line in direct_lines
        ],
        batch_size=500,
    )
    PurchaseOrderControl.objects.create(
        order=order,
        order_type_id=locked.order_type_id,
        order_template_id=locked.order_template_id,
    )
    confirm_purchase_order(order, actor)
    setting = PurchaseSetting.objects.filter(company_id=locked.company_id).only(
        "receiving_behavior",
        "validate_bill_behavior",
    ).first()
    receipts = _require_bounded(
        list(
            GoodsReceipt.objects.filter(order_id=order.pk)
            .order_by("pk")[: MAX_GOODS_RECEIPTS_PER_ORDER + 1]
        ),
        limit=MAX_GOODS_RECEIPTS_PER_ORDER,
        label="Direct Purchase goods receipt history",
    )
    if not receipts:
        create_goods_receipts(order, actor)
        receipts = _require_bounded(
            list(
                GoodsReceipt.objects.filter(order_id=order.pk)
                .order_by("pk")[: MAX_GOODS_RECEIPTS_PER_ORDER + 1]
            ),
            limit=MAX_GOODS_RECEIPTS_PER_ORDER,
            label="Direct Purchase goods receipt history",
        )
    if setting and setting.receiving_behavior == PurchaseSetting.ReceivingBehavior.AUTO:
        if len(receipts) != 1:
            raise ValidationError("Direct Purchase automatic receiving requires exactly one destination warehouse.")
        inspect_goods_receipt(receipts[0], actor)
        receive_goods_receipt(receipts[0], actor)
    billed_order = create_purchase_vendor_bill(order, actor)
    invoice_id = billed_order._created_vendor_bill_id
    if setting and setting.validate_bill_behavior == PurchaseSetting.ValidateBillBehavior.AUTO:
        from modules.finance.models import AccountJournal, AccountPaymentMethod
        from modules.finance.services import post_invoice, register_invoice_payment

        payment_method = (
            AccountPaymentMethod.objects.select_related("journal")
            .filter(
                company_id=locked.company_id,
                active=True,
                direction__in=["outgoing", "both"],
                journal__active=True,
                journal__journal_type=AccountJournal.JournalType.CASH,
            )
            .order_by("pk")
            .first()
        )
        if payment_method is None:
            raise ValidationError("No active cash payment method is configured for automatic Direct Purchase payment.")
        invoice = post_invoice(Invoice.objects.get(pk=invoice_id), actor)
        register_invoice_payment(
            invoice,
            payment_amount=invoice.total,
            payment_date=timezone.localdate(),
            payment_method=payment_method,
            reference=f"Direct Purchase {locked.number}",
            actor=actor,
        )
        PurchaseAccountingDocument.objects.filter(invoice_id=invoice.pk).update(
            status="approved",
            updated_at=timezone.now(),
        )
    locked.purchase_order = order
    locked.status = "approved"
    locked.approver = actor
    locked.save(update_fields=["purchase_order", "status", "approver", "updated_at"])
    return locked


def _load_tender_award_data(locked):
    allocations = _require_bounded(
        list(
            TenderAwardAllocation.objects.select_for_update(of=("self",))
            .filter(tender_id=locked.pk)
            .select_related("tender_line", "bid_line", "bid_line__bid", "bid_line__bid__vendor")
            .order_by("tender_line_id", "rank", "pk")[: MAX_TENDER_AWARD_ALLOCATIONS + 1]
        ),
        limit=MAX_TENDER_AWARD_ALLOCATIONS,
        label="Tender award allocation",
    )
    if allocations:
        winning_bids_by_id = {}
        for allocation in allocations:
            bid = allocation.bid_line.bid
            if bid.status != TenderBid.Status.AWARDED:
                raise ValidationError("Every allocated Tender vendor must be marked Awarded.")
            if allocation.bid_line.no_bid or not allocation.bid_line.compliant:
                raise ValidationError("Tender awards require a compliant submitted bid line.")
            if allocation.allocated_quantity <= 0:
                raise ValidationError("Tender award quantities must be greater than zero.")
            if allocation.allocated_quantity > allocation.bid_line.offered_quantity:
                raise ValidationError("Tender award quantity exceeds the vendor offered quantity.")
            if allocation.unit_price < 0:
                raise ValidationError("Tender award unit price cannot be negative.")
            winning_bids_by_id[bid.pk] = bid
        winning_bids = list(winning_bids_by_id.values())
    else:
        winning_bids = list(
            TenderBid.objects.select_for_update(of=("self",))
            .filter(tender_id=locked.pk, status=TenderBid.Status.AWARDED)
            .select_related("vendor")
            .order_by("pk")[: MAX_RFQ_VENDORS + 1]
        )
    if not winning_bids:
        raise ValidationError("Select at least one winning Tender bid before award.")
    if len(winning_bids) > MAX_RFQ_VENDORS:
        raise ValidationError(f"Tender award exceeds the operational limit of {MAX_RFQ_VENDORS} vendors.")
    if not allocations:
        if len(winning_bids) == 1 and winning_bids[0].allocation_percent <= 0:
            winning_bids[0].allocation_percent = Decimal("100")
        allocation_total = sum((bid.allocation_percent for bid in winning_bids), ZERO)
        if allocation_total != Decimal("100"):
            raise ValidationError("Winning Tender bid allocations must total 100%.")
    tender_lines = _require_bounded(
        list(
            PurchaseTenderLine.objects.select_for_update(of=("self",))
            .filter(tender_id=locked.pk)
            .select_related("product", "request_line")
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Tender",
    )
    if not tender_lines:
        raise ValidationError("Tender requires at least one product line.")
    tender_lines_by_id = {line.pk: line for line in tender_lines}
    if allocations:
        allocated_by_line = defaultdict(lambda: ZERO)
        for allocation in allocations:
            if allocation.tender_line_id not in tender_lines_by_id:
                raise ValidationError("Every Tender allocation must belong to this Tender.")
            if allocation.bid_line.tender_line_id != allocation.tender_line_id:
                raise ValidationError("Tender allocation and bid line must reference the same Tender line.")
            allocated_by_line[allocation.tender_line_id] += allocation.allocated_quantity
        for line in tender_lines:
            if allocated_by_line[line.pk] != line.quantity:
                raise ValidationError(
                    f"Tender line {line.pk} allocations must equal the required quantity of {line.quantity}."
                )
    return winning_bids, allocations, tender_lines


def _tender_award_response_deadline(locked):
    working_days = (
        PurchaseSetting.objects.filter(company_id=locked.company_id)
        .values_list("tender_award_response_working_days", flat=True)
        .first()
        or 3
    )
    current = timezone.now()
    remaining = min(int(working_days), 260)
    while remaining > 0:
        current += timedelta(days=1)
        if current.weekday() < 5:
            remaining -= 1
    return current


def _ensure_tender_award_responses(locked, winning_bids):
    vendor_ids = sorted({bid.vendor_id for bid in winning_bids})
    existing_vendor_ids = set(
        TenderAwardResponse.objects.filter(
            tender_id=locked.pk,
            award_cycle=locked.award_cycle,
            vendor_id__in=vendor_ids,
        ).values_list("vendor_id", flat=True)
    )
    deadline = _tender_award_response_deadline(locked)
    TenderAwardResponse.objects.bulk_create(
        [
            TenderAwardResponse(
                tender_id=locked.pk,
                vendor_id=vendor_id,
                award_cycle=locked.award_cycle,
                deadline=deadline,
            )
            for vendor_id in vendor_ids
            if vendor_id not in existing_vendor_ids
        ],
        batch_size=100,
        ignore_conflicts=True,
    )
    return list(
        TenderAwardResponse.objects.select_for_update(of=("self",))
        .filter(
            tender_id=locked.pk,
            award_cycle=locked.award_cycle,
            vendor_id__in=vendor_ids,
        )
        .order_by("vendor_id")[: MAX_RFQ_VENDORS + 1]
    )


def _generate_tender_purchase_orders(locked, winning_bids, allocations, tender_lines):
    if PurchaseOrderControl.objects.filter(tender_id=locked.pk).exists():
        raise ValidationError("Purchase Orders were already generated for this Tender award.")
    tender_lines_by_id = {line.pk: line for line in tender_lines}
    now = timezone.now()
    request_line_increments = defaultdict(lambda: ZERO)
    order_numbers = (
        allocate_purchase_document_numbers(
            locked.order_type_id,
            locked.company_id,
            "purchase_order_draft",
            count=len(winning_bids),
        )
        if locked.order_type_id
        else [
            f"PO-TNR-{timezone.localdate():%Y%m%d}-{uuid4().hex[:8].upper()}"
            for _bid in winning_bids
        ]
    )
    orders = [
        PurchaseOrder(
            company_id=locked.company_id,
            number=number,
            vendor_id=bid.vendor_id,
            warehouse_id=locked.warehouse_id,
            order_date=timezone.localdate(),
            notes=f"Awarded from Tender {locked.number}.",
        )
        for bid, number in zip(winning_bids, order_numbers)
    ]
    PurchaseOrder.objects.bulk_create(orders, batch_size=100)
    all_order_lines = []
    order_totals = defaultdict(lambda: ZERO)
    order_by_bid_id = {bid.pk: order for bid, order in zip(winning_bids, orders)}
    if allocations:
        for allocation in allocations:
            tender_line = tender_lines_by_id[allocation.tender_line_id]
            order = order_by_bid_id[allocation.bid_line.bid_id]
            order_line = PurchaseOrderLine(
                order=order,
                product_id=tender_line.product_id,
                description=tender_line.description,
                warehouse_id=locked.warehouse_id,
                quantity=allocation.allocated_quantity,
                unit_price=allocation.unit_price,
            )
            all_order_lines.append(order_line)
            order_totals[order.pk] += order_line.quantity * order_line.unit_price
            if tender_line.request_line_id:
                request_line_increments[tender_line.request_line_id] += allocation.allocated_quantity
    else:
        for bid, order in zip(winning_bids, orders):
            ratio = bid.allocation_percent / Decimal("100")
            order_lines = [
                PurchaseOrderLine(
                    order=order,
                    product_id=line.product_id,
                    description=line.description,
                    warehouse_id=locked.warehouse_id,
                    quantity=(line.quantity * ratio).quantize(Decimal("0.001")),
                    unit_price=line.target_unit_price,
                )
                for line in tender_lines
                if (line.quantity * ratio).quantize(Decimal("0.001")) > 0
            ]
            all_order_lines.extend(order_lines)
            order_totals[order.pk] = sum((line.quantity * line.unit_price for line in order_lines), ZERO)
            for line in tender_lines:
                if line.request_line_id:
                    request_line_increments[line.request_line_id] += (
                        line.quantity * ratio
                    ).quantize(Decimal("0.001"))
    PurchaseOrderLine.objects.bulk_create(all_order_lines, batch_size=500)
    for order in orders:
        order.total = order_totals[order.pk]
        order.updated_at = now
    PurchaseOrder.objects.bulk_update(orders, ["total", "updated_at"], batch_size=100)
    PurchaseOrderControl.objects.bulk_create(
        [
            PurchaseOrderControl(
                order=order,
                tender=locked,
                order_type_id=locked.order_type_id,
                order_template_id=locked.order_template_id,
            )
            for order in orders
        ],
        batch_size=100,
    )
    request_lines = PurchaseRequestLine.objects.select_for_update().in_bulk(request_line_increments)
    for line_id, quantity in request_line_increments.items():
        request_line = request_lines[line_id]
        base = request_line.approved_quantity if request_line.approved_quantity > 0 else request_line.quantity
        if request_line.ordered_quantity + quantity > base:
            raise ValidationError("Tender award quantity exceeds the approved Purchase Request quantity.")
        request_line.ordered_quantity += quantity
        _advance_request_line_status(request_line)
        request_line.updated_at = now
    if request_lines:
        PurchaseRequestLine.objects.bulk_update(
            list(request_lines.values()),
            ["ordered_quantity", "line_status", "updated_at"],
            batch_size=500,
        )
    locked._created_purchase_order_ids = [order.pk for order in orders]
    return locked


@transaction.atomic
def transition_tender_award_response(response, action, actor=None):
    locked = TenderAwardResponse.objects.select_for_update(of=("self",)).get(pk=_pk(response))
    if locked.status != TenderAwardResponse.Status.PENDING:
        raise ValidationError("This Tender award response was already submitted.")
    if action == "accept":
        target = TenderAwardResponse.Status.ACCEPTED
    elif action == "reject":
        if not locked.remarks.strip():
            raise ValidationError("Enter rejection remarks before rejecting a Tender award.")
        target = TenderAwardResponse.Status.REJECTED
    elif action == "expire":
        if not locked.deadline or timezone.now() <= locked.deadline:
            raise ValidationError("A Tender award can only be marked No Response after its deadline.")
        target = TenderAwardResponse.Status.NO_RESPONSE
    else:
        raise ValidationError("Unsupported Tender award response action.")
    locked.status = target
    locked.responded_at = timezone.now()
    locked.responded_by = actor
    locked.save(update_fields=["status", "responded_at", "responded_by", "updated_at"])
    return locked


@transaction.atomic
def transition_purchase_tender(tender, action, actor=None, *, reason=""):
    locked = PurchaseTender.objects.select_for_update(of=("self",)).get(pk=_pk(tender))
    transitions = {
        "submit": ({PurchaseTender.Status.DRAFT}, PurchaseTender.Status.WAITING_APPROVAL),
        "approve": ({PurchaseTender.Status.WAITING_APPROVAL}, PurchaseTender.Status.CONFIRMED),
        "reject": ({PurchaseTender.Status.WAITING_APPROVAL}, PurchaseTender.Status.REJECTED),
        "publish": ({PurchaseTender.Status.CONFIRMED}, PurchaseTender.Status.PUBLISHED),
        "open-submission": (
            {PurchaseTender.Status.PUBLISHED},
            PurchaseTender.Status.DOCUMENT_SUBMISSION
            if locked.method == PurchaseTender.Method.PQTE
            else PurchaseTender.Status.BID_SUBMISSION,
        ),
        "open-bids": ({PurchaseTender.Status.DOCUMENT_SUBMISSION}, PurchaseTender.Status.BID_SUBMISSION),
        "evaluate": (
            {PurchaseTender.Status.BID_SUBMISSION, PurchaseTender.Status.SUBMISSION},
            PurchaseTender.Status.BID_SELECTION,
        ),
        "cancel": (
            {
                PurchaseTender.Status.DRAFT,
                PurchaseTender.Status.WAITING_APPROVAL,
                PurchaseTender.Status.CONFIRMED,
                PurchaseTender.Status.PUBLISHED,
                PurchaseTender.Status.DOCUMENT_SUBMISSION,
                PurchaseTender.Status.BID_SUBMISSION,
                PurchaseTender.Status.BID_SELECTION,
            },
            PurchaseTender.Status.CANCELLED,
        ),
    }
    if action in transitions:
        sources, target = transitions[action]
        if locked.status not in sources:
            raise ValidationError("That Tender action is not available in the current state.")
        if action == "open-bids":
            if locked.method != PurchaseTender.Method.PQTE:
                raise ValidationError(
                    "Only Prequalification Tenders can move from Document Submission to Bid Submission."
                )
            qualified_vendor_ids = qualified_tender_document_completion_vendor_ids(locked)
            if not qualified_vendor_ids:
                raise ValidationError(
                    "At least one Document Completion row must be qualified before Bid Submission."
                )
        locked.status = target
        if action == "publish" and not locked.publish_date:
            locked.publish_date = timezone.localdate()
            locked.save(update_fields=["status", "publish_date", "updated_at"])
        else:
            locked.save(update_fields=["status", "updated_at"])
        if action == "open-submission" and locked.method == PurchaseTender.Method.PQTE:
            sync_tender_document_completion(locked)
        return locked
    if action == "award":
        if locked.status != PurchaseTender.Status.BID_SELECTION:
            raise ValidationError("Tender can only be awarded during Bid Selection.")
        winning_bids, allocations, tender_lines = _load_tender_award_data(locked)
        _ensure_tender_award_responses(locked, winning_bids)
        locked.status = PurchaseTender.Status.AWARDED
        locked.decision_approval_status = PurchaseTender.DecisionStatus.DRAFT
        locked.decision_rejection_reason = ""
        locked.save(
            update_fields=[
                "status",
                "decision_approval_status",
                "decision_rejection_reason",
                "updated_at",
            ]
        )
        locked._created_purchase_order_ids = []
        return locked
    if action == "submit-decision":
        if locked.status != PurchaseTender.Status.AWARDED or locked.decision_approval_status not in {
            PurchaseTender.DecisionStatus.DRAFT,
            PurchaseTender.DecisionStatus.REJECTED,
        }:
            raise ValidationError("Only a draft or rejected awarded decision can be submitted.")
        _load_tender_award_data(locked)
        locked.decision_approval_status = PurchaseTender.DecisionStatus.WAITING_APPROVAL
        locked.decision_rejection_reason = ""
        locked.save(update_fields=["decision_approval_status", "decision_rejection_reason", "updated_at"])
        return locked
    if action == "approve-decision":
        if locked.status != PurchaseTender.Status.AWARDED or locked.decision_approval_status != PurchaseTender.DecisionStatus.WAITING_APPROVAL:
            raise ValidationError("Only a waiting Tender decision can be approved.")
        locked.decision_approval_status = PurchaseTender.DecisionStatus.APPROVED
        locked.save(update_fields=["decision_approval_status", "updated_at"])
        return locked
    if action == "reject-decision":
        if locked.status != PurchaseTender.Status.AWARDED or locked.decision_approval_status != PurchaseTender.DecisionStatus.WAITING_APPROVAL:
            raise ValidationError("Only a waiting Tender decision can be rejected.")
        reason = (reason or "").strip()
        if len(reason) < 10:
            raise ValidationError("Tender decision rejection reason must contain at least 10 characters.")
        locked.decision_approval_status = PurchaseTender.DecisionStatus.REJECTED
        locked.decision_rejection_reason = reason
        locked.save(update_fields=["decision_approval_status", "decision_rejection_reason", "updated_at"])
        return locked
    if action == "reset-decision":
        if locked.status != PurchaseTender.Status.AWARDED or locked.decision_approval_status != PurchaseTender.DecisionStatus.APPROVED:
            raise ValidationError("Only an approved Tender decision can be reset.")
        if PurchaseOrderControl.objects.filter(tender_id=locked.pk).exists():
            raise ValidationError("A Tender decision cannot be reset after Purchase Orders are generated.")
        locked.decision_approval_status = PurchaseTender.DecisionStatus.DRAFT
        locked.decision_revision_count += 1
        locked.decision_rejection_reason = ""
        locked.save(
            update_fields=[
                "decision_approval_status",
                "decision_revision_count",
                "decision_rejection_reason",
                "updated_at",
            ]
        )
        return locked
    if action == "reopen-award":
        if locked.status != PurchaseTender.Status.AWARDED:
            raise ValidationError("Only an awarded Tender can be reopened.")
        if PurchaseOrderControl.objects.filter(tender_id=locked.pk).exists():
            raise ValidationError("A Tender award cannot be reopened after Purchase Orders are generated.")
        locked.status = PurchaseTender.Status.BID_SELECTION
        locked.award_cycle += 1
        locked.decision_approval_status = PurchaseTender.DecisionStatus.DRAFT
        locked.decision_rejection_reason = ""
        locked.save(
            update_fields=[
                "status",
                "award_cycle",
                "decision_approval_status",
                "decision_rejection_reason",
                "updated_at",
            ]
        )
        return locked
    if action == "close":
        if locked.status != PurchaseTender.Status.AWARDED:
            raise ValidationError("Only an awarded Tender can be closed.")
        if locked.decision_approval_status != PurchaseTender.DecisionStatus.APPROVED:
            raise ValidationError("Approve the Tender decision before closing the award.")
        winning_bids, allocations, tender_lines = _load_tender_award_data(locked)
        responses = _ensure_tender_award_responses(locked, winning_bids)
        outstanding = [
            response
            for response in responses
            if response.status != TenderAwardResponse.Status.ACCEPTED
        ]
        if outstanding:
            vendor_names = ", ".join(str(response.vendor) for response in outstanding)
            raise ValidationError(f"Tender cannot close until winning vendors accept: {vendor_names}.")
        _generate_tender_purchase_orders(locked, winning_bids, allocations, tender_lines)
        locked.status = PurchaseTender.Status.CLOSED
        locked.save(update_fields=["status", "updated_at"])
        return locked
    raise ValidationError("Unsupported Tender workflow action.")


def _recompute_request_rfq_progress(request_line_ids):
    request_line_ids = list(set(request_line_ids))
    if not request_line_ids:
        return
    lines = list(PurchaseRequestLine.objects.select_for_update().filter(pk__in=request_line_ids).order_by("pk"))
    quantities = {
        row["request_line_id"]: row["quantity"] or ZERO
        for row in PurchaseQuotationLine.objects.filter(
            request_line_id__in=request_line_ids,
        )
        .exclude(quotation__status__in=[PurchaseQuotation.Status.CANCELLED, PurchaseQuotation.Status.EXPIRED])
        .values("request_line_id")
        .annotate(quantity=Sum("quantity"))
    }
    now = timezone.now()
    for line in lines:
        line.rfq_quantity = quantities.get(line.pk, ZERO)
        _advance_request_line_status(line)
        line.updated_at = now
    PurchaseRequestLine.objects.bulk_update(lines, ["rfq_quantity", "line_status", "updated_at"], batch_size=500)


@transaction.atomic
def transition_purchase_quotation(quotation, action, actor=None):
    """Run RFQ state transitions and create the selected vendor's draft PO."""
    del actor
    locked = (
        PurchaseQuotation.objects.select_for_update(of=("self",))
        .select_related("request", "vendor", "warehouse")
        .get(pk=_pk(quotation))
    )
    transitions = {
        "send": ({PurchaseQuotation.Status.DRAFT}, PurchaseQuotation.Status.SENT),
        "receive": ({PurchaseQuotation.Status.SENT}, PurchaseQuotation.Status.RECEIVED),
        "cancel": (
            {PurchaseQuotation.Status.DRAFT, PurchaseQuotation.Status.SENT, PurchaseQuotation.Status.RECEIVED},
            PurchaseQuotation.Status.CANCELLED,
        ),
    }
    if action in transitions:
        sources, target = transitions[action]
        if locked.status not in sources:
            raise ValidationError("That RFQ action is not available in the current state.")
        locked.status = target
        locked.save(update_fields=["status", "updated_at"])
        if action == "cancel":
            request_line_ids = list(locked.lines.values_list("request_line_id", flat=True))
            _cancel_blanket_rfq_release(locked)
            _recompute_request_rfq_progress([value for value in request_line_ids if value])
        return locked
    if action != "select":
        raise ValidationError("Unsupported RFQ workflow action.")
    if locked.status != PurchaseQuotation.Status.RECEIVED:
        raise ValidationError("Only a received vendor quotation can be selected.")
    existing = PurchaseOrderControl.objects.select_related("order").filter(quotation_id=locked.pk).first()
    if existing:
        locked._created_purchase_order_id = existing.order_id
        return locked
    if not locked.warehouse_id:
        raise ValidationError("Destination Warehouse is required before selecting the RFQ.")
    quote_lines = _require_bounded(
        list(
            PurchaseQuotationLine.objects.select_for_update(of=("self",))
            .filter(quotation_id=locked.pk)
            .select_related("product", "request_line")
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="RFQ",
    )
    if not quote_lines:
        raise ValidationError("Add at least one RFQ line before selecting the quotation.")
    quantities_by_request_line = defaultdict(lambda: ZERO)
    for line in quote_lines:
        if line.quantity <= 0 or line.unit_price < 0:
            raise ValidationError("RFQ lines require a positive quantity and a non-negative unit price.")
        if line.request_line_id:
            quantities_by_request_line[line.request_line_id] += line.quantity
    request_lines = PurchaseRequestLine.objects.select_for_update().in_bulk(quantities_by_request_line)
    for line_id, quantity in quantities_by_request_line.items():
        request_line = request_lines.get(line_id)
        if request_line is None or request_line.request_id != locked.request_id:
            raise ValidationError("RFQ line Purchase Request provenance is invalid.")
        base = request_line.approved_quantity if request_line.approved_quantity > 0 else request_line.quantity
        if request_line.ordered_quantity + quantity > base:
            raise ValidationError(
                f"Selected RFQ quantity exceeds the approved quantity for {request_line.product or request_line.description}."
            )

    number = (
        allocate_purchase_document_number(
            locked.order_type_id,
            locked.company_id,
            "purchase_order_draft",
        )
        if locked.order_type_id
        else f"PO-{timezone.localdate():%Y%m%d}-{uuid4().hex[:8].upper()}"
    )
    order = PurchaseOrder.objects.create(
        company_id=locked.company_id,
        number=number,
        vendor_id=locked.vendor_id,
        warehouse_id=locked.warehouse_id,
        order_date=timezone.localdate(),
        status=PurchaseOrder.Status.DRAFT,
        total=locked.quoted_amount,
        notes=f"Created from RFQ {locked.number}.",
    )
    PurchaseOrderLine.objects.bulk_create(
        [
            PurchaseOrderLine(
                order=order,
                product_id=line.product_id,
                description=line.description,
                warehouse_id=locked.warehouse_id,
                quantity=line.quantity,
                unit_price=line.unit_price,
            )
            for line in quote_lines
        ],
        batch_size=500,
    )
    PurchaseOrderControl.objects.create(
        order=order,
        quotation=locked,
        order_type_id=locked.order_type_id,
        order_template_id=locked.order_template_id,
    )
    now = timezone.now()
    for line_id, quantity in quantities_by_request_line.items():
        request_line = request_lines[line_id]
        request_line.ordered_quantity += quantity
        _advance_request_line_status(request_line)
        request_line.updated_at = now
    if request_lines:
        PurchaseRequestLine.objects.bulk_update(
            list(request_lines.values()),
            ["ordered_quantity", "line_status", "updated_at"],
            batch_size=500,
        )
    locked.status = PurchaseQuotation.Status.SELECTED
    locked.save(update_fields=["status", "updated_at"])
    _auto_close_blanket_after_rfq_selection(locked.blanket_order_id)
    if locked.request_id:
        PurchaseRequest.objects.filter(pk=locked.request_id).update(
            status=PurchaseRequest.Status.IN_PROGRESS,
            updated_at=now,
        )
    locked._created_purchase_order_id = order.pk
    return locked


def recalculate_purchase_order_total(order):
    """Refresh one PO total with one database aggregate."""
    total = order.lines.aggregate(
        value=Sum(
            ExpressionWrapper(
                F("quantity") * F("unit_price"),
                output_field=DecimalField(max_digits=18, decimal_places=2),
            )
        )
    )["value"] or ZERO
    PurchaseOrder.objects.filter(pk=order.pk).update(total=total, updated_at=timezone.now())
    order.total = total
    return order


def save_purchase_order_lines(order, formset):
    """Save a bounded document formset and derive its authoritative total."""
    formset.save()
    return recalculate_purchase_order_total(order)


def _configured_purchase_downpayment_amount(plan, order_total):
    if plan.downpayment_type == PurchaseOrderInvoiceAccounting.DownpaymentType.PERCENTAGE:
        amount = order_total * plan.downpayment_percentage / HUNDRED
    else:
        amount = plan.downpayment_amount
    return Decimal(amount).quantize(Decimal("0.01"))


def _posted_purchase_downpayment_balance(plan_id):
    amount_expression = ExpressionWrapper(
        F("invoice_line__quantity") * F("invoice_line__unit_price"),
        output_field=DecimalField(max_digits=30, decimal_places=8),
    )
    source_total = (
        PurchaseInvoiceLineProvenance.objects.filter(
            purchase_invoice__order_accounting_id=plan_id,
            purchase_invoice__invoice_kind=PurchaseInvoiceAccounting.InvoiceKind.DOWNPAYMENT,
            purchase_invoice__invoice__status__in=[Invoice.Status.POSTED, Invoice.Status.PAID],
        ).aggregate(total=Sum(amount_expression))["total"]
        or ZERO
    )
    consumed_total = (
        PurchaseInvoiceLineProvenance.objects.filter(
            purchase_invoice__order_accounting_id=plan_id,
            purchase_invoice__invoice_kind=PurchaseInvoiceAccounting.InvoiceKind.SETTLEMENT,
            purchase_invoice__invoice__status__in=[Invoice.Status.POSTED, Invoice.Status.PAID],
            source_downpayment_line_id__isnull=False,
        ).aggregate(total=Sum(amount_expression))["total"]
        or ZERO
    )
    return max(source_total + consumed_total, ZERO)


def _active_purchase_invoice_provenance(plan, invoice_kind, *, for_update=False):
    queryset = PurchaseInvoiceAccounting.objects.filter(
        order_accounting_id=plan.pk,
        invoice_kind=invoice_kind,
    ).exclude(invoice__status=Invoice.Status.CANCELLED)
    if for_update:
        queryset = queryset.select_for_update(of=("self",))
    rows = list(queryset.select_related("invoice").order_by("pk")[: MAX_PURCHASE_DOWNPAYMENT_INVOICES + 1])
    return _require_bounded(
        rows,
        limit=MAX_PURCHASE_DOWNPAYMENT_INVOICES,
        label="Purchase down-payment invoice history",
    )


def _allocate_amount_by_order_line(order_lines, amount):
    weighted = [(line, line.quantity * line.unit_price) for line in order_lines]
    weighted = [(line, weight) for line, weight in weighted if weight > 0]
    total_weight = sum((weight for _line, weight in weighted), ZERO)
    if not weighted or total_weight <= 0:
        raise ValidationError("Purchase Order lines must have a positive value before creating a down payment.")
    allocations = []
    remaining = amount
    for index, (line, weight) in enumerate(weighted):
        allocated = (
            remaining
            if index == len(weighted) - 1
            else (amount * weight / total_weight).quantize(Decimal("0.01"))
        )
        allocated = min(allocated, remaining)
        if allocated > 0:
            allocations.append((line, allocated))
            remaining -= allocated
    if remaining != 0:
        raise ValidationError("Purchase down-payment allocation could not be balanced to the requested amount.")
    return allocations


@transaction.atomic
def create_purchase_downpayment_bill(order, actor=None):
    locked = (
        PurchaseOrder.objects.select_for_update(of=("self",))
        .select_related("vendor")
        .get(pk=_pk(order))
    )
    if locked.status not in {
        PurchaseOrder.Status.CONFIRMED,
        PurchaseOrder.Status.COMPLETED,
        PurchaseOrder.Status.CLOSED,
    }:
        raise ValidationError("Down-payment bills require an approved Purchase Order.")
    plan = (
        PurchaseOrderInvoiceAccounting.objects.select_for_update(of=("self",))
        .select_related("downpayment_invoice")
        .filter(order_id=locked.pk)
        .first()
    )
    if plan is None:
        raise ValidationError("Create a Purchase Order Billing Plan and set its down-payment amount first.")
    downpayment_amount = _configured_purchase_downpayment_amount(plan, locked.total)
    if downpayment_amount <= 0:
        raise ValidationError("Set a positive Purchase Order down-payment amount or percentage first.")
    if downpayment_amount > locked.total:
        raise ValidationError("Purchase down payment cannot exceed the Purchase Order total.")
    active_purchase_invoices = list(
        PurchaseInvoiceAccounting.objects.select_for_update(of=("self",))
        .select_related("invoice")
        .filter(order_accounting_id=plan.pk)
        .exclude(invoice__status=Invoice.Status.CANCELLED)
        .order_by("pk")[: MAX_PURCHASE_DOWNPAYMENT_INVOICES + 1]
    )
    _require_bounded(
        active_purchase_invoices,
        limit=MAX_PURCHASE_DOWNPAYMENT_INVOICES,
        label="Purchase billing history",
    )
    already_invoiced = sum((row.invoice.total for row in active_purchase_invoices), ZERO)
    if already_invoiced + downpayment_amount > locked.total:
        raise ValidationError("Purchase down payment exceeds the remaining Purchase Order amount due.")
    order_lines = _require_bounded(
        list(
            PurchaseOrderLine.objects.select_for_update(of=("self",))
            .filter(order_id=locked.pk)
            .select_related("product")
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase order",
    )
    allocations = _allocate_amount_by_order_line(order_lines, downpayment_amount)
    suffix = uuid4().hex[:8].upper()
    invoice = Invoice.objects.create(
        company_id=locked.company_id,
        number=f"PDP-{locked.pk}-{suffix}",
        invoice_type=Invoice.InvoiceType.VENDOR,
        partner_id=locked.vendor_id,
        invoice_date=timezone.localdate(),
        due_date=timezone.localdate(),
        total=downpayment_amount,
        notes=f"Purchase down payment {plan.downpayment_sequence} for {locked.number}.",
    )
    invoice_lines = InvoiceLine.objects.bulk_create(
        [
            InvoiceLine(
                invoice=invoice,
                product_id=line.product_id,
                description=(
                    f"Purchase Down Payment {plan.downpayment_sequence} - "
                    f"{line.description or line.product.name}"
                ),
                quantity=Decimal("1"),
                unit_price=allocated,
            )
            for line, allocated in allocations
        ],
        batch_size=500,
    )
    provenance = PurchaseInvoiceAccounting.objects.create(
        invoice=invoice,
        order_accounting=plan,
        invoice_kind=PurchaseInvoiceAccounting.InvoiceKind.DOWNPAYMENT,
    )
    PurchaseInvoiceLineProvenance.objects.bulk_create(
        [
            PurchaseInvoiceLineProvenance(
                invoice_line=invoice_line,
                purchase_invoice=provenance,
                order_line=order_line,
            )
            for invoice_line, (order_line, _allocated) in zip(invoice_lines, allocations)
        ],
        batch_size=500,
    )
    PurchaseAccountingDocument.objects.create(
        order=locked,
        invoice=invoice,
        number=f"PAD-{invoice.number}",
        document_type=PurchaseAccountingDocument.DocumentType.VENDOR_BILL,
        document_date=invoice.invoice_date,
        total_amount=downpayment_amount,
        status="draft",
    )
    plan.downpayment_invoice = invoice
    plan.downpayment_sequence += 1
    plan.status = PurchaseOrderInvoiceAccounting.Status.DOWNPAYMENT_DRAFT
    plan.error_message = ""
    plan.save(
        update_fields=[
            "downpayment_invoice",
            "downpayment_sequence",
            "status",
            "error_message",
            "updated_at",
        ]
    )
    locked._created_vendor_bill_id = invoice.pk
    return locked


def _settle_full_purchase_downpayment(locked, plan, invoiceable, gross, actor):
    from modules.finance.models import (
        CommercialAccountingSettings,
        JournalEntry,
        JournalLine,
        ProductAccountMapping,
    )
    from modules.finance.services import post_journal_entry

    commercial = (
        CommercialAccountingSettings.objects.select_related(
            "purchase_journal",
            "purchase_downpayment_account",
        )
        .filter(company_id=locked.company_id)
        .first()
    )
    if commercial is None:
        raise ValidationError(
            "Configure Sales & Purchase Accounting settings before settling a full down payment."
        )
    product_ids = {line.product_id for line, _quantity in invoiceable}
    mappings = {
        mapping.product_id: mapping
        for mapping in ProductAccountMapping.objects.filter(
            company_id=locked.company_id,
            product_id__in=product_ids,
            active=True,
        ).select_related("expense_account")
    }
    if product_ids - set(mappings):
        raise ValidationError("Configure an expense account for every Purchase Order settlement product.")
    suffix = uuid4().hex[:8].upper()
    entry = JournalEntry.objects.create(
        company_id=locked.company_id,
        journal=commercial.purchase_journal,
        number=f"FDP-{locked.pk}-{suffix}",
        entry_date=timezone.localdate(),
        reference=locked.number,
        description=f"Full Purchase down-payment settlement for {locked.number}.",
        source_type="purchase_full_downpayment",
        source_reference=locked.number,
    )
    journal_lines = [
        JournalLine(
            entry=entry,
            account=mappings[line.product_id].expense_account,
            partner_id=locked.vendor_id,
            description=line.description or line.product.name,
            debit=line.unit_price * quantity,
        )
        for line, quantity in invoiceable
    ]
    journal_lines.append(
        JournalLine(
            entry=entry,
            account=commercial.purchase_downpayment_account,
            partner_id=locked.vendor_id,
            description=f"Purchase Down Payment Applied - {locked.number}",
            credit=gross,
        )
    )
    JournalLine.objects.bulk_create(journal_lines, batch_size=500)
    post_journal_entry(entry, actor)
    now = timezone.now()
    for line, quantity in invoiceable:
        line.billed_quantity = quantity
        line.updated_at = now
    PurchaseOrderLine.objects.bulk_update(
        [line for line, _quantity in invoiceable],
        ["billed_quantity", "updated_at"],
        batch_size=500,
    )
    control, _created = PurchaseOrderControl.objects.get_or_create(order_id=locked.pk)
    control.accounting_status = PurchaseOrderControl.AccountingStatus.BILLED
    control.save(update_fields=["accounting_status", "updated_at"])
    PurchaseAccountingDocument.objects.create(
        order=locked,
        number=f"PAD-FDP-{locked.pk}-{suffix}",
        document_type=PurchaseAccountingDocument.DocumentType.ACCRUAL,
        document_date=timezone.localdate(),
        total_amount=gross,
        journal_reference=entry.number,
        status="approved",
    )
    plan.status = PurchaseOrderInvoiceAccounting.Status.COMPLETED
    plan.error_message = ""
    plan.save(update_fields=["status", "error_message", "updated_at"])
    locked._full_downpayment_settled = True
    return locked


@transaction.atomic
def create_purchase_settlement_bill(order, actor=None):
    locked = (
        PurchaseOrder.objects.select_for_update(of=("self",))
        .select_related("vendor")
        .get(pk=_pk(order))
    )
    if locked.status not in {
        PurchaseOrder.Status.CONFIRMED,
        PurchaseOrder.Status.COMPLETED,
        PurchaseOrder.Status.CLOSED,
    }:
        raise ValidationError("Settlement bills require an approved Purchase Order.")
    plan = (
        PurchaseOrderInvoiceAccounting.objects.select_for_update(of=("self",))
        .filter(order_id=locked.pk)
        .first()
    )
    if plan is None:
        raise ValidationError("Create and post the Purchase Order down-payment bill first.")
    downpayments = _active_purchase_invoice_provenance(
        plan,
        PurchaseInvoiceAccounting.InvoiceKind.DOWNPAYMENT,
        for_update=True,
    )
    if not downpayments:
        raise ValidationError("Create and post the Purchase Order down-payment bill first.")
    if any(
        row.invoice.status not in {Invoice.Status.POSTED, Invoice.Status.PAID}
        for row in downpayments
    ):
        raise ValidationError("Post all Purchase Order down-payment bills before settlement.")
    settlements = _active_purchase_invoice_provenance(
        plan,
        PurchaseInvoiceAccounting.InvoiceKind.SETTLEMENT,
        for_update=True,
    )
    if any(row.invoice.status == Invoice.Status.DRAFT for row in settlements):
        raise ValidationError("Post or cancel the existing draft Purchase settlement bill first.")
    order_lines = _require_bounded(
        list(
            PurchaseOrderLine.objects.select_for_update(of=("self",))
            .filter(order_id=locked.pk)
            .select_related("product")
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase order",
    )
    invoiceable = []
    for line in order_lines:
        basis = line.quantity
        if (
            locked.invoicing_policy == PurchaseOrder.InvoicingPolicy.RECEIVED
            and line.product.product_type == Product.ProductType.STOCKABLE
        ):
            basis = line.received_quantity
        billable_quantity = max(basis - line.billed_quantity, ZERO)
        if billable_quantity > 0:
            invoiceable.append((line, billable_quantity))
    if not invoiceable:
        raise ValidationError("No Purchase Order quantity is eligible for settlement.")
    gross = sum((line.unit_price * quantity for line, quantity in invoiceable), ZERO)
    downpayment_ids = [row.pk for row in downpayments]
    source_lines = _require_bounded(
        list(
            PurchaseInvoiceLineProvenance.objects.select_related(
                "invoice_line",
                "order_line__product",
                "purchase_invoice__invoice",
            )
            .filter(purchase_invoice_id__in=downpayment_ids)
            .order_by("pk")[: MAX_PURCHASE_DOWNPAYMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOWNPAYMENT_LINES,
        label="Purchase down-payment line history",
    )
    if not source_lines:
        raise ValidationError("Posted Purchase down-payment bills are missing exact line provenance.")
    source_ids = [row.pk for row in source_lines]
    consumed_expression = ExpressionWrapper(
        -(F("invoice_line__quantity") * F("invoice_line__unit_price")),
        output_field=DecimalField(max_digits=30, decimal_places=8),
    )
    consumed_by_source = {
        row["source_downpayment_line_id"]: row["amount"] or ZERO
        for row in (
            PurchaseInvoiceLineProvenance.objects.filter(
                source_downpayment_line_id__in=source_ids,
                purchase_invoice__invoice__status__in=[Invoice.Status.POSTED, Invoice.Status.PAID],
            )
            .values("source_downpayment_line_id")
            .annotate(amount=Sum(consumed_expression))
        )
    }
    invoiceable_by_line = {line.pk: (line, quantity) for line, quantity in invoiceable}
    deductions = []
    for source in source_lines:
        source_amount = source.invoice_line.quantity * source.invoice_line.unit_price
        remaining = max(source_amount - consumed_by_source.get(source.pk, ZERO), ZERO)
        invoiceable_row = invoiceable_by_line.get(source.order_line_id)
        if remaining <= 0 or invoiceable_row is None:
            continue
        order_line, quantity = invoiceable_row
        target = (source_amount * quantity / order_line.quantity).quantize(Decimal("0.01"))
        consumed = min(remaining, target)
        if consumed > 0:
            deductions.append((source, order_line, consumed))
    downpayment_total = sum((amount for _source, _line, amount in deductions), ZERO)
    if downpayment_total <= 0:
        raise ValidationError("No posted Purchase down-payment balance is eligible for this settlement quantity.")
    if downpayment_total > gross:
        raise ValidationError("Posted Purchase down payment exceeds the currently billable amount.")
    suffix = uuid4().hex[:8].upper()
    invoice = Invoice.objects.create(
        company_id=locked.company_id,
        number=f"PST-{locked.pk}-{suffix}",
        invoice_type=Invoice.InvoiceType.VENDOR,
        partner_id=locked.vendor_id,
        invoice_date=timezone.localdate(),
        due_date=timezone.localdate(),
        notes=f"Purchase settlement for {locked.number}.",
    )
    invoice_lines = InvoiceLine.objects.bulk_create(
        [
            InvoiceLine(
                invoice=invoice,
                product_id=line.product_id,
                description=line.description or line.product.name,
                quantity=quantity,
                unit_price=line.unit_price,
            )
            for line, quantity in invoiceable
        ]
    )
    provenance = PurchaseInvoiceAccounting.objects.create(
        invoice=invoice,
        order_accounting=plan,
        invoice_kind=PurchaseInvoiceAccounting.InvoiceKind.SETTLEMENT,
        source_downpayment_invoice=(downpayments[0] if len(downpayments) == 1 else None),
    )
    PurchaseInvoiceLineProvenance.objects.bulk_create(
        [
            PurchaseInvoiceLineProvenance(
                invoice_line=invoice_line,
                purchase_invoice=provenance,
                order_line=order_line,
            )
            for invoice_line, (order_line, _quantity) in zip(invoice_lines, invoiceable)
        ],
        batch_size=500,
    )
    settlement_lines = InvoiceLine.objects.bulk_create(
        [
            InvoiceLine(
                invoice=invoice,
                product_id=order_line.product_id,
                description=(
                    f"Purchase Down Payment Applied - {source.purchase_invoice.invoice.number} - "
                    f"{order_line.description or order_line.product.name}"
                ),
                quantity=Decimal("1"),
                unit_price=-amount,
            )
            for source, order_line, amount in deductions
        ],
        batch_size=500,
    )
    PurchaseInvoiceLineProvenance.objects.bulk_create(
        [
            PurchaseInvoiceLineProvenance(
                invoice_line=invoice_line,
                purchase_invoice=provenance,
                source_downpayment_line=source,
            )
            for invoice_line, (source, _order_line, _amount) in zip(settlement_lines, deductions)
        ],
        batch_size=500,
    )
    invoice.total = gross - downpayment_total
    invoice.save(update_fields=["total", "updated_at"])
    accounting_document = PurchaseAccountingDocument.objects.create(
        order=locked,
        invoice=invoice,
        number=f"PAD-{invoice.number}",
        document_type=PurchaseAccountingDocument.DocumentType.VENDOR_BILL,
        document_date=invoice.invoice_date,
        total_amount=invoice.total,
        status="draft",
    )
    PurchaseBillLineProvenance.objects.bulk_create(
        [
            PurchaseBillLineProvenance(
                accounting_document=accounting_document,
                invoice_line=invoice_line,
                order_line=order_line,
                quantity=quantity,
                unit_price=order_line.unit_price,
            )
            for invoice_line, (order_line, quantity) in zip(invoice_lines, invoiceable)
        ],
        batch_size=500,
    )
    plan.settlement_invoice = invoice
    plan.status = PurchaseOrderInvoiceAccounting.Status.SETTLEMENT_DRAFT
    plan.error_message = ""
    plan.save(
        update_fields=[
            "settlement_invoice",
            "status",
            "error_message",
            "updated_at",
        ]
    )
    locked._created_vendor_bill_id = invoice.pk
    return locked


def _sync_service_work_orders_locked(order, order_lines, *, activate=False):
    service_lines = [line for line in order_lines if line.product.product_type == Product.ProductType.SERVICE]
    existing_work_orders = _require_bounded(
        list(
            ServiceWorkOrder.objects.select_for_update(of=("self",))
            .filter(source_order_id=order.pk)
            .order_by("sequence", "pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase Service Work Orders",
    )
    if not service_lines:
        stale_ids = [row.pk for row in existing_work_orders if row.status == ServiceWorkOrder.Status.DRAFT]
        if stale_ids:
            ServiceWorkOrder.objects.filter(pk__in=stale_ids).delete()
        return []

    milestones = _require_bounded(
        list(
            PurchaseOrderMilestone.objects.select_for_update(of=("self",))
            .filter(order_id=order.pk)
            .order_by("sequence", "pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase Order service milestones",
    )
    if not milestones:
        raise ValidationError("At least one Purchase Order milestone is required for service products.")
    if sum((row.percentage for row in milestones), ZERO) != HUNDRED:
        raise ValidationError("Purchase Order service milestone percentages must total exactly 100%.")

    by_milestone = {row.milestone_id: row for row in existing_work_orders if row.milestone_id}
    active_milestone_ids = {row.pk for row in milestones}
    stale_ids = [
        row.pk
        for row in existing_work_orders
        if row.status == ServiceWorkOrder.Status.DRAFT and row.milestone_id not in active_milestone_ids
    ]
    if stale_ids:
        ServiceWorkOrder.objects.filter(pk__in=stale_ids).delete()
    service_total = sum((line.subtotal for line in service_lines), ZERO)
    now = timezone.now()
    created = []
    changed = []
    for milestone in milestones:
        work_order = by_milestone.get(milestone.pk)
        contract_amount = (service_total * milestone.percentage / HUNDRED).quantize(Decimal("0.01"))
        if work_order is None:
            work_order = ServiceWorkOrder(
                company_id=order.company_id,
                number=f"SWO-{order.pk}-{milestone.sequence}-{uuid4().hex[:6].upper()}"[:40],
                sequence=milestone.sequence,
                vendor_id=order.vendor_id,
                source_order_id=order.pk,
                milestone_id=milestone.pk,
                milestone_name=milestone.name,
                contract_term_percentage=milestone.percentage,
                source_order_invoicing_policy=order.invoicing_policy,
                service_description=milestone.name,
                date_start=order.order_date,
                date_end=milestone.due_date,
                contract_amount=contract_amount,
                status=(ServiceWorkOrder.Status.IN_PROGRESS if activate else ServiceWorkOrder.Status.DRAFT),
            )
            created.append(work_order)
        elif work_order.status == ServiceWorkOrder.Status.DRAFT:
            work_order.sequence = milestone.sequence
            work_order.vendor_id = order.vendor_id
            work_order.milestone_name = milestone.name
            work_order.contract_term_percentage = milestone.percentage
            work_order.source_order_invoicing_policy = order.invoicing_policy
            work_order.service_description = milestone.name
            work_order.date_start = order.order_date
            work_order.date_end = milestone.due_date
            work_order.contract_amount = contract_amount
            if activate:
                work_order.status = ServiceWorkOrder.Status.IN_PROGRESS
            work_order.updated_at = now
            changed.append(work_order)
    if created:
        ServiceWorkOrder.objects.bulk_create(created, batch_size=500)
    if changed:
        ServiceWorkOrder.objects.bulk_update(
            changed,
            [
                "sequence",
                "vendor",
                "milestone_name",
                "contract_term_percentage",
                "source_order_invoicing_policy",
                "service_description",
                "date_start",
                "date_end",
                "contract_amount",
                "status",
                "updated_at",
            ],
            batch_size=500,
        )
    work_orders = [by_milestone[row.pk] for row in milestones if row.pk in by_milestone] + created
    work_order_ids = [row.pk for row in work_orders]
    existing_lines = _require_bounded(
        list(
            ServiceWorkOrderLine.objects.select_for_update(of=("self",))
            .filter(work_order_id__in=work_order_ids)
            .order_by("work_order_id", "pk")[: MAX_PURCHASE_DOCUMENT_LINES * max(len(work_orders), 1) + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES * max(len(work_orders), 1),
        label="Purchase Service Work Order lines",
    )
    existing_line_keys = {(row.work_order_id, row.purchase_order_line_id) for row in existing_lines}
    active_line_ids = {line.pk for line in service_lines}
    work_order_status_by_id = {row.pk: row.status for row in work_orders}
    stale_line_ids = [
        row.pk
        for row in existing_lines
        if row.purchase_order_line_id not in active_line_ids
        and work_order_status_by_id.get(row.work_order_id) == ServiceWorkOrder.Status.DRAFT
    ]
    if stale_line_ids:
        ServiceWorkOrderLine.objects.filter(pk__in=stale_line_ids).delete()
    new_lines = []
    for work_order in work_orders:
        for line in service_lines:
            if (work_order.pk, line.pk) in existing_line_keys:
                continue
            new_lines.append(
                ServiceWorkOrderLine(
                    work_order_id=work_order.pk,
                    purchase_order_line_id=line.pk,
                    description=line.description or line.product.name,
                    unit="service",
                    quantity=line.quantity,
                    unit_rate=line.unit_price,
                    milestone_name=work_order.milestone_name,
                    status=(
                        ServiceWorkOrderLine.Status.IN_PROGRESS
                        if activate
                        else ServiceWorkOrderLine.Status.PENDING
                    ),
                )
            )
    if new_lines:
        ServiceWorkOrderLine.objects.bulk_create(new_lines, batch_size=500)
    if activate:
        ServiceWorkOrderLine.objects.filter(
            work_order_id__in=work_order_ids,
            status=ServiceWorkOrderLine.Status.PENDING,
        ).update(status=ServiceWorkOrderLine.Status.IN_PROGRESS, updated_at=now)
    return work_orders


@transaction.atomic
def create_service_work_order_bill(work_order, actor=None):
    locked = (
        ServiceWorkOrder.objects.select_for_update(of=("self",))
        .select_related("source_order", "source_order__vendor", "billing_invoice")
        .get(pk=_pk(work_order))
    )
    if locked.source_order_id is None:
        raise ValidationError("Service Work Order billing requires a source Purchase Order.")
    if locked.status not in {ServiceWorkOrder.Status.IN_PROGRESS, ServiceWorkOrder.Status.DONE}:
        raise ValidationError("Only an active or done Service Work Order can be billed.")
    if (
        locked.source_order_invoicing_policy == PurchaseOrder.InvoicingPolicy.RECEIVED
        and locked.status != ServiceWorkOrder.Status.DONE
    ):
        raise ValidationError("Delivered-policy Service Work Orders must be Done before billing.")
    if locked.billing_invoice_id and locked.billing_invoice.status != Invoice.Status.CANCELLED:
        raise ValidationError("This Service Work Order already has an active vendor bill.")
    rows = _require_bounded(
        list(
            ServiceWorkOrderLine.objects.select_for_update(of=("self",))
            .filter(work_order_id=locked.pk, purchase_order_line__isnull=False)
            .select_related("purchase_order_line", "purchase_order_line__product")
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase Service Work Order",
    )
    if not rows:
        raise ValidationError("Service Work Order has no linked Purchase Order service lines.")
    pending_rows = _require_bounded(
        list(
            PurchaseBillLineProvenance.objects.filter(
                order_line__order_id=locked.source_order_id,
                invoice_line__invoice__status=Invoice.Status.DRAFT,
            )
            .values("order_line_id")
            .annotate(quantity=Sum("quantity"))
            .order_by("order_line_id")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Pending Service Work Order bill line groups",
    )
    pending_by_line = {
        row["order_line_id"]: row["quantity"] or ZERO
        for row in pending_rows
    }
    invoiceable = []
    for row in rows:
        order_line = row.purchase_order_line
        quantity = (order_line.quantity * locked.contract_term_percentage / HUNDRED).quantize(Decimal("0.001"))
        if quantity <= 0:
            raise ValidationError("Service Work Order billing allocation must be greater than zero.")
        if order_line.billed_quantity + pending_by_line.get(order_line.pk, ZERO) + quantity > order_line.quantity:
            raise ValidationError("Service Work Order billing exceeds the remaining Purchase Order service quantity.")
        invoiceable.append((order_line, quantity))

    suffix = uuid4().hex[:8].upper()
    invoice = Invoice.objects.create(
        company_id=locked.company_id,
        number=f"VB-SWO-{locked.pk}-{suffix}",
        invoice_type=Invoice.InvoiceType.VENDOR,
        partner_id=locked.vendor_id,
        invoice_date=timezone.localdate(),
        due_date=timezone.localdate(),
        status=Invoice.Status.DRAFT,
        notes=f"Vendor bill created from Service Work Order {locked.number}.",
    )
    invoice_lines = [
        InvoiceLine(
            invoice=invoice,
            product_id=line.product_id,
            description=f"{locked.milestone_name}: {line.description or line.product.name}",
            quantity=quantity,
            unit_price=line.unit_price,
        )
        for line, quantity in invoiceable
    ]
    InvoiceLine.objects.bulk_create(invoice_lines, batch_size=500)
    total = sum((row.subtotal for row in invoice_lines), ZERO)
    invoice.total = total
    invoice.save(update_fields=["total", "updated_at"])
    accounting_document = PurchaseAccountingDocument.objects.create(
        order_id=locked.source_order_id,
        invoice=invoice,
        number=f"PAD-{invoice.number}",
        document_type=PurchaseAccountingDocument.DocumentType.VENDOR_BILL,
        document_date=invoice.invoice_date,
        total_amount=total,
        status="draft",
    )
    PurchaseBillLineProvenance.objects.bulk_create(
        [
            PurchaseBillLineProvenance(
                accounting_document=accounting_document,
                invoice_line=invoice_line,
                order_line=line,
                service_work_order=locked,
                quantity=quantity,
                unit_price=line.unit_price,
            )
            for invoice_line, (line, quantity) in zip(invoice_lines, invoiceable)
        ],
        batch_size=500,
    )
    locked.billing_invoice = invoice
    locked.contract_amount = total
    locked.save(update_fields=["billing_invoice", "contract_amount", "updated_at"])
    locked._created_vendor_bill_id = invoice.pk
    return locked


@transaction.atomic
def transition_service_work_order(work_order, action, actor=None):
    locked = (
        ServiceWorkOrder.objects.select_for_update(of=("self",))
        .select_related("source_order")
        .get(pk=_pk(work_order))
    )
    lines = _require_bounded(
        list(
            ServiceWorkOrderLine.objects.select_for_update(of=("self",))
            .filter(work_order_id=locked.pk)
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase Service Work Order",
    )
    if action == "start":
        if locked.status != ServiceWorkOrder.Status.DRAFT:
            raise ValidationError("Only a draft Service Work Order can be started.")
        if locked.source_order_id and locked.source_order.status not in {
            PurchaseOrder.Status.CONFIRMED,
            PurchaseOrder.Status.COMPLETED,
        }:
            raise ValidationError("Confirm the source Purchase Order before starting its Service Work Order.")
        if not lines:
            raise ValidationError("Service Work Order has no service product lines.")
        locked.status = ServiceWorkOrder.Status.IN_PROGRESS
        now = timezone.now()
        ServiceWorkOrderLine.objects.filter(
            work_order_id=locked.pk,
            status=ServiceWorkOrderLine.Status.PENDING,
        ).update(status=ServiceWorkOrderLine.Status.IN_PROGRESS, updated_at=now)
    elif action == "done":
        if locked.status != ServiceWorkOrder.Status.IN_PROGRESS:
            raise ValidationError("Only an In Progress Service Work Order can be marked Done.")
        if not lines:
            raise ValidationError("Service Work Order has no service product lines.")
        if any(line.status != ServiceWorkOrderLine.Status.DONE for line in lines):
            raise ValidationError("All Service Work Order lines must be Done first.")
        locked.status = ServiceWorkOrder.Status.DONE
    else:
        raise ValidationError("Unsupported Service Work Order action.")
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def transition_service_work_order_line(work_order_line, action, actor=None):
    locked = (
        ServiceWorkOrderLine.objects.select_for_update(of=("self",))
        .select_related("work_order")
        .get(pk=_pk(work_order_line))
    )
    if action != "done":
        raise ValidationError("Unsupported Service Work Order line action.")
    if locked.status == ServiceWorkOrderLine.Status.DONE:
        return locked
    if locked.work_order.status != ServiceWorkOrder.Status.IN_PROGRESS:
        raise ValidationError("Only In Progress Service Work Order lines can be marked Done.")
    locked.status = ServiceWorkOrderLine.Status.DONE
    locked.save(update_fields=["status", "updated_at"])
    if not ServiceWorkOrderLine.objects.filter(work_order_id=locked.work_order_id).exclude(
        status=ServiceWorkOrderLine.Status.DONE
    ).exists():
        ServiceWorkOrder.objects.filter(
            pk=locked.work_order_id,
            status=ServiceWorkOrder.Status.IN_PROGRESS,
        ).update(status=ServiceWorkOrder.Status.DONE, updated_at=timezone.now())
    return locked


@transaction.atomic
def create_purchase_vendor_bill(order, actor=None):
    """Create one draft vendor bill for the policy-eligible PO quantities."""
    locked = (
        PurchaseOrder.objects.select_for_update(of=("self",))
        .select_related("vendor")
        .get(pk=_pk(order))
    )
    if locked.status not in {
        PurchaseOrder.Status.CONFIRMED,
        PurchaseOrder.Status.COMPLETED,
        PurchaseOrder.Status.CLOSED,
    }:
        raise ValidationError("Vendor bills can only be created from an approved purchase order.")
    billing_plan = PurchaseOrderInvoiceAccounting.objects.filter(order_id=locked.pk).first()
    if billing_plan and billing_plan.downpayment_amount > 0:
        raise ValidationError("Use the down-payment and settlement bill actions for this Purchase Order.")
    lines = _require_bounded(list(
        PurchaseOrderLine.objects.select_for_update(of=("self",))
        .filter(order_id=locked.pk)
        .select_related("product")
        .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
    ), limit=MAX_PURCHASE_DOCUMENT_LINES, label="Purchase order")
    pending_rows = _require_bounded(list(
        PurchaseBillLineProvenance.objects.filter(
            order_line__order_id=locked.pk,
            invoice_line__invoice__status=Invoice.Status.DRAFT,
        )
        .values("order_line_id")
        .annotate(quantity=Sum("quantity"))
        .order_by("order_line_id")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
    ), limit=MAX_PURCHASE_DOCUMENT_LINES, label="Pending vendor bill line groups")
    pending_by_line = {
        row["order_line_id"]: row["quantity"] or ZERO
        for row in pending_rows
    }
    service_work_order_billing = ServiceWorkOrder.objects.filter(source_order_id=locked.pk).exists()
    invoiceable = []
    for line in lines:
        if service_work_order_billing and line.product.product_type == Product.ProductType.SERVICE:
            continue
        basis = line.quantity
        if (
            locked.invoicing_policy == PurchaseOrder.InvoicingPolicy.RECEIVED
            and line.product.product_type == Product.ProductType.STOCKABLE
        ):
            basis = line.received_quantity
        quantity = basis - line.billed_quantity - pending_by_line.get(line.pk, ZERO)
        if quantity > 0:
            invoiceable.append((line, quantity))
    if not invoiceable:
        if service_work_order_billing:
            raise ValidationError("Create service-product bills from the linked Service Work Orders.")
        if locked.invoicing_policy == PurchaseOrder.InvoicingPolicy.RECEIVED:
            raise ValidationError("No received quantity remains to bill.")
        raise ValidationError("No ordered quantity remains to bill.")

    suffix = uuid4().hex[:8].upper()
    invoice = Invoice.objects.create(
        company_id=locked.company_id,
        number=f"VB-{locked.pk}-{suffix}",
        invoice_type=Invoice.InvoiceType.VENDOR,
        partner_id=locked.vendor_id,
        invoice_date=timezone.localdate(),
        due_date=timezone.localdate(),
        status=Invoice.Status.DRAFT,
        notes=f"Vendor bill created from purchase order {locked.number}.",
    )
    invoice_lines = [
        InvoiceLine(
            invoice=invoice,
            product_id=line.product_id,
            description=line.description or line.product.name,
            quantity=quantity,
            unit_price=line.unit_price,
        )
        for line, quantity in invoiceable
    ]
    InvoiceLine.objects.bulk_create(invoice_lines)
    total = sum((invoice_line.subtotal for invoice_line in invoice_lines), ZERO)
    invoice.total = total
    invoice.save(update_fields=["total", "updated_at"])
    accounting_document = PurchaseAccountingDocument.objects.create(
        order=locked,
        invoice=invoice,
        number=f"PAD-{invoice.number}",
        document_type=PurchaseAccountingDocument.DocumentType.VENDOR_BILL,
        document_date=invoice.invoice_date,
        total_amount=total,
        status="draft",
    )
    PurchaseBillLineProvenance.objects.bulk_create(
        [
            PurchaseBillLineProvenance(
                accounting_document=accounting_document,
                invoice_line=invoice_line,
                order_line=line,
                quantity=quantity,
                unit_price=line.unit_price,
            )
            for invoice_line, (line, quantity) in zip(invoice_lines, invoiceable)
        ]
    )
    _auto_close_purchase_order_if_received_and_billed(locked.pk)
    locked._created_vendor_bill_id = invoice.pk
    return locked


def _receiving_totals(lines):
    stock_lines = [line for line in lines if _stockable(line)]
    ordered = sum((line.quantity for line in stock_lines), ZERO)
    received = sum((line.received_quantity for line in stock_lines), ZERO)
    percentage = HUNDRED if not stock_lines else min(HUNDRED, received * HUNDRED / ordered)
    return stock_lines, ordered, received, percentage.quantize(Decimal("0.01"))


def _sync_order_receiving(order, lines):
    stock_lines, ordered, received, percentage = _receiving_totals(lines)
    if not stock_lines or received >= ordered:
        receiving_status = PurchaseOrderControl.ReceivingStatus.RECEIVED
    elif received > 0:
        receiving_status = PurchaseOrderControl.ReceivingStatus.PARTIAL
    else:
        receiving_status = PurchaseOrderControl.ReceivingStatus.NOT_RECEIVED

    PurchaseOrderControl.objects.update_or_create(
        order_id=order.pk,
        defaults={"receiving_status": receiving_status},
    )
    order.received_percentage = percentage
    order.save(update_fields=["received_percentage", "updated_at"])
    return order


def _auto_close_purchase_order_if_received_and_billed(order_id):
    """Match HMX: close a PO only after every received stock line is fully billed."""
    order = PurchaseOrder.objects.select_for_update().filter(pk=order_id).first()
    if order is None or order.status not in {
        PurchaseOrder.Status.CONFIRMED,
        PurchaseOrder.Status.COMPLETED,
    }:
        return False

    lines = _require_bounded(
        list(
            PurchaseOrderLine.objects.select_for_update(of=("self",))
            .filter(order_id=order.pk)
            .select_related("product")
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase order",
    )
    stock_lines = [line for line in lines if _stockable(line) and line.quantity > 0]
    if not stock_lines or any(line.received_quantity != line.quantity for line in stock_lines):
        return False

    pending_rows = _require_bounded(
        list(
            PurchaseBillLineProvenance.objects.filter(
                order_line__order_id=order.pk,
                invoice_line__invoice__status=Invoice.Status.DRAFT,
            )
            .values("order_line_id")
            .annotate(quantity=Sum("quantity"))
            .order_by("order_line_id")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Pending vendor bill line groups",
    )
    pending_by_line = {
        row["order_line_id"]: row["quantity"] or ZERO
        for row in pending_rows
    }
    if any(
        line.billed_quantity + pending_by_line.get(line.pk, ZERO) != line.received_quantity
        for line in stock_lines
    ):
        return False

    if ServiceWorkOrder.objects.filter(source_order_id=order.pk).exclude(
        status=ServiceWorkOrder.Status.DONE
    ).exists():
        return False

    billing_plan_id = (
        PurchaseOrderInvoiceAccounting.objects.filter(order_id=order.pk)
        .values_list("pk", flat=True)
        .first()
    )
    if billing_plan_id:
        active_plan_invoices = PurchaseInvoiceAccounting.objects.filter(
            order_accounting_id=billing_plan_id,
        ).exclude(invoice__status=Invoice.Status.CANCELLED)
        if active_plan_invoices.exclude(
            invoice__status__in=[Invoice.Status.POSTED, Invoice.Status.PAID]
        ).exists():
            return False
        if _posted_purchase_downpayment_balance(billing_plan_id) > 0:
            return False

    order.status = PurchaseOrder.Status.CLOSED
    order.save(update_fields=["status", "updated_at"])
    return True


def _validate_order_for_receiving(order, lines):
    if not lines:
        raise ValidationError("Add at least one purchase order line before approval.")
    for line in lines:
        if line.quantity <= 0:
            raise ValidationError(f"Line {line.pk or '-'} must have a quantity greater than zero.")
        if line.product.company_id != order.company_id:
            raise ValidationError("Every product must belong to the purchase order company.")
        if _stockable(line):
            warehouse = line.warehouse or order.warehouse
            if warehouse is None:
                raise ValidationError(
                    f"Choose a destination warehouse for stockable product {line.product}."
                )
            if warehouse.company_id != order.company_id:
                raise ValidationError("Every destination warehouse must belong to the purchase order company.")


def _validate_vendor_pricelist_moq(order, lines):
    profile_id = (
        VendorProfile.objects.filter(company_id=order.company_id, partner_id=order.vendor_id)
        .values_list("pk", flat=True)
        .first()
    )
    if not profile_id:
        return
    quantity_by_product = defaultdict(lambda: ZERO)
    for line in lines:
        quantity_by_product[line.product_id] += line.quantity
    today = timezone.localdate()
    candidates = _require_bounded(
        list(
            VendorPricelistItem.objects.filter(
                pricelist__company_id=order.company_id,
                pricelist__vendor_id=profile_id,
                pricelist__active=True,
                pricelist__status=VendorPricelist.Status.ACTIVE,
                pricelist__date_start__lte=today,
                product_id__in=quantity_by_product,
                enforce_moq=True,
                active=True,
            )
            .filter(models.Q(pricelist__date_end__isnull=True) | models.Q(pricelist__date_end__gte=today))
            .order_by("product_id", "-pricelist__date_start", "-pricelist_id", "-pk")
            .values("pk", "product_id")[: MAX_VENDOR_PRICELIST_MATCHES + 1]
        ),
        limit=MAX_VENDOR_PRICELIST_MATCHES,
        label="Vendor pricelist MOQ candidates",
    )
    selected = {}
    for row in candidates:
        selected.setdefault(row["product_id"], row["pk"])
    tier_rows = _require_bounded(
        list(
            VendorPricelistTier.objects.filter(item_id__in=selected.values())
            .order_by("item_id", "sequence", "pk")
            .values("item_id", "minimum_quantity")[: MAX_VENDOR_PRICELIST_MATCHES + 1]
        ),
        limit=MAX_VENDOR_PRICELIST_MATCHES,
        label="Vendor pricelist MOQ tiers",
    )
    first_minimums = {}
    for row in tier_rows:
        first_minimums.setdefault(row["item_id"], row["minimum_quantity"])
    product_by_id = {line.product_id: line.product for line in lines}
    for product_id, item_id in selected.items():
        minimum = first_minimums.get(item_id)
        if minimum is None or minimum <= 0:
            raise ValidationError(
                f"Configure a positive first MOQ tier for {product_by_id[product_id]}."
            )
        if quantity_by_product[product_id] < minimum:
            raise ValidationError(
                f"Purchase quantity for {product_by_id[product_id]} is below the vendor MOQ of {minimum}."
            )


def _create_goods_receipts_locked(order, actor=None):
    lines = _require_bounded(list(
        PurchaseOrderLine.objects.select_for_update(of=("self",))
        .filter(order_id=order.pk)
        .select_related("product", "warehouse")
        .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
    ), limit=MAX_PURCHASE_DOCUMENT_LINES, label="Purchase order")
    _validate_order_for_receiving(order, lines)
    pending = {
        row["order_line_id"]: row["quantity"] or ZERO
        for row in GoodsReceiptLine.objects.filter(
            receipt__order_id=order.pk,
            receipt__status__in=[GoodsReceipt.Status.DRAFT, GoodsReceipt.Status.INSPECTING],
        )
        .values("order_line_id")
        .annotate(quantity=Sum("received_quantity"))
    }

    grouped = defaultdict(list)
    for line in lines:
        if not _stockable(line):
            continue
        remaining = line.quantity - line.received_quantity - pending.get(line.pk, ZERO)
        if remaining <= 0:
            continue
        warehouse_id = line.warehouse_id or order.warehouse_id
        grouped[warehouse_id].append((line, remaining))
    if not grouped:
        raise ValidationError("No stockable quantity remains to be prepared for receipt.")

    suffix = uuid4().hex[:8].upper()
    receipt_rows = []
    grouped_rows = []
    was_partial = any(line.received_quantity > 0 for line in lines)
    for index, (warehouse_id, rows) in enumerate(sorted(grouped.items()), start=1):
        number = f"GR-{order.pk}-{suffix}-{index}"
        receipt_rows.append(
            GoodsReceipt(
                company_id=order.company_id,
                number=number,
                order_id=order.pk,
                warehouse_id=warehouse_id,
                receipt_date=timezone.localdate(),
                receiving_mode=(
                    GoodsReceipt.ReceivingMode.PARTIAL if was_partial else GoodsReceipt.ReceivingMode.FULL
                ),
                status=GoodsReceipt.Status.DRAFT,
                notes=f"Prepared from purchase order {order.number}.",
            )
        )
        grouped_rows.append(rows)
    GoodsReceipt.objects.bulk_create(receipt_rows)

    line_rows = []
    for receipt, rows in zip(receipt_rows, grouped_rows):
        for order_line, quantity in rows:
            line_rows.append(
                GoodsReceiptLine(
                    receipt_id=receipt.pk,
                    order_line_id=order_line.pk,
                    received_quantity=quantity,
                    accepted_quantity=quantity,
                )
            )
    GoodsReceiptLine.objects.bulk_create(line_rows)
    order._created_goods_receipt_id = receipt_rows[0].pk
    order._created_goods_receipt_count = len(receipt_rows)
    return order


@transaction.atomic
def create_goods_receipts(order, actor=None):
    locked = (
        PurchaseOrder.objects.select_for_update(of=("self",))
        .select_related("warehouse")
        .get(pk=_pk(order))
    )
    if locked.status not in {PurchaseOrder.Status.CONFIRMED, PurchaseOrder.Status.COMPLETED}:
        raise ValidationError("Goods receipts can only be created from an approved purchase order.")
    return _create_goods_receipts_locked(locked, actor)


def _prepare_purchase_order_for_confirmation(locked):
    lines = _require_bounded(list(
        PurchaseOrderLine.objects.select_for_update(of=("self",))
        .filter(order_id=locked.pk)
        .select_related("product", "warehouse")
        .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
    ), limit=MAX_PURCHASE_DOCUMENT_LINES, label="Purchase order")
    _validate_purchase_vendor_eligibility(locked.company_id, locked.vendor_id)
    _validate_order_for_receiving(locked, lines)
    validate_vendor_pricelist_product_eligibility(
        document=locked,
        lines=lines,
        transaction_date=locked.order_date,
    )
    _validate_vendor_pricelist_moq(locked, lines)
    order_total = sum((line.subtotal for line in lines), ZERO)
    purchase_limit = enforce_vendor_purchase_limit(order=locked, order_total=order_total)
    locked.total = order_total
    return lines, purchase_limit


@transaction.atomic
def submit_purchase_order_for_approval(order, actor=None):
    del actor
    locked = (
        PurchaseOrder.objects.select_for_update(of=("self",))
        .select_related("company", "vendor", "warehouse")
        .get(pk=_pk(order))
    )
    if locked.status not in {PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.SENT}:
        raise ValidationError("Only a draft or sent purchase order can be submitted for approval.")
    _prepare_purchase_order_for_confirmation(locked)
    locked.status = PurchaseOrder.Status.WAITING_APPROVAL
    locked.save(update_fields=["total", "status", "updated_at"])
    return locked


@transaction.atomic
def confirm_purchase_order(order, actor=None):
    locked = (
        PurchaseOrder.objects.select_for_update(of=("self",))
        .select_related("company", "vendor", "warehouse")
        .get(pk=_pk(order))
    )
    if locked.status not in {
        PurchaseOrder.Status.DRAFT,
        PurchaseOrder.Status.WAITING_APPROVAL,
    }:
        raise ValidationError("Only a draft or waiting-approval purchase order can be approved.")
    lines, purchase_limit = _prepare_purchase_order_for_confirmation(locked)
    if purchase_limit.requires_approval and locked.status == PurchaseOrder.Status.DRAFT:
        locked.status = PurchaseOrder.Status.WAITING_APPROVAL
        locked.save(update_fields=["total", "status", "updated_at"])
        return locked
    _sync_service_work_orders_locked(locked, lines, activate=True)
    control = PurchaseOrderControl.objects.select_for_update().filter(order_id=locked.pk).first()
    if (
        control
        and control.order_type_id
        and not DirectPurchase.objects.filter(purchase_order_id=locked.pk).exists()
    ):
        locked.number = allocate_purchase_document_number(
            control.order_type_id,
            locked.company_id,
            "purchase_order",
        )
    locked.status = PurchaseOrder.Status.CONFIRMED
    locked.save(update_fields=["number", "total", "status", "updated_at"])
    PurchaseOrderControl.objects.update_or_create(
        order_id=locked.pk,
        defaults={"confirmed_at": timezone.now()},
    )
    receiving_behavior = (
        PurchaseSetting.objects.filter(company_id=locked.company_id)
        .values_list("receiving_behavior", flat=True)
        .first()
    )
    if receiving_behavior == PurchaseSetting.ReceivingBehavior.AUTO and any(_stockable(line) for line in lines):
        _create_goods_receipts_locked(locked, actor)
    sync_purchase_three_way_match({locked.pk})
    return locked


@transaction.atomic
def inspect_goods_receipt(receipt, actor=None):
    locked = GoodsReceipt.objects.select_for_update().get(pk=_pk(receipt))
    if locked.status != GoodsReceipt.Status.DRAFT:
        raise ValidationError("Only a draft goods receipt can start inspection.")
    if not GoodsReceiptLine.objects.filter(receipt_id=locked.pk).exists():
        raise ValidationError("Add at least one received line before inspection.")
    locked.status = GoodsReceipt.Status.INSPECTING
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def receive_goods_receipt(receipt, actor):
    locked = (
        GoodsReceipt.objects.select_for_update()
        .select_related("order", "warehouse")
        .get(pk=_pk(receipt))
    )
    if locked.status != GoodsReceipt.Status.INSPECTING:
        raise ValidationError("Only a receipt under inspection can be accepted.")
    if actor is None:
        raise ValidationError("A receiving user is required to post inventory.")

    receipt_lines = _require_bounded(list(
        GoodsReceiptLine.objects.select_for_update(of=("self",))
        .filter(receipt_id=locked.pk)
        .select_related("order_line", "order_line__product", "order_line__warehouse")
        .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
    ), limit=MAX_PURCHASE_DOCUMENT_LINES, label="Goods receipt")
    if not receipt_lines:
        raise ValidationError("The goods receipt has no lines.")
    order_line_ids = [line.order_line_id for line in receipt_lines]
    order_lines = PurchaseOrderLine.objects.select_for_update().in_bulk(order_line_ids)
    product_ids = sorted({line.order_line.product_id for line in receipt_lines})
    products = Product.objects.select_for_update().in_bulk(product_ids)

    accepted_total = ZERO
    stock_moves = []
    accepted_by_order_line = defaultdict(Decimal)
    accepted_by_product = defaultdict(Decimal)
    for receipt_line in receipt_lines:
        order_line = order_lines[receipt_line.order_line_id]
        product = products[order_line.product_id]
        if order_line.order_id != locked.order_id:
            raise ValidationError("Every receipt line must belong to the selected purchase order.")
        if product.product_type != Product.ProductType.STOCKABLE:
            raise ValidationError(f"{product} is not stock-managed and cannot be posted by a goods receipt.")
        destination_id = order_line.warehouse_id or locked.order.warehouse_id
        if destination_id != locked.warehouse_id:
            raise ValidationError(f"Receipt warehouse does not match the destination for {product}.")
        if product.company_id != locked.company_id or locked.warehouse.company_id != locked.company_id:
            raise ValidationError("Receipt products, warehouse, and purchase order must belong to one company.")
        if receipt_line.stock_move_id:
            raise ValidationError("This receipt line was already posted to inventory.")
        quantities = (
            receipt_line.received_quantity,
            receipt_line.accepted_quantity,
            receipt_line.rejected_quantity,
        )
        if any(quantity < 0 for quantity in quantities):
            raise ValidationError("Receipt quantities cannot be negative.")
        if receipt_line.received_quantity <= 0:
            raise ValidationError("Every receipt line must have a received quantity greater than zero.")
        if receipt_line.accepted_quantity + receipt_line.rejected_quantity != receipt_line.received_quantity:
            raise ValidationError("Classify every received quantity as accepted or rejected before posting.")
        if order_line.received_quantity + receipt_line.accepted_quantity > order_line.quantity:
            raise ValidationError(f"Accepted quantity exceeds the remaining order quantity for {product}.")
        if receipt_line.accepted_quantity > 0:
            accepted_total += receipt_line.accepted_quantity
            accepted_by_order_line[order_line.pk] += receipt_line.accepted_quantity
            accepted_by_product[product.pk] += receipt_line.accepted_quantity
            stock_moves.append(
                StockMove(
                    company_id=locked.company_id,
                    product_id=product.pk,
                    warehouse_id=locked.warehouse_id,
                    direction=StockMove.Direction.IN,
                    quantity=receipt_line.accepted_quantity,
                    reference=locked.number,
                    movement_date=locked.receipt_date,
                    note=f"Purchase receipt {locked.number} for {locked.order.number}.",
                    created_by=actor,
                )
            )
    if accepted_total <= 0:
        raise ValidationError("No quantity was accepted. Use Reject Receipt when all quantities are rejected.")

    now = timezone.now()
    for product_id, quantity in accepted_by_product.items():
        products[product_id].current_stock += quantity
        products[product_id].updated_at = now
    StockMove.objects.bulk_create(stock_moves)
    Product.objects.bulk_update(
        [products[product_id] for product_id in accepted_by_product],
        ["current_stock", "updated_at"],
    )

    posted_move_iter = iter(stock_moves)
    changed_receipt_lines = []
    for receipt_line in receipt_lines:
        if receipt_line.accepted_quantity > 0:
            receipt_line.stock_move_id = next(posted_move_iter).pk
            receipt_line.updated_at = now
            changed_receipt_lines.append(receipt_line)
    GoodsReceiptLine.objects.bulk_update(changed_receipt_lines, ["stock_move", "updated_at"])

    changed_order_lines = []
    for order_line_id, quantity in accepted_by_order_line.items():
        order_line = order_lines[order_line_id]
        order_line.received_quantity += quantity
        order_line.updated_at = now
        changed_order_lines.append(order_line)
    PurchaseOrderLine.objects.bulk_update(changed_order_lines, ["received_quantity", "updated_at"])

    locked.status = GoodsReceipt.Status.RECEIVED
    locked.save(update_fields=["status", "updated_at"])
    order = PurchaseOrder.objects.select_for_update().get(pk=locked.order_id)
    all_order_lines = _require_bounded(list(
        PurchaseOrderLine.objects.filter(order_id=order.pk)
        .select_related("product")
        .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
    ), limit=MAX_PURCHASE_DOCUMENT_LINES, label="Purchase order")
    _sync_order_receiving(order, all_order_lines)
    sync_purchase_three_way_match({order.pk})
    _auto_close_purchase_order_if_received_and_billed(order.pk)
    return locked


@transaction.atomic
def reject_goods_receipt(receipt, actor=None):
    locked = GoodsReceipt.objects.select_for_update().get(pk=_pk(receipt))
    if locked.status != GoodsReceipt.Status.INSPECTING:
        raise ValidationError("Only a receipt under inspection can be rejected.")
    locked.status = GoodsReceipt.Status.REJECTED
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def cancel_goods_receipt(receipt, actor=None):
    locked = GoodsReceipt.objects.select_for_update().get(pk=_pk(receipt))
    if locked.status != GoodsReceipt.Status.DRAFT:
        raise ValidationError("Only a draft goods receipt can be cancelled.")
    locked.status = GoodsReceipt.Status.CANCELLED
    locked.save(update_fields=["status", "updated_at"])
    return locked


def _locked_transfer_with_lines(transfer):
    locked = (
        PurchaseInventoryTransfer.objects.select_for_update(of=("self",))
        .select_related("receipt", "source_warehouse", "destination_warehouse")
        .get(pk=_pk(transfer))
    )
    lines = _require_bounded(list(
        PurchaseInventoryTransferLine.objects.select_for_update(of=("self",))
        .filter(transfer_id=locked.pk)
        .select_related("product", "receipt_line", "receipt_line__order_line")
        .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
    ), limit=MAX_PURCHASE_DOCUMENT_LINES, label="Inventory transfer")
    if not lines:
        raise ValidationError("Add at least one transfer line.")
    if locked.source_warehouse_id == locked.destination_warehouse_id:
        raise ValidationError("Source and destination warehouses must differ.")
    if locked.source_warehouse.company_id != locked.destination_warehouse.company_id:
        raise ValidationError("Source and destination warehouses must belong to one company.")
    if locked.receipt_id:
        if locked.receipt.status != GoodsReceipt.Status.RECEIVED:
            raise ValidationError("Only a posted goods receipt can be linked to an inventory transfer.")
        if locked.receipt.warehouse_id != locked.source_warehouse_id:
            raise ValidationError("The transfer source must match the goods receipt warehouse.")
    for line in lines:
        if line.quantity <= 0:
            raise ValidationError("Every transfer quantity must be greater than zero.")
        if line.product.company_id != locked.source_warehouse.company_id:
            raise ValidationError("Every transfer product must belong to the warehouse company.")
        if line.receipt_line_id and line.receipt_line.order_line.product_id != line.product_id:
            raise ValidationError("Transfer products must match their linked receipt lines.")
    return locked, lines


@transaction.atomic
def ready_purchase_inventory_transfer(transfer, actor=None):
    locked, _lines = _locked_transfer_with_lines(transfer)
    if locked.status != PurchaseInventoryTransfer.Status.DRAFT:
        raise ValidationError("Only a draft inventory transfer can be marked ready.")
    locked.status = PurchaseInventoryTransfer.Status.READY
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def dispatch_purchase_inventory_transfer(transfer, actor=None):
    locked, _lines = _locked_transfer_with_lines(transfer)
    if locked.status != PurchaseInventoryTransfer.Status.READY:
        raise ValidationError("Only a ready inventory transfer can be dispatched.")
    locked.status = PurchaseInventoryTransfer.Status.IN_TRANSIT
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def complete_purchase_inventory_transfer(transfer, actor):
    locked, lines = _locked_transfer_with_lines(transfer)
    if locked.status != PurchaseInventoryTransfer.Status.IN_TRANSIT:
        raise ValidationError("Only an in-transit inventory transfer can be completed.")
    if actor is None:
        raise ValidationError("A user is required to post inventory transfers.")
    if any(line.source_stock_move_id or line.destination_stock_move_id for line in lines):
        raise ValidationError("This inventory transfer already has posted stock movements.")

    product_ids = sorted({line.product_id for line in lines})
    products = Product.objects.select_for_update().in_bulk(product_ids)
    requested = defaultdict(Decimal)
    for line in lines:
        requested[line.product_id] += line.quantity
    source_balances = {
        row["product_id"]: row["balance"] or ZERO
        for row in StockMove.objects.filter(
            company_id=locked.source_warehouse.company_id,
            warehouse_id=locked.source_warehouse_id,
            product_id__in=product_ids,
        )
        .order_by()
        .values("product_id")
        .annotate(
            balance=Sum(
                Case(
                    When(direction=StockMove.Direction.IN, then=F("quantity")),
                    default=-F("quantity"),
                    output_field=DecimalField(max_digits=18, decimal_places=3),
                )
            )
        )
    }
    for product_id, quantity in requested.items():
        available = source_balances.get(product_id, ZERO)
        if available < quantity:
            raise ValidationError(
                f"Insufficient stock for {products[product_id]} in {locked.source_warehouse}. Available: {available}."
            )

    stock_moves = []
    for line in lines:
        note = f"Internal transfer {locked.reference}."
        stock_moves.extend(
            [
                StockMove(
                    company_id=locked.source_warehouse.company_id,
                    product_id=line.product_id,
                    warehouse_id=locked.source_warehouse_id,
                    direction=StockMove.Direction.OUT,
                    quantity=line.quantity,
                    reference=locked.reference,
                    movement_date=locked.transfer_date,
                    note=note,
                    created_by=actor,
                ),
                StockMove(
                    company_id=locked.destination_warehouse.company_id,
                    product_id=line.product_id,
                    warehouse_id=locked.destination_warehouse_id,
                    direction=StockMove.Direction.IN,
                    quantity=line.quantity,
                    reference=locked.reference,
                    movement_date=locked.transfer_date,
                    note=note,
                    created_by=actor,
                ),
            ]
        )
    StockMove.objects.bulk_create(stock_moves)
    now = timezone.now()
    for index, line in enumerate(lines):
        line.source_stock_move_id = stock_moves[index * 2].pk
        line.destination_stock_move_id = stock_moves[index * 2 + 1].pk
        line.updated_at = now
    PurchaseInventoryTransferLine.objects.bulk_update(
        lines,
        ["source_stock_move", "destination_stock_move", "updated_at"],
    )
    locked.status = PurchaseInventoryTransfer.Status.DONE
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def cancel_purchase_inventory_transfer(transfer, actor=None):
    locked = PurchaseInventoryTransfer.objects.select_for_update().get(pk=_pk(transfer))
    if locked.status not in {
        PurchaseInventoryTransfer.Status.DRAFT,
        PurchaseInventoryTransfer.Status.READY,
    }:
        raise ValidationError("Only a draft or ready inventory transfer can be cancelled.")
    locked.status = PurchaseInventoryTransfer.Status.CANCELLED
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def complete_purchase_order(order, actor=None):
    locked = PurchaseOrder.objects.select_for_update().get(pk=_pk(order))
    if locked.status != PurchaseOrder.Status.CONFIRMED:
        raise ValidationError("Only an approved purchase order can be completed.")
    lines = _require_bounded(
        list(
            locked.lines.select_related("product")
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase order",
    )
    stock_lines, ordered, received, _percentage = _receiving_totals(lines)
    if stock_lines and received < ordered:
        raise ValidationError("Receive every stockable quantity before completing the purchase order.")
    if ServiceWorkOrder.objects.filter(source_order_id=locked.pk).exclude(
        status=ServiceWorkOrder.Status.DONE
    ).exists():
        raise ValidationError("Complete every linked Service Work Order before completing the Purchase Order.")
    locked.status = PurchaseOrder.Status.COMPLETED
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def close_purchase_order(order, actor=None):
    locked = PurchaseOrder.objects.select_for_update().get(pk=_pk(order))
    if locked.status not in {PurchaseOrder.Status.CONFIRMED, PurchaseOrder.Status.COMPLETED}:
        raise ValidationError("Only an approved or completed purchase order can be closed.")
    lines = _require_bounded(
        list(
            locked.lines.select_related("product")
            .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        ),
        limit=MAX_PURCHASE_DOCUMENT_LINES,
        label="Purchase order",
    )
    stock_lines, ordered, received, _percentage = _receiving_totals(lines)
    if stock_lines and received < ordered:
        raise ValidationError("Receive every stockable quantity before closing the purchase order.")
    if ServiceWorkOrder.objects.filter(source_order_id=locked.pk).exclude(
        status=ServiceWorkOrder.Status.DONE
    ).exists():
        raise ValidationError("Complete every linked Service Work Order before closing the Purchase Order.")
    billing_plan_id = (
        PurchaseOrderInvoiceAccounting.objects.filter(order_id=locked.pk)
        .values_list("pk", flat=True)
        .first()
    )
    if billing_plan_id:
        active_invoices = PurchaseInvoiceAccounting.objects.filter(
            order_accounting_id=billing_plan_id,
        ).exclude(invoice__status=Invoice.Status.CANCELLED)
        if active_invoices.exclude(invoice__status__in=[Invoice.Status.POSTED, Invoice.Status.PAID]).exists():
            raise ValidationError("Post or cancel every Purchase down-payment and settlement bill before closing.")
        if _posted_purchase_downpayment_balance(billing_plan_id) > 0:
            raise ValidationError("Settle the posted Purchase down payment before closing the Purchase Order.")
    locked.status = PurchaseOrder.Status.CLOSED
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def cancel_purchase_order(order, actor, *, acknowledge_received_return=False):
    locked = PurchaseOrder.objects.select_for_update().get(pk=_pk(order))
    if locked.status not in {
        PurchaseOrder.Status.DRAFT,
        PurchaseOrder.Status.SENT,
        PurchaseOrder.Status.WAITING_APPROVAL,
        PurchaseOrder.Status.CONFIRMED,
        PurchaseOrder.Status.COMPLETED,
        PurchaseOrder.Status.REVISED,
    }:
        raise ValidationError("This purchase order can no longer be cancelled.")
    if actor is None:
        raise ValidationError("A user is required to cancel inventory receipts.")
    if PurchaseAccountingDocument.objects.filter(
        order_id=locked.pk,
        invoice__isnull=False,
    ).exclude(invoice__status=Invoice.Status.CANCELLED).exists():
        raise ValidationError("Cancel every active vendor bill before cancelling the purchase order.")

    receipts = _require_bounded(list(
        GoodsReceipt.objects.select_for_update()
        .filter(order_id=locked.pk)
        .order_by("pk")[: MAX_GOODS_RECEIPTS_PER_ORDER + 1]
    ), limit=MAX_GOODS_RECEIPTS_PER_ORDER, label="Purchase order receipt history")
    receipt_ids = [receipt.pk for receipt in receipts]
    received_lines = _require_bounded(list(
        GoodsReceiptLine.objects.select_for_update()
        .filter(receipt_id__in=receipt_ids, receipt__status=GoodsReceipt.Status.RECEIVED)
        .select_related("order_line", "order_line__product", "receipt")
        .order_by("pk")[: MAX_PURCHASE_RECEIPT_HISTORY_LINES + 1]
    ), limit=MAX_PURCHASE_RECEIPT_HISTORY_LINES, label="Posted receipt return history")
    if received_lines and not acknowledge_received_return:
        raise ValidationError(
            "Confirm the inventory reversal before cancelling a purchase order with received goods."
        )
    product_ids = sorted({line.order_line.product_id for line in received_lines})
    products = Product.objects.select_for_update().in_bulk(product_ids)

    return_by_product = defaultdict(Decimal)
    return_by_order_line = defaultdict(Decimal)
    return_lines = []
    for line in received_lines:
        quantity = line.accepted_quantity - line.returned_quantity
        if quantity <= 0:
            continue
        if line.return_stock_move_id:
            raise ValidationError("A receipt return is already linked but its returned quantity is incomplete.")
        return_by_product[line.order_line.product_id] += quantity
        return_by_order_line[line.order_line_id] += quantity
        return_lines.append((line, quantity))
    for product_id, quantity in return_by_product.items():
        if products[product_id].current_stock < quantity:
            raise ValidationError(
                f"Cannot return {products[product_id]}: available stock is {products[product_id].current_stock}."
            )

    returned_receipt_ids = sorted({line.receipt_id for line, _quantity in return_lines})
    return_requests = [
        PurchaseReturnRequest(
            company_id=locked.company_id,
            number=f"PRTN-{receipt_id}-{uuid4().hex[:8].upper()}",
            receipt_id=receipt_id,
            return_date=timezone.localdate(),
            reason=f"Automatic vendor return after cancellation of purchase order {locked.number}.",
            status="approved",
            approver=actor,
        )
        for receipt_id in returned_receipt_ids
    ]
    PurchaseReturnRequest.objects.bulk_create(return_requests)

    return_moves = [
        StockMove(
            company_id=locked.company_id,
            product_id=line.order_line.product_id,
            warehouse_id=line.receipt.warehouse_id,
            direction=StockMove.Direction.OUT,
            quantity=quantity,
            reference=f"RETURN-{line.receipt.number}"[:80],
            movement_date=timezone.localdate(),
            note=f"Vendor return after cancellation of purchase order {locked.number}.",
            created_by=actor,
        )
        for line, quantity in return_lines
    ]
    StockMove.objects.bulk_create(return_moves)

    now = timezone.now()
    for product_id, quantity in return_by_product.items():
        products[product_id].current_stock -= quantity
        products[product_id].updated_at = now
    if return_by_product:
        Product.objects.bulk_update(
            [products[product_id] for product_id in return_by_product],
            ["current_stock", "updated_at"],
        )
    for (line, quantity), stock_move in zip(return_lines, return_moves):
        line.returned_quantity += quantity
        line.return_stock_move_id = stock_move.pk
        line.updated_at = now
    if return_lines:
        GoodsReceiptLine.objects.bulk_update(
            [line for line, _quantity in return_lines],
            ["returned_quantity", "return_stock_move", "updated_at"],
        )

    order_lines = PurchaseOrderLine.objects.select_for_update().in_bulk(return_by_order_line)
    for order_line_id, quantity in return_by_order_line.items():
        order_line = order_lines[order_line_id]
        order_line.received_quantity = max(ZERO, order_line.received_quantity - quantity)
        order_line.updated_at = now
    if order_lines:
        PurchaseOrderLine.objects.bulk_update(
            list(order_lines.values()),
            ["received_quantity", "updated_at"],
        )

    GoodsReceipt.objects.filter(
        order_id=locked.pk,
        status__in=[GoodsReceipt.Status.DRAFT, GoodsReceipt.Status.INSPECTING],
    ).update(status=GoodsReceipt.Status.CANCELLED, updated_at=now)
    control, _created = PurchaseOrderControl.objects.get_or_create(order_id=locked.pk)
    control.receiving_status = (
        PurchaseOrderControl.ReceivingStatus.RETURNED
        if return_lines
        else PurchaseOrderControl.ReceivingStatus.NOT_RECEIVED
    )
    control.save(update_fields=["receiving_status", "updated_at"])
    locked.status = PurchaseOrder.Status.CANCELLED
    locked.received_percentage = ZERO
    locked.save(update_fields=["status", "received_percentage", "updated_at"])
    sync_purchase_three_way_match({locked.pk})
    return locked


def handle_purchase_document_action(
    entity,
    instance,
    action,
    actor,
    *,
    acknowledge_received_return=False,
    reason="",
):
    if entity == "purchases":
        handlers = {
            "confirm": confirm_purchase_order,
            "create-receipt": create_goods_receipts,
            "create-bill": create_purchase_vendor_bill,
            "create-downpayment": create_purchase_downpayment_bill,
            "create-settlement": create_purchase_settlement_bill,
            "complete": complete_purchase_order,
            "close": close_purchase_order,
            "cancel": cancel_purchase_order,
            "revise": revise_purchase_order,
        }
    elif entity == "goods-receipts":
        handlers = {
            "inspect": inspect_goods_receipt,
            "receive": receive_goods_receipt,
            "reject": reject_goods_receipt,
            "cancel": cancel_goods_receipt,
        }
    elif entity == "purchase-inventory-transfers":
        handlers = {
            "ready": ready_purchase_inventory_transfer,
            "dispatch": dispatch_purchase_inventory_transfer,
            "complete": complete_purchase_inventory_transfer,
            "cancel": cancel_purchase_inventory_transfer,
        }
    elif entity == "purchase-quotations":
        handlers = {
            "send": transition_purchase_quotation,
            "receive": transition_purchase_quotation,
            "select": transition_purchase_quotation,
            "cancel": transition_purchase_quotation,
        }
    elif entity == "blanket-orders":
        handlers = {
            "submit": transition_blanket_order,
            "approve": transition_blanket_order,
            "reject": transition_blanket_order,
            "create-rfq": transition_blanket_order,
            "run": transition_blanket_order,
            "close": transition_blanket_order,
            "reopen": transition_blanket_order,
            "cancel": transition_blanket_order,
        }
    elif entity == "direct-purchases":
        handlers = {
            "submit": transition_direct_purchase,
            "approve": transition_direct_purchase,
            "reject": transition_direct_purchase,
            "cancel": transition_direct_purchase,
            "reset": transition_direct_purchase,
        }
    elif entity == "purchase-tenders":
        handlers = {
            "submit": transition_purchase_tender,
            "approve": transition_purchase_tender,
            "reject": transition_purchase_tender,
            "publish": transition_purchase_tender,
            "open-submission": transition_purchase_tender,
            "open-bids": transition_purchase_tender,
            "evaluate": transition_purchase_tender,
            "award": transition_purchase_tender,
            "submit-decision": transition_purchase_tender,
            "approve-decision": transition_purchase_tender,
            "reject-decision": transition_purchase_tender,
            "reset-decision": transition_purchase_tender,
            "reopen-award": transition_purchase_tender,
            "close": transition_purchase_tender,
            "cancel": transition_purchase_tender,
        }
    elif entity == "tender-award-responses":
        handlers = {
            "accept": transition_tender_award_response,
            "reject": transition_tender_award_response,
            "expire": transition_tender_award_response,
        }
    elif entity == "vendor-pricelists":
        handlers = {
            "submit": transition_vendor_pricelist,
            "approve": transition_vendor_pricelist,
            "reject": transition_vendor_pricelist,
            "archive": transition_vendor_pricelist,
            "reset": transition_vendor_pricelist,
        }
    elif entity == "vendor-pricelist-amendments":
        handlers = {
            "submit": transition_vendor_pricelist_amendment,
            "approve": transition_vendor_pricelist_amendment,
            "reject": transition_vendor_pricelist_amendment,
            "cancel": transition_vendor_pricelist_amendment,
            "reset": transition_vendor_pricelist_amendment,
        }
    elif entity == "vendor-pricelist-imports":
        handlers = {
            "validate": transition_vendor_pricelist_import,
            "import": transition_vendor_pricelist_import,
        }
    elif entity == "vendor-performance-evaluations":
        handlers = {
            "submit": transition_vendor_performance_evaluation,
            "approve": transition_vendor_performance_evaluation,
            "reject": transition_vendor_performance_evaluation,
        }
    elif entity == "purchase-kpi-targets":
        handlers = {
            "submit": transition_purchase_kpi_target,
            "approve": transition_purchase_kpi_target,
            "reject": transition_purchase_kpi_target,
            "calculate": transition_purchase_kpi_target,
            "close": transition_purchase_kpi_target,
        }
    elif entity == "vendor-profiles":
        handlers = {
            "submit": transition_vendor_profile,
            "approve": transition_vendor_profile,
            "reject": transition_vendor_profile,
            "reset": transition_vendor_profile,
            "blacklist": transition_vendor_profile,
            "unblacklist": transition_vendor_profile,
        }
    elif entity == "vendor-legal-documents":
        handlers = {
            "submit": transition_vendor_legal_document,
            "approve": transition_vendor_legal_document,
            "reject": transition_vendor_legal_document,
        }
    elif entity == "vendor-prequalifications":
        handlers = {
            "submit": transition_vendor_prequalification,
            "review": transition_vendor_prequalification,
            "approve": transition_vendor_prequalification,
            "reject": transition_vendor_prequalification,
        }
    elif entity == "vendor-document-reviews":
        handlers = {
            "submit": transition_vendor_document_review,
            "approve": transition_vendor_document_review,
            "reject": transition_vendor_document_review,
            "revision": transition_vendor_document_review,
            "reset": transition_vendor_document_review,
        }
    elif entity == "service-work-orders":
        handlers = {
            "start": transition_service_work_order,
            "done": transition_service_work_order,
            "create-bill": create_service_work_order_bill,
        }
    elif entity == "service-work-order-lines":
        handlers = {
            "done": transition_service_work_order_line,
        }
    else:
        handlers = {}
    handler = handlers.get(action)
    if handler is None:
        raise ValidationError("Unsupported purchase workflow action.")
    if entity == "purchases" and action == "cancel":
        return handler(
            instance,
            actor,
            acknowledge_received_return=acknowledge_received_return,
        )
    if entity == "purchases" and action == "revise":
        return handler(instance, actor, reason=reason)
    if entity == "service-work-orders" and action == "create-bill":
        return handler(instance, actor)
    if entity in {
        "purchase-quotations",
        "blanket-orders",
        "direct-purchases",
        "purchase-tenders",
        "tender-award-responses",
        "vendor-pricelists",
        "vendor-pricelist-amendments",
        "vendor-pricelist-imports",
        "vendor-performance-evaluations",
        "purchase-kpi-targets",
        "vendor-profiles",
        "vendor-legal-documents",
        "vendor-prequalifications",
        "vendor-document-reviews",
        "service-work-orders",
        "service-work-order-lines",
    }:
        if entity in {
            "purchase-tenders",
            "vendor-performance-evaluations",
            "vendor-profiles",
            "vendor-legal-documents",
            "vendor-prequalifications",
            "vendor-document-reviews",
        }:
            return handler(instance, action, actor, reason=reason)
        return handler(instance, action, actor)
    return handler(instance, actor)


@transaction.atomic
def approve_budget_movement(movement_id, _actor):
    """Approve one submitted movement while locking its budget balance."""
    movement = ProductBudgetMovement.objects.select_for_update().get(pk=movement_id)
    if movement.status != "submitted":
        raise ValidationError("Only a submitted budget movement can be approved.")

    budget = ProductBudget.objects.select_for_update().get(pk=movement.budget_id)
    amount = movement.amount
    movement_type = movement.movement_type

    if movement_type == ProductBudgetMovement.MovementType.RESERVE:
        if not budget.allow_overbudget and amount > budget.available_amount:
            raise ValidationError("The movement exceeds the available product budget.")
        budget.reserved_amount += amount
    elif movement_type == ProductBudgetMovement.MovementType.RELEASE:
        if amount > budget.reserved_amount:
            raise ValidationError("The release exceeds the reserved product budget.")
        budget.reserved_amount -= amount
    elif movement_type == ProductBudgetMovement.MovementType.CONSUME:
        if amount > budget.reserved_amount:
            raise ValidationError("The consumption exceeds the reserved product budget.")
        budget.reserved_amount -= amount
        budget.consumed_amount += amount
    elif movement_type == ProductBudgetMovement.MovementType.REVERSE:
        if amount > budget.consumed_amount:
            raise ValidationError("The reversal exceeds the consumed product budget.")
        budget.consumed_amount -= amount
    elif movement_type == ProductBudgetMovement.MovementType.ADJUST:
        budget.planned_amount += amount
    else:
        raise ValidationError("Unsupported product budget movement type.")

    budget.save(update_fields=["planned_amount", "reserved_amount", "consumed_amount", "updated_at"])
    movement.status = "approved"
    movement.approved_by = _actor
    movement.save(update_fields=["status", "approved_by", "updated_at"])
    return movement
