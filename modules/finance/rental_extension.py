"""Rental-owned accounting provisioning and workflow integration."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Invoice, InvoiceLine
from modules.apphub.workflow_hooks import register_before_workflow_hook

from .invoice_posting import (
    InvoicePostingLine,
    InvoicePostingOverride,
    register_invoice_posting_contributor,
)
from .models import (
    Account,
    ProductAccountMapping,
    RentalAccountingSettings,
    RentalInvoiceAccounting,
    RentalInvoiceLineAccounting,
    RentalOrderAccounting,
)
from .provisioning import register_company_accounting_provisioner
from .services import hold_rental_deposit, release_rental_deposit


def _rental_order_accounting(*, instance, action, actor):
    if action == "confirm":
        hold_rental_deposit(instance, actor)
    elif action == "cancel":
        release_rental_deposit(instance, actor)


def _rental_invoice_posting(*, invoice, rows, partner_account, total, journal, actor=None):
    provenance = (
        RentalInvoiceAccounting.objects.select_for_update(of=("self",))
        .select_related("rental_terms__order", "source_downpayment_invoice__invoice")
        .filter(invoice_id=invoice.pk)
        .first()
    )
    if provenance is None:
        return None
    order = provenance.rental_terms.order
    if invoice.company_id != order.company_id or invoice.partner_id != order.customer_id:
        raise ValidationError("Rental invoice provenance does not match its rental order.")
    settings = RentalAccountingSettings.objects.select_related("rental_downpayment_account").get(
        company_id=invoice.company_id
    )
    row_ids = [row.pk for row in rows]
    line_provenance = {
        item.invoice_line_id: item
        for item in RentalInvoiceLineAccounting.objects.filter(
            rental_invoice=provenance,
            invoice_line_id__in=row_ids,
        ).select_related("source_downpayment_line__rental_invoice__invoice")
    }
    lines = [InvoicePostingLine(
        account=partner_account,
        debit=total,
        partner=invoice.partner,
        description=invoice.number,
        due_date=invoice.due_date,
    )]

    if provenance.invoice_kind == RentalInvoiceAccounting.InvoiceKind.DOWNPAYMENT:
        if len(line_provenance) != len(rows) or any(row.subtotal <= 0 for row in rows):
            raise ValidationError("Every rental down-payment line must carry valid rental provenance.")
        lines.append(InvoicePostingLine(
            account=settings.rental_downpayment_account,
            credit=total,
            partner=invoice.partner,
            description=f"Rental Down Payment: {order.number}",
        ))
    else:
        product_ids = {
            row.product_id for row in rows
            if row.pk not in line_provenance and row.product_id
        }
        mappings = {
            mapping.product_id: mapping
            for mapping in ProductAccountMapping.objects.filter(
                company_id=invoice.company_id,
                product_id__in=product_ids,
                active=True,
            ).select_related("revenue_account")
        }
        for row in rows:
            amount = row.subtotal
            metadata = line_provenance.get(row.pk)
            if metadata:
                source = metadata.source_downpayment_line
                if (
                    amount >= 0
                    or source is None
                    or source.rental_invoice.rental_terms_id != provenance.rental_terms_id
                    or source.rental_invoice.invoice.status not in {Invoice.Status.POSTED, Invoice.Status.PAID}
                ):
                    raise ValidationError("Rental settlement lines require posted same-order down-payment provenance.")
                lines.append(InvoicePostingLine(
                    account=settings.rental_downpayment_account,
                    debit=abs(amount),
                    partner=invoice.partner,
                    description=row.description,
                ))
                continue
            account = mappings.get(row.product_id).revenue_account if row.product_id in mappings else journal.default_credit_account
            if account is None or amount <= 0:
                raise ValidationError("Configure a revenue account for every regular rental settlement line.")
            lines.append(InvoicePostingLine(
                account=account,
                credit=amount,
                partner=invoice.partner,
                description=row.description,
            ))

    return InvoicePostingOverride(
        journal=journal,
        lines=tuple(lines),
        description=f"Rental {provenance.get_invoice_kind_display()}: {order.number}",
    )


@transaction.atomic
def create_rental_downpayment_invoice(rental_terms, actor=None):
    terms = RentalOrderAccounting.objects.select_for_update().select_related("order__customer").get(
        pk=rental_terms.pk
    )
    order = terms.order
    if order.status not in {order.Status.CONFIRMED, order.Status.COMPLETED}:
        raise ValidationError("Confirm the rental order before creating a down-payment invoice.")
    if terms.downpayment_invoice_id:
        raise ValidationError("This rental order already has a down-payment invoice.")
    if terms.downpayment_amount <= 0:
        raise ValidationError("Set a positive rental down-payment amount first.")
    invoice = Invoice.objects.create(
        company=order.company,
        number=f"RDP-{order.number}",
        invoice_type=Invoice.InvoiceType.CUSTOMER,
        partner=order.customer,
        invoice_date=order.order_date,
        due_date=order.order_date,
        notes=f"Rental down payment for {order.number}",
    )
    line = InvoiceLine.objects.create(
        invoice=invoice,
        description=f"Rental Down Payment - {order.number}",
        quantity=Decimal("1"),
        unit_price=terms.downpayment_amount,
    )
    provenance = RentalInvoiceAccounting.objects.create(
        invoice=invoice,
        rental_terms=terms,
        invoice_kind=RentalInvoiceAccounting.InvoiceKind.DOWNPAYMENT,
    )
    RentalInvoiceLineAccounting.objects.create(invoice_line=line, rental_invoice=provenance)
    terms.downpayment_invoice = invoice
    terms.save(update_fields=["downpayment_invoice", "updated_at"])
    return terms


@transaction.atomic
def create_rental_settlement_invoice(rental_terms, actor=None):
    terms = RentalOrderAccounting.objects.select_for_update().select_related(
        "order__customer", "downpayment_invoice"
    ).get(pk=rental_terms.pk)
    order = terms.order
    if order.status not in {order.Status.CONFIRMED, order.Status.COMPLETED}:
        raise ValidationError("Confirm the rental order before creating its settlement invoice.")
    if terms.settlement_invoice_id:
        raise ValidationError("This rental order already has a settlement invoice.")
    order_lines = list(order.lines.select_related("product").filter(display_type=""))
    if not order_lines:
        raise ValidationError("Add at least one billable rental order line.")
    source_provenance = None
    downpayment_total = Decimal("0")
    if terms.downpayment_invoice_id:
        if terms.downpayment_invoice.status not in {Invoice.Status.POSTED, Invoice.Status.PAID}:
            raise ValidationError("Post the rental down-payment invoice before creating settlement.")
        source_provenance = RentalInvoiceAccounting.objects.select_related("invoice").get(
            invoice_id=terms.downpayment_invoice_id,
            rental_terms=terms,
            invoice_kind=RentalInvoiceAccounting.InvoiceKind.DOWNPAYMENT,
        )
        downpayment_total = terms.downpayment_invoice.total
    gross = sum((line.subtotal for line in order_lines), Decimal("0"))
    if gross - downpayment_total <= 0:
        raise ValidationError("Rental settlement must have a positive balance after down payment.")
    invoice = Invoice.objects.create(
        company=order.company,
        number=f"RNT-{order.number}",
        invoice_type=Invoice.InvoiceType.CUSTOMER,
        partner=order.customer,
        invoice_date=order.order_date,
        due_date=order.order_date,
        notes=f"Rental settlement for {order.number}",
    )
    InvoiceLine.objects.bulk_create([
        InvoiceLine(
            invoice=invoice,
            product=line.product,
            description=line.description or line.product.name,
            quantity=line.quantity,
            unit_price=line.unit_price * (Decimal("1") - line.discount_percent / Decimal("100")),
        )
        for line in order_lines
    ])
    provenance = RentalInvoiceAccounting.objects.create(
        invoice=invoice,
        rental_terms=terms,
        invoice_kind=RentalInvoiceAccounting.InvoiceKind.SETTLEMENT,
        source_downpayment_invoice=source_provenance,
    )
    if source_provenance:
        settlement_line = InvoiceLine.objects.create(
            invoice=invoice,
            description=f"Rental Down Payment Applied - {order.number}",
            quantity=Decimal("1"),
            unit_price=-downpayment_total,
        )
        source_line = source_provenance.line_accounting_records.select_related("invoice_line").first()
        if source_line is None:
            raise ValidationError("The source rental down-payment invoice is missing line provenance.")
        RentalInvoiceLineAccounting.objects.create(
            invoice_line=settlement_line,
            rental_invoice=provenance,
            source_downpayment_line=source_line,
        )
    terms.settlement_invoice = invoice
    terms.save(update_fields=["settlement_invoice", "updated_at"])
    return terms


def provision_rental_accounting(*, company, accounts, journals, actor=None):
    parent, parent_created = Account.objects.get_or_create(
        company=company,
        code="2000",
        defaults={"name": "Current Liabilities", "account_type": Account.AccountType.LIABILITY},
    )
    if parent.account_type != Account.AccountType.LIABILITY:
        raise ValidationError("Account code 2000 is already used by a non-liability account.")
    downpayment = Account.objects.filter(company=company, code="2006").first()
    downpayment_created = downpayment is None
    if downpayment is None:
        downpayment = Account.objects.create(
            company=company,
            code="2006",
            name="Rental Down Payment",
            account_type=Account.AccountType.LIABILITY,
            parent=parent,
        )
    elif (
        downpayment.name != "Rental Down Payment"
        or downpayment.account_type != Account.AccountType.LIABILITY
        or downpayment.parent_id != parent.pk
    ):
        raise ValidationError("Account code 2006 is already used by another account configuration.")
    _settings, settings_created = RentalAccountingSettings.objects.get_or_create(
        company=company, defaults={"rental_downpayment_account": downpayment}
    )
    return {
        "extension": "rental_accounting",
        "accounts_created": int(parent_created) + int(downpayment_created),
        "settings_created": settings_created,
    }


def register_rental_accounting_extension():
    register_before_workflow_hook("core.saleorder", "confirm", _rental_order_accounting)
    register_before_workflow_hook("core.saleorder", "cancel", _rental_order_accounting)
    register_invoice_posting_contributor(_rental_invoice_posting)
    register_company_accounting_provisioner(provision_rental_accounting)
