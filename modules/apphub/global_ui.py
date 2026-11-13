"""Module-owned extension points rendered inside the shared ERP shell."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GlobalUIExtension:
    key: str
    template_name: str
    predicate: object
    priority: int


_extensions = {}


def register_global_ui_extension(key, template_name, *, predicate=None, priority=100):
    """Register or replace a deterministic shell extension.

    Add-ons own their template and visibility predicate. The shared shell only
    knows how to render registered extensions, so uninstalling an add-on does
    not leave module-specific branches behind in ``base.html``.
    """
    extension = GlobalUIExtension(
        key=str(key),
        template_name=str(template_name),
        predicate=predicate,
        priority=int(priority),
    )
    _extensions[extension.key] = extension
    return extension


def iter_global_ui_extensions(context):
    for extension in sorted(_extensions.values(), key=lambda item: (item.priority, item.key)):
        if extension.predicate is None or extension.predicate(context):
            yield extension
