"""Small registry for module-owned hooks around generic ERP workflows."""

from collections import defaultdict


_BEFORE_HOOKS = defaultdict(list)


def register_before_workflow_hook(model_label, action, callback):
    key = (model_label.lower(), action)
    if callback not in _BEFORE_HOOKS[key]:
        _BEFORE_HOOKS[key].append(callback)


def run_before_workflow_hooks(instance, action, actor):
    key = (instance._meta.label_lower, action)
    for callback in _BEFORE_HOOKS.get(key, ()):
        callback(instance=instance, action=action, actor=actor)
