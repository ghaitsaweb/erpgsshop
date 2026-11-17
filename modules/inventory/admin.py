from django.contrib import admin

from .models import (
    InventoryBalanceEvent,
    InventoryLocation,
    InventoryLotSerial,
    InventoryPackage,
    InventoryPackageAllocation,
    InventoryPackageMovementHistory,
    InventoryPackageType,
    InventoryPicking,
    InventoryPickingLine,
    InventoryProofOfDelivery,
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
admin.site.register(InventoryPackageType)
admin.site.register(InventoryPackage)
admin.site.register(InventoryPackageAllocation)
admin.site.register(InventoryPackageMovementHistory)
admin.site.register(InventoryPicking)
admin.site.register(InventoryPickingLine)
admin.site.register(InventoryProofOfDelivery)
admin.site.register(InventoryStockBalance)
admin.site.register(InventoryBalanceEvent)
admin.site.register(InventoryReorderRule)
admin.site.register(InventoryReplenishmentSnapshot)
admin.site.register(InventoryReservation)
admin.site.register(InventoryReservationAllocation)
admin.site.register(InventoryStockCount)
admin.site.register(InventoryStockCountLine)
admin.site.register(InventoryStockFreeze)
