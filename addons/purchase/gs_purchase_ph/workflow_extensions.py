"""Document registry contributions owned by GS Purchase Philippines."""

from core.document_registry import register_document_option

from .localized_reports import prepare_purchase_pdf_context


def register_purchase_ph_extensions():
    register_document_option(
        "purchases",
        "pdf_context_builder",
        prepare_purchase_pdf_context,
    )
    register_document_option(
        "purchase-quotations",
        "pdf_context_builder",
        prepare_purchase_pdf_context,
    )
