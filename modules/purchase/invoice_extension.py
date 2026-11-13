"""Purchase-owned vendor-bill lifecycle integration."""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone

from core.models import Product, PurchaseOrder, PurchaseOrderLine
from modules.finance.invoice_posting import (
    InvoicePostingLine,
    InvoicePostingOverride,
    register_invoice_cancellation_observer,
    register_invoice_post_observer,
    register_invoice_posting_contributor,
    register_invoice_posting_failure_observer,
)

from .models import (
    PurchaseAccountingDocument,
    PurchaseBillLineProvenance,
    PurchaseInvoiceAccounting,
    PurchaseInvoiceLineProvenance,
    PurchaseJournalLineAnalyticSnapshot,
    PurchaseOrderControl,
    PurchaseOrderLinePricingProvenance,
    PurchaseOrderInvoiceAccounting,
    PurchaseOrderMilestone,
    ServiceWorkOrder,
)
from .purchase_cost_center import (
    MAX_COST_CENTER_ASSIGNMENTS,
    assignment_kwargs,
    validate_purchase_cost_center_batch,
)
from .three_way_match import (
    sync_purchase_three_way_match,
    validate_strict_purchase_three_way_match,
)


ZERO = Decimal("0")
MAX_PURCHASE_BILL_LINES = 500
MAX_PURCHASE_JOURNAL_ANALYTIC_REFERENCES = (
    (MAX_PURCHASE_BILL_LINES + 1) * MAX_COST_CENTER_ASSIGNMENTS
)


def apply_purchase_cost_centers_to_journal_entry(
    entry,
    *,
    order,
    assignments,
    order_line_ids,
):
    """Materialize bounded Purchase assignments on Finance journal lines."""
    if len(assignments) > MAX_PURCHASE_BILL_LINES + 1:
        raise ValidationError(
            f"Purchase journal analytic snapshot exceeds {MAX_PURCHASE_BILL_LINES + 1} lines."
        )
    if len(assignments) != len(order_line_ids):
        raise ValidationError("Purchase journal analytic source mapping is incomplete.")
    normalized = validate_purchase_cost_center_batch(order.company_id, assignments)
    journal_lines = list(
        entry.lines.order_by("pk")[: MAX_PURCHASE_BILL_LINES + 2]
    )
    if len(journal_lines) != len(normalized):
        raise ValidationError("Purchase journal analytic lines do not match the posting plan.")

    cost_center_ids = {
        cost_center_id
        for assignment in normalized
        for cost_center_id in (
            assignment["cost_center_distribution_ids"]
            + [
                row["cost_center_id"]
                for row in assignment["cost_center_distribution_data"]
            ]
        )
    }
    cost_center_codes = {
        assignment["cost_center"]
        for assignment in normalized
        if assignment["cost_center"]
    }
    from modules.finance.models import (
        AccountingCostCenter,
        JournalLineCostCenterAllocation,
    )

    centers = list(
        AccountingCostCenter.objects.filter(company_id=order.company_id)
        .filter(Q(pk__in=cost_center_ids) | Q(code__in=cost_center_codes))
        .order_by("pk")
        [: MAX_PURCHASE_JOURNAL_ANALYTIC_REFERENCES + 1]
    )
    if len(centers) > MAX_PURCHASE_JOURNAL_ANALYTIC_REFERENCES:
        raise ValidationError("Purchase journal analytic references exceed the operational limit.")
    center_by_id = {center.pk: center for center in centers}
    center_by_code = {center.code: center for center in centers}
    allocations = []
    snapshots = []
    changed_lines = []
    for journal_line, assignment, order_line_id in zip(
        journal_lines,
        normalized,
        order_line_ids,
    ):
        rows = assignment["cost_center_distribution_data"]
        primary = center_by_code.get(assignment["cost_center"])
        if primary is None:
            assigned_ids = assignment["cost_center_distribution_ids"] or [
                row["cost_center_id"] for row in rows
            ]
            if len(assigned_ids) == 1:
                primary = center_by_id.get(assigned_ids[0])
        if primary is not None and journal_line.cost_center_id != primary.pk:
            journal_line.cost_center_id = primary.pk
            changed_lines.append(journal_line)

        allocation_rows = rows
        if not allocation_rows and primary is not None:
            allocation_rows = [
                {"cost_center_id": primary.pk, "percentage": "100"}
            ]
        allocations.extend(
            JournalLineCostCenterAllocation(
                journal_line=journal_line,
                cost_center_id=row["cost_center_id"],
                percentage=Decimal(row["percentage"]),
            )
            for row in allocation_rows
        )
        snapshots.append(
            PurchaseJournalLineAnalyticSnapshot(
                journal_line=journal_line,
                order=order,
                order_line_id=order_line_id,
                **assignment,
            )
        )
    if changed_lines:
        type(journal_lines[0]).objects.bulk_update(
            changed_lines,
            ["cost_center"],
            batch_size=500,
        )
    if allocations:
        JournalLineCostCenterAllocation.objects.bulk_create(
            allocations,
            batch_size=500,
        )
    PurchaseJournalLineAnalyticSnapshot.objects.bulk_create(
        snapshots,
        batch_size=500,
    )


def _purchase_invoice_posting(*, invoice, rows, partner_account, total, journal, actor=None):
    validate_strict_purchase_three_way_match(invoice=invoice, rows=rows)
    provenance = (
        PurchaseInvoiceAccounting.objects.select_for_update(of=("self",))
        .select_related("order_accounting__order", "source_downpayment_invoice__invoice")
        .filter(invoice_id=invoice.pk)
        .first()
    )
    if provenance is None:
        return None
    from modules.finance.models import CommercialAccountingSettings, ProductAccountMapping

    commercial = (
        CommercialAccountingSettings.objects.select_related(
            "purchase_journal",
            "purchase_downpayment_account",
        )
        .filter(company_id=invoice.company_id)
        .first()
    )
    if commercial is None:
        raise ValidationError(
            "Configure Sales & Purchase Accounting settings before posting a Purchase Order bill."
        )
    row_ids = [row.pk for row in rows]
    line_sources = {
        source.invoice_line_id: source
        for source in PurchaseInvoiceLineProvenance.objects.filter(
            purchase_invoice=provenance,
            invoice_line_id__in=row_ids,
        ).select_related("order_line", "source_downpayment_line")
    }
    if len(line_sources) != len(rows):
        raise ValidationError("Every Purchase Order bill line must carry exact purchase provenance.")
    order = provenance.order_accounting.order
    control = PurchaseOrderControl.objects.filter(order_id=order.pk).first()
    order_line_ids = {
        source.order_line_id
        for source in line_sources.values()
        if source.order_line_id
    }
    pricing_by_order_line_id = {
        row.order_line_id: row
        for row in PurchaseOrderLinePricingProvenance.objects.filter(
            order_line_id__in=order_line_ids
        )
    }
    posting_lines = []
    posting_assignments = []
    posting_order_line_ids = []
    if provenance.invoice_kind == PurchaseInvoiceAccounting.InvoiceKind.DOWNPAYMENT:
        if any(source.source_downpayment_line_id for source in line_sources.values()):
            raise ValidationError("Purchase down-payment bill lines cannot carry settlement provenance.")
        posting_lines.append(
            InvoicePostingLine(
                account=commercial.purchase_downpayment_account,
                debit=total,
                partner=invoice.partner,
                description=f"Purchase Down Payment: {provenance.order_accounting.order.number}",
            )
        )
        posting_assignments.append(assignment_kwargs(control))
        posting_order_line_ids.append(None)
    else:
        product_ids = {
            source.order_line.product_id
            for source in line_sources.values()
            if source.order_line_id
        }
        mappings = {
            mapping.product_id: mapping
            for mapping in ProductAccountMapping.objects.filter(
                company_id=invoice.company_id,
                product_id__in=product_ids,
                active=True,
            ).select_related("expense_account")
        }
        missing_product_ids = product_ids - set(mappings)
        if missing_product_ids:
            raise ValidationError("Configure an expense account for every Purchase Order settlement product.")
        for row in rows:
            source = line_sources[row.pk]
            amount = row.quantity * row.unit_price
            if source.order_line_id:
                posting_lines.append(
                    InvoicePostingLine(
                        account=mappings[source.order_line.product_id].expense_account,
                        debit=amount,
                        partner=invoice.partner,
                        description=row.description,
                    )
                )
                posting_assignments.append(
                    assignment_kwargs(
                        pricing_by_order_line_id.get(source.order_line_id),
                        parent=control,
                    )
                )
                posting_order_line_ids.append(source.order_line_id)
            elif source.source_downpayment_line_id:
                if amount >= 0:
                    raise ValidationError("Purchase settlement down-payment applications must be negative.")
                posting_lines.append(
                    InvoicePostingLine(
                        account=commercial.purchase_downpayment_account,
                        credit=-amount,
                        partner=invoice.partner,
                        description=row.description,
                    )
                )
                posting_assignments.append(assignment_kwargs(control))
                posting_order_line_ids.append(None)
            else:
                raise ValidationError("Purchase settlement lines require an order line or down-payment source.")
    if total > 0:
        posting_lines.append(
            InvoicePostingLine(
                account=partner_account,
                credit=total,
                partner=invoice.partner,
                description=invoice.number,
                due_date=invoice.due_date,
            )
        )
        posting_assignments.append(assignment_kwargs(control))
        posting_order_line_ids.append(None)

    def apply_analytic_snapshots(entry):
        apply_purchase_cost_centers_to_journal_entry(
            entry,
            order=order,
            assignments=posting_assignments,
            order_line_ids=posting_order_line_ids,
        )

    return InvoicePostingOverride(
        lines=tuple(posting_lines),
        journal=commercial.purchase_journal,
        description=invoice.notes,
        total=total,
        after_post=apply_analytic_snapshots,
    )


def _remaining_posted_downpayment_balance(plan_id):
    amount_expression = ExpressionWrapper(
        F("invoice_line__quantity") * F("invoice_line__unit_price"),
        output_field=DecimalField(max_digits=30, decimal_places=8),
    )
    source_total = (
        PurchaseInvoiceLineProvenance.objects.filter(
            purchase_invoice__order_accounting_id=plan_id,
            purchase_invoice__invoice_kind=PurchaseInvoiceAccounting.InvoiceKind.DOWNPAYMENT,
            purchase_invoice__invoice__status__in=["posted", "paid"],
        ).aggregate(total=Sum(amount_expression))["total"]
        or ZERO
    )
    consumed_total = (
        PurchaseInvoiceLineProvenance.objects.filter(
            purchase_invoice__order_accounting_id=plan_id,
            purchase_invoice__invoice_kind=PurchaseInvoiceAccounting.InvoiceKind.SETTLEMENT,
            purchase_invoice__invoice__status__in=["posted", "paid"],
            source_downpayment_line_id__isnull=False,
        ).aggregate(total=Sum(amount_expression))["total"]
        or ZERO
    )
    return max(source_total + consumed_total, ZERO)


def _sync_billing_plan_after_post(invoice):
    provenance = PurchaseInvoiceAccounting.objects.filter(invoice_id=invoice.pk).first()
    if provenance is None:
        return
    if provenance.invoice_kind == PurchaseInvoiceAccounting.InvoiceKind.DOWNPAYMENT:
        target = PurchaseOrderInvoiceAccounting.Status.DOWNPAYMENT_POSTED
    else:
        target = (
            PurchaseOrderInvoiceAccounting.Status.COMPLETED
            if _remaining_posted_downpayment_balance(provenance.order_accounting_id) <= 0
            else PurchaseOrderInvoiceAccounting.Status.DOWNPAYMENT_POSTED
        )
    PurchaseOrderInvoiceAccounting.objects.filter(pk=provenance.order_accounting_id).update(
        status=target,
        error_message="",
        updated_at=timezone.now(),
    )


def _sync_billing_plan_after_cancel(invoice):
    provenance = (
        PurchaseInvoiceAccounting.objects.select_related("order_accounting__settlement_invoice")
        .filter(invoice_id=invoice.pk)
        .first()
    )
    if provenance is None:
        return
    plan = provenance.order_accounting
    if provenance.invoice_kind == PurchaseInvoiceAccounting.InvoiceKind.DOWNPAYMENT:
        active_applications = PurchaseInvoiceLineProvenance.objects.filter(
            source_downpayment_line__purchase_invoice=provenance,
        ).exclude(purchase_invoice__invoice__status="cancelled")
        if active_applications.exists():
            raise ValidationError("Cancel every linked Purchase settlement bill before cancelling its down payment.")
    target = (
        PurchaseOrderInvoiceAccounting.Status.READY
        if provenance.invoice_kind == PurchaseInvoiceAccounting.InvoiceKind.DOWNPAYMENT
        else PurchaseOrderInvoiceAccounting.Status.DOWNPAYMENT_POSTED
    )
    PurchaseOrderInvoiceAccounting.objects.filter(pk=provenance.order_accounting_id).update(
        status=target,
        updated_at=timezone.now(),
    )


def _sync_billing_plan_after_failure(*, invoice, error):
    PurchaseOrderInvoiceAccounting.objects.filter(
        invoice_provenance__invoice_id=invoice.pk,
    ).update(
        status=PurchaseOrderInvoiceAccounting.Status.ACCOUNTING_ERROR,
        error_message=str(error)[:2000],
        updated_at=timezone.now(),
    )


def _one_vendor_bill_document(queryset):
    documents = list(queryset.order_by("pk")[:2])
    if len(documents) > 1:
        raise ValidationError("A vendor bill cannot link to more than one purchase accounting document.")
    return documents[0] if documents else None


def _bounded_provenance(queryset):
    rows = list(queryset.order_by("order_line_id", "pk")[: MAX_PURCHASE_BILL_LINES + 1])
    if len(rows) > MAX_PURCHASE_BILL_LINES:
        raise ValidationError(
            f"Vendor bill provenance exceeds the operational limit of {MAX_PURCHASE_BILL_LINES} rows."
        )
    return rows


def _sync_accounting_controls(order_ids):
    if not order_ids:
        return
    orders = {
        order.pk: order
        for order in PurchaseOrder.objects.filter(pk__in=order_ids).order_by("pk")
    }
    lines_by_order = defaultdict(list)
    for line in (
        PurchaseOrderLine.objects.filter(order_id__in=order_ids)
        .select_related("product")
        .order_by("order_id", "pk")
    ):
        lines_by_order[line.order_id].append(line)

    controls = {
        control.order_id: control
        for control in PurchaseOrderControl.objects.filter(order_id__in=order_ids)
    }
    missing = [
        PurchaseOrderControl(order_id=order_id)
        for order_id in order_ids
        if order_id not in controls
    ]
    if missing:
        PurchaseOrderControl.objects.bulk_create(missing)
        controls.update({control.order_id: control for control in missing})

    now = timezone.now()
    changed = []
    for order_id, order in orders.items():
        lines = lines_by_order[order_id]
        has_billable = False
        fully_billed = True
        any_billed = False
        for line in lines:
            basis = line.quantity
            if (
                order.invoicing_policy == PurchaseOrder.InvoicingPolicy.RECEIVED
                and line.product.product_type == Product.ProductType.STOCKABLE
            ):
                basis = line.received_quantity
            if basis <= 0:
                continue
            has_billable = True
            any_billed = any_billed or line.billed_quantity > 0
            fully_billed = fully_billed and line.billed_quantity >= basis
        control = controls[order_id]
        if has_billable and fully_billed:
            control.accounting_status = PurchaseOrderControl.AccountingStatus.BILLED
        elif any_billed:
            control.accounting_status = PurchaseOrderControl.AccountingStatus.PARTIAL
        else:
            control.accounting_status = PurchaseOrderControl.AccountingStatus.NOT_BILLED
        control.updated_at = now
        changed.append(control)
    PurchaseOrderControl.objects.bulk_update(changed, ["accounting_status", "updated_at"])


@transaction.atomic
def mark_purchase_vendor_bill_posted(*, invoice, entry=None, actor=None):
    document = _one_vendor_bill_document(
        PurchaseAccountingDocument.objects.select_for_update()
        .filter(
            invoice_id=invoice.pk,
            document_type=PurchaseAccountingDocument.DocumentType.VENDOR_BILL,
            status="draft",
        )
    )
    if document is None:
        return
    provenance = _bounded_provenance(
        PurchaseBillLineProvenance.objects.filter(accounting_document_id=document.pk)
    )
    quantities = defaultdict(Decimal)
    service_work_order_ids = set()
    for row in provenance:
        quantities[row.order_line_id] += row.quantity
        if row.service_work_order_id:
            service_work_order_ids.add(row.service_work_order_id)
    order_lines = PurchaseOrderLine.objects.select_for_update().in_bulk(quantities)
    now = timezone.now()
    for order_line_id, quantity in quantities.items():
        line = order_lines[order_line_id]
        line.billed_quantity += quantity
        line.updated_at = now
    if order_lines:
        PurchaseOrderLine.objects.bulk_update(
            list(order_lines.values()),
            ["billed_quantity", "updated_at"],
        )
    document.status = "approved"
    document.journal_reference = getattr(entry, "number", "") or ""
    document.updated_at = now
    document.save(update_fields=["status", "journal_reference", "updated_at"])
    if service_work_order_ids:
        ServiceWorkOrder.objects.filter(pk__in=service_work_order_ids).update(
            progress_paid=Decimal("100"),
            is_billed=True,
            updated_at=now,
        )
        milestone_ids = ServiceWorkOrder.objects.filter(
            pk__in=service_work_order_ids,
            milestone_id__isnull=False,
        ).values("milestone_id")
        PurchaseOrderMilestone.objects.filter(pk__in=milestone_ids).update(
            status=PurchaseOrderMilestone.Status.COMPLETED,
            updated_at=now,
        )
    _sync_accounting_controls({document.order_id})
    _sync_billing_plan_after_post(invoice)
    sync_purchase_three_way_match({document.order_id})
    from .services import _auto_close_purchase_order_if_received_and_billed

    _auto_close_purchase_order_if_received_and_billed(document.order_id)


@transaction.atomic
def mark_purchase_vendor_bill_cancelled(*, invoice, actor=None):
    document = _one_vendor_bill_document(
        PurchaseAccountingDocument.objects.select_for_update()
        .filter(
            invoice_id=invoice.pk,
            document_type=PurchaseAccountingDocument.DocumentType.VENDOR_BILL,
        )
        .exclude(status="cancelled")
    )
    if document is None:
        return
    quantities = defaultdict(Decimal)
    service_work_order_ids = set()
    if document.status == "approved":
        for row in _bounded_provenance(
            PurchaseBillLineProvenance.objects.filter(accounting_document_id=document.pk)
        ):
            quantities[row.order_line_id] += row.quantity
            if row.service_work_order_id:
                service_work_order_ids.add(row.service_work_order_id)
    order_lines = PurchaseOrderLine.objects.select_for_update().in_bulk(quantities)
    now = timezone.now()
    for order_line_id, quantity in quantities.items():
        line = order_lines[order_line_id]
        line.billed_quantity = max(ZERO, line.billed_quantity - quantity)
        line.updated_at = now
    if order_lines:
        PurchaseOrderLine.objects.bulk_update(
            list(order_lines.values()),
            ["billed_quantity", "updated_at"],
        )
    document.status = "cancelled"
    document.updated_at = now
    document.save(update_fields=["status", "updated_at"])
    if service_work_order_ids:
        ServiceWorkOrder.objects.filter(pk__in=service_work_order_ids).update(
            progress_paid=ZERO,
            is_billed=False,
            updated_at=now,
        )
        milestone_ids = ServiceWorkOrder.objects.filter(
            pk__in=service_work_order_ids,
            milestone_id__isnull=False,
        ).values("milestone_id")
        PurchaseOrderMilestone.objects.filter(pk__in=milestone_ids).update(
            status=PurchaseOrderMilestone.Status.DUE,
            updated_at=now,
        )
    _sync_accounting_controls({document.order_id})
    _sync_billing_plan_after_cancel(invoice)
    sync_purchase_three_way_match({document.order_id})


def register_purchase_invoice_extension():
    register_invoice_posting_contributor(_purchase_invoice_posting)
    register_invoice_post_observer(mark_purchase_vendor_bill_posted)
    register_invoice_posting_failure_observer(_sync_billing_plan_after_failure)
    register_invoice_cancellation_observer(mark_purchase_vendor_bill_cancelled)
