from django.contrib import admin

from .models import (
    Asset,
    BillOfMaterial,
    BillOfMaterialLine,
    ConstructionProject,
    MaintenanceRequest,
    ManufacturingOrder,
    Project,
    ProjectTask,
    QualityInspection,
    WorkPackage,
)


class BOMLineInline(admin.TabularInline):
    model = BillOfMaterialLine
    extra = 1


@admin.register(BillOfMaterial)
class BillOfMaterialAdmin(admin.ModelAdmin):
    list_display = ("code", "company", "product", "output_quantity", "active")
    inlines = [BOMLineInline]


class ProjectTaskInline(admin.TabularInline):
    model = ProjectTask
    extra = 1


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "company", "status", "progress", "start_date", "end_date")
    inlines = [ProjectTaskInline]


class WorkPackageInline(admin.TabularInline):
    model = WorkPackage
    extra = 1


@admin.register(ConstructionProject)
class ConstructionProjectAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "company", "customer", "status", "progress", "contract_value")
    inlines = [WorkPackageInline]


for model in [ManufacturingOrder, QualityInspection, Asset, MaintenanceRequest]:
    admin.site.register(model)

