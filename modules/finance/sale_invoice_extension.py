"""Master-sale down-payment and promotion accounting extension."""

from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.db.models.signals import post_save
from django.utils import timezone

from core.models import Invoice, InvoiceLine
from core.sales import MAX_SALES_ORDER_LINES
from modules.apphub.workflow_hooks import register_before_workflow_hook

from .invoice_posting import (
    InvoicePostingLine,
    InvoicePostingOverride,
    register_invoice_cancellation_observer,
    register_invoice_post_observer,
    register_invoice_posting_contributor,
    register_invoice_posting_failure_observer,
)
from .models import (
    Account,
    AccountTax,
    CommercialAccountingSettings,
    ProductAccountMapping,
    SaleOrderInvoiceAccounting,
    SaleOrderLocalization,
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
    ).filter(company_id=invoice.company_id).first()
    if commercial is None:
        raise ValidationError(
            "Configure Sales & Purchase Accounting settings before posting a Sales Order invoice."
        )
    row_ids = [row.pk for row in rows]
    line_sources = {
        item.invoice_line_id: item
        for item in SalesInvoiceLineProvenance.objects.filter(
            sales_invoice=provenance, invoice_line_id__in=row_ids
        ).select_related(
            "sale_order_line",
            "source_downpayment_line__sales_invoice__invoice",
        )
    }
    split_groups = defaultdict(list)
    for split in SalesInvoicePromotionSplit.objects.filter(
        invoice_line_id__in=row_ids
    ).select_related("promotion", "expense_account_snapshot"):
        split_groups[split.invoice_line_id].append(split)
    lines = []
    invoice_total = total
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
            row.product_id
            for row in rows
            if line_sources.get(row.pk) and line_sources[row.pk].sale_order_line_id and row.product_id
        }
        mappings = {
            item.product_id: item
            for item in ProductAccountMapping.objects.filter(
                company_id=invoice.company_id, product_id__in=product_ids, active=True
            ).select_related("revenue_account")
        }
        regular_sources = {
            item.sale_order_line_id: item.sale_order_line
            for item in line_sources.values()
            if item.sale_order_line_id
        }
        standard_tax_rates = {
            source.tax_percent for source in regular_sources.values() if source.tax_percent > 0
        }
        localization = SaleOrderLocalization.objects.filter(order_id=order.pk).first()
        if localization is not None and standard_tax_rates:
            raise ValidationError(
                "Use either Sales line tax percentages or Localized Sales Tax selections, not both."
            )
        localized_by_source = defaultdict(list)
        if localization is not None:
            from .services import confirm_sale_order_localization

            localization = confirm_sale_order_localization(localization, actor)
            for tax_line in localization.tax_lines.filter(
                order_line_id__in=regular_sources
            ).select_related("tax_code__posting_account"):
                localized_by_source[tax_line.order_line_id].append(tax_line)

        standard_tax_accounts = {}
        if standard_tax_rates:
            candidates_by_rate = defaultdict(list)
            for tax in AccountTax.objects.filter(
                company_id=invoice.company_id,
                active=True,
                method=AccountTax.TaxMethod.PERCENT,
                price_included=False,
                tax_scope__in=[AccountTax.TaxScope.SALES, AccountTax.TaxScope.BOTH],
                rate__in=standard_tax_rates,
            ).select_related("account"):
                candidates_by_rate[tax.rate].append(tax)
            for rate in standard_tax_rates:
                candidates = candidates_by_rate.get(rate, ())
                if len(candidates) != 1:
                    raise ValidationError(
                        f"Configure exactly one active, price-excluded Sales tax at {rate}% for this company."
                    )
                standard_tax_accounts[rate] = candidates[0]

        tax_lines_by_account = defaultdict(Decimal)
        tax_descriptions = {}
        for row in rows:
            amount = row.subtotal
            source_meta = line_sources.get(row.pk)
            if source_meta and source_meta.source_downpayment_line_id:
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
            if source_meta is None or source_meta.sale_order_line_id is None:
                raise ValidationError("Every regular sales invoice line must carry Sales Order line provenance.")
            splits = split_groups.get(row.pk, ())
            promotion_discount = sum((split.discount_amount for split in splits), Decimal("0"))
            account = mappings.get(row.product_id).revenue_account if row.product_id in mappings else journal.default_credit_account
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
            source_line = source_meta.sale_order_line
            if source_line.tax_percent > 0:
                tax = standard_tax_accounts[source_line.tax_percent]
                tax_amount = (amount * source_line.tax_percent / Decimal("100")).quantize(Decimal("0.01"))
                tax_lines_by_account[tax.account_id] += tax_amount
                tax_descriptions[tax.account_id] = f"Tax {tax.code}: {order.number}"
            for localized_tax in localized_by_source.get(source_line.pk, ()):
                if localized_tax.tax_amount:
                    account_id = localized_tax.tax_code.posting_account_id
                    if account_id is None:
                        raise ValidationError(
                            f"Configure a posting account for tax code {localized_tax.tax_code.code}."
                        )
                    tax_lines_by_account[account_id] += localized_tax.tax_amount
                    tax_descriptions[account_id] = f"Tax {localized_tax.tax_code.code}: {order.number}"
        invoice_tax_total = sum(tax_lines_by_account.values(), Decimal("0"))
        invoice_total = total + invoice_tax_total
        standard_accounts_by_id = {
            tax.account_id: tax.account for tax in standard_tax_accounts.values()
        }
        lines.extend(
            InvoicePostingLine(
                account=standard_accounts_by_id[account_id],
                credit=amount,
                partner=invoice.partner,
                description=tax_descriptions[account_id],
            )
            for account_id, amount in tax_lines_by_account.items()
            if account_id in standard_accounts_by_id
        )
        standard_account_ids = set(standard_accounts_by_id)
        localized_account_ids = set(tax_lines_by_account) - standard_account_ids
        if localized_account_ids:
            localized_accounts = {
                account.pk: account
                for account in Account.objects.filter(
                    company_id=invoice.company_id,
                    pk__in=localized_account_ids,
                )
            }
            if len(localized_accounts) != len(localized_account_ids):
                raise ValidationError("One or more localized Sales tax accounts are invalid for this company.")
            lines.extend(
                InvoicePostingLine(
                    account=localized_accounts[account_id],
                    credit=tax_lines_by_account[account_id],
                    partner=invoice.partner,
                    description=tax_descriptions[account_id],
                )
                for account_id in localized_account_ids
            )
    lines.insert(0, InvoicePostingLine(
        account=partner_account,
        debit=invoice_total,
        partner=invoice.partner,
        description=invoice.number,
        due_date=invoice.due_date,
    ))
    return InvoicePostingOverride(
        journal=journal,
        lines=tuple(lines),
        description=f"{provenance.get_invoice_kind_display()}: {order.number}",
        total=invoice_total,
    )


@transaction.atomic
def _create_sales_downpayment_invoice_atomic(order_accounting, actor=None):
    link = SaleOrderInvoiceAccounting.objects.select_for_update().select_related("order__customer").get(
        pk=order_accounting.pk
    )
    order = link.order
    if order.status != order.Status.SALE:
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
    link = SaleOrderInvoiceAccounting.objects.select_for_update(of=("self",)).select_related(
        "order__customer", "downpayment_invoice"
    ).get(pk=order_accounting.pk)
    order = link.order
    if order.status != order.Status.SALE:
        raise ValidationError("Confirm the sales order before creating settlement.")
    if link.settlement_invoice_id:
        raise ValidationError("This sales order already has a settlement invoice.")
    order_lines = list(
        order.lines.select_related("product")
        .filter(display_type="")
        .order_by("pk")[: MAX_SALES_ORDER_LINES + 1]
    )
    if not order_lines:
        raise ValidationError("Add at least one sales order line.")
    if len(order_lines) > MAX_SALES_ORDER_LINES:
        raise ValidationError(f"A Sales Order supports at most {MAX_SALES_ORDER_LINES} detail lines.")
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
    global_factor = Decimal("1") - order.global_discount_percent / Decimal("100")
    invoiceable_rows = []
    for row in order_lines:
        invoice_quantity = (
            row.delivered_quantity
            if order.invoicing_policy == order.InvoicingPolicy.DELIVERED
            and row.product.product_type != row.product.ProductType.SERVICE
            else row.quantity
        )
        if invoice_quantity <= 0:
            continue
        invoiceable_rows.append((row, invoice_quantity))
    if not invoiceable_rows:
        raise ValidationError("There are no quantities available to invoice under the selected invoicing policy.")
    gross = sum(
        (
            quantity
            * row.unit_price
            * (Decimal("1") - row.discount_percent / Decimal("100"))
            * global_factor
        )
        for row, quantity in invoiceable_rows
    ).quantize(Decimal("0.01"))
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
    invoice_lines = InvoiceLine.objects.bulk_create([
        InvoiceLine(
            invoice=invoice,
            product=row.product,
            description=row.description or row.product.name,
            quantity=quantity,
            unit_price=(
                row.unit_price
                * (Decimal("1") - row.discount_percent / Decimal("100"))
                * global_factor
            ),
        )
        for row, quantity in invoiceable_rows
    ])
    provenance = SalesInvoiceAccounting.objects.create(
        invoice=invoice,
        order_accounting=link,
        invoice_kind=SalesInvoiceAccounting.InvoiceKind.SETTLEMENT,
        source_downpayment_invoice=source_provenance,
    )
    SalesInvoiceLineProvenance.objects.bulk_create([
        SalesInvoiceLineProvenance(
            invoice_line=invoice_line,
            sales_invoice=provenance,
            sale_order_line=order_line,
        )
        for invoice_line, (order_line, _quantity) in zip(invoice_lines, invoiceable_rows)
    ])
    now = timezone.now()
    for order_line, quantity in invoiceable_rows:
        order_line.invoiced_quantity = quantity
        order_line.updated_at = now
    order.lines.model.objects.bulk_update(
        [row for row, _quantity in invoiceable_rows],
        ["invoiced_quantity", "updated_at"],
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
    invoice.total = gross - downpayment_total
    invoice.save(update_fields=["total", "updated_at"])
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


@transaction.atomic
def create_sales_order_invoice(order, actor=None):
    """Create the final customer invoice as a draft, matching the HMX Sales flow."""
    locked_order = order.__class__.objects.select_for_update().get(pk=order.pk)
    if locked_order.status != locked_order.Status.SALE:
        raise ValidationError("Only a document at Sale Order status can be invoiced.")
    link, _created = SaleOrderInvoiceAccounting.objects.get_or_create(order=locked_order)
    if not link.settlement_invoice_id:
        create_sales_settlement_invoice(link, actor)
    return locked_order


@transaction.atomic
def create_and_post_sales_order_invoice(order, actor=None):
    """Create and post in one service call for integrations; the UI uses the HMX two-step flow."""
    locked_order = create_sales_order_invoice(order, actor)
    link = SaleOrderInvoiceAccounting.objects.select_for_update().get(order=locked_order)
    invoice = Invoice.objects.select_for_update().get(pk=link.settlement_invoice_id)
    if invoice.status == Invoice.Status.DRAFT:
        from .services import post_invoice

        invoice = post_invoice(invoice, actor)
    if invoice.status not in {Invoice.Status.POSTED, Invoice.Status.PAID}:
        raise ValidationError("The Sales invoice did not reach a posted accounting status.")
    return locked_order


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
    order_accounting = SalesInvoiceAccounting.objects.filter(
        invoice_id=invoice.pk,
        invoice_kind=SalesInvoiceAccounting.InvoiceKind.SETTLEMENT,
    ).values("order_accounting_id", "order_accounting__order_id").first()
    if order_accounting:
        SaleOrderInvoiceAccounting.objects.filter(pk=order_accounting["order_accounting_id"]).update(
            status=SaleOrderInvoiceAccounting.Status.COMPLETED,
            error_message="",
        )


@transaction.atomic
def auto_close_sales_order_for_invoice(invoice_id):
    """Match HMX auto-close once payment, billing, and delivery are complete."""
    provenance = (
        SalesInvoiceAccounting.objects.select_related("order_accounting")
        .filter(invoice_id=invoice_id)
        .first()
    )
    if provenance is None:
        return None

    link = (
        SaleOrderInvoiceAccounting.objects.select_for_update(of=("self",))
        .select_related("downpayment_invoice", "settlement_invoice")
        .get(pk=provenance.order_accounting_id)
    )
    order = (
        link.order.__class__.objects.select_for_update()
        .get(pk=link.order_id)
    )
    if order.status != order.Status.SALE or not link.settlement_invoice_id:
        return None

    active_invoices = [
        invoice
        for invoice in (link.downpayment_invoice, link.settlement_invoice)
        if invoice is not None and invoice.status != Invoice.Status.CANCELLED
    ]
    if not active_invoices or any(
        invoice.status not in {Invoice.Status.POSTED, Invoice.Status.PAID}
        or invoice.total <= 0
        or invoice.paid_amount < invoice.total
        for invoice in active_invoices
    ):
        return None

    order_lines = list(
        order.lines.select_related("product")
        .filter(display_type="")
        .order_by("pk")[: MAX_SALES_ORDER_LINES + 1]
    )
    if not order_lines or len(order_lines) > MAX_SALES_ORDER_LINES:
        return None

    invoiced_by_line = {
        row["sale_order_line_id"]: row["quantity"] or Decimal("0")
        for row in SalesInvoiceLineProvenance.objects.filter(
            sales_invoice__order_accounting_id=link.pk,
            sales_invoice__invoice_id__in=[invoice.pk for invoice in active_invoices],
            sale_order_line_id__isnull=False,
        )
        .values("sale_order_line_id")
        .annotate(quantity=Sum("invoice_line__quantity"))
    }

    now = timezone.now()
    for line in order_lines:
        invoiced_quantity = invoiced_by_line.get(line.pk, Decimal("0"))
        line.invoiced_quantity = invoiced_quantity
        line.updated_at = now
        is_service = line.product.product_type == line.product.ProductType.SERVICE
        if not is_service and line.delivered_quantity < line.quantity:
            return None
        required_quantity = line.quantity if is_service else line.delivered_quantity
        if invoiced_quantity < required_quantity:
            return None

    order.lines.model.objects.bulk_update(
        order_lines,
        ["invoiced_quantity", "updated_at"],
        batch_size=MAX_SALES_ORDER_LINES,
    )
    order.status = order.Status.CLOSED
    order.close_reason = ""
    order.close_note = ""
    order.save(update_fields=["status", "close_reason", "close_note", "updated_at"])
    return order


def _auto_close_sales_order_on_invoice_save(sender, instance, raw=False, **kwargs):
    if not raw:
        auto_close_sales_order_for_invoice(instance.pk)


def _auto_close_sales_order_on_invoice_line_save(sender, instance, raw=False, **kwargs):
    if not raw:
        auto_close_sales_order_for_invoice(instance.invoice_id)


def _validate_sales_order_invoice_workflow(*, instance, action, actor=None):
    link = (
        SaleOrderInvoiceAccounting.objects.select_related(
            "downpayment_invoice",
            "settlement_invoice",
        )
        .filter(order_id=instance.pk)
        .first()
    )
    if link is None:
        return
    invoices = [
        invoice
        for invoice in (link.downpayment_invoice, link.settlement_invoice)
        if invoice is not None and invoice.status != Invoice.Status.CANCELLED
    ]
    if action == "close" and any(invoice.status == Invoice.Status.DRAFT for invoice in invoices):
        raise ValidationError("Post or cancel every draft Sales invoice before closing this Sales Order.")
    if action == "cancel" and invoices:
        raise ValidationError("Cancel or reverse the linked Sales invoices before cancelling this Sales Order.")


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
    register_before_workflow_hook("core.saleorder", "close", _validate_sales_order_invoice_workflow)
    register_before_workflow_hook("core.saleorder", "cancel", _validate_sales_order_invoice_workflow)
    post_save.connect(
        _auto_close_sales_order_on_invoice_save,
        sender=Invoice,
        dispatch_uid="gsshop_finance.auto_close_sales_order_on_invoice_save",
    )
    post_save.connect(
        _auto_close_sales_order_on_invoice_line_save,
        sender=InvoiceLine,
        dispatch_uid="gsshop_finance.auto_close_sales_order_on_invoice_line_save",
    )
