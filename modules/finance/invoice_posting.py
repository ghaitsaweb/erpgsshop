"""Extension points for module-owned invoice accounting behavior.

Contributors may replace the standard revenue/expense journal lines for a
specific invoice.  The registry is intentionally bounded by installed
modules, so adding an extension does not add work proportional to invoice
line or tenant volume.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class InvoicePostingLine:
    account: object
    debit: object = 0
    credit: object = 0
    partner: object | None = None
    description: str = ""
    due_date: object | None = None


@dataclass(frozen=True)
class InvoicePostingOverride:
    lines: tuple[InvoicePostingLine, ...]
    journal: object | None = None
    description: str = ""
    after_post: Callable | None = None


_CONTRIBUTORS = []
_FAILURE_OBSERVERS = []
_CANCELLATION_OBSERVERS = []
_POST_OBSERVERS = []


def register_invoice_posting_contributor(callback):
    if callback not in _CONTRIBUTORS:
        _CONTRIBUTORS.append(callback)


def register_invoice_posting_failure_observer(callback):
    if callback not in _FAILURE_OBSERVERS:
        _FAILURE_OBSERVERS.append(callback)


def register_invoice_cancellation_observer(callback):
    if callback not in _CANCELLATION_OBSERVERS:
        _CANCELLATION_OBSERVERS.append(callback)


def register_invoice_post_observer(callback):
    if callback not in _POST_OBSERVERS:
        _POST_OBSERVERS.append(callback)


def resolve_invoice_posting_override(**context):
    for callback in _CONTRIBUTORS:
        override = callback(**context)
        if override is not None:
            return override
    return None


def notify_invoice_posting_failure(*, invoice, error):
    for callback in _FAILURE_OBSERVERS:
        callback(invoice=invoice, error=error)


def notify_invoice_cancelled(*, invoice, actor=None):
    for callback in _CANCELLATION_OBSERVERS:
        callback(invoice=invoice, actor=actor)


def notify_invoice_posted(*, invoice, entry, actor=None):
    for callback in _POST_OBSERVERS:
        callback(invoice=invoice, entry=entry, actor=actor)
