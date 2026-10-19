"""Metadata helpers for the opt-in technical UI inspector."""

from django.core.exceptions import FieldDoesNotExist


def developer_metadata(model, *, entity="", instance=None, object_id=None):
    """Return safe, display-ready metadata for one ORM model and record."""
    if instance is not None:
        object_id = instance.pk
    meta = model._meta
    return {
        "entity": entity,
        "model": meta.label,
        "model_name": str(meta.verbose_name),
        "table": meta.db_table,
        "object_id": "" if object_id is None else str(object_id),
        "object_name": str(instance) if instance is not None else "New record",
    }


def developer_field_metadata(model, field_name, *, form_field=None):
    """Describe a model field, while also supporting virtual form-only fields."""
    try:
        model_field = model._meta.get_field(field_name)
    except FieldDoesNotExist:
        model_field = None

    if model_field is None:
        return {
            "name": field_name,
            "type": form_field.__class__.__name__ if form_field else "Form field",
            "column": "",
            "relation": "",
            "kind": "virtual",
        }

    related_model = getattr(model_field, "related_model", None)
    return {
        "name": model_field.name,
        "type": model_field.get_internal_type(),
        "column": getattr(model_field, "column", "") or "",
        "relation": related_model._meta.label if related_model else "",
        "kind": "model",
    }
