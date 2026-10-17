"""Invoice lifecycle integration for registered outbound accounting connectors."""

from .invoice_posting import (
    register_invoice_cancellation_observer,
    register_invoice_post_observer,
)
from .models import ExternalAccountingConnector, ExternalInvoiceSync


def _enqueue_external_invoice_syncs(*, invoice, entry, actor=None):
    connectors = ExternalAccountingConnector.objects.filter(
        company_id=invoice.company_id,
        active=True,
    ).only("id")
    existing_connector_ids = set(
        ExternalInvoiceSync.objects.filter(
            invoice_id=invoice.pk,
            connector_id__in=connectors.values("id"),
        ).values_list("connector_id", flat=True)
    )
    ExternalInvoiceSync.objects.bulk_create([
        ExternalInvoiceSync(
            connector_id=connector.pk,
            invoice=invoice,
            requested_by=actor,
        )
        for connector in connectors
        if connector.pk not in existing_connector_ids
    ])


def _cancel_external_invoice_syncs(*, invoice, actor=None):
    ExternalInvoiceSync.objects.filter(invoice_id=invoice.pk).exclude(
        status=ExternalInvoiceSync.Status.CANCELLED
    ).update(status=ExternalInvoiceSync.Status.CANCELLED, error_message="")


def register_external_invoice_extension():
    register_invoice_post_observer(_enqueue_external_invoice_syncs)
    register_invoice_cancellation_observer(_cancel_external_invoice_syncs)
