from django.contrib import admin

from .models import (
    InventoryBalanceEvent,
    InventoryLocation,
    InventoryLotSerial,
    InventoryPicking,
    InventoryPickingLine,
    InventoryReorderRule,
    InventoryReplenishmentSnapshot,
    InventoryReservation,
    InventoryReservationAllocation,
    InventoryStockBalance,
    InventoryStockCount,
    InventoryStockCountLine,
    InventoryStockFreeze,
)


admin.site.register(InventoryLocation)
admin.site.register(InventoryLotSerial)
admin.site.register(InventoryPicking)
admin.site.register(InventoryPickingLine)
admin.site.register(InventoryStockBalance)
admin.site.register(InventoryBalanceEvent)
admin.site.register(InventoryReorderRule)
admin.site.register(InventoryReplenishmentSnapshot)
admin.site.register(InventoryReservation)
admin.site.register(InventoryReservationAllocation)
admin.site.register(InventoryStockCount)
admin.site.register(InventoryStockCountLine)
admin.site.register(InventoryStockFreeze)
