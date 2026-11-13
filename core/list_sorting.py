"""Safe server-side sorting metadata for generic entity lists."""

from django.core.exceptions import FieldDoesNotExist


SORT_DIRECTIONS = {"asc", "desc"}


def _sortable_model_field(model, field_name):
    """Return a concrete field that Django can safely use in ``order_by``."""
    try:
        field = model._meta.get_field(field_name)
    except FieldDoesNotExist:
        return None
    if not field.concrete or field.many_to_many or field.one_to_many:
        return None
    return field


def prepare_list_sorting(request, model, columns):
    """Decorate visible columns and return a deterministic ORM ordering.

    Only concrete model fields already present in the list configuration are
    accepted. This keeps the query-string value behind a strict allowlist.
    """
    sortable = {
        column["name"]: _sortable_model_field(model, column["name"])
        for column in columns
    }
    requested_sort = request.GET.get("sort", "").strip()
    current_sort = requested_sort if sortable.get(requested_sort) is not None else ""
    requested_direction = request.GET.get("direction", "").strip().lower()
    current_direction = (
        requested_direction if requested_direction in SORT_DIRECTIONS else "asc"
    )

    decorated_columns = []
    for column in columns:
        column = dict(column)
        is_sortable = sortable[column["name"]] is not None
        is_active = is_sortable and column["name"] == current_sort
        next_direction = "desc" if is_active and current_direction == "asc" else "asc"
        params = request.GET.copy()
        params.pop("page", None)
        params["sort"] = column["name"]
        params["direction"] = next_direction
        column.update(
            {
                "sortable": is_sortable,
                "sort_active": is_active,
                "sort_direction": current_direction if is_active else "",
                "aria_sort": (
                    "ascending"
                    if is_active and current_direction == "asc"
                    else "descending"
                    if is_active
                    else "none"
                ),
                "sort_url": f"?{params.urlencode()}" if is_sortable else "",
                "next_sort_direction": next_direction,
            }
        )
        decorated_columns.append(column)

    ordering = ()
    if current_sort:
        prefix = "-" if current_direction == "desc" else ""
        ordering = (f"{prefix}{current_sort}",)
        primary_key = model._meta.pk.name
        if current_sort != primary_key:
            ordering += (f"{prefix}{primary_key}",)

    return decorated_columns, ordering, current_sort, current_direction


def query_without_page(request):
    """Preserve list state while letting pagination replace its page number."""
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()
