"""Bounded, permission-aware read operations exposed by Hashy Bridge."""

from __future__ import annotations

import datetime
import os
import uuid
from decimal import Decimal

from django.core.exceptions import FieldDoesNotExist
from django.db import connection, models, transaction
from django.db.models import Count, Sum

from core.views import ENTITY_CONFIG, _can, _scope_queryset

from .errors import forbidden, invalid_request


MAX_PAGE_SIZE = 100
MAX_BATCH_OPERATIONS = 20
MAX_FIELDS = 20
MAX_FILTERS = 20
MAX_IN_VALUES = 100
MAX_TEXT_VALUE = 2000
FILTER_OPERATORS = {"eq": "", "in": "__in", "lt": "__lt", "lte": "__lte", "gt": "__gt", "gte": "__gte", "isnull": "__isnull"}
SENSITIVE_NAMES = {"password", "secret", "token", "credential", "api_key", "private_key"}


def _config_for(principal, entity):
    config = ENTITY_CONFIG.get(entity)
    if config is None:
        raise invalid_request("Unknown ERP entity.")
    model = config["model"]
    if not _can(principal.user, "view", model, entity):
        raise forbidden("Token user cannot view this ERP entity.")
    return config


def _candidate_names(config):
    names = {config["model"]._meta.pk.name}
    names.update(str(item[0]) for item in config.get("columns", ()) if item)
    names.update(str(item) for item in config.get("form_fields", ()))
    form_class = config.get("form")
    meta_fields = getattr(getattr(form_class, "_meta", None), "fields", None)
    if isinstance(meta_fields, (list, tuple)):
        names.update(str(item) for item in meta_fields)
    names.update(str(item) for item in config.get("bridge_fields", ()))
    return names


def exposed_fields(config):
    model = config["model"]
    result = {}
    for name in sorted(_candidate_names(config)):
        if "__" in name or any(part in name.lower() for part in SENSITIVE_NAMES):
            continue
        try:
            field = model._meta.get_field(name)
        except FieldDoesNotExist:
            continue
        if not field.concrete or field.many_to_many or field.one_to_many:
            continue
        if isinstance(field, (models.BinaryField, models.JSONField)):
            continue
        result[name] = field
    return result


def _requested_fields(config, raw_fields):
    available = exposed_fields(config)
    if raw_fields in (None, []):
        names = list(available)[:MAX_FIELDS]
    elif not isinstance(raw_fields, list) or not raw_fields or len(raw_fields) > MAX_FIELDS:
        raise invalid_request(f"fields must contain 1 to {MAX_FIELDS} field names.")
    else:
        names = [str(item) for item in raw_fields]
    if len(names) != len(set(names)) or any(name not in available for name in names):
        raise invalid_request("One or more fields are unavailable for this ERP entity.")
    return [(name, available[name]) for name in names]


def _coerce_positive_int(value, name, *, maximum=None, allow_zero=False):
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise invalid_request(f"{name} must be an integer.") from exc
    minimum = 0 if allow_zero else 1
    if parsed < minimum or (maximum is not None and parsed > maximum):
        suffix = f" and at most {maximum}" if maximum is not None else ""
        raise invalid_request(f"{name} must be at least {minimum}{suffix}.")
    return parsed


def _apply_filters(queryset, config, raw_filters):
    if raw_filters in (None, []):
        return queryset
    if not isinstance(raw_filters, list) or len(raw_filters) > MAX_FILTERS:
        raise invalid_request(f"filters must be a list of at most {MAX_FILTERS} items.")
    available = exposed_fields(config)
    conditions = {}
    for item in raw_filters:
        if not isinstance(item, dict):
            raise invalid_request("Every filter must be an object.")
        name = str(item.get("field") or "")
        operator = str(item.get("op") or "eq")
        if name not in available or operator not in FILTER_OPERATORS:
            raise invalid_request("A filter field or operator is unavailable.")
        value = item.get("value")
        if operator == "in":
            if not isinstance(value, list) or not value or len(value) > MAX_IN_VALUES:
                raise invalid_request(f"An in filter requires 1 to {MAX_IN_VALUES} values.")
        if operator == "isnull" and not isinstance(value, bool):
            raise invalid_request("An isnull filter requires a boolean value.")
        lookup = f"{name}{FILTER_OPERATORS[operator]}"
        if lookup in conditions:
            raise invalid_request("Duplicate filters for the same field and operator are not supported.")
        conditions[lookup] = value
    try:
        return queryset.filter(**conditions)
    except (TypeError, ValueError) as exc:
        raise invalid_request("A filter value has the wrong type.") from exc


def _base_queryset(principal, entity, config, filters):
    queryset = _scope_queryset(principal.user, entity, config["model"].objects.all())
    return _apply_filters(queryset, config, filters)


def _json_value(value):
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "name") and hasattr(value, "storage"):
        return value.name
    if isinstance(value, str) and len(value) > MAX_TEXT_VALUE:
        return value[:MAX_TEXT_VALUE]
    return value


def _row(instance, fields):
    result = {}
    for name, field in fields:
        value = getattr(instance, field.attname if field.is_relation else name)
        result[name] = _json_value(value)
    return result


def search_read(principal, payload):
    entity = str(payload.get("entity") or "")
    config = _config_for(principal, entity)
    fields = _requested_fields(config, payload.get("fields"))
    page_size = _coerce_positive_int(payload.get("page_size", 50), "page_size", maximum=MAX_PAGE_SIZE)
    after_id = _coerce_positive_int(payload.get("after_id", 0), "after_id", allow_zero=True)
    queryset = _base_queryset(principal, entity, config, payload.get("filters"))
    with transaction.atomic():
        _apply_statement_timeout()
        page = list(queryset.filter(pk__gt=after_id).order_by("pk")[: page_size + 1])
    has_more = len(page) > page_size
    page = page[:page_size]
    return {
        "entity": entity,
        "records": [_row(instance, fields) for instance in page],
        "has_more": has_more,
        "next_after_id": page[-1].pk if has_more and page else None,
    }


def _numeric_field(field):
    return isinstance(
        field,
        (
            models.DecimalField,
            models.FloatField,
            models.IntegerField,
            models.BigIntegerField,
            models.PositiveIntegerField,
            models.PositiveBigIntegerField,
        ),
    )


def _apply_statement_timeout():
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = %s", [5000])


def read_group(principal, payload):
    entity = str(payload.get("entity") or "")
    config = _config_for(principal, entity)
    available = exposed_fields(config)
    group_by = payload.get("group_by")
    measures = payload.get("measures", [])
    if not isinstance(measures, list) or len(measures) > 10:
        raise invalid_request("measures must be a list of at most 10 numeric fields.")
    if len(measures) != len(set(measures)):
        raise invalid_request("measures cannot contain duplicates.")
    if any(name not in available or not _numeric_field(available[name]) for name in measures):
        raise invalid_request("Every measure must be an available numeric field.")
    annotations = {"record_count": Count("pk")}
    annotations.update({f"{name}_sum": Sum(name) for name in measures})
    queryset = _base_queryset(principal, entity, config, payload.get("filters"))
    with transaction.atomic():
        _apply_statement_timeout()
        if group_by in (None, ""):
            values = queryset.aggregate(**annotations)
            return {"entity": entity, "group_by": None, "groups": [{key: _json_value(value) for key, value in values.items()}], "has_more": False}
        group_by = str(group_by)
        field = available.get(group_by)
        if field is None or field.null or isinstance(field, (models.TextField, models.BinaryField, models.JSONField)):
            raise invalid_request("group_by must be an available non-null scalar field.")
        page_size = _coerce_positive_int(payload.get("page_size", 50), "page_size", maximum=MAX_PAGE_SIZE)
        after_group = payload.get("after_group")
        if after_group is not None:
            try:
                queryset = queryset.filter(**{f"{group_by}__gt": after_group})
            except (TypeError, ValueError) as exc:
                raise invalid_request("after_group has the wrong type.") from exc
        rows = list(queryset.values(group_by).annotate(**annotations).order_by(group_by)[: page_size + 1])
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    return {
        "entity": entity,
        "group_by": group_by,
        "groups": [{key: _json_value(value) for key, value in row.items()} for row in rows],
        "has_more": has_more,
        "next_after_group": _json_value(rows[-1][group_by]) if has_more and rows else None,
    }


def schema(principal, payload):
    requested = payload.get("entity")
    entities = [str(requested)] if requested else sorted(ENTITY_CONFIG)
    result = []
    for entity in entities:
        config = ENTITY_CONFIG.get(entity)
        if config is None or not _can(principal.user, "view", config["model"], entity):
            continue
        fields = []
        for name, field in exposed_fields(config).items():
            fields.append(
                {
                    "name": name,
                    "type": field.get_internal_type(),
                    "nullable": field.null,
                    "relation": field.related_model._meta.label_lower if field.is_relation and field.related_model else None,
                }
            )
        result.append({"entity": entity, "model": config["model"]._meta.label_lower, "fields": fields})
    if requested and not result:
        raise forbidden("Token user cannot inspect this ERP entity.")
    return {"entities": result}


def health(principal, payload):
    return {
        "module": "erpshop-hashy-bridge",
        "version": "1.0.0",
        "user": principal.user.get_username(),
        "company_id": principal.company.pk,
        "branch_id": principal.branch.pk,
        "features": {
            "search_read": True,
            "read_group": True,
            "schema": True,
            "batch": True,
            "writes": False,
            "deletes": False,
        },
    }


def instance_info(principal, payload):
    return {
        "module": "erpshop-hashy-bridge",
        "version": "1.0.0",
        "instance_code": os.getenv("ERPGSSHOP_HASHY_BRIDGE_INSTANCE_CODE", "").strip(),
        "public_base_url": os.getenv("ERPGSSHOP_PUBLIC_BASE_URL", "").strip(),
        "company_id": principal.company.pk,
    }


READ_METHODS = {
    "health": health,
    "instance_info": instance_info,
    "schema": schema,
    "search_read": search_read,
    "read_group": read_group,
}


def batch(principal, payload):
    operations = payload.get("operations")
    if not isinstance(operations, list) or not operations or len(operations) > MAX_BATCH_OPERATIONS:
        raise invalid_request(f"operations must contain 1 to {MAX_BATCH_OPERATIONS} items.")
    results = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise invalid_request("Every batch operation must be an object.")
        method = str(operation.get("method") or "")
        handler = READ_METHODS.get(method)
        if handler is None or method == "health":
            raise invalid_request(f"Unsupported batch method at position {index}.")
        arguments = operation.get("payload", {})
        if not isinstance(arguments, dict):
            raise invalid_request(f"Batch payload at position {index} must be an object.")
        results.append({"index": index, "method": method, "data": handler(principal, arguments)})
    return {"results": results}
