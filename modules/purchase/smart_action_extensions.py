"""Purchase-owned exceptional smart-action preparation hooks."""

from core.smart_actions import register_smart_action_preparer

from .vendor_transaction_history import prepare_vendor_transaction_history


def register_purchase_smart_action_extensions():
    register_smart_action_preparer(
        "vendor-profiles.transaction-history",
        prepare_vendor_transaction_history,
    )
