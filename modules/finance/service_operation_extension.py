"""Accounting adapter for approved service work orders."""

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Invoice, InvoiceLine

from .models import ServiceWorkOrderAccounting
from .invoice_posting import (
    register_invoice_cancellation_observer,
    register_invoice_post_observer,
)


@transaction.atomic
def _create_service_work_order_vendor_bill_atomic(accounting_link, actor=None):
    link = (
        ServiceWorkOrderAccounting.objects.select_for_update(of=("self",))
        .select_related("work_order__vendor")
        .get(pk=accounting_link.pk)
    )
    order = link.work_order
    if order.status != "approved":
        raise ValidationError("Approve the service work order before creating its vendor bill.")
    if link.vendor_bill_id:
        raise ValidationError("This service work order already has a vendor bill.")
    rows = list(order.lines.all())
    if not rows:
        raise ValidationError("Add at least one billable service work order line.")
    total = sum((row.subtotal for row in rows), 0)
    if total <= 0:
        raise ValidationError("The service work order vendor bill total must be positive.")
    invoice = Invoice.objects.create(
        company=order.company,
        number=f"SWO-{order.number}",
        invoice_type=Invoice.InvoiceType.VENDOR,
        partner=order.vendor,
        invoice_date=order.date_end or order.date_start,
        due_date=order.date_end or order.date_start,
        notes=order.notes or order.service_description,
    )
    InvoiceLine.objects.bulk_create([
        InvoiceLine(
            invoice=invoice,
            description=row.description,
            quantity=row.quantity,
            unit_price=row.unit_rate * (1 + row.tax_percent / 100),
        )
        for row in rows
    ])
    link.vendor_bill = invoice
    link.status = ServiceWorkOrderAccounting.Status.BILL_DRAFT
    link.error_message = ""
    link.save(update_fields=["vendor_bill", "status", "error_message", "updated_at"])
    return link


def create_service_work_order_vendor_bill(accounting_link, actor=None):
    try:
        return _create_service_work_order_vendor_bill_atomic(accounting_link, actor)
    except Exception as exc:
        ServiceWorkOrderAccounting.objects.filter(pk=accounting_link.pk).update(
            status=ServiceWorkOrderAccounting.Status.ACCOUNTING_ERROR,
            error_message=str(exc)[:2000],
        )
        raise


def retry_service_work_order_accounting(accounting_link, actor=None):
    if accounting_link.status != ServiceWorkOrderAccounting.Status.ACCOUNTING_ERROR:
        raise ValidationError("Only a failed service work order accounting link can be retried.")
    accounting_link.status = (
        ServiceWorkOrderAccounting.Status.BILL_DRAFT
        if accounting_link.vendor_bill_id
        else ServiceWorkOrderAccounting.Status.READY
    )
    accounting_link.error_message = ""
    accounting_link.save(update_fields=["status", "error_message", "updated_at"])
    return accounting_link


def _service_bill_posted(*, invoice, entry, actor=None):
    ServiceWorkOrderAccounting.objects.filter(vendor_bill_id=invoice.pk).update(
        status=ServiceWorkOrderAccounting.Status.BILL_POSTED,
        error_message="",
    )


def _service_bill_cancelled(*, invoice, actor=None):
    ServiceWorkOrderAccounting.objects.filter(vendor_bill_id=invoice.pk).update(
        vendor_bill=None,
        status=ServiceWorkOrderAccounting.Status.READY,
        error_message="",
    )


def register_service_operation_accounting_extension():
    register_invoice_post_observer(_service_bill_posted)
    register_invoice_cancellation_observer(_service_bill_cancelled)
