from django.contrib.auth.models import Group, Permission


INVENTORY_ADMINISTRATOR = "Inventory Administrator"
INVENTORY_MANAGER = "Inventory Manager"
INVENTORY_SUPERVISOR = "Inventory Supervisor"
INVENTORY_USER = "Inventory User"

INVENTORY_MODELS = {
    "inventorylocation",
    "inventorylotserial",
    "inventorypackage",
    "inventorypackageallocation",
    "inventorypackagemovementhistory",
    "inventorypackagetype",
    "inventorypicking",
    "inventorypickingline",
    "inventoryproofofdelivery",
    "inventorystockbalance",
    "inventorybalanceevent",
    "inventoryreorderrule",
    "inventoryreplenishmentsnapshot",
    "inventoryreservation",
    "inventoryreservationallocation",
    "inventorystockcount",
    "inventorystockcountline",
    "inventorystockfreeze",
}
CORE_INVENTORY_MODELS = {"product", "warehouse", "stockmove"}
PURCHASE_INVENTORY_MODELS = {
    "purchaseinventorytransfer",
    "purchaseinventorytransferline",
}


def _permissions(app_label, models, actions):
    codenames = {f"{action}_{model}" for model in models for action in actions}
    return Permission.objects.filter(
        content_type__app_label=app_label,
        content_type__model__in=models,
        codename__in=codenames,
    )


def ensure_inventory_roles(**_kwargs):
    all_actions = {"view", "add", "change", "delete"}
    operational_actions = {"view", "add", "change"}
    inventory_all = _permissions("gsshop_inventory", INVENTORY_MODELS, all_actions)
    inventory_operational = _permissions(
        "gsshop_inventory", INVENTORY_MODELS, operational_actions
    )
    core_all = _permissions("core", CORE_INVENTORY_MODELS, all_actions)
    core_operational = _permissions("core", CORE_INVENTORY_MODELS, operational_actions)
    transfer_all = _permissions(
        "gsshop_purchase", PURCHASE_INVENTORY_MODELS, all_actions
    )
    transfer_operational = _permissions(
        "gsshop_purchase", PURCHASE_INVENTORY_MODELS, operational_actions
    )
    role_permissions = {
        INVENTORY_ADMINISTRATOR: inventory_all | core_all | transfer_all,
        INVENTORY_MANAGER: inventory_operational | core_operational | transfer_operational,
        INVENTORY_SUPERVISOR: _permissions(
            "gsshop_inventory", INVENTORY_MODELS, {"view", "add", "change"}
        )
        | _permissions("core", CORE_INVENTORY_MODELS, {"view"})
        | _permissions(
            "gsshop_purchase", PURCHASE_INVENTORY_MODELS, {"view", "add", "change"}
        ),
        INVENTORY_USER: _permissions("gsshop_inventory", INVENTORY_MODELS, {"view"})
        | _permissions("core", CORE_INVENTORY_MODELS, {"view"})
        | _permissions("gsshop_purchase", PURCHASE_INVENTORY_MODELS, {"view"}),
    }
    names = tuple(role_permissions)
    Group.objects.bulk_create(
        [Group(name=name) for name in names],
        ignore_conflicts=True,
        batch_size=100,
    )
    groups = Group.objects.in_bulk(names, field_name="name")
    for name, permissions in role_permissions.items():
        groups[name].permissions.add(*permissions)
