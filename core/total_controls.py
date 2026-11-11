"""Generic controls for batching document-total recalculation signals."""

from contextlib import contextmanager
from contextvars import ContextVar


_suppressed_document_totals = ContextVar(
    "suppressed_document_totals",
    default=frozenset(),
)


def document_total_recalculation_suppressed(document_key):
    return document_key in _suppressed_document_totals.get()


@contextmanager
def suppress_document_total_recalculation(document_key):
    current = _suppressed_document_totals.get()
    token = _suppressed_document_totals.set(current | {document_key})
    try:
        yield
    finally:
        _suppressed_document_totals.reset(token)
