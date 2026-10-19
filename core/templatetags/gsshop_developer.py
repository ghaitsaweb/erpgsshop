from django import template

from core.developer import developer_field_metadata


register = template.Library()


@register.simple_tag
def developer_field(field, model):
    """Expose model metadata for a BoundField without affecting normal UI."""
    metadata = developer_field_metadata(model, field.name, form_field=field.field)
    return {
        **metadata,
        "label": str(field.label),
        "required": bool(field.field.required),
    }
