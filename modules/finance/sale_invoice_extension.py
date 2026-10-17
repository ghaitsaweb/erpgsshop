"""Master-sale down-payment and promotion accounting extension."""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Invoice, InvoiceLine

from .invoice_posting import (
    InvoicePostingLine,
    InvoicePostingOverride,
    register_invoice_cancellation_observer,
    register_invoice_post_observer,
    register_invoice_posting_contributor,
    register_invoice_posting_failure_observer,
)
from .models import (
    CommercialAccountingSettings,
    ProductAccountMapping,
    SaleOrderInvoiceAccounting,
    SalesInvoiceAccounting,
    SalesInvoiceLineProvenance,
    SalesInvoicePromotionSplit,
)


def _sales_invoice_posting(*, invoice, rows, partner_account, total, journal, actor=None):
    provenance = (
        SalesInvoiceAccounting.objects.select_for_update(of=("self",))
        .select_related("order_accounting__order", "source_downpayment_invoice__invoice")
        .filter(invoice_id=invoice.pk)
        .first()
    )
    if provenance is None:
        return None
    order = provenance.order_accounting.order
    commercial = CommercialAccountingSettings.objects.select_related(
        "sales_journal", "sales_downpayment_account"
    ).get(company_id=invoice.company_id)
    row_ids = [row.pk for row in rows]
    line_sources = {
        item.invoice_line_id: item
        for item in SalesInvoiceLineProvenance.objects.filter(
            sales_invoice=provenance, invoice_line_id__in=row_ids
        ).select_related("source_downpayment_line__sales_invoice__invoice")
    }
    split_groups = defaultdict(list)
    for split in SalesInvoicePromotionSplit.objects.filter(
        invoice_line_id__in=row_ids
    ).select_related("promotion", "expense_account_snapshot"):
        split_groups[split.invoice_line_id].append(split)
    lines = [InvoicePostingLine(
        account=partner_account,
        debit=total,
        partner=invoice.partner,
        description=invoice.number,
        due_date=invoice.due_date,
    )]
    if provenance.invoice_kind == SalesInvoiceAccounting.InvoiceKind.DOWNPAYMENT:
        if len(line_sources) != len(rows) or any(row.subtotal <= 0 for row in rows):
            raise ValidationError("Every sales down-payment line must carry down-payment provenance.")
        lines.append(InvoicePostingLine(
            account=commercial.sales_downpayment_account,
            credit=total,
            partner=invoice.partner,
            description=f"Sales Down Payment: {order.number}",
        ))
    else:
        product_ids = {
            row.product_id for row in rows if row.pk not in line_sources and row.product_id
        }
        mappings = {
            item.product_id: item
            for item in ProductAccountMapping.objects.filter(
                company_id=invoice.company_id, product_id__in=product_ids, active=True
            ).select_related("revenue_account")
        }
        for row in rows:
            amount = row.subtotal
            source_meta = line_sources.get(row.pk)
            if source_meta:
                source = source_meta.source_downpayment_line
                if (
                    amount >= 0
                    or source is None
                    or source.sales_invoice.order_accounting_id != provenance.order_accounting_id
                    or source.sales_invoice.invoice.status not in {Invoice.Status.POSTED, Invoice.Status.PAID}
                ):
                    raise ValidationError("Sales settlement down-payment lines require posted same-order provenance.")
                lines.append(InvoicePostingLine(
                    account=commercial.sales_downpayment_account,
                    debit=abs(amount),
                    partner=invoice.partner,
                    description=row.description,
                ))
                continue
            splits = split_groups.get(row.pk, ())
            promotion_discount = sum((split.discount_amount for split in splits), Decimal("0"))
            account = mappings.get(row.product_id).revenue_account if row.product_id in mappings else commercial.sales_journal.default_credit_account
            if account is None or amount < 0:
                raise ValidationError("Configure a revenue account for every sales settlement line.")
            lines.append(InvoicePostingLine(
                account=account,
                credit=amount + promotion_discount,
                partner=invoice.partner,
                description=row.description,
            ))
            lines.extend(
                InvoicePostingLine(
                    account=split.expense_account_snapshot,
                    debit=split.discount_amount,
                    partner=invoice.partner,
                    description=f"Promotion {split.promotion.code}: {row.description}",
                )
                for split in splits
            )
    return InvoicePostingOverride(
        journal=commercial.sales_journal,
        lines=tuple(lines),
        description=f"{provenance.get_invoice_kind_display()}: {order.number}",
    )


@transaction.atomic
def _create_sales_downpayment_invoice_atomic(order_accounting, actor=None):
    link = SaleOrderInvoiceAccounting.objects.select_for_update().select_related("order__customer").get(
        pk=order_accounting.pk
    )
    order = link.order
    if order.status != order.Status.CONFIRMED:
        raise ValidationError("Confirm the sales order before creating a down-payment invoice.")
    if link.downpayment_invoice_id:
        raise ValidationError("This sales order already has a down-payment invoice.")
    if link.downpayment_amount <= 0:
        raise ValidationError("Set a positive sales down-payment amount first.")
    invoice = Invoice.objects.create(
        company=order.company,
        number=f"SDP-{order.number}",
        invoice_type=Invoice.InvoiceType.CUSTOMER,
        partner=order.customer,
        invoice_date=order.order_date,
        due_date=order.order_date,
        notes=f"Sales down payment for {order.number}",
    )
    line = InvoiceLine.objects.create(
        invoice=invoice,
        description=f"Sales Down Payment - {order.number}",
        quantity=Decimal("1"),
        unit_price=link.downpayment_amount,
    )
    provenance = SalesInvoiceAccounting.objects.create(
        invoice=invoice,
        order_accounting=link,
        invoice_kind=SalesInvoiceAccounting.InvoiceKind.DOWNPAYMENT,
    )
    SalesInvoiceLineProvenance.objects.create(invoice_line=line, sales_invoice=provenance)
    link.downpayment_invoice = invoice
    link.status = SaleOrderInvoiceAccounting.Status.DOWNPAYMENT_DRAFT
    link.error_message = ""
    link.save(update_fields=["downpayment_invoice", "status", "error_message", "updated_at"])
    return link


def create_sales_downpayment_invoice(order_accounting, actor=None):
    try:
        return _create_sales_downpayment_invoice_atomic(order_accounting, actor)
    except Exception as exc:
        SaleOrderInvoiceAccounting.objects.filter(pk=order_accounting.pk).update(
            status=SaleOrderInvoiceAccounting.Status.ACCOUNTING_ERROR,
            error_message=str(exc)[:2000],
        )
        raise


@transaction.atomic
def _create_sales_settlement_invoice_atomic(order_accounting, actor=None):
    link = SaleOrderInvoiceAccounting.objects.select_for_update().select_related(
        "order__customer", "downpayment_invoice"
    ).get(pk=order_accounting.pk)
    order = link.order
    if order.status != order.Status.CONFIRMED:
        raise ValidationError("Confirm the sales order before creating settlement.")
    if link.settlement_invoice_id:
        raise ValidationError("This sales order already has a settlement invoice.")
    order_lines = list(order.lines.select_related("product").filter(display_type=""))
    if not order_lines:
        raise ValidationError("Add at least one sales order line.")
    source_provenance = None
    downpayment_total = Decimal("0")
    if link.downpayment_invoice_id:
        if link.downpayment_invoice.status not in {Invoice.Status.POSTED, Invoice.Status.PAID}:
            raise ValidationError("Post the sales down-payment invoice before settlement.")
        source_provenance = SalesInvoiceAccounting.objects.get(
            invoice_id=link.downpayment_invoice_id,
            order_accounting=link,
            invoice_kind=SalesInvoiceAccounting.InvoiceKind.DOWNPAYMENT,
        )
        downpayment_total = link.downpayment_invoice.total
    gross = sum((row.subtotal for row in order_lines), Decimal("0"))
    if gross - downpayment_total <= 0:
        raise ValidationError("Sales settlement must have a positive balance after down payment.")
    invoice = Invoice.objects.create(
        company=order.company,
        number=f"SAL-{order.number}",
        invoice_type=Invoice.InvoiceType.CUSTOMER,
        partner=order.customer,
        invoice_date=order.order_date,
        due_date=order.order_date,
        notes=f"Sales settlement for {order.number}",
    )
    InvoiceLine.objects.bulk_create([
        InvoiceLine(
            invoice=invoice,
            product=row.product,
            description=row.description or row.product.name,
            quantity=row.quantity,
            unit_price=row.unit_price * (Decimal("1") - row.discount_percent / Decimal("100")),
        )
        for row in order_lines
    ])
    provenance = SalesInvoiceAccounting.objects.create(
        invoice=invoice,
        order_accounting=link,
        invoice_kind=SalesInvoiceAccounting.InvoiceKind.SETTLEMENT,
        source_downpayment_invoice=source_provenance,
    )
    if source_provenance:
        settlement_line = InvoiceLine.objects.create(
            invoice=invoice,
            description=f"Sales Down Payment Applied - {order.number}",
            quantity=Decimal("1"),
            unit_price=-downpayment_total,
        )
        source_line = source_provenance.line_provenance.first()
        if source_line is None:
            raise ValidationError("Sales down-payment invoice is missing line provenance.")
        SalesInvoiceLineProvenance.objects.create(
            invoice_line=settlement_line,
            sales_invoice=provenance,
            source_downpayment_line=source_line,
        )
    link.settlement_invoice = invoice
    link.status = SaleOrderInvoiceAccounting.Status.SETTLEMENT_DRAFT
    link.error_message = ""
    link.save(update_fields=["settlement_invoice", "status", "error_message", "updated_at"])
    return link


def create_sales_settlement_invoice(order_accounting, actor=None):
    try:
        return _create_sales_settlement_invoice_atomic(order_accounting, actor)
    except Exception as exc:
        SaleOrderInvoiceAccounting.objects.filter(pk=order_accounting.pk).update(
            status=SaleOrderInvoiceAccounting.Status.ACCOUNTING_ERROR,
            error_message=str(exc)[:2000],
        )
        raise


def retry_sales_invoice_accounting(order_accounting, actor=None):
    if order_accounting.status != SaleOrderInvoiceAccounting.Status.ACCOUNTING_ERROR:
        raise ValidationError("Only failed sales invoice accounting can be retried.")
    order_accounting.status = (
        SaleOrderInvoiceAccounting.Status.SETTLEMENT_DRAFT
        if order_accounting.settlement_invoice_id
        else SaleOrderInvoiceAccounting.Status.DOWNPAYMENT_DRAFT
        if order_accounting.downpayment_invoice_id
        else SaleOrderInvoiceAccounting.Status.READY
    )
    order_accounting.error_message = ""
    order_accounting.save(update_fields=["status", "error_message", "updated_at"])
    return order_accounting


def _sales_invoice_posted(*, invoice, entry, actor=None):
    order_accounting_id = SalesInvoiceAccounting.objects.filter(
        invoice_id=invoice.pk,
        invoice_kind=SalesInvoiceAccounting.InvoiceKind.SETTLEMENT,
    ).values_list("order_accounting_id", flat=True).first()
    if order_accounting_id:
        SaleOrderInvoiceAccounting.objects.filter(pk=order_accounting_id).update(
            status=SaleOrderInvoiceAccounting.Status.COMPLETED,
            error_message="",
        )


def _sales_invoice_failure(*, invoice, error):
    SaleOrderInvoiceAccounting.objects.filter(invoice_provenance__invoice_id=invoice.pk).update(
        status=SaleOrderInvoiceAccounting.Status.ACCOUNTING_ERROR,
        error_message=str(error)[:2000],
    )


def _sales_invoice_cancelled(*, invoice, actor=None):
    provenance = SalesInvoiceAccounting.objects.select_related("order_accounting").filter(invoice_id=invoice.pk).first()
    if provenance is None:
        return
    link = provenance.order_accounting
    if provenance.invoice_kind == SalesInvoiceAccounting.InvoiceKind.SETTLEMENT:
        link.settlement_invoice = None
        link.status = (
            SaleOrderInvoiceAccounting.Status.DOWNPAYMENT_DRAFT
            if link.downpayment_invoice_id
            else SaleOrderInvoiceAccounting.Status.READY
        )
        fields = ["settlement_invoice", "status", "updated_at"]
    else:
        link.downpayment_invoice = None
        link.status = SaleOrderInvoiceAccounting.Status.READY
        fields = ["downpayment_invoice", "status", "updated_at"]
    link.save(update_fields=fields)


def register_sale_invoice_accounting_extension():
    register_invoice_posting_contributor(_sales_invoice_posting)
    register_invoice_post_observer(_sales_invoice_posted)
    register_invoice_posting_failure_observer(_sales_invoice_failure)
    register_invoice_cancellation_observer(_sales_invoice_cancelled)
