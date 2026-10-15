from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import Company, Contact, Employee, Product, TimeStampedModel, Warehouse


class BillOfMaterial(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="bills_of_material")
    code = models.CharField(max_length=40)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="boms")
    output_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_bom_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.product.name}"


class BillOfMaterialLine(TimeStampedModel):
    bom = models.ForeignKey(BillOfMaterial, on_delete=models.CASCADE, related_name="components")
    component = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="used_in_boms")
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, blank=True, null=True)

    def __str__(self):
        return f"{self.bom.code} · {self.component.sku}"


class ManufacturingOrder(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        IN_PROGRESS = "in_progress", "In Progress"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="manufacturing_orders")
    number = models.CharField(max_length=40)
    bom = models.ForeignKey(BillOfMaterial, on_delete=models.PROTECT, related_name="manufacturing_orders")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="manufacturing_orders")
    planned_start = models.DateTimeField()
    planned_end = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    responsible = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="manufacturing_orders")

    class Meta:
        ordering = ["-planned_start"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_manufacturing_number_per_company")]

    def __str__(self):
        return self.number


class QualityInspection(TimeStampedModel):
    class Result(models.TextChoices):
        PENDING = "pending", "Pending"
        PASS = "pass", "Pass"
        FAIL = "fail", "Fail"
        CONDITIONAL = "conditional", "Conditional"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="quality_inspections")
    number = models.CharField(max_length=40)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="quality_inspections")
    inspection_date = models.DateField(default=timezone.localdate)
    reference = models.CharField(max_length=80, blank=True)
    result = models.CharField(max_length=16, choices=Result.choices, default=Result.PENDING)
    inspector = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="quality_inspections")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-inspection_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_quality_number_per_company")]

    def __str__(self):
        return self.number


class Project(TimeStampedModel):
    class Status(models.TextChoices):
        PLANNING = "planning", "Planning"
        ACTIVE = "active", "Active"
        ON_HOLD = "on_hold", "On Hold"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="projects")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=180)
    customer = models.ForeignKey(Contact, on_delete=models.SET_NULL, blank=True, null=True, related_name="projects")
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="managed_projects")
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    budget = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    progress = models.PositiveSmallIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNING)

    class Meta:
        ordering = ["-start_date", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_project_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ProjectTask(TimeStampedModel):
    class Status(models.TextChoices):
        TODO = "todo", "To Do"
        IN_PROGRESS = "in_progress", "In Progress"
        BLOCKED = "blocked", "Blocked"
        DONE = "done", "Done"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    title = models.CharField(max_length=180)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="project_tasks")
    due_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.TODO)
    progress = models.PositiveSmallIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["status", "due_date", "id"]

    def __str__(self):
        return self.title


class Asset(TimeStampedModel):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        IN_USE = "in_use", "In Use"
        MAINTENANCE = "maintenance", "Maintenance"
        RETIRED = "retired", "Retired"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="assets")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, blank=True, null=True, related_name="assets")
    purchase_date = models.DateField(blank=True, null=True)
    acquisition_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    assigned_employee = models.ForeignKey(Employee, on_delete=models.SET_NULL, blank=True, null=True, related_name="assigned_assets")
    location = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.AVAILABLE)

    class Meta:
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_asset_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class MaintenanceRequest(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        SCHEDULED = "scheduled", "Scheduled"
        IN_PROGRESS = "in_progress", "In Progress"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="maintenance_requests")
    number = models.CharField(max_length=40, unique=True)
    request_date = models.DateField(default=timezone.localdate)
    scheduled_date = models.DateField(blank=True, null=True)
    description = models.TextField()
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="maintenance_requests")
    estimated_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    actual_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["-request_date", "-id"]

    def __str__(self):
        return self.number


class ConstructionProject(TimeStampedModel):
    class Status(models.TextChoices):
        TENDER = "tender", "Tender"
        ACTIVE = "active", "Active"
        ON_HOLD = "on_hold", "On Hold"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="construction_projects")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=180)
    customer = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="construction_projects")
    site_address = models.TextField(blank=True)
    contract_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    progress = models.PositiveSmallIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.TENDER)

    class Meta:
        ordering = ["-start_date", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_construction_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class WorkPackage(TimeStampedModel):
    construction_project = models.ForeignKey(ConstructionProject, on_delete=models.CASCADE, related_name="work_packages")
    code = models.CharField(max_length=40)
    name = models.CharField(max_length=160)
    budget = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    actual_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    progress = models.PositiveSmallIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    status = models.CharField(max_length=16, choices=Project.Status.choices, default=Project.Status.PLANNING)

    class Meta:
        ordering = ["construction_project", "code"]
        constraints = [models.UniqueConstraint(fields=["construction_project", "code"], name="unique_work_package_per_project")]

    def __str__(self):
        return f"{self.construction_project.code} · {self.code}"

