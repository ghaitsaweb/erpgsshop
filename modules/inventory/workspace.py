from modules.apphub.workspaces import register_workspace_contribution


def register_inventory_workspace():
    register_workspace_contribution(
        "inventory",
        group_label="Inventory Control",
        group_icon="I",
        items=[
            {"entity": "inventory-stock-balances", "label": "Stock Balance"},
            {"entity": "inventory-replenishment", "label": "Replenishment Review"},
        ],
    )
    register_workspace_contribution(
        "inventory",
        group_label="Planning & Transfers",
        group_icon="P",
        items=[
            {"entity": "inventory-reorder-rules", "label": "Reordering Rules"},
            {"entity": "purchase-inventory-transfers", "label": "Internal Transfers"},
        ],
    )
    register_workspace_contribution(
        "inventory",
        group_label="Master Data",
        group_icon="M",
        items=[
            {"entity": "inventory-locations", "label": "Locations"},
            {"entity": "inventory-lots", "label": "Lot / Serial Numbers"},
        ],
    )
