"""Bounded Sales Order detail persistence and total recalculation controls."""

from contextlib import contextmanager
from contextvars import ContextVar
from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal
import re
import uuid

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import F, Max, Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.html import strip_tags

from modules.apphub.workflow_hooks import run_before_workflow_hooks
from modules.approval_workflow.models import ApprovalInstance
from modules.approval_workflow.services import (
    can_user_act,
    perform_approval_action,
    submit_for_approval,
)

from .models import (
    Company,
    CompanySaleSetting,
    SaleOrder,
    SaleOrderLine,
    SaleOrderType,
    SaleOrderTypeSequence,
    SaleQuotationEmail,
    recalculate_sale_order_totals,
)


MAX_SALES_ORDER_LINES = 500
MAX_EXPIRED_SALES_PER_RUN = 500
MAX_SALES_MERGE_ORDERS = 25
DEFAULT_SALES_QUOTATION_VALIDITY_DAYS = 7
MAX_SALES_EMAIL_RECIPIENTS = 50
_suppress_sale_total_recalculation = ContextVar(
    "suppress_sale_total_recalculation",
    default=False,
)


def sale_total_recalculation_suppressed():
    return _suppress_sale_total_recalculation.get()


@contextmanager
def suppress_sale_total_recalculation():
    token = _suppress_sale_total_recalculation.set(True)
    try:
        yield
    finally:
        _suppress_sale_total_recalculation.reset(token)


def save_sale_order_lines(order, formset):
    """Persist one bounded formset with batched writes and one totals query."""
    creates, updates, delete_ids = [], [], []
    now = timezone.now()
    update_fields = [
        "product",
        "description",
        "quantity",
        "uom",
        "delivery_address",
        "delivery_date",
        "unit_price",
        "discount_percent",
        "tax_percent",
        "rental_start_date",
        "rental_return_date",
        "rental_status",
        "updated_at",
    ]
    for form in formset.forms:
        data = form.cleaned_data
        if not data:
            continue
        if data.get("DELETE"):
            if form.instance.pk:
                delete_ids.append(form.instance.pk)
            continue
        line = form.save(commit=False)
        line.order = order
        if not line.uom and line.product_id:
            line.uom = line.product.unit
        line.updated_at = now
        if line.pk:
            updates.append(line)
        else:
            line.created_at = now
            creates.append(line)

    touched_ids = {line.pk for line in updates} | set(delete_ids)
    if touched_ids:
        owned_ids = set(
            SaleOrderLine.objects.filter(order_id=order.pk, pk__in=touched_ids)
            .order_by()
            .values_list("pk", flat=True)
        )
        if owned_ids != touched_ids:
            raise ValidationError("Sales line does not belong to this order.")

    final_count = order.lines.count() - len(delete_ids) + len(creates)
    if final_count > MAX_SALES_ORDER_LINES:
        raise ValidationError(
            f"A Sales Order supports at most {MAX_SALES_ORDER_LINES} detail lines."
        )

    with suppress_sale_total_recalculation():
        if delete_ids:
            SaleOrderLine.objects.filter(order_id=order.pk, pk__in=delete_ids).delete()
        if updates:
            SaleOrderLine.objects.bulk_update(updates, update_fields)
        if creates:
            SaleOrderLine.objects.bulk_create(creates)
    recalculate_sale_order_totals(order)


def _validate_sale_order_ready(order):
    if not order.lines.filter(display_type="").exists():
        raise ValidationError("At least one Order Line is required before continuing.")
    if order.total <= Decimal("0"):
        raise ValidationError("Grand Total must be greater than zero.")


def get_sale_quotation_validity_days(company_id):
    days = (
        CompanySaleSetting.objects.filter(company_id=company_id)
        .values_list("quotation_expiry_days", flat=True)
        .first()
    )
    return days or DEFAULT_SALES_QUOTATION_VALIDITY_DAYS


def get_default_sale_order_type(company_id):
    return (
        SaleOrderType.objects.filter(origin=SaleOrderType.Origin.SALES)
        .filter(Q(companies__isnull=True) | Q(companies__pk=company_id))
        .order_by("name", "pk")
        .distinct()
        .first()
    )


def allocate_sale_document_number(order_type_id, company_id, *, document_kind):
    if document_kind not in {"quotation", "order"}:
        raise ValidationError("Unsupported Sales document sequence kind.")
    company = Company.objects.only("pk", "code").get(pk=company_id)
    period = timezone.localdate().strftime("%Y-%m")
    prefix = "SQ" if document_kind == "quotation" else "SO"
    period_field = f"{document_kind}_period"
    next_field = f"{document_kind}_next"

    with transaction.atomic():
        order_type = SaleOrderType.objects.select_for_update().get(pk=order_type_id)
        sequence = (
            SaleOrderTypeSequence.objects.order_by("pk").select_for_update(of=("self",))
            .filter(order_type=order_type, company_id=company_id)
            .first()
        )
        if sequence is None:
            sequence = (
                SaleOrderTypeSequence.objects.order_by("pk").select_for_update(of=("self",))
                .filter(order_type=order_type, company__isnull=True)
                .first()
            )
        if sequence is None:
            is_company_scoped = order_type.companies.filter(pk=company_id).exists()
            sequence = SaleOrderTypeSequence.objects.create(
                order_type=order_type,
                company_id=company_id if is_company_scoped else None,
            )

        if getattr(sequence, period_field) != period:
            setattr(sequence, period_field, period)
            setattr(sequence, next_field, 1)
        number = getattr(sequence, next_field)
        setattr(sequence, next_field, number + 1)
        sequence.save(update_fields=[period_field, next_field, "updated_at"])

    return (
        f"{prefix}/{order_type.prefix_sequence}/{company.code}/"
        f"{timezone.localdate():%Y/%m}/{number:04d}"
    )[:40]


def get_sale_default_delivery_days(company_id):
    days = (
        CompanySaleSetting.objects.filter(company_id=company_id)
        .values_list("default_delivery_date", flat=True)
        .first()
    )
    return 0 if days is None else days


def sale_order_revision_allowed(company_id):
    allowed = (
        CompanySaleSetting.objects.filter(company_id=company_id)
        .values_list("allow_order_revision", flat=True)
        .first()
    )
    return True if allowed is None else allowed


def _parse_sales_email_recipients(value, *, label, required):
    recipients = [item.strip() for item in str(value or "").split(",") if item.strip()]
    if required and not recipients:
        raise ValidationError(f"{label} must contain at least one email address.")
    if len(recipients) > MAX_SALES_EMAIL_RECIPIENTS:
        raise ValidationError(
            f"{label} supports at most {MAX_SALES_EMAIL_RECIPIENTS} recipients."
        )
    for recipient in recipients:
        try:
            validate_email(recipient)
        except ValidationError as exc:
            raise ValidationError(f"Invalid {label} email address: {recipient}") from exc
    normalized = ", ".join(recipients)
    if len(normalized) > SaleQuotationEmail._meta.get_field("email_to").max_length:
        raise ValidationError(f"{label} email addresses are too long.")
    return recipients, normalized


def _mark_sales_email_failed(attempt_id, exc):
    reason = str(exc).strip() or exc.__class__.__name__
    SaleQuotationEmail.objects.filter(
        pk=attempt_id,
        status=SaleQuotationEmail.Status.PENDING,
    ).update(
        status=SaleQuotationEmail.Status.FAILED,
        failure_reason=reason[:2000],
        updated_at=timezone.now(),
    )
    return SaleQuotationEmail.objects.get(pk=attempt_id)


def send_sale_quotation_email(
    order,
    actor,
    *,
    email_to,
    email_cc="",
    subject="",
    body_html="",
):
    """Send one quotation and retain a bounded success/failure audit trail."""
    if not getattr(actor, "is_authenticated", False) or not actor.pk:
        raise ValidationError("An authenticated sender is required.")

    to_recipients, normalized_to = _parse_sales_email_recipients(
        email_to,
        label="To",
        required=True,
    )
    cc_recipients, normalized_cc = _parse_sales_email_recipients(
        email_cc,
        label="CC",
        required=False,
    )
    subject = str(subject or "").strip()
    if not subject:
        raise ValidationError("Subject is required.")
    if len(subject) > SaleQuotationEmail._meta.get_field("subject").max_length:
        raise ValidationError("Subject is too long.")
    body_html = str(body_html or "").strip()
    if not body_html:
        raise ValidationError("Message is required.")

    with transaction.atomic():
        locked = (
            SaleOrder.objects.select_for_update()
            .select_related("company", "customer")
            .get(pk=order.pk)
        )
        if locked.status not in {SaleOrder.Status.DRAFT, SaleOrder.Status.SENT}:
            raise ValidationError("Only Draft or Sent quotations can be emailed.")
        _validate_sale_order_ready(locked)
        lines = list(
            locked.lines.select_related("product").order_by("pk")[: MAX_SALES_ORDER_LINES + 1]
        )
        if len(lines) > MAX_SALES_ORDER_LINES:
            raise ValidationError(
                f"A Sales Order supports at most {MAX_SALES_ORDER_LINES} detail lines."
            )
        attempt = SaleQuotationEmail.objects.create(
            order=locked,
            sent_by=actor,
            email_to=normalized_to,
            email_cc=normalized_cc,
            subject=subject,
            body_html=body_html,
        )

    try:
        from .document_pdf import render_document_pdf

        document_config = {
            "header_fields": [
                "company", "order_type", "number", "customer", "validity_date", "order_date", "currency",
                "price_list", "payment_terms", "invoicing_policy", "salesperson_name",
                "customer_reference", "delivery_address", "invoice_address", "delivery_date",
                "global_discount_percent", "terms_conditions", "notes",
            ],
            "total_fields": [
                ("Subtotal", "subtotal_amount"),
                ("Discount", "discount_amount"),
                ("Untaxed", "untaxed_amount"),
                ("Tax", "tax_amount"),
                ("Total", "total"),
            ],
        }
        pdf = render_document_pdf("sales", locked, lines, document_config)
        plain_body = strip_tags(body_html).strip() or f"Quotation {locked.number} is attached."
        message = EmailMultiAlternatives(
            subject=subject,
            body=plain_body,
            to=to_recipients,
            cc=cc_recipients,
        )
        message.attach_alternative(body_html, "text/html")
        safe_number = re.sub(r"[^A-Za-z0-9_.-]+", "-", locked.number).strip("-") or "quotation"
        message.attach(f"quotation-{safe_number}.pdf", pdf, "application/pdf")
        if message.send(fail_silently=False) != 1:
            raise RuntimeError("The email backend did not confirm delivery.")
    except Exception as exc:
        return _mark_sales_email_failed(attempt.pk, exc)

    sent_at = timezone.now()
    with transaction.atomic():
        locked_attempt = SaleQuotationEmail.objects.select_for_update().get(pk=attempt.pk)
        current_order = SaleOrder.objects.select_for_update().get(pk=locked_attempt.order_id)
        locked_attempt.status = SaleQuotationEmail.Status.SENT
        locked_attempt.failure_reason = ""
        locked_attempt.sent_at = sent_at
        locked_attempt.save(
            update_fields=["status", "failure_reason", "sent_at", "updated_at"]
        )
        if current_order.status in {SaleOrder.Status.DRAFT, SaleOrder.Status.SENT}:
            current_order.status = SaleOrder.Status.SENT
            current_order.save(update_fields=["status", "updated_at"])
    return locked_attempt


def apply_sale_approval_submit(order, _actor=None):
    if order.status not in {SaleOrder.Status.DRAFT, SaleOrder.Status.SENT}:
        raise ValidationError("Only Draft or Sent quotations can be submitted for approval.")
    _validate_sale_order_ready(order)
    order.status = SaleOrder.Status.TO_APPROVE
    order.save(update_fields=["status", "updated_at"])
    return order


def apply_sale_approval_result(order, action, _actor=None):
    if order.status != SaleOrder.Status.TO_APPROVE:
        raise ValidationError("Only quotations waiting for approval can be approved or rejected.")
    if action == "approve":
        quotation_number = order.number
        order.status = SaleOrder.Status.SALE
        order.quotation_reference = order.quotation_reference or quotation_number
        setting = CompanySaleSetting.objects.filter(company_id=order.company_id).only(
            "revised_sequence_rule"
        ).first()
        append_revision = bool(
            order.revision_number
            and (not setting or setting.revised_sequence_rule == CompanySaleSetting.RevisionSequenceRule.APPEND_SUFFIX)
        )
        if not append_revision:
            order.number = allocate_sale_document_number(
                order.order_type_id,
                order.company_id,
                document_kind="order",
            )
        order.confirmation_date = timezone.now()
        order.reject_reason = ""
        update_fields = [
            "status",
            "quotation_reference",
            "number",
            "confirmation_date",
            "reject_reason",
            "updated_at",
        ]
    elif action == "reject":
        order.status = SaleOrder.Status.REJECTED
        update_fields = ["status", "updated_at"]
    else:
        raise ValidationError("Unsupported Sales approval action.")
    order.save(update_fields=update_fields)
    return order


def _pending_sale_approval(order):
    content_type = ContentType.objects.get_for_model(order, for_concrete_model=False)
    return (
        ApprovalInstance.objects.select_related("rule", "workflow", "content_type", "company")
        .filter(
            content_type=content_type,
            object_id=order.pk,
            status=ApprovalInstance.Status.PENDING,
        )
        .first()
    )


def can_sale_workflow_action(user, action, order):
    if action == "revise" and not sale_order_revision_allowed(order.company_id):
        return False
    if action not in {"approve", "reject"}:
        return True
    approval = _pending_sale_approval(order)
    return bool(approval and can_user_act(approval, user))


def _revision_number(root):
    latest = (
        SaleOrder.objects.filter(Q(pk=root.pk) | Q(revision_root_id=root.pk))
        .aggregate(value=Max("revision_number"))["value"]
        or 0
    )
    return latest + 1


def _revision_order_number(root, revision_number):
    suffix = f"-R{revision_number:02d}"
    return f"{root.number[: 40 - len(suffix)]}{suffix}"


def _new_sales_number(prefix, source_number=""):
    source = "".join(character for character in source_number.upper() if character.isalnum())[:12]
    token = uuid.uuid4().hex[:10].upper()
    return f"{prefix}-{source}-{token}"[:40]


def _duplicate_sale_order(order):
    source_lines = list(order.lines.order_by("pk")[: MAX_SALES_ORDER_LINES + 1])
    if len(source_lines) > MAX_SALES_ORDER_LINES:
        raise ValidationError(
            f"A Sales Order supports at most {MAX_SALES_ORDER_LINES} detail lines."
        )
    duplicate = SaleOrder.objects.create(
        company_id=order.company_id,
        order_type_id=order.order_type_id,
        number=_new_sales_number("SQ-DUP", order.number),
        order_date=timezone.localdate(),
        status=SaleOrder.Status.DRAFT,
        customer_id=order.customer_id,
        validity_date=timezone.localdate() + timedelta(
            days=get_sale_quotation_validity_days(order.company_id)
        ),
        customer_reference=order.customer_reference,
        salesperson_name=order.salesperson_name,
        price_list=order.price_list,
        payment_terms=order.payment_terms,
        currency=order.currency,
        delivery_address=order.delivery_address,
        invoice_address=order.invoice_address,
        invoicing_policy=order.invoicing_policy,
        global_discount_percent=order.global_discount_percent,
        terms_conditions=order.terms_conditions,
        notes=order.notes,
    )
    now = timezone.now()
    SaleOrderLine.objects.bulk_create(
        [
            SaleOrderLine(
                order=duplicate,
                product_id=line.product_id,
                display_type=line.display_type,
                description=line.description,
                quantity=line.quantity,
                uom=line.uom,
                delivery_address=line.delivery_address,
                unit_price=line.unit_price,
                discount_percent=line.discount_percent,
                tax_percent=line.tax_percent,
                rental_start_date=line.rental_start_date,
                rental_return_date=line.rental_return_date,
                rental_status=line.rental_status,
                created_at=now,
                updated_at=now,
            )
            for line in source_lines
        ],
        batch_size=MAX_SALES_ORDER_LINES,
    )
    recalculate_sale_order_totals(duplicate)
    duplicate.refresh_from_db()
    return duplicate


@transaction.atomic
def merge_sale_quotations(order_ids, actor=None):
    """Merge compatible HMX quotations and cancel their immutable sources."""
    selected_ids = list(dict.fromkeys(int(order_id) for order_id in order_ids))
    if len(selected_ids) < 2:
        raise ValidationError("Select at least two quotations to merge.")
    if len(selected_ids) > MAX_SALES_MERGE_ORDERS:
        raise ValidationError(
            f"Select no more than {MAX_SALES_MERGE_ORDERS} quotations at a time."
        )

    locked_orders = list(
        SaleOrder.objects.select_for_update()
        .filter(pk__in=selected_ids)
        .order_by("pk")
    )
    by_id = {order.pk: order for order in locked_orders}
    if len(by_id) != len(selected_ids):
        raise ValidationError("One or more selected quotations no longer exist.")
    orders = [by_id[order_id] for order_id in selected_ids]
    invalid = [
        order.number
        for order in orders
        if order.status not in {SaleOrder.Status.DRAFT, SaleOrder.Status.SENT}
    ]
    if invalid:
        raise ValidationError(
            f"Only Draft or Sent quotations can be merged. Not mergeable: {', '.join(invalid)}"
        )
    first = orders[0]
    if any(order.customer_id != first.customer_id for order in orders):
        raise ValidationError("All selected quotations must belong to the same customer.")
    if any(
        order.company_id != first.company_id
        or order.currency != first.currency
        or order.order_type_id != first.order_type_id
        for order in orders
    ):
        raise ValidationError(
            "All selected quotations must have the same company, currency, and Order Type."
        )

    source_lines = list(
        SaleOrderLine.objects.filter(order_id__in=selected_ids)
        .order_by("order_id", "pk")[: MAX_SALES_ORDER_LINES * len(selected_ids) + 1]
    )
    if len(source_lines) > MAX_SALES_ORDER_LINES * len(selected_ids):
        raise ValidationError("The selected quotation lines exceed the bounded merge size.")
    aggregated = OrderedDict()
    for line in source_lines:
        if line.display_type:
            key = ("display", line.pk)
        else:
            key = (
                "product",
                line.product_id,
                line.uom,
                line.unit_price,
                line.delivery_address,
                line.delivery_date,
                line.discount_percent,
                line.tax_percent,
            )
        if key not in aggregated:
            aggregated[key] = {
                "product_id": line.product_id,
                "display_type": line.display_type,
                "description": line.description,
                "quantity": line.quantity,
                "uom": line.uom,
                "delivery_address": line.delivery_address,
                "delivery_date": line.delivery_date,
                "unit_price": line.unit_price,
                "discount_percent": line.discount_percent,
                "tax_percent": line.tax_percent,
                "rental_start_date": line.rental_start_date,
                "rental_return_date": line.rental_return_date,
                "rental_status": line.rental_status,
            }
        elif not line.display_type:
            aggregated[key]["quantity"] += line.quantity
    if not aggregated:
        raise ValidationError("The selected quotations do not contain mergeable lines.")
    if len(aggregated) > MAX_SALES_ORDER_LINES:
        raise ValidationError(
            f"A merged Sales quotation supports at most {MAX_SALES_ORDER_LINES} detail lines."
        )

    merged = SaleOrder.objects.create(
        company_id=first.company_id,
        order_type_id=first.order_type_id,
        number=_new_sales_number("SQ-MRG", first.number),
        order_date=timezone.localdate(),
        status=SaleOrder.Status.DRAFT,
        customer_id=first.customer_id,
        validity_date=timezone.localdate() + timedelta(
            days=get_sale_quotation_validity_days(first.company_id)
        ),
        customer_reference=first.customer_reference,
        salesperson_name=first.salesperson_name,
        price_list=first.price_list,
        payment_terms=first.payment_terms,
        currency=first.currency,
        delivery_address=first.delivery_address,
        invoice_address=first.invoice_address,
        invoicing_policy=first.invoicing_policy,
        global_discount_percent=Decimal("0"),
        terms_conditions=first.terms_conditions,
        notes=first.notes,
    )
    now = timezone.now()
    SaleOrderLine.objects.bulk_create(
        [SaleOrderLine(order=merged, created_at=now, updated_at=now, **values) for values in aggregated.values()],
        batch_size=MAX_SALES_ORDER_LINES,
    )
    recalculate_sale_order_totals(merged)
    source_codes = ", ".join(order.number for order in orders)
    reason = f"Merged into {merged.number}. Combined quotations: {source_codes}"
    SaleOrder.objects.filter(pk__in=selected_ids).update(
        status=SaleOrder.Status.CANCELLED,
        cancel_reason=reason,
        updated_at=now,
    )
    merged.refresh_from_db()
    return merged


def _create_sale_revision(order):
    if order.status not in {SaleOrder.Status.CANCELLED, SaleOrder.Status.REJECTED}:
        raise ValidationError("Only Cancelled or Rejected documents can be revised.")
    if SaleOrder.objects.filter(previous_revision_id=order.pk).exists():
        raise ValidationError("This Sales document already has a revised document.")
    if not sale_order_revision_allowed(order.company_id):
        raise ValidationError("Order revision is disabled in Sales Settings.")

    root_id = order.revision_root_id or order.pk
    root = SaleOrder.objects.select_for_update().get(pk=root_id)
    revision_number = _revision_number(root)
    setting = CompanySaleSetting.objects.filter(company_id=order.company_id).only(
        "revised_sequence_rule"
    ).first()
    sequence_rule = (
        setting.revised_sequence_rule
        if setting
        else CompanySaleSetting.RevisionSequenceRule.APPEND_SUFFIX
    )
    if sequence_rule == CompanySaleSetting.RevisionSequenceRule.NEW_SEQUENCE:
        revision_name = allocate_sale_document_number(
            order.order_type_id,
            order.company_id,
            document_kind="quotation",
        )
    elif sequence_rule == CompanySaleSetting.RevisionSequenceRule.APPEND_SUFFIX:
        revision_name = _revision_order_number(root, revision_number)
    else:
        raise ValidationError("Invalid revised sequence rule in Sales Settings.")
    revised = SaleOrder.objects.create(
        company_id=order.company_id,
        order_type_id=order.order_type_id,
        number=revision_name,
        order_date=order.order_date,
        status=SaleOrder.Status.DRAFT,
        customer_id=order.customer_id,
        validity_date=order.validity_date,
        customer_reference=order.customer_reference,
        salesperson_name=order.salesperson_name,
        price_list=order.price_list,
        payment_terms=order.payment_terms,
        currency=order.currency,
        delivery_address=order.delivery_address,
        invoice_address=order.invoice_address,
        delivery_date=order.delivery_date,
        invoicing_policy=order.invoicing_policy,
        global_discount_percent=order.global_discount_percent,
        terms_conditions=order.terms_conditions,
        notes=order.notes,
        revision_root=root,
        previous_revision=order,
        revision_number=revision_number,
    )
    source_lines = list(order.lines.order_by("pk")[: MAX_SALES_ORDER_LINES + 1])
    if len(source_lines) > MAX_SALES_ORDER_LINES:
        raise ValidationError(
            f"A Sales Order supports at most {MAX_SALES_ORDER_LINES} detail lines."
        )
    now = timezone.now()
    SaleOrderLine.objects.bulk_create(
        [
            SaleOrderLine(
                order=revised,
                product_id=line.product_id,
                display_type=line.display_type,
                description=line.description,
                quantity=line.quantity,
                uom=line.uom,
                delivery_address=line.delivery_address,
                delivery_date=line.delivery_date,
                unit_price=line.unit_price,
                discount_percent=line.discount_percent,
                tax_percent=line.tax_percent,
                rental_start_date=line.rental_start_date,
                rental_return_date=line.rental_return_date,
                rental_status=line.rental_status,
                created_at=now,
                updated_at=now,
            )
            for line in source_lines
        ],
        batch_size=MAX_SALES_ORDER_LINES,
    )
    recalculate_sale_order_totals(revised)
    order.status = SaleOrder.Status.REVISED
    order.save(update_fields=["status", "updated_at"])
    return revised


@transaction.atomic
def transition_sale_order(
    order,
    action,
    actor,
    *,
    reason="",
    note="",
    acknowledge_incomplete=False,
    validity_date=None,
):
    """Apply the HMX Sales lifecycle with locked, rollback-safe transitions."""
    locked = SaleOrder.objects.select_for_update().get(pk=order.pk)
    reason = (reason or "").strip()
    note = (note or "").strip()

    if action == "send":
        if locked.status not in {SaleOrder.Status.DRAFT, SaleOrder.Status.SENT}:
            raise ValidationError("Only Draft or Sent quotations can be marked as sent.")
        locked.status = SaleOrder.Status.SENT
        locked.save(update_fields=["status", "updated_at"])
        return locked

    if action == "duplicate":
        return _duplicate_sale_order(locked)

    if action == "submit":
        submit_for_approval(
            locked,
            actor,
            entity="sales",
            apply_submit=apply_sale_approval_submit,
        )
        locked.refresh_from_db()
        return locked

    if action in {"approve", "reject"}:
        approval = _pending_sale_approval(locked)
        if approval is None:
            raise ValidationError("This Sales quotation has no pending approval.")
        outcome = perform_approval_action(
            approval,
            actor,
            action,
            reason=reason,
            apply_result=lambda target, result_action: apply_sale_approval_result(
                target,
                result_action,
                actor,
            ),
        )
        locked.refresh_from_db()
        locked._approval_advanced = not outcome.completed
        if outcome.completed and action == "approve":
            run_before_workflow_hooks(locked, "confirm", actor)
        elif outcome.completed and action == "reject":
            locked.reject_reason = reason
            locked.save(update_fields=["reject_reason", "updated_at"])
        return locked

    if action == "cancel":
        if locked.status not in {
            SaleOrder.Status.DRAFT,
            SaleOrder.Status.SENT,
            SaleOrder.Status.SALE,
        }:
            raise ValidationError("Only Draft, Sent, or Sale Order documents can be cancelled.")
        if not reason:
            raise ValidationError("A cancellation reason is required.")
        run_before_workflow_hooks(locked, "cancel", actor)
        locked.status = SaleOrder.Status.CANCELLED
        locked.cancel_reason = reason
        locked.save(update_fields=["status", "cancel_reason", "updated_at"])
        return locked

    if action == "close":
        if locked.status != SaleOrder.Status.SALE:
            raise ValidationError("Only documents at Sale Order status can be closed.")
        if reason not in SaleOrder.CloseReason.values:
            raise ValidationError("A valid close reason is required.")
        if locked.lines.filter(
            display_type="",
            delivered_quantity__gt=F("invoiced_quantity"),
        ).exists():
            raise ValidationError(
                "Delivered quantities must be fully invoiced before closing this Sales Order."
            )
        incomplete_delivery = locked.lines.filter(display_type="").exclude(
            product__product_type="service"
        ).filter(delivered_quantity__lt=F("quantity")).exists()
        if incomplete_delivery and not acknowledge_incomplete:
            raise ValidationError(
                "Acknowledge that undelivered quantities will be released before closing this Sales Order."
            )
        run_before_workflow_hooks(locked, "close", actor)
        locked.status = SaleOrder.Status.CLOSED
        locked.close_reason = reason
        locked.close_note = note
        locked.save(update_fields=["status", "close_reason", "close_note", "updated_at"])
        return locked

    if action == "revise":
        return _create_sale_revision(locked)

    if action == "reset-draft":
        if locked.status != SaleOrder.Status.EXPIRED:
            raise ValidationError("Only Expired quotations can be reset to Draft.")
        next_validity_date = parse_date(validity_date) if isinstance(validity_date, str) else validity_date
        if next_validity_date is None:
            next_validity_date = timezone.localdate() + timedelta(
                days=get_sale_quotation_validity_days(locked.company_id)
            )
        if next_validity_date < timezone.localdate():
            raise ValidationError("Choose a new quotation expiration date that is today or later.")
        locked.status = SaleOrder.Status.DRAFT
        locked.validity_date = next_validity_date
        locked.delivery_date = None
        locked.save(update_fields=["status", "validity_date", "delivery_date", "updated_at"])
        locked.lines.update(delivery_date=None, updated_at=timezone.now())
        return locked

    raise ValidationError("Unsupported Sales workflow action.")


def expire_due_sales_quotations(run_date=None, batch_size=MAX_EXPIRED_SALES_PER_RUN):
    """Expire one bounded keyset batch; later scheduler runs pick up the remainder."""
    if batch_size < 1 or batch_size > MAX_EXPIRED_SALES_PER_RUN:
        raise ValidationError(
            f"Sales quotation expiry batch size must be between 1 and {MAX_EXPIRED_SALES_PER_RUN}."
        )
    run_date = run_date or timezone.localdate()
    ids = list(
        SaleOrder.objects.filter(
            validity_date__lte=run_date,
            status__in=[
                SaleOrder.Status.DRAFT,
                SaleOrder.Status.SENT,
                SaleOrder.Status.TO_APPROVE,
            ],
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:batch_size]
    )
    if not ids:
        return 0
    return SaleOrder.objects.filter(
        pk__in=ids,
        status__in=[
            SaleOrder.Status.DRAFT,
            SaleOrder.Status.SENT,
            SaleOrder.Status.TO_APPROVE,
        ],
    ).update(status=SaleOrder.Status.EXPIRED, updated_at=timezone.now())
