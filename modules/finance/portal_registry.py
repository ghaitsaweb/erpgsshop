"""Discoverable, customer-scoped accounting portal document registry."""

from dataclasses import dataclass
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q

from core.models import Invoice, SaleOrder

from .models import CustomerPortalAccess, PortalDocumentReference


@dataclass(frozen=True)
class PortalDocumentDefinition:
    key: str
    label: str
    singular: str
    description: str
    model: object
    customer_field: str
    company_field: str
    number_field: str
    date_field: str
    amount_field: str
    status_field: str
    allowed_statuses: tuple[str, ...]
    line_relation: str
    line_description_field: str
    line_quantity_field: str
    line_unit_price_field: str
    search_fields: tuple[str, ...] = ()
    select_related: tuple[str, ...] = ()


_DOCUMENT_TYPES = {}


def register_portal_document(definition):
    previous = _DOCUMENT_TYPES.get(definition.key)
    if previous is not None and previous != definition:
        raise ValueError(f"Conflicting customer portal document key: {definition.key}")
    _DOCUMENT_TYPES[definition.key] = definition


def portal_document_definitions():
    return tuple(_DOCUMENT_TYPES.values())


def portal_document_definition(key):
    try:
        return _DOCUMENT_TYPES[key]
    except KeyError as exc:
        raise ValidationError("Unknown portal document type.") from exc


def _field_value(record, path):
    value = record
    for part in path.split("__"):
        value = getattr(value, part)
    return value


def _portal_customer(user):
    if user.is_staff or user.is_superuser:
        return None
    access = CustomerPortalAccess.objects.select_related("customer").filter(user=user, active=True).first()
    if access is None:
        raise PermissionDenied("No active customer portal access is linked to this account.")
    return access.customer


def _scoped_queryset(user, definition):
    queryset = definition.model.objects.filter(
        **{f"{definition.status_field}__in": definition.allowed_statuses}
    )
    customer = _portal_customer(user)
    if customer is not None:
        queryset = queryset.filter(
            **{
                f"{definition.customer_field}_id": customer.pk,
                f"{definition.company_field}_id": customer.company_id,
            }
        )
    return queryset.select_related(*definition.select_related).prefetch_related(definition.line_relation)


def _reference_map(definition, records):
    content_type = ContentType.objects.get_for_model(definition.model)
    record_ids = [record.pk for record in records]
    existing = {
        reference.object_id: reference
        for reference in PortalDocumentReference.objects.filter(
            content_type=content_type, object_id__in=record_ids
        )
    }
    PortalDocumentReference.objects.bulk_create(
        [
            PortalDocumentReference(
                document_type=definition.key,
                content_type=content_type,
                object_id=record.pk,
                company_id=_field_value(record, f"{definition.company_field}_id"),
                customer_id=_field_value(record, f"{definition.customer_field}_id"),
            )
            for record in records
            if record.pk not in existing
        ],
        ignore_conflicts=True,
    )
    return {
        reference.object_id: reference
        for reference in PortalDocumentReference.objects.filter(
            content_type=content_type, object_id__in=record_ids
        )
    }


def _money(value):
    return str(Decimal(value or 0).quantize(Decimal("0.01")))


def _serialize(definition, record, reference, *, detail=False):
    payload = {
        "uuid": str(reference.portal_uuid),
        "number": _field_value(record, definition.number_field),
        "date": _field_value(record, definition.date_field),
        "amount": _money(_field_value(record, definition.amount_field)),
        "status": _field_value(record, definition.status_field),
        "customer": str(_field_value(record, definition.customer_field)),
        "company": str(_field_value(record, definition.company_field)),
    }
    if detail:
        payload["lines"] = [
            {
                "description": _field_value(line, definition.line_description_field),
                "quantity": str(_field_value(line, definition.line_quantity_field)),
                "unit_price": _money(_field_value(line, definition.line_unit_price_field)),
                "subtotal": _money(
                    _field_value(line, definition.line_quantity_field)
                    * _field_value(line, definition.line_unit_price_field)
                ),
            }
            for line in getattr(record, definition.line_relation).all()
        ]
    return payload


def list_portal_documents(user, key, *, search="", page=1, page_size=20):
    definition = portal_document_definition(key)
    queryset = _scoped_queryset(user, definition).order_by(f"-{definition.date_field}", "-id")
    if search:
        query = Q()
        for field in definition.search_fields:
            query |= Q(**{f"{field}__icontains": search})
        queryset = queryset.filter(query)
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 20), 1), 100)
    total = queryset.count()
    records = list(queryset[(page - 1) * page_size:page * page_size])
    references = _reference_map(definition, records)
    return {
        "definition": definition,
        "items": [_serialize(definition, record, references[record.pk]) for record in records],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size,
    }


def get_portal_document(user, key, portal_uuid):
    definition = portal_document_definition(key)
    content_type = ContentType.objects.get_for_model(definition.model)
    reference = PortalDocumentReference.objects.filter(
        portal_uuid=portal_uuid,
        document_type=key,
        content_type=content_type,
    ).first()
    if reference is None:
        return None
    record = _scoped_queryset(user, definition).filter(pk=reference.object_id).first()
    if record is None:
        return None
    if (
        reference.company_id != _field_value(record, f"{definition.company_field}_id")
        or reference.customer_id != _field_value(record, f"{definition.customer_field}_id")
    ):
        return None
    return {"definition": definition, "document": _serialize(definition, record, reference, detail=True)}


def register_builtin_portal_documents():
    register_portal_document(PortalDocumentDefinition(
        key="sales-orders",
        label="Sales Orders",
        singular="Sales Order",
        description="Confirmed sales orders placed by your account.",
        model=SaleOrder,
        customer_field="customer",
        company_field="company",
        number_field="number",
        date_field="order_date",
        amount_field="total",
        status_field="status",
        allowed_statuses=(SaleOrder.Status.CONFIRMED, SaleOrder.Status.COMPLETED),
        line_relation="lines",
        line_description_field="description",
        line_quantity_field="quantity",
        line_unit_price_field="unit_price",
        search_fields=("number", "customer_reference", "customer__name"),
        select_related=("customer", "company"),
    ))
    register_portal_document(PortalDocumentDefinition(
        key="invoices",
        label="Invoices",
        singular="Invoice",
        description="Posted invoices issued to your account.",
        model=Invoice,
        customer_field="partner",
        company_field="company",
        number_field="number",
        date_field="invoice_date",
        amount_field="total",
        status_field="status",
        allowed_statuses=(Invoice.Status.POSTED, Invoice.Status.PAID),
        line_relation="lines",
        line_description_field="description",
        line_quantity_field="quantity",
        line_unit_price_field="unit_price",
        search_fields=("number", "partner__name", "notes"),
        select_related=("partner", "company"),
    ))
