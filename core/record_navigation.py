"""Bounded previous/next navigation for scoped record form querysets."""

from django.urls import reverse


def build_record_navigation(queryset, instance, entity, query_string=""):
    """Return adjacent scoped records without materializing an ID collection."""
    if instance is None or instance.pk is None:
        return None

    unordered = queryset.order_by()
    previous_pk = (
        unordered.filter(pk__lt=instance.pk)
        .order_by("-pk")
        .values_list("pk", flat=True)
        .first()
    )
    next_pk = (
        unordered.filter(pk__gt=instance.pk)
        .order_by("pk")
        .values_list("pk", flat=True)
        .first()
    )

    def edit_url(pk):
        if pk is None:
            return ""
        url = reverse("entity_edit", kwargs={"entity": entity, "pk": pk})
        return f"{url}?{query_string}" if query_string else url

    return {
        "entity": entity,
        "current_pk": instance.pk,
        "previous_url": edit_url(previous_pk),
        "next_url": edit_url(next_pk),
        "query_string": query_string,
    }
