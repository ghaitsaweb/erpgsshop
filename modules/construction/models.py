from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import Company, Contact, Employee, Invoice, Product, PurchaseOrder, PurchaseOrderLine, StockMove, TimeStampedModel, Warehouse
from modules.finance.models import Account, JournalEntry
from modules.hr.models import JobPosition
from modules.operations.models import Asset, ConstructionProject, WorkPackage
from modules.purchase.models import PurchaseRequest, PurchaseRequestLine


class ApprovalStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class ExecutionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PLANNED = "planned", "Planned"
    IN_PROGRESS = "in_progress", "In Progress"
    DONE = "done", "Done"
    ON_HOLD = "on_hold", "On Hold"
    CANCELLED = "cancelled", "Cancelled"


class ConstructionProductGroup(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="construction_product_groups")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    branch = models.CharField(max_length=120, blank=True)
    products = models.ManyToManyField(Product, blank=True, related_name="construction_product_groups")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_construction_product_group_code")
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ConstructionBOQTemplate(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="construction_boq_templates")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    branch = models.CharField(max_length=120, blank=True)
    internal_notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_construction_boq_template")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ConstructionBOQTemplateItem(TimeStampedModel):
    template = models.ForeignKey(ConstructionBOQTemplate, on_delete=models.CASCADE, related_name="items")
    sequence = models.PositiveIntegerField(default=10)
    parent_item = models.ForeignKey("self", on_delete=models.CASCADE, blank=True, null=True, related_name="children")
    code = models.CharField(max_length=80, blank=True)
    name = models.CharField(max_length=240, blank=True)
    level = models.PositiveIntegerField(default=0)
    is_leaf_node = models.BooleanField(default=True)
    is_direct_amount = models.BooleanField(default=False)
    direct_amount_info = models.CharField(max_length=240, blank=True)
    product_group = models.ForeignKey(
        ConstructionProductGroup,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="template_items",
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_template_items")
    description = models.CharField(max_length=240)
    unit = models.CharField(max_length=24, default="Unit")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1, validators=[MinValueValidator(Decimal("0"))])
    unit_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0, validators=[MinValueValidator(Decimal("0"))])

    class Meta:
        ordering = ["template", "sequence", "id"]

    @property
    def amount(self):
        return self.quantity * self.unit_cost

    def __str__(self):
        return f"{self.template.code} · {self.description}"


class ConstructionContract(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ON_HOLD = "on_hold", "On Hold"
        COMPLETED = "completed", "Completed"
        TERMINATED = "terminated", "Terminated"
        CANCELLED = "cancelled", "Cancelled"

    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="contracts")
    contract_type = models.CharField(max_length=24, choices=(("owner", "Owner Contract"), ("subcontract", "Subcontract")), default="owner")
    billing_type = models.CharField(max_length=24, choices=(("progress", "Progress"), ("milestone", "Milestone"), ("fixed", "Fixed Price")), default="progress")
    number = models.CharField(max_length=40)
    customer = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="construction_contracts")
    title = models.CharField(max_length=180)
    contract_date = models.DateField(default=timezone.localdate)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    contract_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    linked_boq = models.ForeignKey("ConstructionBOQ", on_delete=models.SET_NULL, blank=True, null=True, related_name="linked_contracts")
    linked_cost_sheet = models.ForeignKey("ConstructionCostSheet", on_delete=models.SET_NULL, blank=True, null=True, related_name="linked_contracts")
    responsible_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="responsible_construction_contracts")
    currency = models.CharField(max_length=3, default="IDR")
    branch = models.CharField(max_length=120, blank=True)
    dp_type = models.CharField(max_length=16, choices=(("percent", "Percentage"), ("fixed", "Fixed Amount")), default="percent")
    down_payment_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    down_payment_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    down_payment_status = models.CharField(max_length=20, choices=(("pending", "Pending"), ("partial", "Partially Invoiced"), ("invoiced", "Invoiced"), ("paid", "Paid")), default="pending")
    retention_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    retention2_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    retention1_release_date = models.DateField(blank=True, null=True)
    retention2_release_date = models.DateField(blank=True, null=True)
    retention1_status = models.CharField(max_length=20, default="pending")
    retention2_status = models.CharField(max_length=20, default="pending")
    retention1_invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_retention1_contracts")
    retention2_invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_retention2_contracts")
    is_opening_balance = models.BooleanField(default=False)
    legacy_reference = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-contract_date", "number"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="unique_construction_contract_number")]

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date must be on or after start date."})
        if self.customer_id and self.project_id and self.customer.company_id != self.project.company_id:
            raise ValidationError({"customer": "Customer and project must belong to the same company."})

    def __str__(self):
        return self.number


class ConstructionContractMilestone(TimeStampedModel):
    contract = models.ForeignKey(ConstructionContract, on_delete=models.CASCADE, related_name="milestones")
    sequence = models.PositiveIntegerField(default=10)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    due_date = models.DateField(blank=True, null=True)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    achieved = models.BooleanField(default=False)
    achieved_date = models.DateField(blank=True, null=True)
    based_on_work_item = models.BooleanField(default=False)
    linked_boq_items = models.ManyToManyField("ConstructionBOQItem", blank=True, related_name="contract_milestones")
    status = models.CharField(max_length=20, choices=(("pending", "Pending"), ("achieved", "Achieved"), ("invoiced", "Invoiced")), default="pending")

    class Meta:
        ordering = ["contract", "sequence", "id"]

    def __str__(self):
        return f"{self.contract.number} · {self.name}"


class ConstructionBOQ(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        REVIEW = "review", "Under Review"
        APPROVED = "approved", "Approved"
        REVISED = "revised", "Revised"
        CANCELLED = "cancelled", "Cancelled"

    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="boqs")
    contract = models.ForeignKey(ConstructionContract, on_delete=models.SET_NULL, blank=True, null=True, related_name="boqs")
    number = models.CharField(max_length=40)
    title = models.CharField(max_length=180)
    revision = models.PositiveIntegerField(default=0)
    boq_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)
    use_quantity_surveying = models.BooleanField(default=True)
    template = models.ForeignKey(ConstructionBOQTemplate, on_delete=models.SET_NULL, blank=True, null=True, related_name="boqs")
    template_loaded_at = models.DateTimeField(blank=True, null=True)
    template_loaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="loaded_construction_boq_templates")
    internal_notes = models.TextField(blank=True)
    global_markup_type = models.CharField(max_length=12, choices=(("percent", "Percentage"), ("fixed", "Fixed")), default="percent")
    global_markup_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    global_discount_type = models.CharField(max_length=12, choices=(("percent", "Percentage"), ("fixed", "Fixed")), default="percent")
    global_discount_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    global_tax_percent = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    total_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    total_revenue_before_tax = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    total_tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    total_revenue_including_tax = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    total_margin = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    revision_history = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-boq_date", "number", "-revision"]
        constraints = [
            models.UniqueConstraint(fields=["project", "number", "revision"], name="unique_construction_boq_revision")
        ]

    def clean(self):
        if self.contract_id and self.project_id and self.contract.project_id != self.project_id:
            raise ValidationError({"contract": "Contract must belong to the selected project."})

    def __str__(self):
        return f"{self.number} R{self.revision}"


class ConstructionBOQItem(TimeStampedModel):
    class ProcurementMethod(models.TextChoices):
        STOCK = "stock", "Stock"
        PURCHASE = "purchase", "Purchase"
        SUBCONTRACT = "subcontract", "Subcontract"
        INTERNAL = "internal", "Internal"

    boq = models.ForeignKey(ConstructionBOQ, on_delete=models.CASCADE, related_name="items")
    sequence = models.PositiveIntegerField(default=10)
    parent_item = models.ForeignKey("self", on_delete=models.CASCADE, blank=True, null=True, related_name="children")
    wbs_code = models.CharField(max_length=80, blank=True)
    level = models.PositiveIntegerField(default=0)
    is_leaf_node = models.BooleanField(default=True)
    is_direct_amount = models.BooleanField(default=False)
    direct_amount_info = models.CharField(max_length=240, blank=True)
    work_package = models.ForeignKey(WorkPackage, on_delete=models.SET_NULL, blank=True, null=True, related_name="boq_items")
    product_group = models.ForeignKey(ConstructionProductGroup, on_delete=models.SET_NULL, blank=True, null=True, related_name="boq_items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_boq_items")
    description = models.CharField(max_length=240)
    unit = models.CharField(max_length=24, default="Unit")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1, validators=[MinValueValidator(Decimal("0"))])
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0, validators=[MinValueValidator(Decimal("0"))])
    budget_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0, validators=[MinValueValidator(Decimal("0"))])
    actual_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    physical_progress = models.DecimalField(max_digits=7, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    commercial_markup_type = models.CharField(max_length=12, choices=(("percent", "Percentage"), ("fixed", "Fixed")), default="percent")
    commercial_markup_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    commercial_discount_type = models.CharField(max_length=12, choices=(("percent", "Percentage"), ("fixed", "Fixed")), default="percent")
    commercial_discount_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    commercial_tax_percent = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    commercial_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    procurement_method = models.CharField(max_length=16, choices=ProcurementMethod.choices, default=ProcurementMethod.PURCHASE)

    class Meta:
        ordering = ["boq", "sequence", "id"]

    @property
    def sales_amount(self):
        return self.quantity * self.unit_price

    @property
    def budget_amount(self):
        return self.quantity * self.budget_cost

    def clean(self):
        if self.work_package_id and self.boq_id and self.work_package.construction_project_id != self.boq.project_id:
            raise ValidationError({"work_package": "Work package must belong to the BOQ project."})

    def __str__(self):
        return f"{self.boq.number} · {self.description}"


class ConstructionCostSheet(TimeStampedModel):
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="cost_sheets")
    boq = models.ForeignKey(ConstructionBOQ, on_delete=models.SET_NULL, blank=True, null=True, related_name="cost_sheets")
    number = models.CharField(max_length=40)
    title = models.CharField(max_length=180)
    project_manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="managed_construction_cost_sheets")
    period = models.CharField(max_length=40, blank=True)
    branch = models.CharField(max_length=120, blank=True)
    sheet_date = models.DateField(default=timezone.localdate)
    budget_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    committed_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    actual_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    revised_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    revised_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    utilised_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    estimated_margin = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    margin_percent = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    procurement_validation_level = models.CharField(max_length=16, choices=(("none", "None"), ("warning", "Warning"), ("block", "Block")), default="warning")
    procurement_warning_threshold = models.DecimalField(max_digits=7, decimal_places=2, default=80)
    procurement_block_threshold = models.DecimalField(max_digits=7, decimal_places=2, default=100)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)

    class Meta:
        ordering = ["-sheet_date", "number"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="unique_construction_cost_sheet")]

    def clean(self):
        if self.boq_id and self.project_id and self.boq.project_id != self.project_id:
            raise ValidationError({"boq": "BOQ must belong to the selected project."})

    def __str__(self):
        return self.number


class ConstructionCostSheetItem(TimeStampedModel):
    cost_sheet = models.ForeignKey(ConstructionCostSheet, on_delete=models.CASCADE, related_name="items")
    boq_item = models.ForeignKey(ConstructionBOQItem, on_delete=models.SET_NULL, blank=True, null=True, related_name="cost_items")
    parent_item = models.ForeignKey("self", on_delete=models.CASCADE, blank=True, null=True, related_name="children")
    sequence = models.PositiveIntegerField(default=10)
    wbs_code = models.CharField(max_length=80, blank=True)
    level = models.PositiveIntegerField(default=0)
    is_leaf_node = models.BooleanField(default=True)
    is_cost_sheet_only = models.BooleanField(default=False)
    is_direct_amount = models.BooleanField(default=False)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_cost_items")
    description = models.CharField(max_length=240)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    actual_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    actual_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    revenue_adjustment = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    revised_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    budget_cost_adjustment = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    quantity_adjustment = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    unit_rate_adjustment = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    revised_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    utilised_budget = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    planned_start = models.DateField(blank=True, null=True)
    planned_end = models.DateField(blank=True, null=True)
    execution_progress = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    procurement_quantity_compatible = models.BooleanField(default=True)

    class Meta:
        ordering = ["cost_sheet", "id"]

    @property
    def planned_amount(self):
        return self.quantity * self.unit_cost

    def __str__(self):
        return f"{self.cost_sheet.number} · {self.description}"


class ConstructionBudgetChangeRequest(TimeStampedModel):
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="budget_change_requests")
    cost_sheet = models.ForeignKey(ConstructionCostSheet, on_delete=models.PROTECT, blank=True, null=True, related_name="budget_change_requests")
    number = models.CharField(max_length=40)
    request_date = models.DateField(default=timezone.localdate)
    title = models.CharField(max_length=180)
    reason = models.TextField()
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    current_total_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    revised_total_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_increase = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_decrease = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    margin_impact = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="construction_budget_requests")
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_budget_approvals")
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)

    class Meta:
        ordering = ["-request_date", "number"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="unique_construction_budget_change")]

    def __str__(self):
        return self.number


class ConstructionBudgetChangeLine(TimeStampedModel):
    request = models.ForeignKey(ConstructionBudgetChangeRequest, on_delete=models.CASCADE, related_name="lines")
    cost_sheet_item = models.ForeignKey(ConstructionCostSheetItem, on_delete=models.SET_NULL, blank=True, null=True, related_name="budget_changes")
    cost_sheet_resource = models.ForeignKey("ConstructionCostSheetResource", on_delete=models.SET_NULL, blank=True, null=True, related_name="budget_changes")
    sequence = models.PositiveIntegerField(default=10)
    line_type = models.CharField(max_length=16, choices=(("existing", "Existing"), ("new_wbs", "New WBS"), ("new_resource", "New Resource")), default="existing")
    change_type = models.CharField(max_length=20, choices=(("quantity", "Quantity"), ("rate", "Rate"), ("scope", "Scope"), ("zero", "Zero Out")), default="quantity")
    description = models.CharField(max_length=240)
    previous_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    proposed_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    current_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    revised_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    current_unit_rate = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    revised_unit_rate = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    new_wbs_code = models.CharField(max_length=80, blank=True)
    new_wbs_name = models.CharField(max_length=240, blank=True)
    new_description = models.TextField(blank=True)
    new_plan_start = models.DateField(blank=True, null=True)
    new_plan_end = models.DateField(blank=True, null=True)
    cost_category = models.CharField(max_length=20, blank=True)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_budget_change_lines")
    job_position = models.ForeignKey(JobPosition, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_budget_change_lines")
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_budget_change_lines")
    work_package = models.ForeignKey(WorkPackage, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_budget_change_lines")
    zeroed = models.BooleanField(default=False)

    class Meta:
        ordering = ["request", "id"]

    @property
    def variance(self):
        return self.proposed_amount - self.previous_amount

    def __str__(self):
        return f"{self.request.number} · {self.description}"


class ConstructionJobOrder(TimeStampedModel):
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="job_orders")
    work_package = models.ForeignKey(WorkPackage, on_delete=models.SET_NULL, blank=True, null=True, related_name="job_orders")
    cost_sheet = models.ForeignKey(ConstructionCostSheet, on_delete=models.PROTECT, blank=True, null=True, related_name="job_orders")
    cost_sheet_item = models.ForeignKey(ConstructionCostSheetItem, on_delete=models.PROTECT, blank=True, null=True, related_name="job_orders")
    cost_sheet_resource = models.ForeignKey("ConstructionCostSheetResource", on_delete=models.PROTECT, blank=True, null=True, related_name="job_orders")
    boq_item = models.ForeignKey(ConstructionBOQItem, on_delete=models.PROTECT, blank=True, null=True, related_name="job_orders")
    source_type = models.CharField(max_length=24, choices=(("cost_sheet_item", "Cost Sheet Item"), ("cost_sheet_resource", "Cost Sheet Resource"), ("manual", "Manual")), default="manual")
    execution_type = models.CharField(max_length=16, choices=(("internal", "Internal"), ("subcontract", "Subcontract")), default="internal")
    subcontractor = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_job_orders_as_subcontractor")
    subcontract_unit_rate = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    is_billable = models.BooleanField(default=True)
    commercial_progress_eligible = models.BooleanField(default=True)
    number = models.CharField(max_length=40)
    title = models.CharField(max_length=180)
    responsible = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_job_orders")
    planned_start = models.DateField()
    planned_end = models.DateField(blank=True, null=True)
    actual_start = models.DateField(blank=True, null=True)
    actual_end = models.DateField(blank=True, null=True)
    planned_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    completed_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    allocated_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    unit = models.CharField(max_length=24, default="Unit")
    progress = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    status = models.CharField(max_length=16, choices=ExecutionStatus.choices, default=ExecutionStatus.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-planned_start", "number"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="unique_construction_job_order")]

    def clean(self):
        if self.planned_end and self.planned_end < self.planned_start:
            raise ValidationError({"planned_end": "Planned end must be on or after planned start."})
        if self.actual_start and self.actual_end and self.actual_end < self.actual_start:
            raise ValidationError({"actual_end": "Actual end must be on or after actual start."})
        if self.work_package_id and self.project_id and self.work_package.construction_project_id != self.project_id:
            raise ValidationError({"work_package": "Work package must belong to the selected project."})

    def __str__(self):
        return self.number


class ConstructionProgressLog(TimeStampedModel):
    job_order = models.ForeignKey(ConstructionJobOrder, on_delete=models.CASCADE, related_name="progress_logs")
    log_date = models.DateField(default=timezone.localdate)
    progress = models.DecimalField(max_digits=5, decimal_places=2, validators=[MaxValueValidator(100)])
    completed_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    cumulative_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    evidence = models.FileField(upload_to="construction/progress/", blank=True)
    weather = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="construction_progress_logs")

    class Meta:
        ordering = ["-log_date", "-id"]

    def __str__(self):
        return f"{self.job_order.number} · {self.log_date}"


class ConstructionTaskWorker(TimeStampedModel):
    job_order = models.ForeignKey(ConstructionJobOrder, on_delete=models.CASCADE, related_name="workers")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="construction_assignments")
    role = models.CharField(max_length=120, blank=True)
    assigned_date = models.DateField(default=timezone.localdate)
    released_date = models.DateField(blank=True, null=True)
    planned_hours = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    actual_hours = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["job_order", "employee__name"]
        constraints = [models.UniqueConstraint(fields=["job_order", "employee"], name="unique_construction_task_worker")]

    def __str__(self):
        return f"{self.job_order.number} · {self.employee.name}"


class ConstructionQuantitySurvey(TimeStampedModel):
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="quantity_surveys")
    job_order = models.ForeignKey(ConstructionJobOrder, on_delete=models.SET_NULL, blank=True, null=True, related_name="quantity_surveys")
    contract = models.ForeignKey(ConstructionContract, on_delete=models.PROTECT, blank=True, null=True, related_name="quantity_surveys")
    boq = models.ForeignKey(ConstructionBOQ, on_delete=models.PROTECT, blank=True, null=True, related_name="quantity_surveys")
    number = models.CharField(max_length=40)
    survey_date = models.DateField(default=timezone.localdate)
    survey_start_date = models.DateField(blank=True, null=True)
    survey_end_date = models.DateField(blank=True, null=True)
    responsible_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="responsible_quantity_surveys")
    currency = models.CharField(max_length=3, default="IDR")
    surveyor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="construction_quantity_surveys")
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-survey_date", "number"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="unique_construction_quantity_survey")]

    def clean(self):
        if self.job_order_id and self.project_id and self.job_order.project_id != self.project_id:
            raise ValidationError({"job_order": "Job order must belong to the selected project."})

    def __str__(self):
        return self.number


class ConstructionQuantitySurveyLine(TimeStampedModel):
    survey = models.ForeignKey(ConstructionQuantitySurvey, on_delete=models.CASCADE, related_name="lines")
    boq_item = models.ForeignKey(ConstructionBOQItem, on_delete=models.PROTECT, related_name="survey_lines")
    sequence = models.PositiveIntegerField(default=10)
    previous_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    measured_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    accepted_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    contract_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    unit_rate = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    progress_percent = models.DecimalField(max_digits=7, decimal_places=2, default=0, editable=False)
    location = models.CharField(max_length=180, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["survey", "id"]

    def clean(self):
        if self.accepted_quantity > self.measured_quantity:
            raise ValidationError({"accepted_quantity": "Accepted quantity cannot exceed measured quantity."})

    def __str__(self):
        return f"{self.survey.number} · {self.boq_item.description}"


class ConstructionResourceActualization(TimeStampedModel):
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="resource_actualizations")
    job_order = models.ForeignKey(ConstructionJobOrder, on_delete=models.SET_NULL, blank=True, null=True, related_name="resource_actualizations")
    cost_sheet = models.ForeignKey(ConstructionCostSheet, on_delete=models.PROTECT, blank=True, null=True, related_name="resource_actualizations")
    cost_sheet_item = models.ForeignKey(ConstructionCostSheetItem, on_delete=models.PROTECT, blank=True, null=True, related_name="resource_actualizations")
    boq_item = models.ForeignKey(ConstructionBOQItem, on_delete=models.PROTECT, blank=True, null=True, related_name="resource_actualizations")
    source_type = models.CharField(max_length=24, choices=(("cost_sheet", "Cost Sheet"), ("job_order", "Job Order"), ("manual", "Manual")), default="manual")
    site_location = models.CharField(max_length=180, blank=True)
    actualized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_actualizations")
    number = models.CharField(max_length=40)
    actualization_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    notes = models.TextField(blank=True)
    budget_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_actual_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    variance_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    submitted_at = models.DateTimeField(blank=True, null=True)
    confirmed_at = models.DateTimeField(blank=True, null=True)
    approved_at = models.DateTimeField(blank=True, null=True)
    rejected_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-actualization_date", "number"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="unique_construction_resource_actualization")]

    def __str__(self):
        return self.number


class ConstructionResourceActualizationLine(TimeStampedModel):
    class ResourceType(models.TextChoices):
        MATERIAL = "material", "Material"
        LABOUR = "labour", "Labour"
        EQUIPMENT = "equipment", "Equipment"
        FACILITY = "facility", "Facility"

    actualization = models.ForeignKey(ConstructionResourceActualization, on_delete=models.CASCADE, related_name="lines")
    sequence = models.PositiveIntegerField(default=10)
    cost_sheet_resource = models.ForeignKey("ConstructionCostSheetResource", on_delete=models.PROTECT, blank=True, null=True, related_name="actualization_lines")
    job_order = models.ForeignKey(ConstructionJobOrder, on_delete=models.PROTECT, blank=True, null=True, related_name="actualization_lines")
    resource_type = models.CharField(max_length=16, choices=ResourceType.choices)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_resource_lines")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_resource_lines")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_resource_lines")
    description = models.CharField(max_length=240)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    revised_quantity_snapshot = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    revised_cost_snapshot = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    remaining_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    remaining_budget = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    source_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_actualization_source_lines")
    reference_document = models.CharField(max_length=120, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["actualization", "id"]

    @property
    def amount(self):
        return self.quantity * self.unit_cost

    def clean(self):
        selected = sum(bool(value) for value in (self.product_id, self.employee_id, self.asset_id))
        if selected > 1:
            raise ValidationError("Select at most one product, employee, or asset for a resource line.")

    def __str__(self):
        return f"{self.actualization.number} · {self.description}"


class ConstructionTimesheet(TimeStampedModel):
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="timesheets")
    number = models.CharField(max_length=40)
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_timesheet_approvals")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-period_start", "number"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="unique_construction_timesheet")]

    def clean(self):
        if self.period_end < self.period_start:
            raise ValidationError({"period_end": "Period end must be on or after period start."})

    def __str__(self):
        return self.number


class ConstructionTimesheetLine(TimeStampedModel):
    timesheet = models.ForeignKey(ConstructionTimesheet, on_delete=models.CASCADE, related_name="lines")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="construction_timesheet_lines")
    job_order = models.ForeignKey(ConstructionJobOrder, on_delete=models.PROTECT, related_name="timesheet_lines")
    work_date = models.DateField(default=timezone.localdate)
    hours = models.DecimalField(max_digits=6, decimal_places=2, validators=[MinValueValidator(Decimal("0.01")), MaxValueValidator(24)])
    description = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["-work_date", "employee__name"]

    def clean(self):
        if self.timesheet_id and self.job_order_id and self.timesheet.project_id != self.job_order.project_id:
            raise ValidationError({"job_order": "Job order must belong to the timesheet project."})
        if self.timesheet_id and self.work_date:
            if not self.timesheet.period_start <= self.work_date <= self.timesheet.period_end:
                raise ValidationError({"work_date": "Work date must be inside the timesheet period."})

    def __str__(self):
        return f"{self.timesheet.number} · {self.employee.name}"


class ConstructionProgressiveClaim(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        CERTIFIED = "certified", "Certified"
        INVOICED = "invoiced", "Invoiced"
        PAID = "paid", "Paid"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="progressive_claims")
    contract = models.ForeignKey(ConstructionContract, on_delete=models.PROTECT, related_name="progressive_claims")
    boq = models.ForeignKey(ConstructionBOQ, on_delete=models.PROTECT, blank=True, null=True, related_name="progressive_claims")
    quantity_survey = models.ForeignKey(ConstructionQuantitySurvey, on_delete=models.PROTECT, blank=True, null=True, related_name="progressive_claims")
    number = models.CharField(max_length=40)
    period_start = models.DateField()
    period_end = models.DateField()
    claim_date = models.DateField(default=timezone.localdate)
    claim_type = models.CharField(max_length=16, choices=(("owner", "Owner Claim"), ("subcontract", "Subcontract Claim")), default="owner")
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_progressive_claims")
    responsible_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="responsible_progressive_claims")
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="prepared_progressive_claims")
    currency = models.CharField(max_length=3, default="IDR")
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_progressive_claims")
    gross_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    retention_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    advance_recovery = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    retention2_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    certified_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-period_end", "number"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="unique_construction_progress_claim")]

    @property
    def net_amount(self):
        return self.gross_amount - self.retention_amount - self.advance_recovery

    def clean(self):
        if self.period_end < self.period_start:
            raise ValidationError({"period_end": "Period end must be on or after period start."})
        if self.contract_id and self.project_id and self.contract.project_id != self.project_id:
            raise ValidationError({"contract": "Contract must belong to the selected project."})

    def __str__(self):
        return self.number


class ConstructionProgressiveClaimLine(TimeStampedModel):
    claim = models.ForeignKey(ConstructionProgressiveClaim, on_delete=models.CASCADE, related_name="lines")
    boq_item = models.ForeignKey(ConstructionBOQItem, on_delete=models.PROTECT, related_name="claim_lines")
    sequence = models.PositiveIntegerField(default=10)
    quantity_survey_line = models.ForeignKey(ConstructionQuantitySurveyLine, on_delete=models.PROTECT, blank=True, null=True, related_name="claim_lines")
    job_orders = models.ManyToManyField(ConstructionJobOrder, blank=True, related_name="claim_lines")
    previous_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    current_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    certified_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    contract_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    progress_percent = models.DecimalField(max_digits=7, decimal_places=2, default=0, editable=False)
    source = models.CharField(max_length=16, choices=(("manual", "Manual"), ("qs", "Quantity Survey"), ("job_order", "Job Order")), default="manual")

    class Meta:
        ordering = ["claim", "id"]

    @property
    def current_amount(self):
        return self.current_quantity * self.unit_price

    def __str__(self):
        return f"{self.claim.number} · {self.boq_item.description}"


class ConstructionRiskRegister(TimeStampedModel):
    class Probability(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        MITIGATING = "mitigating", "Mitigating"
        CLOSED = "closed", "Closed"
        ACCEPTED = "accepted", "Accepted"

    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="risks")
    number = models.CharField(max_length=40)
    title = models.CharField(max_length=180)
    category = models.CharField(max_length=80, blank=True)
    probability = models.CharField(max_length=10, choices=Probability.choices, default=Probability.MEDIUM)
    impact = models.CharField(max_length=10, choices=Probability.choices, default=Probability.MEDIUM)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_risks")
    mitigation = models.TextField(blank=True)
    due_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["status", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="unique_construction_risk")]

    def __str__(self):
        return self.number


class ConstructionIssueLog(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="issues")
    risk = models.ForeignKey(ConstructionRiskRegister, on_delete=models.SET_NULL, blank=True, null=True, related_name="issues")
    number = models.CharField(max_length=40)
    title = models.CharField(max_length=180)
    description = models.TextField()
    priority = models.CharField(max_length=10, choices=ConstructionRiskRegister.Probability.choices, default=ConstructionRiskRegister.Probability.MEDIUM)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_issues")
    target_date = models.DateField(blank=True, null=True)
    resolution = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["status", "-created_at"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="unique_construction_issue")]

    def clean(self):
        if self.risk_id and self.project_id and self.risk.project_id != self.project_id:
            raise ValidationError({"risk": "Risk must belong to the selected project."})

    def __str__(self):
        return self.number


class ConstructionOpeningBalance(TimeStampedModel):
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="opening_balances")
    reference = models.CharField(max_length=40)
    balance_date = models.DateField(default=timezone.localdate)
    budget_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    committed_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    actual_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    source_note = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)

    class Meta:
        ordering = ["-balance_date", "reference"]
        constraints = [models.UniqueConstraint(fields=["project", "reference"], name="unique_construction_opening_balance")]

    def __str__(self):
        return self.reference


class ConstructionProjectAccountSettings(TimeStampedModel):
    project = models.OneToOneField(ConstructionProject, on_delete=models.CASCADE, related_name="account_settings")
    revenue_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="construction_revenue_settings")
    cost_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="construction_cost_settings")
    work_in_progress_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="construction_wip_settings")
    retention_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="construction_retention_settings")
    owner_down_payment_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_owner_dp_settings")
    owner_claim_request_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_owner_claim_settings")
    owner_penalty_receivable_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_owner_penalty_settings")
    subcontract_cost_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_subcontract_cost_settings")
    subcontract_down_payment_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_subcontract_dp_settings")
    subcontract_claim_request_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_subcontract_claim_settings")
    subcontract_retention_payable_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_subcontract_retention_settings")
    subcontract_penalty_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_subcontract_penalty_settings")
    alternative_cip_account = models.ForeignKey(Account, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_alternative_cip_settings")
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Construction project account settings"
        ordering = ["project__code"]

    def clean(self):
        if not self.project_id:
            return
        account_company_ids = {
            account.company_id
            for account in (
                self.revenue_account, self.cost_account, self.work_in_progress_account, self.retention_account,
                self.owner_down_payment_account, self.owner_claim_request_account, self.owner_penalty_receivable_account,
                self.subcontract_cost_account, self.subcontract_down_payment_account,
                self.subcontract_claim_request_account, self.subcontract_retention_payable_account,
                self.subcontract_penalty_account, self.alternative_cip_account,
            )
            if account
        }
        if account_company_ids and account_company_ids != {self.project.company_id}:
            raise ValidationError("All accounts must belong to the construction project company.")

    def __str__(self):
        return f"Accounts · {self.project.code}"


class ConstructionMaterialDemand(TimeStampedModel):
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="material_demands")
    job_order = models.ForeignKey(ConstructionJobOrder, on_delete=models.SET_NULL, blank=True, null=True, related_name="material_demands")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="construction_material_demands")
    number = models.CharField(max_length=40)
    request_date = models.DateField(default=timezone.localdate)
    required_date = models.DateField()
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="construction_material_demands")
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-request_date", "number"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="unique_construction_material_demand")]

    def clean(self):
        if self.required_date < self.request_date:
            raise ValidationError({"required_date": "Required date must be on or after request date."})
        if self.warehouse_id and self.project_id and self.warehouse.company_id != self.project.company_id:
            raise ValidationError({"warehouse": "Warehouse and project must belong to the same company."})

    def __str__(self):
        return self.number


class ConstructionMaterialDemandLine(TimeStampedModel):
    demand = models.ForeignKey(ConstructionMaterialDemand, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="construction_material_demands")
    description = models.CharField(max_length=240, blank=True)
    requested_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    allocated_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    delivered_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)

    class Meta:
        ordering = ["demand", "id"]

    def clean(self):
        if self.allocated_quantity > self.requested_quantity:
            raise ValidationError({"allocated_quantity": "Allocated quantity cannot exceed requested quantity."})
        if self.delivered_quantity > self.allocated_quantity:
            raise ValidationError({"delivered_quantity": "Delivered quantity cannot exceed allocated quantity."})

    def __str__(self):
        return f"{self.demand.number} · {self.product.sku}"


class ConstructionProcurementAllocation(TimeStampedModel):
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="procurement_allocations")
    boq_item = models.ForeignKey(ConstructionBOQItem, on_delete=models.SET_NULL, blank=True, null=True, related_name="procurement_allocations")
    purchase_request = models.ForeignKey(PurchaseRequest, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_allocations")
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_allocations")
    description = models.CharField(max_length=240)
    allocated_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    allocated_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    budget_acknowledged = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["project", "id"]

    def clean(self):
        if self.boq_item_id and self.project_id and self.boq_item.boq.project_id != self.project_id:
            raise ValidationError({"boq_item": "BOQ item must belong to the selected project."})

    def __str__(self):
        return f"{self.project.code} · {self.description}"


class ConstructionSubcontractJobOrder(TimeStampedModel):
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="subcontract_job_orders")
    job_order = models.ForeignKey(ConstructionJobOrder, on_delete=models.SET_NULL, blank=True, null=True, related_name="subcontracts")
    number = models.CharField(max_length=40)
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="construction_subcontracts")
    scope = models.TextField()
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    contract_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    progress = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    status = models.CharField(max_length=16, choices=ExecutionStatus.choices, default=ExecutionStatus.DRAFT)

    class Meta:
        ordering = ["-start_date", "number"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="unique_construction_subcontract_jo")]

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date must be on or after start date."})
        if self.vendor_id and self.project_id and self.vendor.company_id != self.project.company_id:
            raise ValidationError({"vendor": "Vendor and project must belong to the same company."})

    def __str__(self):
        return self.number


class ConstructionBOQTemplateCostLine(TimeStampedModel):
    class CostCategory(models.TextChoices):
        MATERIAL = "material", "Material"
        LABOUR = "labour", "Labour"
        EQUIPMENT = "equipment", "Equipment"
        SUBCONTRACT = "subcontract", "Subcontract"
        FACILITY = "facility", "Facility"
        OTHER = "other", "Other"

    template_item = models.ForeignKey(ConstructionBOQTemplateItem, on_delete=models.CASCADE, related_name="cost_lines")
    sequence = models.PositiveIntegerField(default=10)
    input_type = models.CharField(max_length=16, choices=(("product", "Product"), ("manual", "Manual")), default="product")
    cost_category = models.CharField(max_length=16, choices=CostCategory.choices, default=CostCategory.MATERIAL)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_template_cost_lines")
    job_position = models.ForeignKey(JobPosition, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_template_cost_lines")
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_template_cost_lines")
    work_package = models.ForeignKey(WorkPackage, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_template_cost_lines")
    description = models.CharField(max_length=240)
    ownership = models.CharField(max_length=16, choices=(("owned", "Owned"), ("rented", "Rented"), ("subcontract", "Subcontract")), default="owned")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit = models.CharField(max_length=24, default="Unit")
    unit_rate = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["template_item", "sequence", "id"]

    @property
    def amount(self):
        return self.quantity * self.unit_rate

    def clean(self):
        if self.input_type == "product" and not self.product_id:
            raise ValidationError({"product": "Product is required for a product resource."})

    def __str__(self):
        return f"{self.template_item} · {self.description}"


class ConstructionContractDPLine(TimeStampedModel):
    contract = models.ForeignKey(ConstructionContract, on_delete=models.CASCADE, related_name="down_payment_lines")
    sequence = models.PositiveIntegerField(default=10)
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_dp_lines")
    invoice_date = models.DateField(blank=True, null=True)
    portion_percent = models.DecimalField(max_digits=7, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    invoice_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    payment_status = models.CharField(max_length=20, choices=(("pending", "Pending"), ("invoiced", "Invoiced"), ("paid", "Paid")), default="pending")

    class Meta:
        ordering = ["contract", "sequence", "id"]

    def clean(self):
        if self.invoice_id and self.invoice.company_id != self.contract.project.company_id:
            raise ValidationError({"invoice": "Invoice must belong to the contract company."})

    def __str__(self):
        return f"{self.contract.number} · DP {self.sequence}"


class ConstructionBOQCostLine(TimeStampedModel):
    boq_item = models.ForeignKey(ConstructionBOQItem, on_delete=models.CASCADE, related_name="cost_lines")
    sequence = models.PositiveIntegerField(default=10)
    input_type = models.CharField(max_length=16, choices=(("product", "Product"), ("manual", "Manual")), default="product")
    cost_category = models.CharField(max_length=16, choices=ConstructionBOQTemplateCostLine.CostCategory.choices, default="material")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_boq_cost_lines")
    job_position = models.ForeignKey(JobPosition, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_boq_cost_lines")
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_boq_cost_lines")
    work_package = models.ForeignKey(WorkPackage, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_boq_cost_lines")
    description = models.CharField(max_length=240)
    ownership = models.CharField(max_length=16, choices=(("owned", "Owned"), ("rented", "Rented"), ("subcontract", "Subcontract")), default="owned")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit = models.CharField(max_length=24, default="Unit")
    unit_rate = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    markup_type = models.CharField(max_length=12, choices=(("percent", "Percentage"), ("fixed", "Fixed")), default="percent")
    markup_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    discount_type = models.CharField(max_length=12, choices=(("percent", "Percentage"), ("fixed", "Fixed")), default="percent")
    discount_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tax_percent = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    revenue_including_tax = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["boq_item", "sequence", "id"]

    def clean(self):
        if self.input_type == "product" and not self.product_id:
            raise ValidationError({"product": "Product is required for a product resource."})

    def __str__(self):
        return f"{self.boq_item} · {self.description}"


class ConstructionCostSheetResource(TimeStampedModel):
    cost_sheet_item = models.ForeignKey(ConstructionCostSheetItem, on_delete=models.CASCADE, related_name="resources")
    boq_resource = models.ForeignKey(ConstructionBOQCostLine, on_delete=models.SET_NULL, blank=True, null=True, related_name="cost_sheet_resources")
    sequence = models.PositiveIntegerField(default=10)
    cost_category = models.CharField(max_length=16, choices=ConstructionBOQTemplateCostLine.CostCategory.choices, default="material")
    input_type = models.CharField(max_length=16, choices=(("product", "Product"), ("manual", "Manual")), default="product")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_cost_sheet_resources")
    job_position = models.ForeignKey(JobPosition, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_cost_sheet_resources")
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_cost_sheet_resources")
    work_package = models.ForeignKey(WorkPackage, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_cost_sheet_resources")
    description = models.CharField(max_length=240)
    unit = models.CharField(max_length=24, default="Unit")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit_rate = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    budget_cost_adjustment = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    revised_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    ownership = models.CharField(max_length=16, choices=(("owned", "Owned"), ("rented", "Rented"), ("subcontract", "Subcontract")), default="owned")
    inherited_from_boq = models.BooleanField(default=False)
    reserved_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    delivered_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    utilised_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    utilised_budget = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    onsite_stock_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    procurement_quantity_compatible = models.BooleanField(default=True)
    opening_balance_reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["cost_sheet_item", "sequence", "id"]

    @property
    def total_cost(self):
        return self.quantity * self.unit_rate

    @property
    def balance_quantity(self):
        return self.quantity - self.reserved_quantity - self.utilised_quantity

    def clean(self):
        if self.delivered_quantity > self.reserved_quantity:
            raise ValidationError({"delivered_quantity": "Delivered quantity cannot exceed reserved quantity."})
        if self.input_type == "product" and not self.product_id:
            raise ValidationError({"product": "Product is required for a product resource."})

    def __str__(self):
        return f"{self.cost_sheet_item} · {self.description}"


class ConstructionEquipmentFacility(TimeStampedModel):
    job_order = models.ForeignKey(ConstructionJobOrder, on_delete=models.CASCADE, related_name="equipment_facilities")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="construction_equipment_allocations")
    category = models.CharField(max_length=80, blank=True)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    planned_hours = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    actual_hours = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["job_order", "start_date", "id"]

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date must be on or after start date."})
        if self.asset_id and self.job_order_id and self.asset.company_id != self.job_order.project.company_id:
            raise ValidationError({"asset": "Asset and job order must belong to the same company."})

    def __str__(self):
        return f"{self.job_order.number} · {self.asset}"


class ConstructionQuantitySurveyJOSource(TimeStampedModel):
    survey_line = models.ForeignKey(ConstructionQuantitySurveyLine, on_delete=models.CASCADE, related_name="job_order_sources")
    progress_log = models.ForeignKey(ConstructionProgressLog, on_delete=models.PROTECT, related_name="quantity_survey_sources")
    approved_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    previous_approved_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["survey_line", "progress_log__log_date", "id"]
        constraints = [models.UniqueConstraint(fields=["survey_line", "progress_log"], name="unique_qs_progress_source")]

    @property
    def available_quantity(self):
        return max(self.progress_log.completed_quantity - self.previous_approved_quantity, Decimal("0"))

    def clean(self):
        if self.approved_quantity > self.available_quantity:
            raise ValidationError({"approved_quantity": "Approved quantity exceeds available progress quantity."})

    def __str__(self):
        return f"{self.survey_line} · {self.progress_log}"


class ConstructionProgressiveClaimCumulative(TimeStampedModel):
    claim = models.ForeignKey(ConstructionProgressiveClaim, on_delete=models.CASCADE, related_name="cumulative_lines")
    sequence = models.PositiveIntegerField(default=10)
    boq_item = models.ForeignKey(ConstructionBOQItem, on_delete=models.PROTECT, related_name="claim_cumulative_lines")
    previous_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    this_period_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    cumulative_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    contract_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    progress_percent = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    cumulative_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    invoice_reference = models.CharField(max_length=80, blank=True)
    display_status = models.CharField(max_length=20, blank=True)
    is_current_claim = models.BooleanField(default=False)

    class Meta:
        ordering = ["claim", "sequence", "id"]

    @property
    def remaining_quantity(self):
        return self.contract_quantity - self.cumulative_quantity

    def __str__(self):
        return f"{self.claim.number} · {self.boq_item}"


class ConstructionProgressiveClaimAttachment(TimeStampedModel):
    claim = models.ForeignKey(ConstructionProgressiveClaim, on_delete=models.CASCADE, related_name="attachments")
    sequence = models.PositiveIntegerField(default=10)
    document_name = models.CharField(max_length=180)
    document_type = models.CharField(max_length=60, blank=True)
    document_date = models.DateField(default=timezone.localdate)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="construction_claim_attachments")
    file = models.FileField(upload_to="construction/claims/")

    class Meta:
        ordering = ["claim", "sequence", "id"]

    def __str__(self):
        return f"{self.claim.number} · {self.document_name}"


class ConstructionProjectProfile(TimeStampedModel):
    project = models.OneToOneField(ConstructionProject, on_delete=models.CASCADE, related_name="construction_profile")
    project_type = models.CharField(max_length=32, blank=True)
    construction_type = models.CharField(max_length=32, blank=True)
    owner_client = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="owned_construction_projects")
    financial_progress = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    warehouses = models.ManyToManyField(Warehouse, blank=True, related_name="construction_projects")
    account_snapshot_ready = models.BooleanField(default=False)
    account_snapshot_locked = models.BooleanField(default=False)
    account_snapshot_note = models.TextField(blank=True)

    class Meta:
        ordering = ["project__code"]

    def __str__(self):
        return f"Profile · {self.project.code}"


class ConstructionProjectAccountSettingsLine(TimeStampedModel):
    settings = models.ForeignKey(ConstructionProjectAccountSettings, on_delete=models.CASCADE, related_name="cip_lines")
    resource_category = models.CharField(max_length=16, choices=ConstructionBOQTemplateCostLine.CostCategory.choices)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="construction_cip_setting_lines")
    usage_help = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["settings", "resource_category", "id"]
        constraints = [models.UniqueConstraint(fields=["settings", "resource_category"], name="unique_cip_account_category")]

    def clean(self):
        if self.settings_id and self.account_id and self.settings.project.company_id != self.account.company_id:
            raise ValidationError({"account": "Account must belong to the project company."})

    def __str__(self):
        return f"{self.settings.project.code} · {self.resource_category}"


class ConstructionProjectAccountSnapshotLine(TimeStampedModel):
    project = models.ForeignKey(ConstructionProject, on_delete=models.CASCADE, related_name="account_snapshot_lines")
    resource_category = models.CharField(max_length=32)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="construction_project_snapshots")
    account_display = models.CharField(max_length=240)
    account_type_label = models.CharField(max_length=80, blank=True)
    usage_help = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["project", "resource_category", "id"]
        constraints = [models.UniqueConstraint(fields=["project", "resource_category"], name="unique_project_account_snapshot_category")]

    def __str__(self):
        return f"{self.project.code} · {self.resource_category}"


class ConstructionOpeningBalanceImport(TimeStampedModel):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        VALIDATED = "validated", "Validated"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    name = models.CharField(max_length=80)
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="opening_balance_imports")
    source_type = models.CharField(max_length=16, choices=(("file", "File"), ("json", "JSON"), ("manual", "Manual")), default="file")
    source_file = models.FileField(upload_to="construction/opening-balances/", blank=True)
    source_checksum = models.CharField(max_length=64, blank=True)
    source_reference = models.CharField(max_length=120, blank=True)
    cutoff_date = models.DateField()
    canonical_payload = models.JSONField(default=dict, blank=True)
    validation_snapshot = models.JSONField(default=dict, blank=True)
    reconciliation_snapshot = models.JSONField(default=dict, blank=True)
    fingerprint = models.CharField(max_length=64, blank=True, db_index=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.DRAFT)
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="construction_opening_balance_imports")
    attempted_count = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    created_links = models.JSONField(default=list, blank=True)
    error_report = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "name"]

    def __str__(self):
        return self.name


class ConstructionOpeningBalanceExportSnapshot(TimeStampedModel):
    export_token = models.CharField(max_length=64, unique=True)
    schema_version = models.CharField(max_length=20, default="1.0")
    exported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="construction_opening_balance_exports")
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="opening_balance_exports")
    boq = models.ForeignKey(ConstructionBOQ, on_delete=models.SET_NULL, blank=True, null=True, related_name="opening_balance_exports")
    cutoff_date = models.DateField()
    baseline = models.JSONField(default=dict)
    baseline_digest = models.CharField(max_length=64)
    state = models.CharField(max_length=16, choices=(("active", "Active"), ("consumed", "Consumed"), ("revoked", "Revoked")), default="active")
    consumed_by = models.ForeignKey(ConstructionOpeningBalanceImport, on_delete=models.SET_NULL, blank=True, null=True, related_name="consumed_snapshots")
    consumed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.export_token


class ConstructionBOQImportLog(TimeStampedModel):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        VALIDATED = "validated", "Validated"
        IMPORTED = "imported", "Imported"
        FAILED = "failed", "Failed"

    boq = models.ForeignKey(ConstructionBOQ, on_delete=models.CASCADE, related_name="import_logs")
    source_type = models.CharField(max_length=16, choices=(("file", "File"), ("json", "JSON"), ("ai", "AI-Assisted")), default="file")
    source_file = models.FileField(upload_to="construction/boq-imports/", blank=True)
    source_metadata = models.JSONField(default=dict, blank=True)
    source_checksum = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=list, blank=True)
    normalized_payload = models.JSONField(default=list, blank=True)
    validation_errors = models.JSONField(default=list, blank=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.DRAFT)
    attempted_count = models.PositiveIntegerField(default=0)
    imported_count = models.PositiveIntegerField(default=0)
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="construction_boq_imports")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.boq.number} · {self.created_at:%Y-%m-%d %H:%M}"


class ConstructionProcurementBudgetAcknowledgement(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="construction_budget_acknowledgements")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="construction_budget_acknowledgements")
    model_name = models.CharField(max_length=80)
    operation = models.CharField(max_length=40)
    document_fingerprint = models.CharField(max_length=64)
    policy_fingerprint = models.CharField(max_length=64)
    exposure_fingerprint = models.CharField(max_length=64)
    snapshot = models.JSONField(default=dict)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    used_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.model_name}:{self.operation}:{self.user}"


class ConstructionProcurementBudgetAudit(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="construction_budget_audits")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="construction_budget_audits")
    document_model = models.CharField(max_length=80)
    document_id = models.PositiveIntegerField()
    document_name = models.CharField(max_length=160)
    operation = models.CharField(max_length=40)
    state_before = models.CharField(max_length=32, blank=True)
    state_after = models.CharField(max_length=32, blank=True)
    decision = models.CharField(max_length=16, choices=(("allow", "Allow"), ("warn", "Warn"), ("block", "Block")))
    acknowledged = models.BooleanField(default=False)
    document_fingerprint = models.CharField(max_length=64)
    policy_fingerprint = models.CharField(max_length=64)
    exposure_fingerprint = models.CharField(max_length=64)
    snapshot = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.document_name} · {self.decision}"


class ConstructionProcurementLine(TimeStampedModel):
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="procurement_lines")
    cost_sheet = models.ForeignKey(ConstructionCostSheet, on_delete=models.PROTECT, related_name="procurement_lines")
    cost_sheet_item = models.ForeignKey(ConstructionCostSheetItem, on_delete=models.PROTECT, related_name="procurement_lines")
    cost_sheet_resource = models.ForeignKey(ConstructionCostSheetResource, on_delete=models.PROTECT, related_name="procurement_lines")
    purchase_request_line = models.ForeignKey(PurchaseRequestLine, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_procurement_lines")
    purchase_order_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_procurement_lines")
    reserved_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    ordered_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    received_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    unit_rate = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    budget_decision = models.CharField(max_length=16, choices=(("allow", "Allow"), ("warn", "Warn"), ("block", "Block")), default="allow")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["project", "cost_sheet", "id"]

    @property
    def committed_amount(self):
        return self.ordered_quantity * self.unit_rate

    def clean(self):
        errors = {}
        if self.cost_sheet_id and self.project_id and self.cost_sheet.project_id != self.project_id:
            errors["cost_sheet"] = "Cost sheet must belong to the selected project."
        if self.cost_sheet_item_id and self.cost_sheet_id and self.cost_sheet_item.cost_sheet_id != self.cost_sheet_id:
            errors["cost_sheet_item"] = "Cost sheet item must belong to the selected cost sheet."
        if self.cost_sheet_resource_id and self.cost_sheet_item_id and self.cost_sheet_resource.cost_sheet_item_id != self.cost_sheet_item_id:
            errors["cost_sheet_resource"] = "Resource must belong to the selected cost sheet item."
        if self.received_quantity > self.ordered_quantity:
            errors["received_quantity"] = "Received quantity cannot exceed ordered quantity."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.project.code} · {self.cost_sheet_resource.description}"


class ConstructionInventoryTransfer(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready"
        IN_TRANSIT = "in_transit", "In Transit"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="inventory_transfers")
    number = models.CharField(max_length=40)
    source_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="construction_outbound_transfers")
    destination_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="construction_inbound_transfers")
    transfer_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_inventory_transfers")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-transfer_date", "number"]
        constraints = [models.UniqueConstraint(fields=["project", "number"], name="unique_construction_inventory_transfer")]

    def clean(self):
        company_id = self.project.company_id if self.project_id else None
        if company_id and (self.source_warehouse.company_id != company_id or self.destination_warehouse.company_id != company_id):
            raise ValidationError("Project and both warehouses must belong to the same company.")
        if self.source_warehouse_id == self.destination_warehouse_id:
            raise ValidationError({"destination_warehouse": "Destination warehouse must differ from source."})

    def __str__(self):
        return self.number


class ConstructionInventoryTransferLine(TimeStampedModel):
    transfer = models.ForeignKey(ConstructionInventoryTransfer, on_delete=models.CASCADE, related_name="lines")
    material_demand_line = models.ForeignKey(ConstructionMaterialDemandLine, on_delete=models.SET_NULL, blank=True, null=True, related_name="transfer_lines")
    cost_sheet_resource = models.ForeignKey(ConstructionCostSheetResource, on_delete=models.SET_NULL, blank=True, null=True, related_name="transfer_lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="construction_inventory_transfer_lines")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1, validators=[MinValueValidator(Decimal("0.001"))])
    stock_move_out = models.ForeignKey(StockMove, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_transfer_out_lines")
    stock_move_in = models.ForeignKey(StockMove, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_transfer_in_lines")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["transfer", "id"]

    def clean(self):
        if self.product_id and self.transfer_id and self.product.company_id != self.transfer.project.company_id:
            raise ValidationError({"product": "Product and project must belong to the same company."})

    def __str__(self):
        return f"{self.transfer.number} · {self.product}"


class ConstructionSubcontractProgressLine(TimeStampedModel):
    subcontract = models.ForeignKey(ConstructionSubcontractJobOrder, on_delete=models.CASCADE, related_name="progress_lines")
    progress_date = models.DateField(default=timezone.localdate)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    cumulative_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    progress_percent = models.DecimalField(max_digits=7, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    evidence = models.FileField(upload_to="construction/subcontracts/progress/", blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["subcontract", "progress_date", "id"]

    def __str__(self):
        return f"{self.subcontract.number} · {self.progress_date}"


class ConstructionSubcontractMilestone(TimeStampedModel):
    subcontract = models.ForeignKey(ConstructionSubcontractJobOrder, on_delete=models.CASCADE, related_name="milestones")
    sequence = models.PositiveIntegerField(default=10)
    name = models.CharField(max_length=180)
    percentage = models.DecimalField(max_digits=7, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    due_date = models.DateField(blank=True, null=True)
    achieved = models.BooleanField(default=False)
    achieved_date = models.DateField(blank=True, null=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, blank=True, null=True, related_name="construction_subcontract_milestones")

    class Meta:
        ordering = ["subcontract", "sequence", "id"]

    def __str__(self):
        return f"{self.subcontract.number} · {self.name}"


class ConstructionAccountingPosting(TimeStampedModel):
    class PostingType(models.TextChoices):
        OWNER_CLAIM = "owner_claim", "Owner Claim"
        SUBCONTRACT_CLAIM = "subcontract_claim", "Subcontract Claim"
        RESOURCE_ACTUALIZATION = "resource_actualization", "Resource Actualization"
        RETENTION = "retention", "Retention"
        DOWN_PAYMENT = "down_payment", "Down Payment"

    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="accounting_postings")
    posting_type = models.CharField(max_length=32, choices=PostingType.choices)
    reference = models.CharField(max_length=80)
    posting_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    progressive_claim = models.ForeignKey(ConstructionProgressiveClaim, on_delete=models.PROTECT, blank=True, null=True, related_name="accounting_postings")
    actualization = models.ForeignKey(ConstructionResourceActualization, on_delete=models.PROTECT, blank=True, null=True, related_name="accounting_postings")
    contract = models.ForeignKey(ConstructionContract, on_delete=models.PROTECT, blank=True, null=True, related_name="accounting_postings")
    journal_entry = models.OneToOneField(JournalEntry, on_delete=models.PROTECT, blank=True, null=True, related_name="construction_posting")
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="construction_accounting_postings")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-posting_date", "reference"]
        constraints = [models.UniqueConstraint(fields=["project", "posting_type", "reference"], name="unique_construction_accounting_posting")]

    def clean(self):
        sources = sum(bool(value) for value in (self.progressive_claim_id, self.actualization_id, self.contract_id))
        if sources != 1:
            raise ValidationError("Select exactly one construction source document.")
        if self.journal_entry_id and self.journal_entry.company_id != self.project.company_id:
            raise ValidationError({"journal_entry": "Journal entry must belong to the project company."})

    def __str__(self):
        return f"{self.reference} · {self.get_posting_type_display()}"
