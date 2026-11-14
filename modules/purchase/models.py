from calendar import monthrange
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import (
    Company,
    Contact,
    InvoiceLine,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    StockMove,
    TimeStampedModel,
    Warehouse,
)


def current_year():
    return timezone.localdate().year


def add_calendar_months(value, months):
    """Return the HMX-style calendar-month expiry without adding a dependency."""
    month_index = value.month - 1 + int(months)
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, monthrange(year, month)[1]))


def validate_purchase_configuration_company(instance, errors):
    order_type_id = getattr(instance, "order_type_id", None)
    order_template_id = getattr(instance, "order_template_id", None)
    if order_type_id and instance.company_id:
        order_type = instance.order_type
        if order_type.company_id != instance.company_id and not order_type.companies.filter(pk=instance.company_id).exists():
            errors["order_type"] = "Order Type must be available to the selected company."
    if order_template_id and instance.company_id and instance.order_template.company_id != instance.company_id:
        errors["order_template"] = "Order Template must belong to the selected company."


class ApprovalStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class ThreeWayMatchStatus(models.TextChoices):
    AWAITING_RECEIPT = "awaiting_receipt", "Awaiting Receipt"
    PARTIALLY_RECEIVED = "partially_received", "Partially Received"
    PARTIALLY_MATCHED = "partially_matched", "Partially Matched"
    FULLY_MATCHED = "fully_matched", "Fully Matched"
    RECEIPT_EXCEPTION = "receipt_exception", "Receipt Exception"
    BILLING_EXCEPTION = "billing_exception", "Billing Exception"
    PRICE_EXCEPTION = "price_exception", "Price Exception"


THREE_WAY_MATCH_STATUS_PRECEDENCE = {
    ThreeWayMatchStatus.AWAITING_RECEIPT: 1,
    ThreeWayMatchStatus.PARTIALLY_RECEIVED: 2,
    ThreeWayMatchStatus.PARTIALLY_MATCHED: 3,
    ThreeWayMatchStatus.FULLY_MATCHED: 4,
    ThreeWayMatchStatus.RECEIPT_EXCEPTION: 5,
    ThreeWayMatchStatus.BILLING_EXCEPTION: 6,
    ThreeWayMatchStatus.PRICE_EXCEPTION: 7,
}


PURCHASE_KPI_DEFINITIONS = {
    "savings_percent": {
        "label": "Savings %",
        "unit": "Percent",
        "direction": "higher_is_better",
        "calculation": "((Budget - Actual) / Budget) x 100",
    },
    "purchase_price_variance": {
        "label": "Purchase Price Variance",
        "unit": "%",
        "direction": "lower_is_better",
        "calculation": "(Actual - Baseline) / Baseline",
    },
    "on_time_po_rate": {
        "label": "On-Time Purchase Order",
        "unit": "%",
        "direction": "higher_is_better",
        "calculation": "On-time PO / Total PO",
    },
    "pr_to_po_cycle_time": {
        "label": "PR to PO Cycle Time",
        "unit": "Days",
        "direction": "lower_is_better",
        "calculation": "PO Date - PR Approved Date",
    },
    "cycle_time": {
        "label": "Cycle Time (days)",
        "unit": "Days",
        "direction": "lower_is_better",
        "calculation": "Average confirmation cycle time",
    },
    "po_count": {
        "label": "PO Count",
        "unit": "Count",
        "direction": "higher_is_better",
        "calculation": "Confirmed purchase order count",
    },
    "supplier_compliance": {
        "label": "Supplier Compliance",
        "unit": "%",
        "direction": "higher_is_better",
        "calculation": "Compliant receipts / Total receipts",
    },
}
PURCHASE_KPI_METRIC_CHOICES = [
    (key, definition["label"]) for key, definition in PURCHASE_KPI_DEFINITIONS.items()
]


class PurchaseCostCenterStructuredFields(models.Model):
    """Portable purchase-side representation of HMX analytic assignments."""

    class Mode(models.TextChoices):
        SINGLE = "single", "Single Cost Center"
        DISTRIBUTION = "distribution", "Distribution"

    cost_center_mode = models.CharField(
        max_length=16,
        choices=Mode.choices,
        default=Mode.SINGLE,
        blank=True,
    )
    cost_center_distribution_ids = models.JSONField(default=list, blank=True)
    cost_center_distribution_data = models.JSONField(default=list, blank=True)
    cost_center_analytic_tag_ids = models.JSONField(default=list, blank=True)

    class Meta:
        abstract = True


class PurchaseRequest(PurchaseCostCenterStructuredFields, TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending Approval"
        REJECTED = "rejected", "Rejected"
        PARTIALLY_APPROVED = "partial_approved", "Partially Approved"
        APPROVED = "approved", "Approved"
        PARTIALLY_RFQ = "partial_rfq", "Partially RFQ"
        RFQ = "rfq", "RFQ Created"
        PARTIALLY_ORDERED = "partial_ordered", "Partially Ordered"
        FULLY_ORDERED = "fully_ordered", "Fully Ordered"
        IN_PROGRESS = "in_progress", "In Progress"
        CLOSED = "closed", "Closed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_requests")
    order_type = models.ForeignKey(
        "PurchaseOrderType",
        on_delete=models.PROTECT,
        related_name="purchase_requests",
        blank=True,
        null=True,
    )
    team = models.ForeignKey(
        "PurchaseTeam",
        on_delete=models.SET_NULL,
        related_name="requests",
        blank=True,
        null=True,
    )
    number = models.CharField(max_length=40)
    requestor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_requests")
    request_date = models.DateField(default=timezone.localdate)
    required_date = models.DateField(blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)
    destination_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name="purchase_requests",
        null=True,
        blank=True,
        help_text="Default destination warehouse carried into RFQs and purchase orders.",
    )
    currency = models.CharField(max_length=3, default="IDR")
    cost_center = models.CharField(max_length=80, blank=True)
    justification = models.TextField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    rejection_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    remark_template = models.ForeignKey(
        "PurchaseRemarkTemplate",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="purchase_requests",
    )
    remark_template_code = models.CharField(max_length=64, blank=True, editable=False)
    remark_template_name = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        ordering = ["-request_date", "number"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="unique_purchase_request_number_per_company")
        ]

    def clean(self):
        self.apply_expiry_default()
        errors = {}
        validate_purchase_configuration_company(self, errors)
        if self.team_id and self.team.company_id != self.company_id:
            errors["team"] = "Purchase Team must belong to the selected company."
        if self.required_date and self.required_date < self.request_date:
            errors["required_date"] = "Required date must be on or after request date."
        if self.expiry_date and self.expiry_date <= self.request_date:
            errors["expiry_date"] = "Expiry date must be after request date."
        if (
            self.destination_warehouse_id
            and self.company_id
            and self.destination_warehouse.company_id != self.company_id
        ):
            errors["destination_warehouse"] = "Destination warehouse must belong to the selected company."
        if errors:
            raise ValidationError(errors)

    def apply_expiry_default(self):
        if self.expiry_date or not self.company_id:
            return
        period_days = PurchaseSetting.objects.filter(company_id=self.company_id).values_list(
            "pr_expiry_period_days",
            flat=True,
        ).first()
        if period_days:
            self.expiry_date = self.request_date + timedelta(days=period_days)

    def save(self, *args, **kwargs):
        self.apply_expiry_default()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and self.expiry_date:
            kwargs["update_fields"] = set(update_fields) | {"expiry_date"}
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.number


class PurchaseRequestLine(PurchaseCostCenterStructuredFields, TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending"
        REJECTED = "rejected", "Rejected"
        PARTIALLY_APPROVED = "partially_approved", "Partially Approved"
        APPROVED = "approved", "Approved"
        PARTIALLY_RFQ = "partially_rfq", "Partially RFQ"
        RFQ = "rfq", "RFQ"
        PARTIALLY_ORDERED = "partially_ordered", "Partially Ordered"
        FULLY_ORDERED = "fully_ordered", "Fully Ordered"
        IN_PROGRESS = "in_progress", "In Progress"
        CLOSED = "closed", "Closed"
        EXPIRED = "expired", "Expired"

    request = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, related_name="lines")
    origin_request_line = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="re_requested_lines",
        editable=False,
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="purchase_request_lines")
    description = models.CharField(max_length=240)
    specification = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1, validators=[MinValueValidator(Decimal("0.001"))])
    approved_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    rfq_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0, editable=False)
    blanket_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0, editable=False)
    tender_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0, editable=False)
    direct_purchase_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0, editable=False)
    re_request_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0, editable=False)
    ordered_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    estimated_unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cost_center = models.CharField(max_length=80, blank=True)
    expected_date = models.DateField(blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)
    destination_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="purchase_request_lines",
    )
    line_status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT, editable=False)
    approval_justification = models.TextField(blank=True)
    rejection_reason = models.CharField(max_length=120, blank=True)
    approval_action = models.CharField(
        max_length=12,
        choices=[("approve", "Approve"), ("reject", "Reject")],
        blank=True,
        editable=False,
    )

    class Meta:
        ordering = ["id"]

    def clean(self):
        if self.request_id:
            self.expected_date = self.expected_date or self.request.required_date
            self.destination_warehouse_id = (
                self.destination_warehouse_id or self.request.destination_warehouse_id
            )
        if not self.expiry_date and self.request_id:
            self.expiry_date = self.request.expiry_date
        errors = {}
        if self.approved_quantity < 0:
            errors["approved_quantity"] = "Approved quantity cannot be negative."
        if self.ordered_quantity < 0 or self.ordered_quantity > self.approved_quantity:
            errors["ordered_quantity"] = "Ordered quantity must be between zero and approved quantity."
        if (
            self.destination_warehouse_id
            and self.request_id
            and self.destination_warehouse.company_id != self.request.company_id
        ):
            errors["destination_warehouse"] = "Line warehouse must belong to the Purchase Request company."
        for field_name in (
            "rfq_quantity",
            "blanket_quantity",
            "tender_quantity",
            "direct_purchase_quantity",
            "re_request_quantity",
        ):
            if getattr(self, field_name) < 0:
                errors[field_name] = "Converted quantities cannot be negative."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.request_id:
            self.expected_date = self.expected_date or self.request.required_date
            self.expiry_date = self.expiry_date or self.request.expiry_date
            self.destination_warehouse_id = (
                self.destination_warehouse_id or self.request.destination_warehouse_id
            )
        return super().save(*args, **kwargs)

    @property
    def estimated_total(self):
        return self.quantity * self.estimated_unit_price

    @property
    def approval_base_quantity(self):
        if self.line_status == self.Status.REJECTED:
            return Decimal("0")
        return self.approved_quantity if self.approved_quantity > 0 else self.quantity

    @property
    def remaining_to_source(self):
        return max(
            self.approval_base_quantity
            - self.rfq_quantity
            - self.blanket_quantity
            - self.tender_quantity
            - self.direct_purchase_quantity,
            Decimal("0"),
        )

    @property
    def remaining_to_re_request(self):
        converted = (
            self.rfq_quantity
            + self.blanket_quantity
            + self.tender_quantity
            + self.direct_purchase_quantity
        )
        approval_gap = max(self.quantity - self.approved_quantity, Decimal("0"))
        procurement_gap = max(self.approval_base_quantity - converted, Decimal("0"))
        return max(max(approval_gap, procurement_gap) - self.re_request_quantity, Decimal("0"))

    def __str__(self):
        return f"{self.request} - {self.description}"


class PurchaseQuotation(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        RECEIVED = "received", "Vendor Response Received"
        SELECTED = "selected", "Selected"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_quotations")
    order_type = models.ForeignKey(
        "PurchaseOrderType",
        on_delete=models.PROTECT,
        related_name="purchase_quotations",
        blank=True,
        null=True,
    )
    order_template = models.ForeignKey(
        "PurchaseOrderTemplate",
        on_delete=models.PROTECT,
        related_name="purchase_quotations",
        blank=True,
        null=True,
    )
    number = models.CharField(max_length=40)
    request = models.ForeignKey(PurchaseRequest, on_delete=models.SET_NULL, blank=True, null=True, related_name="quotations")
    blanket_order = models.ForeignKey(
        "BlanketOrder",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="quotations",
    )
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="purchase_quotations")
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="purchase_quotations",
    )
    issue_date = models.DateField(default=timezone.localdate)
    response_deadline = models.DateField(blank=True)
    expected_date = models.DateField(blank=True, null=True)
    currency = models.CharField(max_length=3, default="IDR")
    quoted_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.DRAFT,
        editable=False,
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-issue_date"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="unique_purchase_quotation_number_per_company")
        ]

    def __str__(self):
        return self.number

    def clean(self):
        self.apply_expiry_default()
        errors = {}
        validate_purchase_configuration_company(self, errors)
        if self.vendor_id and self.company_id and self.vendor.company_id != self.company_id:
            errors["vendor"] = "Vendor must belong to the selected company."
        if self.request_id and self.request.company_id != self.company_id:
            errors["request"] = "Purchase request must belong to the selected company."
        if self.warehouse_id and self.warehouse.company_id != self.company_id:
            errors["warehouse"] = "Destination warehouse must belong to the selected company."
        if self.blanket_order_id:
            if self.blanket_order.company_id != self.company_id:
                errors["blanket_order"] = "Blanket Order must belong to the quotation company."
            elif self.vendor_id and self.blanket_order.vendor_id != self.vendor_id:
                errors["blanket_order"] = "Blanket Order vendor must match the quotation vendor."
        if self.response_deadline and self.response_deadline < self.issue_date:
            errors["response_deadline"] = "Response deadline must be on or after issue date."
        if errors:
            raise ValidationError(errors)

    def apply_expiry_default(self):
        if self.response_deadline or not self.company_id:
            return
        period_days = PurchaseSetting.objects.filter(company_id=self.company_id).values_list(
            "rfq_expiry_period_days",
            flat=True,
        ).first()
        if period_days:
            self.response_deadline = self.issue_date + timedelta(days=period_days)

    def save(self, *args, **kwargs):
        self.apply_expiry_default()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and self.response_deadline:
            kwargs["update_fields"] = set(update_fields) | {"response_deadline"}
        return super().save(*args, **kwargs)


class DirectPurchase(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="direct_purchases")
    order_type = models.ForeignKey(
        "PurchaseOrderType",
        on_delete=models.PROTECT,
        related_name="direct_purchases",
        blank=True,
        null=True,
    )
    order_template = models.ForeignKey(
        "PurchaseOrderTemplate",
        on_delete=models.PROTECT,
        related_name="direct_purchases",
        blank=True,
        null=True,
    )
    number = models.CharField(max_length=40)
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="direct_purchases")
    request = models.ForeignKey(
        PurchaseRequest,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="direct_purchases",
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="direct_purchases",
    )
    purchase_order = models.OneToOneField(
        PurchaseOrder,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="direct_purchase_source",
        editable=False,
    )
    purchase_date = models.DateField(default=timezone.localdate)
    description = models.TextField()
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    justification = models.TextField()
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="direct_purchase_approvals")

    class Meta:
        ordering = ["-purchase_date"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_direct_purchase_number_per_company")]

    def __str__(self):
        return self.number

    def clean(self):
        errors = {}
        validate_purchase_configuration_company(self, errors)
        if self.vendor_id and self.vendor.company_id != self.company_id:
            errors["vendor"] = "Vendor must belong to the selected company."
        if self.warehouse_id and self.warehouse.company_id != self.company_id:
            errors["warehouse"] = "Warehouse must belong to the selected company."
        if self.request_id and self.request.company_id != self.company_id:
            errors["request"] = "Purchase Request must belong to the selected company."
        if errors:
            raise ValidationError(errors)


class ServiceWorkOrder(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        IN_PROGRESS = "in_progress", "In Progress"
        DONE = "done", "Done"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="service_work_orders")
    number = models.CharField(max_length=40, blank=True, editable=False)
    sequence = models.PositiveIntegerField(default=10, editable=False)
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="service_work_orders")
    source_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="service_work_orders",
    )
    milestone = models.ForeignKey(
        "PurchaseOrderMilestone",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="service_work_orders",
    )
    milestone_name = models.CharField(max_length=255, blank=True)
    contract_term_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    source_order_invoicing_policy = models.CharField(max_length=16, blank=True, editable=False)
    progress_paid = models.DecimalField(max_digits=5, decimal_places=2, default=0, editable=False)
    is_billed = models.BooleanField(default=False, editable=False)
    billing_invoice = models.ForeignKey(
        "core.Invoice",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="service_work_orders",
        editable=False,
    )
    service_description = models.TextField()
    date_start = models.DateField()
    date_end = models.DateField(blank=True, null=True)
    contract_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="service_work_order_approvals")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_start"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_service_work_order_number_per_company")]

    def clean(self):
        errors = {}
        if self.date_end and self.date_end < self.date_start:
            errors["date_end"] = "End date must be on or after start date."
        if self.source_order_id:
            if self.source_order.company_id != self.company_id:
                errors["source_order"] = "Source Purchase Order must belong to the selected company."
            if self.source_order.vendor_id != self.vendor_id:
                errors["vendor"] = "Service Work Order vendor must match its source Purchase Order."
        if self.milestone_id and self.source_order_id and self.milestone.order_id != self.source_order_id:
            errors["milestone"] = "Milestone must belong to the source Purchase Order."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.number


class AwardStrategy(TimeStampedModel):
    class Strategy(models.TextChoices):
        SINGLE = "single", "Single Vendor"
        SPLIT_RATIO = "split_ratio", "Split Ratio"
        MANUAL = "manual", "Manual Allocation"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_award_strategies")
    name = models.CharField(max_length=120)
    strategy = models.CharField(max_length=20, choices=Strategy.choices, default=Strategy.SINGLE)
    scoring_method = models.CharField(max_length=160, blank=True)
    split_rank_1_ratio = models.PositiveSmallIntegerField(
        default=60,
        validators=[MaxValueValidator(100)],
    )
    split_rank_2_ratio = models.PositiveSmallIntegerField(
        default=30,
        validators=[MaxValueValidator(100)],
    )
    split_rank_3_ratio = models.PositiveSmallIntegerField(
        default=10,
        validators=[MaxValueValidator(100)],
    )
    capacity_fill_limit_percent = models.PositiveSmallIntegerField(
        default=100,
        validators=[MinValueValidator(1), MaxValueValidator(100)],
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_award_strategy_per_company")]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.strategy == self.Strategy.SPLIT_RATIO and (
            self.split_rank_1_ratio + self.split_rank_2_ratio + self.split_rank_3_ratio
        ) != 100:
            raise ValidationError(
                {"split_rank_1_ratio": "Split strategy ratios must total 100%."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class PurchaseTender(PurchaseCostCenterStructuredFields, TimeStampedModel):
    class Category(models.TextChoices):
        OPEX = "opex", "OPEX"
        CAPEX = "capex", "CAPEX"

    class TenderType(models.TextChoices):
        OPEN = "open", "Open Tender"
        INVITED = "invited", "Invited Tender"

    class Method(models.TextChoices):
        OTE = "ote", "Open Tender Evaluation"
        ITE = "ite", "Invited Tender Evaluation"
        PQTE = "pqte", "Prequalification Tender Evaluation"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        WAITING_APPROVAL = "waiting_approval", "Waiting Approval"
        CONFIRMED = "confirmed", "Confirmed"
        PUBLISHED = "published", "Published"
        DOCUMENT_SUBMISSION = "document_submission", "Document Submission"
        BID_SUBMISSION = "bid_submission", "Bid Submission"
        BID_SELECTION = "bid_selection", "Bid Selection"
        SUBMISSION = "submission", "Bid Submission"
        EVALUATION = "evaluation", "Evaluation"
        AWARDED = "awarded", "Awarded"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"
        REJECTED = "rejected", "Rejected"

    class DecisionStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        WAITING_APPROVAL = "waiting_approval", "Waiting Approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_tenders")
    order_type = models.ForeignKey(
        "PurchaseOrderType",
        on_delete=models.PROTECT,
        related_name="purchase_tenders",
        blank=True,
        null=True,
    )
    order_template = models.ForeignKey(
        "PurchaseOrderTemplate",
        on_delete=models.PROTECT,
        related_name="purchase_tenders",
        blank=True,
        null=True,
    )
    number = models.CharField(max_length=40)
    title = models.CharField(max_length=180)
    category = models.CharField(max_length=10, choices=Category.choices)
    tender_type = models.CharField(max_length=12, choices=TenderType.choices)
    method = models.CharField(max_length=8, choices=Method.choices, default=Method.OTE)
    award_strategy = models.ForeignKey(AwardStrategy, on_delete=models.PROTECT, related_name="tenders")
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="purchase_tenders",
    )
    currency = models.CharField(max_length=3, default="IDR")
    cost_center = models.CharField(max_length=80, blank=True)
    publish_date = models.DateField(blank=True, null=True)
    bid_start_date = models.DateField(blank=True, null=True)
    close_date = models.DateField(blank=True)
    validity_days = models.PositiveIntegerField(default=30)
    budget_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    evaluation_price_weight = models.PositiveSmallIntegerField(
        default=50,
        validators=[MaxValueValidator(100)],
    )
    evaluation_delivery_weight = models.PositiveSmallIntegerField(
        default=20,
        validators=[MaxValueValidator(100)],
    )
    evaluation_compliance_weight = models.PositiveSmallIntegerField(
        default=15,
        validators=[MaxValueValidator(100)],
    )
    evaluation_term_weight = models.PositiveSmallIntegerField(
        default=5,
        validators=[MaxValueValidator(100)],
    )
    evaluation_history_weight = models.PositiveSmallIntegerField(
        default=10,
        validators=[MaxValueValidator(100)],
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    decision_approval_status = models.CharField(
        max_length=24,
        choices=DecisionStatus.choices,
        default=DecisionStatus.DRAFT,
        editable=False,
    )
    decision_rejection_reason = models.TextField(blank=True, editable=False)
    decision_revision_count = models.PositiveIntegerField(default=0, editable=False)
    award_cycle = models.PositiveIntegerField(default=1, editable=False)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_purchase_tender_number_per_company")]

    def __str__(self):
        return self.number

    @property
    def evaluation_weight_total(self):
        return sum(
            (
                self.evaluation_price_weight,
                self.evaluation_delivery_weight,
                self.evaluation_compliance_weight,
                self.evaluation_term_weight,
                self.evaluation_history_weight,
            )
        )

    def clean(self):
        self.apply_expiry_default()
        errors = {}
        validate_purchase_configuration_company(self, errors)
        if self.publish_date and self.close_date and self.close_date < self.publish_date:
            errors["close_date"] = "Close date must be on or after publish date."
        if self.bid_start_date and self.close_date and self.close_date < self.bid_start_date:
            errors["close_date"] = "Close date must be on or after bid start date."
        if self.warehouse_id and self.warehouse.company_id != self.company_id:
            errors["warehouse"] = "Warehouse must belong to the selected company."
        if self.evaluation_weight_total != 100:
            errors["evaluation_price_weight"] = "Evaluation criteria weights must total exactly 100%."
        if errors:
            raise ValidationError(errors)

    def apply_expiry_default(self):
        if self.close_date or not self.company_id:
            return
        period_days = PurchaseSetting.objects.filter(company_id=self.company_id).values_list(
            "pt_expiry_period_days",
            flat=True,
        ).first()
        if period_days:
            self.close_date = timezone.localdate() + timedelta(days=period_days)

    def save(self, *args, **kwargs):
        self.apply_expiry_default()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and self.close_date:
            kwargs["update_fields"] = set(update_fields) | {"close_date"}
        return super().save(*args, **kwargs)


class TenderBid(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        SHORTLISTED = "shortlisted", "Shortlisted"
        AWARDED = "awarded", "Awarded"
        REJECTED = "rejected", "Rejected"

    tender = models.ForeignKey(PurchaseTender, on_delete=models.CASCADE, related_name="bids")
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="tender_bids")
    submitted_at = models.DateTimeField(blank=True, null=True)
    bid_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    technical_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    commercial_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    price_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, editable=False)
    delivery_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, editable=False)
    compliance_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, editable=False)
    term_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, editable=False)
    history_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, editable=False)
    total_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, editable=False)
    rank = models.PositiveIntegerField(default=0, editable=False)
    scored_at = models.DateTimeField(blank=True, null=True, editable=False)
    allocation_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["bid_amount", "-technical_score"]
        constraints = [models.UniqueConstraint(fields=["tender", "vendor"], name="unique_vendor_bid_per_tender")]

    def __str__(self):
        return f"{self.tender} - {self.vendor}"

    def clean(self):
        errors = {}
        if self.vendor_id and self.vendor.company_id != self.tender.company_id:
            errors["vendor"] = "Tender Bid vendor must belong to the Tender company."
        if (
            self.tender_id
            and self.vendor_id
            and self.tender.method == PurchaseTender.Method.PQTE
            and self.tender.status
            in {
                PurchaseTender.Status.BID_SUBMISSION,
                PurchaseTender.Status.BID_SELECTION,
                PurchaseTender.Status.AWARDED,
                PurchaseTender.Status.CLOSED,
            }
        ):
            profile_id = (
                VendorProfile.objects.filter(
                    company_id=self.tender.company_id,
                    partner_id=self.vendor_id,
                    active=True,
                )
                .values_list("pk", flat=True)
                .first()
            )
            if not profile_id or not VendorPrequalification.objects.filter(
                tender_id=self.tender_id,
                vendor_id=profile_id,
                status=VendorPrequalification.Status.APPROVED,
            ).exists():
                errors["vendor"] = (
                    "Vendor must pass Tender Document Completion before entering Bid Submission."
                )
        if errors:
            raise ValidationError(errors)


class BlanketOrder(PurchaseCostCenterStructuredFields, TimeStampedModel):
    class AgreementPolicy(models.TextChoices):
        QUANTITY_PERIOD = "quantity_period", "Quantity and Period"
        PERIOD_ONLY = "period_only", "Period Only"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        WAITING_APPROVAL = "waiting_approval", "Waiting Approval"
        CONFIRMED = "confirmed", "Confirmed"
        RUNNING = "running", "Running"
        ACTIVE = "active", "Active"
        EXHAUSTED = "exhausted", "Exhausted"
        EXPIRED = "expired", "Expired"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"
        REJECTED = "rejected", "Rejected"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="blanket_orders")
    order_type = models.ForeignKey(
        "PurchaseOrderType",
        on_delete=models.PROTECT,
        related_name="blanket_orders",
        blank=True,
        null=True,
    )
    order_template = models.ForeignKey(
        "PurchaseOrderTemplate",
        on_delete=models.PROTECT,
        related_name="blanket_orders",
        blank=True,
        null=True,
    )
    number = models.CharField(max_length=40)
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="blanket_orders")
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="blanket_orders",
    )
    currency = models.CharField(max_length=3, default="IDR")
    cost_center = models.CharField(max_length=80, blank=True)
    date_start = models.DateField()
    date_end = models.DateField()
    agreement_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    untaxed_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    tax_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    consumed_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    agreement_policy = models.CharField(
        max_length=32,
        choices=AgreementPolicy.choices,
        default=AgreementPolicy.QUANTITY_PERIOD,
    )
    terms = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    auto_closed = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ["-date_start"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_blanket_order_number_per_company")]

    def clean(self):
        errors = {}
        validate_purchase_configuration_company(self, errors)
        if self.date_end <= self.date_start:
            errors["date_end"] = "End date must be later than start date."
        elif self.date_end == timezone.localdate():
            errors["date_end"] = "End date cannot be today's date."
        if self.consumed_value > self.agreement_value:
            errors["consumed_value"] = "Consumed value cannot exceed agreement value."
        if self.vendor_id and self.vendor.company_id != self.company_id:
            errors["vendor"] = "Vendor must belong to the selected company."
        if self.warehouse_id and self.warehouse.company_id != self.company_id:
            errors["warehouse"] = "Warehouse must belong to the selected company."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.number


class VendorCategory(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="vendor_categories")
    code = models.CharField(max_length=30, blank=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    risk_level = models.CharField(max_length=20, default="standard")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_vendor_category_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = f"VC-{uuid4().hex[:12].upper()}"
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"code"}
        return super().save(*args, **kwargs)


class PurchaseLegalDocument(TimeStampedModel):
    class DocumentUsage(models.TextChoices):
        STANDARD_VENDOR = "standard_vendor", "Standard Vendor"
        TENDER_DOCUMENT = "tender_document", "Tender Document"
        ADDITIONAL_VENDOR = "additional_vendor", "Additional Vendor"

    class RequirementType(models.TextChoices):
        REQUIRED = "required", "Required"
        ADDITIONAL = "additional", "Additional"
        OPTIONAL = "optional", "Optional"

    class ApplyTo(models.TextChoices):
        CATEGORY = "category", "Vendor Category"
        VENDOR = "vendor", "Specific Vendor"
        BOTH = "both", "Both"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_legal_document_types")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    document_usage = models.CharField(
        max_length=50,
        choices=DocumentUsage.choices,
        default=DocumentUsage.STANDARD_VENDOR,
    )
    requirement_type = models.CharField(
        max_length=16,
        choices=RequirementType.choices,
        default=RequirementType.REQUIRED,
    )
    apply_to = models.CharField(max_length=16, choices=ApplyTo.choices, default=ApplyTo.BOTH)
    applicable_vendors = models.ManyToManyField(
        "VendorProfile",
        blank=True,
        related_name="applicable_legal_document_types",
    )
    applicable_categories = models.ManyToManyField(
        VendorCategory,
        blank=True,
        related_name="applicable_legal_document_types",
    )
    is_blocking = models.BooleanField(default=True)
    expiry_applicable = models.BooleanField(default=False)
    default_validity_months = models.PositiveIntegerField(blank=True, null=True)
    expiry_date_required = models.BooleanField(default=False)
    reminder_before_expiry_days = models.PositiveIntegerField(blank=True, null=True)
    is_demo = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "name"],
                condition=models.Q(is_demo=False),
                name="unique_live_purchase_legal_document_name",
            )
        ]

    def clean(self):
        errors = {}
        if self.requirement_type == self.RequirementType.OPTIONAL and self.is_blocking:
            errors["is_blocking"] = "Optional documents cannot be blocking."
        if self.expiry_date_required and not self.expiry_applicable:
            errors["expiry_date_required"] = "Expiry date cannot be required when expiry is not applicable."
        if not self.expiry_applicable and self.default_validity_months is not None:
            errors["default_validity_months"] = "Default validity requires expiry tracking."
        if not self.expiry_applicable and self.reminder_before_expiry_days is not None:
            errors["reminder_before_expiry_days"] = "Expiry reminders require expiry tracking."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name


class PurchaseLegalDocumentAttachment(TimeStampedModel):
    MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024
    MAX_TOTAL_SIZE_BYTES = 100 * 1024 * 1024

    legal_document = models.ForeignKey(
        PurchaseLegalDocument,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(
        upload_to="purchase_legal_docs/attachments/",
        validators=[FileExtensionValidator(["pdf"])],
    )
    file_name = models.CharField(max_length=255, editable=False)
    size_bytes = models.PositiveBigIntegerField(default=0, editable=False)

    class Meta:
        ordering = ["legal_document", "file_name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["legal_document", "file_name"],
                name="unique_purchase_legal_document_attachment_name",
            )
        ]

    def clean(self):
        errors = {}
        if self.file:
            self.file_name = self.file.name.rsplit("/", 1)[-1]
            self.size_bytes = self.file.size
            if self.size_bytes > self.MAX_FILE_SIZE_BYTES:
                errors["file"] = "Each legal-document attachment must be 25 MB or smaller."
            try:
                position = self.file.tell()
            except (AttributeError, OSError):
                position = None
            try:
                self.file.seek(0)
                if self.file.read(4) != b"%PDF":
                    errors["file"] = "Legal-document attachments must be valid PDF files."
            finally:
                if position is not None:
                    self.file.seek(position)
        if self.legal_document_id:
            existing_total = (
                type(self).objects.filter(legal_document_id=self.legal_document_id)
                .exclude(pk=self.pk)
                .aggregate(total=models.Sum("size_bytes"))["total"]
                or 0
            )
            if existing_total + self.size_bytes > self.MAX_TOTAL_SIZE_BYTES:
                errors["file"] = "Total legal-document attachments must be 100 MB or smaller."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.file_name


class VendorProfile(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        WAITING_APPROVAL = "waiting_approval", "Waiting For Approval"
        ACTIVE = "confirmed", "Confirmed"
        BLACKLISTED = "blacklisted", "Blacklisted"
        REJECTED = "rejected", "Rejected"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_vendor_profiles")
    partner = models.OneToOneField(Contact, on_delete=models.PROTECT, related_name="purchase_vendor_profile")
    category = models.ForeignKey(VendorCategory, on_delete=models.SET_NULL, blank=True, null=True, related_name="vendors")
    registration_number = models.CharField(max_length=80, blank=True)
    tax_number = models.CharField(max_length=80, blank=True)
    mobile_number = models.CharField(max_length=40, blank=True)
    bank_account = models.CharField(max_length=80, blank=True)
    parent_vendor = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="subsidiary_vendors",
    )
    payment_terms = models.TextField(
        blank=True,
        help_text="Default payment terms copied to new Purchase Orders when enabled in Purchase Settings.",
    )
    purchase_credit_limit = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Purchase Limit",
        help_text="Purchase limit assigned to this vendor; new vendors inherit Purchase Settings when omitted.",
    )
    rating = models.DecimalField(max_digits=4, decimal_places=2, default=0, validators=[MaxValueValidator(5)])
    active = models.BooleanField(default=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="approved_vendor_profiles",
        editable=False,
    )
    approved_at = models.DateTimeField(blank=True, null=True, editable=False)
    rejection_reason = models.TextField(blank=True, editable=False)
    status_before_blacklist = models.CharField(max_length=32, blank=True, editable=False)
    vendor_blacklist_date = models.DateTimeField(blank=True, null=True, editable=False)
    vendor_blacklist_reason = models.CharField(max_length=200, blank=True, editable=False)
    vendor_blacklist_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="blacklisted_vendor_profiles",
        editable=False,
    )
    latest_performance_score = models.DecimalField(max_digits=8, decimal_places=2, default=0, editable=False)
    latest_performance_score_date = models.DateField(blank=True, null=True, editable=False)
    latest_performance_score_status = models.CharField(max_length=32, blank=True, editable=False)
    latest_performance_score_period_start = models.DateField(blank=True, null=True, editable=False)
    latest_performance_score_period_end = models.DateField(blank=True, null=True, editable=False)
    latest_performance_score_template = models.ForeignKey(
        "VendorPerformanceTemplate",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="latest_scored_vendors",
        editable=False,
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["partner__name"]

    def clean(self):
        errors = {}
        if self.partner_id and self.partner.company_id != self.company_id:
            errors["partner"] = "Vendor contact must belong to the selected company."
        if self.category_id and self.category.company_id != self.company_id:
            errors["category"] = "Vendor category must belong to the selected company."
        if self.parent_vendor_id:
            if self.parent_vendor_id == self.pk:
                errors["parent_vendor"] = "A vendor cannot be its own parent."
            elif self.parent_vendor.company_id != self.company_id:
                errors["parent_vendor"] = "Parent Vendor must belong to the selected company."
        if self.registration_number and type(self).objects.filter(
            company_id=self.company_id,
            registration_number__iexact=self.registration_number,
        ).exclude(pk=self.pk).exists():
            errors["registration_number"] = "Vendor registration number must be unique per company."
        if not self.registration_number and self.company_id:
            sequence_mode = PurchaseSetting.objects.filter(company_id=self.company_id).values_list(
                "vendor_sequence_mode",
                flat=True,
            ).first()
            if sequence_mode == PurchaseSetting.SequenceMode.MANUAL:
                errors["registration_number"] = "Vendor Code is required when manual generation is enabled."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        setting_values = (
            PurchaseSetting.objects.filter(company_id=self.company_id)
            .values("vendor_sequence_mode", "credit_limit")
            .first()
        ) or {}
        if self._state.adding and self.purchase_credit_limit is None:
            self.purchase_credit_limit = setting_values.get("credit_limit") or Decimal("0")
        if self.purchase_credit_limit is not None and self.purchase_credit_limit < 0:
            raise ValidationError({"purchase_credit_limit": "Purchase Limit cannot be negative."})
        if not self.registration_number:
            sequence_mode = setting_values.get("vendor_sequence_mode")
            if sequence_mode == PurchaseSetting.SequenceMode.MANUAL:
                raise ValidationError(
                    {"registration_number": "Vendor Code is required when manual generation is enabled."}
                )
            self.registration_number = f"VEN-{uuid4().hex[:12].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return str(self.partner)


class VendorChangeProposal(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        WAITING_APPROVAL = "waiting_approval", "Waiting Approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    vendor = models.ForeignKey(
        VendorProfile,
        on_delete=models.PROTECT,
        related_name="change_proposals",
    )
    proposed_values = models.JSONField(default=dict)
    original_snapshot = models.JSONField(default=dict, editable=False)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_vendor_changes",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="reviewed_vendor_changes",
        editable=False,
    )
    reviewed_at = models.DateTimeField(blank=True, null=True, editable=False)
    review_reason = models.TextField(blank=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor"],
                condition=models.Q(status__in=["draft", "waiting_approval"]),
                name="unique_open_vendor_change_proposal",
            )
        ]

    def __str__(self):
        return f"{self.vendor} - {self.get_status_display()}"


class VendorLegalDocument(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        NO_EXPIRY = "no_expiry", "No Expiry"
        VALID = "valid", "Valid"
        EXPIRING_SOON = "expiring_soon", "Expiring Soon"
        EXPIRED = "expired", "Expired"
        MISSING_REQUIRED_EXPIRY = "missing_required_expiry", "Missing Required Expiry"
        REJECTED = "rejected", "Rejected"
        SUPERSEDED = "superseded", "Superseded"

    class RequirementType(models.TextChoices):
        REQUIRED = "required", "Required"
        ADDITIONAL = "additional", "Additional"
        OPTIONAL = "optional", "Optional"

    class ReviewStatus(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_REVIEW = "pending_review", "Pending Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SUPERSEDED = "superseded", "Superseded"

    vendor = models.ForeignKey(VendorProfile, on_delete=models.CASCADE, related_name="legal_documents")
    legal_document = models.ForeignKey(
        PurchaseLegalDocument,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="vendor_documents",
    )
    document_type = models.CharField(max_length=255, blank=True)
    document_number = models.CharField(max_length=100, blank=True)
    issue_date = models.DateField(blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)
    attachment_url = models.URLField(blank=True)
    mandatory = models.BooleanField(default=True)
    requirement_type = models.CharField(
        max_length=16,
        choices=RequirementType.choices,
        default=RequirementType.REQUIRED,
    )
    is_blocking = models.BooleanField(default=True)
    expiry_required = models.BooleanField(default=False)
    reminder_before_expiry_days = models.PositiveIntegerField(default=30)
    last_reminder_date = models.DateField(blank=True, null=True, editable=False)
    renewal_source = models.CharField(max_length=32, blank=True)
    previous_version = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="renewal_versions",
        editable=False,
    )
    review_status = models.CharField(
        max_length=32,
        choices=ReviewStatus.choices,
        default=ReviewStatus.DRAFT,
    )
    rejection_reason = models.TextField(blank=True, editable=False)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="reviewed_vendor_legal_documents",
        editable=False,
    )
    reviewed_at = models.DateTimeField(blank=True, null=True, editable=False)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.DRAFT,
        editable=False,
    )

    class Meta:
        ordering = ["vendor__partner__name", "document_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "legal_document"],
                condition=(
                    models.Q(legal_document__isnull=False, renewal_source="")
                    & ~models.Q(review_status="superseded")
                ),
                name="unique_active_vendor_legal_requirement",
            )
        ]

    def apply_legal_document_configuration(self):
        if not self.legal_document_id:
            return
        config = self.legal_document
        self.document_type = config.name
        self.requirement_type = config.requirement_type
        self.mandatory = config.requirement_type == PurchaseLegalDocument.RequirementType.REQUIRED
        self.is_blocking = config.is_blocking
        self.expiry_required = config.expiry_date_required
        self.reminder_before_expiry_days = config.reminder_before_expiry_days or 0
        if (
            self.issue_date
            and not self.expiry_date
            and config.expiry_applicable
            and config.default_validity_months
        ):
            self.expiry_date = add_calendar_months(self.issue_date, config.default_validity_months)

    def clean(self):
        self.apply_legal_document_configuration()
        errors = {}
        if not self.legal_document_id and not self.document_type.strip():
            errors["document_type"] = "Document type is required."
        if self.legal_document_id and self.legal_document.company_id != self.vendor.company_id:
            errors["legal_document"] = "Legal document type must belong to the Vendor company."
        if self.expiry_date and self.issue_date and self.expiry_date < self.issue_date:
            errors["expiry_date"] = "Expiry date cannot be earlier than issue date."
        if self.attachment_url and self.expiry_required and not self.expiry_date:
            errors["expiry_date"] = "Expiry date is required for this document."
        if self.requirement_type == self.RequirementType.OPTIONAL and self.is_blocking:
            errors["is_blocking"] = "Optional legal documents cannot block Vendor approval."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.apply_legal_document_configuration()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and self.legal_document_id:
            kwargs["update_fields"] = set(update_fields) | {
                "document_type",
                "requirement_type",
                "mandatory",
                "is_blocking",
                "expiry_required",
                "reminder_before_expiry_days",
                "expiry_date",
            }
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.vendor} - {self.document_type}"


class VendorPrequalification(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending"
        UNDER_REVIEW = "review", "Under Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="vendor_prequalifications",
    )
    tender = models.ForeignKey(
        PurchaseTender,
        on_delete=models.CASCADE,
        blank=False,
        null=True,
        related_name="prequalifications",
    )
    vendor = models.ForeignKey(VendorProfile, on_delete=models.PROTECT, related_name="prequalifications")
    number = models.CharField(max_length=64, unique=True, blank=True, editable=False)
    submitted_date = models.DateField(blank=True, null=True)
    review_due_date = models.DateField(blank=True, null=True)
    score = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    document_fulfillment_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        editable=False,
        validators=[MaxValueValidator(100)],
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="vendor_prequalification_reviews")
    rejection_reason = models.TextField(blank=True, editable=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tender", "vendor"],
                condition=models.Q(tender__isnull=False),
                name="unique_prequalification_tender_vendor",
            )
        ]

    def clean(self):
        errors = {}
        if self.tender_id:
            if self.company_id and self.company_id != self.tender.company_id:
                errors["company"] = "Prequalification company must match the Tender company."
            if self.vendor_id and self.vendor.company_id != self.tender.company_id:
                errors["vendor"] = "Prequalification vendor must belong to the Tender company."
        elif self.vendor_id and self.company_id and self.vendor.company_id != self.company_id:
            errors["vendor"] = "Prequalification vendor must belong to the selected company."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.tender_id:
            self.company_id = self.tender.company_id
        elif self.vendor_id and not self.company_id:
            self.company_id = self.vendor.company_id
        if not self.number:
            self.number = f"PQ-{timezone.localdate():%Y%m%d}-{uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.number


class VendorDocumentReview(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending"
        ACCEPTED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        REVISION = "revision", "Revision"

    prequalification = models.ForeignKey(VendorPrequalification, on_delete=models.CASCADE, related_name="document_reviews")
    sequence = models.PositiveIntegerField(default=10)
    document_type = models.CharField(max_length=255, blank=True)
    document = models.ForeignKey(
        VendorLegalDocument,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="reviews",
    )
    attachment_url = models.URLField(blank=True)
    requirement_type = models.CharField(
        max_length=16,
        choices=VendorLegalDocument.RequirementType.choices,
        default=VendorLegalDocument.RequirementType.REQUIRED,
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="vendor_document_reviews",
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["prequalification", "sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["prequalification", "document_type"],
                condition=~models.Q(document_type=""),
                name="unique_prequalification_document_type",
            )
        ]

    def clean(self):
        errors = {}
        if self.document_id and self.document.vendor_id != self.prequalification.vendor_id:
            errors["document"] = "Document must belong to the prequalification vendor."
        if self.document_id and self.document_type and self.document.document_type != self.document_type:
            errors["document"] = "Document type must match the prequalification requirement."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.prequalification} - {self.document}"


class VendorPricelist(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        WAITING_APPROVAL = "waiting_approval", "Waiting for Approval"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        INACTIVE = "inactive", "Inactive"
        REJECTED = "rejected", "Rejected"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="vendor_pricelists")
    name = models.CharField(max_length=100, blank=True)
    code = models.CharField(max_length=100, blank=True)
    vendor = models.ForeignKey(VendorProfile, on_delete=models.PROTECT, related_name="pricelists")
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="vendor_pricelists",
        blank=True,
        null=True,
        help_text="Legacy single-item price; use Pricelist Items for HMX-compatible tiered pricing.",
    )
    minimum_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="IDR")
    date_start = models.DateField(default=timezone.localdate)
    date_end = models.DateField(blank=True, null=True)
    lead_time_days = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    source_import = models.ForeignKey(
        "VendorPricelistImportLog",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="pricelists",
        editable=False,
    )

    class Meta:
        ordering = ["product__name", "unit_price"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "product", "minimum_quantity", "date_start"],
                name="unique_vendor_pricelist_effective_tier",
            )
        ]

    def __str__(self):
        return self.code or self.name or f"{self.vendor} - {self.product or 'Pricelist'}"

    def clean(self):
        errors = {}
        if self.vendor_id and self.company_id and self.vendor.company_id != self.company_id:
            errors["vendor"] = "Vendor must belong to the pricelist company."
        if self.product_id and self.company_id and self.product.company_id != self.company_id:
            errors["product"] = "Legacy pricelist product must belong to the pricelist company."
        if self.date_end and self.date_end < self.date_start:
            errors["date_end"] = "Expiry date must be on or after effective date."
        if self.minimum_quantity <= 0:
            errors["minimum_quantity"] = "Minimum quantity must be greater than zero."
        if self.product_id and self.unit_price <= 0:
            errors["unit_price"] = "Legacy single-item unit price must be greater than zero."
        if errors:
            raise ValidationError(errors)


class VendorPricelistItem(TimeStampedModel):
    pricelist = models.ForeignKey(VendorPricelist, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="vendor_pricelist_items")
    uom = models.CharField(max_length=24, blank=True)
    vendor_uom = models.CharField(max_length=64, blank=True)
    vendor_product_code = models.CharField(max_length=100, blank=True)
    vendor_product_name = models.CharField(max_length=160, blank=True)
    variant = models.CharField(max_length=80, default="standard")
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    lead_time_days = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    enforce_moq = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["product__name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["pricelist", "product"], name="unique_product_per_vendor_pricelist")
        ]

    def clean(self):
        errors = {}
        if self.product_id and self.pricelist_id and self.product.company_id != self.pricelist.company_id:
            errors["product"] = "Pricelist item product must belong to the pricelist company."
        if self.unit_price <= 0:
            errors["unit_price"] = "Unit price must be greater than zero."
        if self.lead_time_days <= 0:
            errors["lead_time_days"] = "Lead time must be greater than zero."
        if self.enforce_moq and self.pk:
            first_minimum = self.tiers.order_by("sequence", "pk").values_list("minimum_quantity", flat=True).first()
            if first_minimum is None or first_minimum <= 0:
                errors["enforce_moq"] = "Create a positive first tier before enforcing MOQ."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.pricelist} - {self.product}"


class VendorPricelistTier(TimeStampedModel):
    item = models.ForeignKey(VendorPricelistItem, on_delete=models.CASCADE, related_name="tiers")
    sequence = models.PositiveIntegerField(default=1)
    minimum_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    maximum_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    tier_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    discount_formula = models.CharField(max_length=64, blank=True)
    discount_from_tier = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="discount_dependents",
    )
    free_product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="vendor_pricelist_free_tiers",
    )
    free_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    free_uom = models.CharField(max_length=24, blank=True)

    class Meta:
        ordering = ["item", "sequence", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["item", "sequence"], name="unique_vendor_pricelist_tier_sequence")
        ]

    @staticmethod
    def apply_discount_formula(price, formula):
        result = Decimal(price)
        normalized = (formula or "").replace(" ", "")
        if not normalized:
            return result.quantize(Decimal("0.01"))
        for component in normalized.split("+"):
            is_percent = component.endswith("%")
            raw_value = component[:-1] if is_percent else component
            try:
                discount = Decimal(raw_value)
            except (InvalidOperation, TypeError):
                raise ValidationError({"discount_formula": "Discount formula is invalid."})
            if discount < 0 or (is_percent and discount > 100):
                raise ValidationError({"discount_formula": "Discount formula contains an invalid value."})
            result = result * (Decimal("1") - discount / Decimal("100")) if is_percent else result - discount
        if result < 0:
            raise ValidationError({"discount_formula": "Discount formula cannot produce a negative price."})
        return result.quantize(Decimal("0.01"))

    @property
    def effective_price(self):
        base = self.discount_from_tier.effective_price if self.discount_from_tier_id else self.tier_price
        return self.apply_discount_formula(base, self.discount_formula)

    def clean(self):
        errors = {}
        if self.minimum_quantity < 0 or self.maximum_quantity < 0:
            errors["minimum_quantity"] = "Tier quantities cannot be negative."
        if self.maximum_quantity and self.maximum_quantity < self.minimum_quantity:
            errors["maximum_quantity"] = "Maximum quantity must be zero or at least the minimum quantity."
        if self.tier_price <= 0 and not self.discount_from_tier_id:
            errors["tier_price"] = "Tier price must be greater than zero when no source tier is selected."
        if self.discount_from_tier_id:
            if self.discount_from_tier_id == self.pk:
                errors["discount_from_tier"] = "A tier cannot discount itself."
            elif self.discount_from_tier.item_id != self.item_id:
                errors["discount_from_tier"] = "Discount source tier must belong to the same pricelist item."
            elif self.discount_from_tier.sequence >= self.sequence:
                errors["discount_from_tier"] = "Discount source tier must be an earlier tier."
        if self.free_product_id and self.free_product.company_id != self.item.pricelist.company_id:
            errors["free_product"] = "Free product must belong to the pricelist company."
        if self.free_quantity < 0:
            errors["free_quantity"] = "Free quantity cannot be negative."
        if self.discount_formula:
            try:
                self.apply_discount_formula(
                    self.discount_from_tier.effective_price if self.discount_from_tier_id else self.tier_price,
                    self.discount_formula,
                )
            except ValidationError as exc:
                errors.update(exc.message_dict)
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.item} - Tier {self.sequence}"


class VendorPricelistAmendment(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        WAITING_APPROVAL = "waiting_approval", "Waiting For Approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    pricelist = models.ForeignKey(VendorPricelist, on_delete=models.PROTECT, related_name="amendments")
    effective_date = models.DateField(default=timezone.localdate)
    previous_price = models.DecimalField(max_digits=18, decimal_places=2, default=0, blank=True)
    new_price = models.DecimalField(max_digits=18, decimal_places=2, default=0, blank=True)
    reason = models.TextField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="vendor_pricelist_approvals")

    class Meta:
        ordering = ["-effective_date"]

    def __str__(self):
        return f"{self.pricelist} - {self.effective_date}"


class VendorPricelistAmendmentLine(TimeStampedModel):
    class TargetType(models.TextChoices):
        ITEM = "item", "Pricelist Item"
        TIER = "tier", "Pricelist Tier"

    amendment = models.ForeignKey(
        VendorPricelistAmendment,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    target_type = models.CharField(max_length=8, choices=TargetType.choices)
    item = models.ForeignKey(
        VendorPricelistItem,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="amendment_lines",
    )
    tier = models.ForeignKey(
        VendorPricelistTier,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="amendment_lines",
    )
    field_key = models.CharField(max_length=64)
    old_value = models.TextField(blank=True)
    new_value = models.TextField()

    class Meta:
        ordering = ["amendment", "pk"]

    def clean(self):
        errors = {}
        if self.target_type == self.TargetType.ITEM and (not self.item_id or self.tier_id):
            errors["item"] = "Item amendments require one item and no tier."
        if self.target_type == self.TargetType.TIER and (not self.tier_id or self.item_id):
            errors["tier"] = "Tier amendments require one tier and no item."
        target_pricelist_id = None
        if self.item_id:
            target_pricelist_id = self.item.pricelist_id
        elif self.tier_id:
            target_pricelist_id = self.tier.item.pricelist_id
        if target_pricelist_id and target_pricelist_id != self.amendment.pricelist_id:
            errors["amendment"] = "Amendment lines must belong to the selected pricelist."
        allowed = {
            self.TargetType.ITEM: {"unit_price", "lead_time_days", "enforce_moq"},
            self.TargetType.TIER: {
                "minimum_quantity",
                "maximum_quantity",
                "tier_price",
                "discount_formula",
                "free_quantity",
            },
        }
        if self.field_key not in allowed.get(self.target_type, set()):
            errors["field_key"] = "This field cannot be changed through a pricelist amendment."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.amendment} - {self.field_key}"


class VendorPricelistImportLog(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        VALIDATED = "validated", "Validated"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Completed With Errors"
        FAILED = "failed", "Failed"

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="vendor_pricelist_imports",
    )
    source_file = models.FileField(
        upload_to="purchase/vendor_pricelist_imports/%Y/%m/",
        validators=[FileExtensionValidator(["csv"])],
    )
    has_header = models.BooleanField(default=True)
    field_mapping = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    total_rows = models.PositiveIntegerField(default=0, editable=False)
    valid_rows = models.PositiveIntegerField(default=0, editable=False)
    error_rows = models.PositiveIntegerField(default=0, editable=False)
    inserted_count = models.PositiveIntegerField(default=0, editable=False)
    updated_count = models.PositiveIntegerField(default=0, editable=False)
    skipped_blank_rows = models.PositiveIntegerField(default=0, editable=False)
    normalized_data = models.JSONField(default=list, blank=True, editable=False)
    errors = models.JSONField(default=list, blank=True, editable=False)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="vendor_pricelist_imports",
        editable=False,
    )
    started_at = models.DateTimeField(blank=True, null=True, editable=False)
    completed_at = models.DateTimeField(blank=True, null=True, editable=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        filename = self.source_file.name.rsplit("/", 1)[-1] if self.source_file else "CSV Import"
        return f"{filename} - {self.get_status_display()}"


class VendorPricelistImportPreview(TimeStampedModel):
    import_log = models.OneToOneField(
        VendorPricelistImportLog,
        on_delete=models.CASCADE,
        related_name="preview",
    )
    valid_data = models.JSONField(default=list, blank=True, editable=False)
    error_data = models.JSONField(default=list, blank=True, editable=False)
    available_columns = models.JSONField(default=list, blank=True, editable=False)
    validated_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-validated_at"]

    def __str__(self):
        return f"Preview - {self.import_log}"


class VendorPerformanceTemplate(TimeStampedModel):
    class PartialScoringRule(models.TextChoices):
        MARK_INSUFFICIENT = "mark_insufficient", "Mark as Insufficient Transaction Data"
        QUALITATIVE_ONLY = "qualitative_only", "Qualitative Score Only"
        BLOCK_FINAL_SCORE = "block_final_score", "Block Final Score"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="vendor_performance_templates")
    code = models.CharField(max_length=30, blank=True, editable=False)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    effective_date = models.DateField(blank=True, null=True)
    evaluation_period = models.PositiveIntegerField(default=12, validators=[MinValueValidator(1)])
    score_scale = models.DecimalField(max_digits=8, decimal_places=2, default=5, validators=[MinValueValidator(Decimal("0.01"))])
    qualitative_weight = models.DecimalField(max_digits=6, decimal_places=2, default=40)
    quantitative_weight = models.DecimalField(max_digits=6, decimal_places=2, default=60)
    delivery_accuracy_weight = models.DecimalField(max_digits=6, decimal_places=2, default=50)
    rejection_rate_weight = models.DecimalField(max_digits=6, decimal_places=2, default=50)
    minimum_transaction_threshold = models.PositiveIntegerField(default=1)
    partial_scoring_rule = models.CharField(
        max_length=32,
        choices=PartialScoringRule.choices,
        default=PartialScoringRule.MARK_INSUFFICIENT,
    )
    vendor_categories = models.ManyToManyField(
        VendorCategory,
        blank=True,
        related_name="vendor_performance_templates",
    )
    notes = models.TextField(blank=True)
    # Kept as read-only compatibility columns for records created before the
    # HMX scoring-template contract was introduced.
    quality_weight = models.DecimalField(max_digits=5, decimal_places=2, default=40, editable=False)
    delivery_weight = models.DecimalField(max_digits=5, decimal_places=2, default=30, editable=False)
    service_weight = models.DecimalField(max_digits=5, decimal_places=2, default=30, editable=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_vendor_performance_template_code")]

    def clean(self):
        errors = {}
        if not " ".join((self.name or "").split()):
            errors["name"] = "Template name is required."
        if self.qualitative_weight + self.quantitative_weight != Decimal("100"):
            errors["qualitative_weight"] = "Qualitative and quantitative weights must total 100%."
        if self.delivery_accuracy_weight + self.rejection_rate_weight != Decimal("100"):
            errors["delivery_accuracy_weight"] = "Delivery accuracy and rejection rate weights must total 100%."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            persisted_code = type(self).objects.filter(pk=self.pk).values_list("code", flat=True).first()
            if persisted_code and self.code != persisted_code:
                raise ValidationError({"code": "Template code cannot be changed."})
        elif self.code:
            raise ValidationError({"code": "Template code is generated automatically."})
        if not self.code:
            self.code = f"VPT-{uuid4().hex[:12].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.name}"


class VendorPerformanceTemplateLine(TimeStampedModel):
    template = models.ForeignKey(VendorPerformanceTemplate, on_delete=models.CASCADE, related_name="lines")
    sequence = models.PositiveIntegerField(default=10)
    parameter_name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    weight = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    maximum_score = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=5,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    mandatory = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["template", "sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["template", "parameter_name"],
                name="unique_vendor_performance_template_parameter",
            )
        ]

    def clean(self):
        errors = {}
        normalized = " ".join((self.parameter_name or "").split())
        if not normalized:
            errors["parameter_name"] = "Parameter name is required."
        elif type(self).objects.filter(
            template_id=self.template_id,
            parameter_name__iexact=normalized,
        ).exclude(pk=self.pk).exists():
            errors["parameter_name"] = "Parameter names must be unique within a template."
        if self.weight < 0 or self.weight > 100:
            errors["weight"] = "Weight must be between 0 and 100."
        if errors:
            raise ValidationError(errors)
        self.parameter_name = normalized

    def save(self, *args, **kwargs):
        self.parameter_name = " ".join((self.parameter_name or "").split())
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.template} - {self.parameter_name}"


class VendorPerformanceEvaluation(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        WAITING_APPROVAL = "waiting_approval", "Waiting Approval"
        CONFIRMED = "confirmed", "Confirmed"
        REJECTED = "rejected", "Rejected"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="vendor_performance_evaluations")
    number = models.CharField(max_length=64, blank=True, editable=False)
    vendor = models.ForeignKey(VendorProfile, on_delete=models.PROTECT, related_name="performance_evaluations")
    template = models.ForeignKey(VendorPerformanceTemplate, on_delete=models.PROTECT, related_name="evaluations")
    evaluator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="vendor_performance_evaluations",
    )
    order = models.ForeignKey(PurchaseOrder, on_delete=models.SET_NULL, blank=True, null=True, related_name="vendor_evaluations")
    period_start = models.DateField()
    period_end = models.DateField()
    qualitative_score = models.DecimalField(max_digits=8, decimal_places=2, default=0, editable=False)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="confirmed_vendor_performance_evaluations",
        editable=False,
    )
    confirmed_at = models.DateTimeField(blank=True, null=True, editable=False)
    rejection_reason = models.TextField(blank=True, editable=False)
    # Legacy fixed-category score fields remain readable for old records only.
    quality_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)], editable=False)
    delivery_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)], editable=False)
    service_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)], editable=False)
    overall_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, editable=False)
    rating = models.CharField(max_length=16, default="fair", editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-period_end"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "number"],
                name="unique_vendor_performance_evaluation_number",
            )
        ]

    def clean(self):
        errors = {}
        if self.period_end <= self.period_start:
            errors["period_end"] = "Period end must be after period start."
        if self.vendor_id and self.company_id != self.vendor.company_id:
            errors["vendor"] = "Vendor must belong to the selected company."
        if self.template_id and self.company_id != self.template.company_id:
            errors["template"] = "Template must belong to the selected company."
        if self.template_id and not self.template.active:
            errors["template"] = "The selected template must be active."
        if self.order_id and self.order.company_id != self.company_id:
            errors["order"] = "Purchase order must belong to the selected company."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        previous = None
        if not is_new:
            previous = type(self).objects.filter(pk=self.pk).values(
                "number",
                "template_id",
                "status",
                "company_id",
                "vendor_id",
                "evaluator_id",
                "period_start",
                "period_end",
                "order_id",
                "notes",
            ).first()
            if previous and previous["number"] and self.number != previous["number"]:
                raise ValidationError({"number": "Evaluation number cannot be changed."})
            if previous and previous["status"] != self.Status.DRAFT:
                editable_values = {
                    "company_id": self.company_id,
                    "vendor_id": self.vendor_id,
                    "template_id": self.template_id,
                    "evaluator_id": self.evaluator_id,
                    "period_start": self.period_start,
                    "period_end": self.period_end,
                    "order_id": self.order_id,
                    "notes": self.notes,
                }
                if any(previous[key] != value for key, value in editable_values.items()):
                    raise ValidationError("Only draft Vendor Performance Evaluations can be modified.")
        elif self.number:
            raise ValidationError({"number": "Evaluation number is generated automatically."})
        if not self.number:
            self.number = f"VPE-{uuid4().hex[:12].upper()}"
        if self.vendor_id and not self.company_id:
            self.company_id = self.vendor.company_id
        super().save(*args, **kwargs)
        template_changed = is_new or (previous and previous["template_id"] != self.template_id)
        if template_changed and self.status == self.Status.DRAFT:
            VendorPerformanceEvaluationLine.objects.filter(evaluation_id=self.pk).delete()
            template_lines = list(self.template.lines.order_by("sequence", "id")[:500])
            VendorPerformanceEvaluationLine.objects.bulk_create(
                [
                    VendorPerformanceEvaluationLine(
                        evaluation_id=self.pk,
                        sequence=line.sequence,
                        parameter_name=line.parameter_name,
                        description=line.description,
                        weight=line.weight,
                        maximum_score=line.maximum_score,
                        mandatory=line.mandatory,
                        active=line.active,
                    )
                    for line in template_lines
                ]
            )

    def delete(self, *args, **kwargs):
        if self.status != self.Status.DRAFT:
            raise ValidationError("Only draft Vendor Performance Evaluations can be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return self.number or f"{self.vendor} - {self.period_end}"


class VendorPerformanceEvaluationLine(TimeStampedModel):
    class Assessment(models.TextChoices):
        VERY_POOR = "Very_Poor", "Very Poor"
        POOR = "Poor", "Poor"
        FAIR = "Fair", "Fair"
        GOOD = "Good", "Good"
        EXCELLENT = "Excellent", "Excellent"

    evaluation = models.ForeignKey(VendorPerformanceEvaluation, on_delete=models.CASCADE, related_name="lines")
    sequence = models.PositiveIntegerField(default=10, editable=False)
    parameter_name = models.CharField(max_length=255, editable=False)
    description = models.TextField(blank=True, editable=False)
    weight = models.DecimalField(max_digits=6, decimal_places=2, default=0, editable=False)
    maximum_score = models.DecimalField(max_digits=8, decimal_places=2, default=5, editable=False)
    mandatory = models.BooleanField(default=False, editable=False)
    active = models.BooleanField(default=True, editable=False)
    assessment = models.CharField(max_length=20, choices=Assessment.choices, blank=True)
    officer_comment = models.TextField(blank=True)
    score = models.DecimalField(max_digits=8, decimal_places=2, blank=True, null=True)

    class Meta:
        ordering = ["evaluation", "sequence", "id"]

    def clean(self):
        errors = {}
        if self.evaluation_id and self.evaluation.status != VendorPerformanceEvaluation.Status.DRAFT:
            errors["evaluation"] = "Only draft Vendor Performance Evaluation lines can be changed."
        if self.score is not None and self.score < 0:
            errors["score"] = "Score cannot be negative."
        if self.score is not None and self.score > self.maximum_score:
            errors["score"] = "Score cannot exceed the snapshot maximum score."
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "evaluation_id",
                "sequence",
                "parameter_name",
                "description",
                "weight",
                "maximum_score",
                "mandatory",
                "active",
            ).first()
            if previous:
                for field_name, old_value in previous.items():
                    if getattr(self, field_name) != old_value:
                        error_field = "evaluation" if field_name == "evaluation_id" else field_name
                        errors[error_field] = "Evaluation snapshot fields cannot be changed."
        if errors:
            raise ValidationError(errors)

    def delete(self, *args, **kwargs):
        raise ValidationError("Evaluation lines can only be replaced by changing the draft template.")

    def __str__(self):
        return f"{self.evaluation} - {self.parameter_name}"


class VendorPerformanceScoreHistory(TimeStampedModel):
    class Status(models.TextChoices):
        CALCULATED = "calculated", "Calculated"
        PARTIAL = "partial", "Partially Calculated"
        INSUFFICIENT = "insufficient", "Insufficient Transaction Data"
        BLOCKED = "blocked", "Blocked"

    vendor = models.ForeignKey(VendorProfile, on_delete=models.PROTECT, related_name="performance_score_history")
    template = models.ForeignKey(VendorPerformanceTemplate, on_delete=models.PROTECT, related_name="score_history")
    period_start = models.DateField()
    period_end = models.DateField()
    calculated_at = models.DateTimeField(default=timezone.now)
    qualitative_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    delivery_accuracy_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    rejection_rate_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    quantitative_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    final_score = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.CALCULATED)
    source_transaction_summary = models.JSONField(default=dict)
    template_snapshot = models.JSONField(default=dict)
    qualitative_evaluation = models.ForeignKey(
        VendorPerformanceEvaluation,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="score_history",
    )
    evaluator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="vendor_performance_score_history",
    )

    class Meta:
        ordering = ["-calculated_at", "-id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Score history is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Score history is immutable.")

    def __str__(self):
        return f"{self.vendor} - {self.period_end}"


class PurchaseTeam(TimeStampedModel):
    class RoutingParameter(models.TextChoices):
        PRODUCT_CATEGORY = "product_category", "Product Category"
        ORDER_TYPE = "order_type", "Order Type"
        VENDOR_CATEGORY = "vendor_category", "Vendor Category"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_teams")
    code = models.CharField(max_length=100, blank=True, editable=False)
    name = models.CharField(max_length=120)
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="managed_purchase_teams")
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=16,
        choices=[("active", "Active"), ("inactive", "Inactive")],
        default="active",
    )
    parameter = models.CharField(
        max_length=24,
        choices=RoutingParameter.choices,
        default=RoutingParameter.PRODUCT_CATEGORY,
    )
    product_categories = models.ManyToManyField(
        "PurchaseProductCategory",
        blank=True,
        related_name="purchase_teams",
    )
    order_types = models.ManyToManyField(
        "PurchaseOrderType",
        blank=True,
        related_name="purchase_teams",
    )
    vendor_categories = models.ManyToManyField(
        VendorCategory,
        blank=True,
        related_name="purchase_teams",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_purchase_team_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}" if self.code else self.name

    def clean(self):
        super().clean()
        errors = {}
        if self.manager_id and self.company_id:
            manager_companies = getattr(self.manager, "companies", None)
            if (
                manager_companies is not None
                and manager_companies.exists()
                and not manager_companies.filter(pk=self.company_id).exists()
            ):
                errors["manager"] = "Team Lead must have access to the Purchase Team company."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = f"PT-{uuid4().hex[:8].upper()}"
        self.status = "active" if self.active else "inactive"
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"code", "status"}
        result = super().save(*args, **kwargs)
        if self.manager_id:
            PurchaseTeamMember.objects.get_or_create(
                team_id=self.pk,
                user_id=self.manager_id,
                defaults={"role": "Team Lead"},
            )
        return result

    def delete(self, *args, **kwargs):
        if self.code:
            raise ValidationError("Cannot delete a Purchase Team. Archive it instead.")
        return super().delete(*args, **kwargs)


class PurchaseTeamMember(TimeStampedModel):
    team = models.ForeignKey(PurchaseTeam, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_team_memberships")
    role = models.CharField(max_length=80, default="Buyer")
    categories = models.ManyToManyField(
        "PurchaseProductCategory",
        blank=True,
        related_name="purchase_team_member_assignments",
    )
    is_primary = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["team__name", "user__username"]
        constraints = [models.UniqueConstraint(fields=["team", "user"], name="unique_purchase_team_member")]

    def __str__(self):
        return f"{self.team} - {self.user}"

    def clean(self):
        super().clean()
        errors = {}
        if self.team_id and self.user_id:
            user_companies = getattr(self.user, "companies", None)
            if (
                user_companies is not None
                and user_companies.exists()
                and not user_companies.filter(pk=self.team.company_id).exists()
            ):
                errors["user"] = "Member must have access to the Purchase Team company."
        if self.is_primary and self.pk and not self.categories.exists():
            errors["is_primary"] = "A Primary Buyer must have at least one assigned category."
        if self.pk and self.team_id and self.categories.exclude(company_id=self.team.company_id).exists():
            errors["categories"] = "Assigned categories must belong to the Purchase Team company."
        if errors:
            raise ValidationError(errors)

    def delete(self, *args, **kwargs):
        if self.team_id and self.user_id == self.team.manager_id:
            raise ValidationError("The Team Lead cannot be removed from the Purchase Team.")
        return super().delete(*args, **kwargs)


class PurchaseKPITemplate(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_kpi_templates")
    code = models.CharField(max_length=30, blank=True)
    name = models.CharField(max_length=120)
    unit = models.CharField(max_length=40, default="percent", blank=True)
    direction = models.CharField(max_length=20, default="higher_is_better", blank=True)
    description = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1, editable=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_purchase_kpi_template_code")]

    def __str__(self):
        return f"{self.code} - {self.name}" if self.code else self.name

    @property
    def total_weight(self):
        if not self.pk:
            return Decimal("0")
        return self.lines.aggregate(total=models.Sum("weight"))["total"] or Decimal("0")

    def validate_ready(self):
        lines = list(self.lines.order_by("sequence", "pk")[:101])
        if not lines:
            raise ValidationError("A KPI Template must contain at least one KPI parameter.")
        if len(lines) > 100:
            raise ValidationError("A KPI Template supports at most 100 KPI parameters.")
        if sum((line.weight for line in lines), Decimal("0")) != Decimal("100.00"):
            raise ValidationError("Total KPI Template line weight must equal exactly 100%.")

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = f"KPI-{uuid4().hex[:8].upper()}"
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.targets.exists():
            raise ValidationError("KPI Template cannot be deleted after it is used. Deactivate it instead.")
        return super().delete(*args, **kwargs)


class PurchaseKPITemplateLine(TimeStampedModel):
    template = models.ForeignKey(PurchaseKPITemplate, on_delete=models.CASCADE, related_name="lines")
    sequence = models.PositiveIntegerField(default=10)
    metric = models.CharField(max_length=50, choices=PURCHASE_KPI_METRIC_CHOICES)
    weight = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    default_target = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    unit = models.CharField(max_length=64, blank=True)
    direction = models.CharField(
        max_length=24,
        choices=[("higher_is_better", "Higher is Better"), ("lower_is_better", "Lower is Better")],
        blank=True,
    )
    calculation = models.TextField(blank=True)

    class Meta:
        ordering = ["template", "sequence", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["template", "metric"], name="unique_purchase_kpi_metric_per_template")
        ]

    def clean(self):
        super().clean()
        definition = PURCHASE_KPI_DEFINITIONS.get(self.metric)
        if not definition:
            raise ValidationError({"metric": "Select a permitted Purchase KPI definition."})
        if self.weight <= 0:
            raise ValidationError({"weight": "Weight must be greater than zero."})
        if self.weight > 100:
            raise ValidationError({"weight": "Weight cannot exceed 100%."})

    def save(self, *args, **kwargs):
        definition = PURCHASE_KPI_DEFINITIONS.get(self.metric, {})
        self.unit = self.unit or definition.get("unit", "")
        self.direction = self.direction or definition.get("direction", "")
        self.calculation = self.calculation or definition.get("calculation", "")
        adding = self._state.adding
        previous = None
        if not adding:
            previous = type(self).objects.filter(pk=self.pk).values(
                "sequence", "metric", "weight", "default_target", "unit", "direction", "calculation"
            ).first()
        result = super().save(*args, **kwargs)
        current = {
            "sequence": self.sequence,
            "metric": self.metric,
            "weight": self.weight,
            "default_target": self.default_target,
            "unit": self.unit,
            "direction": self.direction,
            "calculation": self.calculation,
        }
        if adding or previous != current:
            PurchaseKPITemplate.objects.filter(pk=self.template_id).update(version=models.F("version") + 1)
        return result

    def delete(self, *args, **kwargs):
        template_id = self.template_id
        result = super().delete(*args, **kwargs)
        PurchaseKPITemplate.objects.filter(pk=template_id).update(version=models.F("version") + 1)
        return result

    def __str__(self):
        return f"{self.template} - {self.get_metric_display()}"


class PurchaseKPITarget(TimeStampedModel):
    class Scope(models.TextChoices):
        TEAM = "team", "Team"
        BUYER = "buyer", "Purchase Person"

    class PeriodType(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        ANNUAL = "annual", "Annual"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        WAITING_APPROVAL = "waiting_approval", "Waiting Approval"
        ACTIVE = "active", "Active"
        REJECTED = "rejected", "Rejected"
        CLOSED = "closed", "Closed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_kpi_targets")
    name = models.CharField(max_length=255)
    team = models.ForeignKey(PurchaseTeam, on_delete=models.PROTECT, related_name="kpi_targets", blank=True, null=True)
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="purchase_kpi_targets",
        blank=True,
        null=True,
    )
    target_scope = models.CharField(max_length=20, choices=Scope.choices, default=Scope.TEAM)
    template = models.ForeignKey(PurchaseKPITemplate, on_delete=models.PROTECT, related_name="targets")
    period_type = models.CharField(max_length=20, choices=PeriodType.choices, default=PeriodType.MONTHLY)
    period_start = models.DateField()
    period_end = models.DateField()
    target_value = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    actual_value = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    template_version = models.PositiveIntegerField(blank=True, null=True, editable=False)
    template_batch = models.UUIDField(default=uuid4, editable=False)
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="activated_purchase_kpi_targets",
        blank=True,
        null=True,
        editable=False,
    )
    activated_at = models.DateTimeField(blank=True, null=True, editable=False)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="closed_purchase_kpi_targets",
        blank=True,
        null=True,
        editable=False,
    )
    closed_at = models.DateTimeField(blank=True, null=True, editable=False)

    class Meta:
        ordering = ["-period_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "template", "period_start"],
                condition=models.Q(team__isnull=False),
                name="unique_purchase_kpi_target_team_period",
            ),
            models.UniqueConstraint(
                fields=["buyer", "template", "period_start"],
                condition=models.Q(buyer__isnull=False),
                name="unique_purchase_kpi_target_buyer_period",
            ),
        ]

    def __str__(self):
        return self.name or f"{self.team or self.buyer} - {self.template}"

    def clean(self):
        super().clean()
        errors = {}
        if self.period_start and self.period_end and self.period_start > self.period_end:
            errors["period_end"] = "Target Period End must be on or after Target Period Start."
        if self.target_scope == self.Scope.TEAM:
            if not self.team_id:
                errors["team"] = "Purchase Team is required for Team Scope."
            if self.buyer_id:
                errors["buyer"] = "Team Scope does not use an individual Buyer."
        elif self.target_scope == self.Scope.BUYER:
            if not self.buyer_id:
                errors["buyer"] = "Buyer is required for Buyer Scope."
            if self.team_id:
                errors["team"] = "Purchase Team must be empty for Buyer Scope."
        if self.team_id and self.company_id and self.team.company_id != self.company_id:
            errors["team"] = "Purchase Team must belong to the KPI Target company."
        if self.template_id and self.company_id and self.template.company_id != self.company_id:
            errors["template"] = "KPI Template must belong to the KPI Target company."
        if self.pk and self.status != self.Status.DRAFT:
            old = type(self).objects.filter(pk=self.pk).values("template_id").first()
            if old and old["template_id"] != self.template_id:
                errors["template"] = "Only a Draft KPI Target can change its KPI Template."
        if errors:
            raise ValidationError(errors)

    def validate_ready(self):
        self.full_clean()
        self.template.validate_ready()
        lines = list(self.lines.order_by("sequence", "pk")[:101])
        if not lines:
            raise ValidationError("A KPI Target must contain at least one KPI line.")
        if len(lines) > 100:
            raise ValidationError("A KPI Target supports at most 100 KPI lines.")
        if sum((line.snapshot_weight for line in lines), Decimal("0")) != Decimal("100.00"):
            raise ValidationError("KPI Target line weights must total exactly 100%.")

    def save(self, *args, **kwargs):
        adding = self._state.adding
        if not self.company_id and self.team_id:
            self.company_id = self.team.company_id
        if not self.name:
            self.name = f"{self.template.name} {self.period_start:%b %Y}" if self.period_start else self.template.name
        if adding:
            self.template_version = self.template.version
        result = super().save(*args, **kwargs)
        if adding and not self.lines.exists():
            template_lines = list(self.template.lines.order_by("sequence", "pk")[:101])
            if len(template_lines) > 100:
                raise ValidationError("A KPI Target supports at most 100 KPI lines.")
            PurchaseKPITargetLine.objects.bulk_create(
                [
                    PurchaseKPITargetLine(
                        target=self,
                        sequence=line.sequence,
                        metric=line.metric,
                        target_value=line.default_target,
                        snapshot_weight=line.weight,
                        snapshot_target_value=line.default_target,
                        snapshot_unit=line.unit,
                        snapshot_direction=line.direction,
                        snapshot_calculation=line.calculation,
                    )
                    for line in template_lines
                ],
                batch_size=100,
            )
        return result

    @property
    def achievement_percent(self):
        lines = list(self.lines.only("actual_value", "target_value", "snapshot_weight", "snapshot_direction")[:101])
        if not lines:
            return Decimal("0")
        weighted = sum((line.achievement_percent * line.snapshot_weight for line in lines), Decimal("0"))
        total_weight = sum((line.snapshot_weight for line in lines), Decimal("0"))
        return (weighted / total_weight).quantize(Decimal("0.01")) if total_weight else Decimal("0")


class PurchaseKPITargetLine(TimeStampedModel):
    target = models.ForeignKey(PurchaseKPITarget, on_delete=models.CASCADE, related_name="lines")
    sequence = models.PositiveIntegerField(default=10)
    metric = models.CharField(max_length=50, choices=PURCHASE_KPI_METRIC_CHOICES)
    target_value = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    actual_value = models.DecimalField(max_digits=18, decimal_places=4, default=0, editable=False)
    snapshot_weight = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    snapshot_target_value = models.DecimalField(max_digits=18, decimal_places=4, default=0, editable=False)
    snapshot_unit = models.CharField(max_length=64, blank=True, editable=False)
    snapshot_direction = models.CharField(max_length=24, blank=True, editable=False)
    snapshot_calculation = models.TextField(blank=True, editable=False)
    calculation_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="activated_purchase_kpi_target_lines",
        blank=True,
        null=True,
        editable=False,
    )
    activated_at = models.DateTimeField(blank=True, null=True, editable=False)
    last_calculated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="calculated_purchase_kpi_lines",
        blank=True,
        null=True,
        editable=False,
    )
    last_calculated_at = models.DateTimeField(blank=True, null=True, editable=False)

    class Meta:
        ordering = ["target", "sequence", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["target", "metric"], name="unique_purchase_kpi_metric_per_target")
        ]

    @property
    def achievement_percent(self):
        if self.target_value == 0:
            return Decimal("0")
        if self.snapshot_direction == "lower_is_better":
            if self.actual_value <= 0:
                return Decimal("0")
            return min((self.target_value / self.actual_value) * Decimal("100"), Decimal("100"))
        return min((self.actual_value / self.target_value) * Decimal("100"), Decimal("100"))

    @property
    def performance_status(self):
        achievement = self.achievement_percent
        if achievement >= 90:
            return "On Track"
        if achievement >= 70:
            return "At Risk"
        return "Behind"

    def clean(self):
        super().clean()
        if self.metric not in PURCHASE_KPI_DEFINITIONS:
            raise ValidationError({"metric": "Select a permitted Purchase KPI definition."})
        if self.snapshot_weight <= 0 or self.snapshot_weight > 100:
            raise ValidationError({"snapshot_weight": "KPI weight must be greater than zero and no more than 100%."})
        if self.pk and self.target.status != PurchaseKPITarget.Status.DRAFT:
            previous = type(self).objects.filter(pk=self.pk).values("target_value", "metric", "snapshot_weight").first()
            if previous and (
                previous["target_value"] != self.target_value
                or previous["metric"] != self.metric
                or previous["snapshot_weight"] != self.snapshot_weight
            ):
                raise ValidationError("Only Draft KPI Target lines can be edited.")

    def __str__(self):
        return f"{self.target} - {self.get_metric_display()}"


class PurchaseOrderType(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_order_types")
    companies = models.ManyToManyField(Company, blank=True, related_name="available_purchase_order_types")
    code = models.CharField(max_length=30, blank=True)
    name = models.CharField(max_length=120)
    sequence_prefix = models.CharField(max_length=20, default="PO")
    prefix_sequence = models.CharField(max_length=10, blank=True, editable=False)
    icon = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    product_categories = models.ManyToManyField(
        "PurchaseProductCategory",
        blank=True,
        related_name="purchase_order_types",
    )
    requires_approval = models.BooleanField(default=True)
    allow_direct_purchase = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_purchase_order_type_code")]

    def __str__(self):
        return f"{self.code} - {self.name}" if self.code else self.name

    def _generate_prefix(self):
        words = [word for word in (self.name or "").split() if word]
        base = ("".join(word[0] for word in words).upper() or "POT")[:10]
        candidates = [base]
        for index in range(2, 1000):
            suffix = str(index)
            candidates.append(f"{base[: 10 - len(suffix)]}{suffix}")
        existing = set(
            type(self).objects.exclude(pk=self.pk)
            .exclude(prefix_sequence="")
            .filter(prefix_sequence__in=candidates)
            .values_list("prefix_sequence", flat=True)[:999]
        )
        for candidate in candidates:
            if candidate not in existing:
                return candidate
        raise ValidationError("Could not allocate a unique Purchase Order Type prefix.")

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = f"POT-{uuid4().hex[:8].upper()}"
        if not self.prefix_sequence:
            self.prefix_sequence = self._generate_prefix()
        self.sequence_prefix = self.prefix_sequence
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.orders.exists() or self.templates.exists():
            raise ValidationError("Purchase Order Type is in use. Archive it instead.")
        return super().delete(*args, **kwargs)


class PurchaseOrderTypeSequenceLine(TimeStampedModel):
    order_type = models.ForeignKey(PurchaseOrderType, on_delete=models.CASCADE, related_name="sequence_lines")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="purchase_order_type_sequences")
    period = models.CharField(max_length=7, blank=True, editable=False)
    purchase_request_next = models.PositiveIntegerField(default=1, editable=False)
    purchase_tender_next = models.PositiveIntegerField(default=1, editable=False)
    blanket_order_next = models.PositiveIntegerField(default=1, editable=False)
    rfq_next = models.PositiveIntegerField(default=1, editable=False)
    purchase_order_draft_next = models.PositiveIntegerField(default=1, editable=False)
    direct_purchase_order_next = models.PositiveIntegerField(default=1, editable=False)
    purchase_order_next = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["order_type__name", "company__name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["order_type", "company"],
                name="unique_purchase_order_type_company_sequence",
            )
        ]

    def __str__(self):
        return f"{self.order_type} / {self.company}"


class PurchaseOrderTemplate(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_order_templates")
    vendor = models.ForeignKey(
        Contact,
        on_delete=models.PROTECT,
        related_name="purchase_order_templates",
        blank=True,
        null=True,
    )
    order_type = models.ForeignKey(
        PurchaseOrderType,
        on_delete=models.PROTECT,
        related_name="templates",
        blank=True,
        null=True,
    )
    name = models.CharField(max_length=120)
    payment_terms = models.TextField(blank=True)
    delivery_terms = models.TextField(blank=True)
    legal_terms = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_purchase_order_template_name")]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        errors = {}
        if self.vendor_id and self.vendor.company_id != self.company_id:
            errors["vendor"] = "Vendor must belong to the Purchase Order Template company."
        if self.order_type_id and self.order_type.company_id != self.company_id and not self.order_type.companies.filter(pk=self.company_id).exists():
            errors["order_type"] = "Order Type must be available to the Purchase Order Template company."
        if errors:
            raise ValidationError(errors)


class PurchaseOrderTemplateLine(TimeStampedModel):
    order_template = models.ForeignKey(PurchaseOrderTemplate, on_delete=models.CASCADE, related_name="lines")
    sequence = models.PositiveIntegerField(default=10)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="purchase_order_template_lines")
    description = models.CharField(max_length=255, blank=True)
    quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=1,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    uom = models.CharField(max_length=24, blank=True)
    price = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        ordering = ["order_template", "sequence", "pk"]

    def clean(self):
        super().clean()
        errors = {}
        if self.product_id and self.order_template_id and self.product.company_id != self.order_template.company_id:
            errors["product"] = "Product must belong to the Purchase Order Template company."
        if self.quantity <= 0:
            errors["quantity"] = "Quantity must be greater than zero."
        if self.price < 0:
            errors["price"] = "Price cannot be negative."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.product_id:
            self.description = self.description or self.product.name
            self.uom = self.uom or self.product.unit
            if self._state.adding and not self.price:
                self.price = self.product.purchase_price
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order_template} - {self.product}"


class PurchaseRemarkTemplate(TimeStampedModel):
    class RemarkType(models.TextChoices):
        GENERAL = "general", "General"
        DELIVERY = "delivery", "Delivery"
        PAYMENT = "payment", "Payment"
        WARRANTY = "warranty", "Warranty"
        TERMS = "terms_conditions", "Terms & Conditions"

    class ApplicableDocument(models.TextChoices):
        PURCHASE_REQUEST = "pr", "Purchase Request"
        PURCHASE_ORDER = "po", "Purchase Order"
        ALL = "all", "Purchase Request & Purchase Order"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_remark_templates")
    code = models.CharField(max_length=64, blank=True, editable=False)
    name = models.CharField(max_length=255)
    remark_type = models.CharField(max_length=32, choices=RemarkType.choices, default=RemarkType.GENERAL)
    applicable_document = models.CharField(
        max_length=8,
        choices=ApplicableDocument.choices,
        default=ApplicableDocument.ALL,
    )
    content = models.TextField()
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="unique_purchase_remark_template_code",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            persisted_code = type(self).objects.filter(pk=self.pk).values_list("code", flat=True).first()
            if persisted_code and self.code != persisted_code:
                raise ValidationError({"code": "Remark Template code cannot be changed."})
        elif self.code:
            raise ValidationError({"code": "Remark Template code is generated automatically."})
        if not self.code:
            self.code = f"PRM-{self.company.code}-{uuid4().hex[:12].upper()}"[:64]
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.name}"


class PurchaseMilestoneTemplate(TimeStampedModel):
    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="purchase_milestone_templates",
    )
    order_template = models.ForeignKey(
        PurchaseOrderTemplate,
        on_delete=models.CASCADE,
        related_name="milestone_templates",
        blank=True,
        null=True,
    )
    sequence = models.PositiveIntegerField(default=10)
    name = models.CharField(max_length=120)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    due_days = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["company", "name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["order_template", "sequence"],
                condition=models.Q(order_template__isnull=False),
                name="unique_purchase_milestone_template_sequence",
            )
        ]

    def __str__(self):
        return self.name

    @property
    def total_contract_terms(self):
        if not self.pk:
            return Decimal("0")
        return self.lines.aggregate(total=models.Sum("contract_term_percentage"))["total"] or Decimal("0")

    def validate_ready(self):
        lines = list(self.lines.order_by("sequence", "pk")[:101])
        if not lines:
            raise ValidationError("At least one milestone line is required.")
        if len(lines) > 100:
            raise ValidationError("A Milestone Template supports at most 100 lines.")
        if sum((line.contract_term_percentage for line in lines), Decimal("0")) != Decimal("100.00"):
            raise ValidationError("Total Contract Terms must equal exactly 100%.")


class PurchaseMilestoneTemplateLine(TimeStampedModel):
    template = models.ForeignKey(PurchaseMilestoneTemplate, on_delete=models.CASCADE, related_name="lines")
    sequence = models.PositiveIntegerField(default=10)
    milestone_name = models.CharField(max_length=255)
    contract_term_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MaxValueValidator(100)],
    )
    due_days = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["template", "sequence", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["template", "sequence"],
                name="unique_purchase_milestone_template_line_sequence",
            )
        ]

    def clean(self):
        super().clean()
        if self.contract_term_percentage <= 0:
            raise ValidationError({"contract_term_percentage": "Contract Term must be greater than zero."})

    def __str__(self):
        return f"{self.template} - {self.milestone_name}"


class PurchaseSetting(TimeStampedModel):
    class SequenceMode(models.TextChoices):
        AUTOMATIC = "auto", "Automatic"
        MANUAL = "manual", "Manual"

    class RFQVendorEligibility(models.TextChoices):
        APPROVED_ONLY = "approved_only", "Approved Vendors Only"
        ALLOW_DRAFT = "allow_draft_on_rfq", "Allow Draft Vendors on RFQ"

    class ReceivingBehavior(models.TextChoices):
        MANUAL = "manual", "Create goods receipts manually"
        AUTO = "auto", "Prepare draft goods receipts on PO approval"

    class ValidateBillBehavior(models.TextChoices):
        MANUAL = "manual", "Create vendor bill in draft"
        AUTO = "auto", "Post and pay vendor bill automatically"

    class ThreeWayMatchMode(models.TextChoices):
        MONITORING_ONLY = "monitoring_only", "Monitoring Only"
        STRICT = "strict", "Strict"

    class RevisionSequenceMode(models.TextChoices):
        APPEND_SUFFIX = "append_suffix", "Append Revision Suffix"
        FOLLOW_SEQUENCE = "follow_sequence", "Follow Sequence Master Data"

    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="purchase_setting")
    vendor_sequence_mode = models.CharField(
        max_length=16,
        choices=SequenceMode.choices,
        default=SequenceMode.AUTOMATIC,
    )
    default_currency = models.CharField(max_length=3, default="IDR")
    request_approval_levels = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(5)])
    order_approval_levels = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(5)])
    allow_overbudget = models.BooleanField(default=False)
    receipt_tolerance_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    receiving_behavior = models.CharField(
        max_length=12,
        choices=ReceivingBehavior.choices,
        default=ReceivingBehavior.MANUAL,
    )
    validate_bill_behavior = models.CharField(
        max_length=12,
        choices=ValidateBillBehavior.choices,
        default=ValidateBillBehavior.MANUAL,
    )
    credit_limit = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Default Purchase Limit",
    )
    is_soft_block_limit = models.BooleanField(default=False, verbose_name="Soft Block Limit")
    soft_block_threshold_percent = models.DecimalField(
        max_digits=9,
        decimal_places=4,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Soft Block Threshold Percentage",
    )
    is_warning_block_limit = models.BooleanField(default=False, verbose_name="Warning Block Limit")
    warning_block_threshold_percent = models.DecimalField(
        max_digits=9,
        decimal_places=4,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Warning Block Threshold Percentage",
    )
    is_hard_block_limit = models.BooleanField(default=False, verbose_name="Hard Block Limit")
    hard_block_threshold_percent = models.DecimalField(
        max_digits=9,
        decimal_places=4,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Hard Block Threshold Percentage",
    )
    purchase_three_way_match_mode = models.CharField(
        max_length=20,
        choices=ThreeWayMatchMode.choices,
        default=ThreeWayMatchMode.MONITORING_ONLY,
        verbose_name="3-Way Matching Mode",
    )
    purchase_match_quantity_tolerance = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Quantity Tolerance",
        help_text="Allowed absolute quantity variance in the Purchase Order line unit.",
    )
    purchase_match_price_tolerance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name="Unit Price Tolerance",
        help_text="Allowed absolute unit-price variance in the Purchase Order currency.",
    )
    restrict_product_by_vendor_pricelist = models.BooleanField(
        default=False,
        verbose_name="Product Vendor Pricelist Filter",
        help_text=(
            "When enabled, Purchase Order and Blanket Order lines may only use products in an active "
            "vendor pricelist valid on the transaction date."
        ),
    )
    is_use_vendor_payment_terms = models.BooleanField(
        default=True,
        verbose_name="Use Vendor Default Payment Terms",
    )
    allow_order_revision = models.BooleanField(
        default=False,
        verbose_name="Allow Order Revision",
        help_text="Allow eligible Purchase Orders to create a new draft revision.",
    )
    order_revision_sequence_mode = models.CharField(
        max_length=32,
        choices=RevisionSequenceMode.choices,
        blank=True,
        default="",
        verbose_name="Revised Document Sequence Numbering",
    )
    tender_award_response_working_days = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(260)],
    )
    pr_expiry_period_days = models.PositiveIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1)],
    )
    rfq_expiry_period_days = models.PositiveIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1)],
    )
    pt_expiry_period_days = models.PositiveIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1)],
    )
    require_vendor_prequalification = models.BooleanField(default=True)
    rfq_vendor_eligibility = models.CharField(
        max_length=24,
        choices=RFQVendorEligibility.choices,
        default=RFQVendorEligibility.APPROVED_ONLY,
    )
    vendor_performance_scoring_enabled = models.BooleanField(default=False)
    vendor_performance_evaluation_period = models.PositiveIntegerField(
        default=12,
        validators=[MinValueValidator(1)],
    )
    vendor_performance_recalculation_enabled = models.BooleanField(default=False)
    vendor_performance_default_template = models.ForeignKey(
        VendorPerformanceTemplate,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="default_for_purchase_settings",
    )

    class Meta:
        ordering = ["company__name"]

    def clean(self):
        errors = {}
        if (
            self.is_warning_block_limit
            and self.is_soft_block_limit
            and self.warning_block_threshold_percent >= self.soft_block_threshold_percent
        ):
            errors["warning_block_threshold_percent"] = (
                "Warning threshold must be less than Soft Block threshold."
            )
        if (
            self.is_soft_block_limit
            and self.is_hard_block_limit
            and self.soft_block_threshold_percent >= self.hard_block_threshold_percent
        ):
            errors["soft_block_threshold_percent"] = (
                "Soft Block threshold must be less than Hard Block threshold."
            )
        template = self.vendor_performance_default_template
        if template and template.company_id != self.company_id:
            errors["vendor_performance_default_template"] = (
                "The default Vendor Performance Template must belong to the Purchase Settings company."
            )
        if self.vendor_performance_scoring_enabled:
            if not template:
                errors["vendor_performance_default_template"] = (
                    "A default Vendor Performance Template is required when scoring is enabled."
                )
            elif not template.active:
                errors["vendor_performance_default_template"] = (
                    "The default Vendor Performance Template must be active."
                )
            else:
                if template.qualitative_weight + template.quantitative_weight != Decimal("100"):
                    errors["vendor_performance_default_template"] = (
                        "Template qualitative and quantitative weights must total 100%."
                    )
                elif template.delivery_accuracy_weight + template.rejection_rate_weight != Decimal("100"):
                    errors["vendor_performance_default_template"] = (
                        "Template delivery accuracy and rejection rate weights must total 100%."
                    )
                else:
                    active_lines = template.lines.filter(active=True).aggregate(
                        count=models.Count("pk"),
                        total=models.Sum("weight"),
                    )
                    if not active_lines["count"]:
                        errors["vendor_performance_default_template"] = (
                            "The default Vendor Performance Template requires at least one active criterion."
                        )
                    elif active_lines["total"] != Decimal("100"):
                        errors["vendor_performance_default_template"] = (
                            "Active Vendor Performance Template criterion weights must total 100%."
                        )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Purchase Settings - {self.company}"


class PurchaseOrderControl(PurchaseCostCenterStructuredFields, TimeStampedModel):
    class DurationUnit(models.TextChoices):
        DAY = "day", "Day"
        WEEK = "week", "Week"
        MONTH = "month", "Month"
        YEAR = "year", "Year"

    class ReceivingStatus(models.TextChoices):
        NOT_RECEIVED = "not_received", "Not Received"
        PARTIAL = "partial", "Partially Received"
        RECEIVED = "received", "Fully Received"
        RETURNED = "returned", "Returned"

    class AccountingStatus(models.TextChoices):
        NOT_BILLED = "not_billed", "Not Billed"
        PARTIAL = "partial", "Partially Billed"
        BILLED = "billed", "Fully Billed"
        PAID = "paid", "Paid"

    order = models.OneToOneField(PurchaseOrder, on_delete=models.CASCADE, related_name="purchase_control")
    order_type = models.ForeignKey(PurchaseOrderType, on_delete=models.SET_NULL, blank=True, null=True, related_name="orders")
    order_template = models.ForeignKey(
        PurchaseOrderTemplate,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="orders",
    )
    milestone_template = models.ForeignKey(
        PurchaseMilestoneTemplate,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="orders",
    )
    quotation = models.ForeignKey(
        PurchaseQuotation,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="orders",
    )
    blanket_order = models.ForeignKey(
        BlanketOrder,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="orders",
    )
    tender = models.ForeignKey(
        PurchaseTender,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="orders",
    )
    vendor_pricelist = models.ForeignKey(
        VendorPricelist,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="orders",
    )
    team = models.ForeignKey(PurchaseTeam, on_delete=models.SET_NULL, blank=True, null=True, related_name="orders")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="assigned_purchase_order_controls",
    )
    confirmed_at = models.DateTimeField(blank=True, null=True, editable=False)
    revision_number = models.PositiveIntegerField(default=0)
    expiry_date = models.DateField(blank=True, null=True)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    cost_center = models.CharField(max_length=80, blank=True)
    contract_terms = models.TextField(blank=True)
    payment_terms = models.TextField(blank=True)
    delivery_terms = models.TextField(blank=True)
    legal_terms = models.TextField(blank=True)
    vendor_address = models.TextField(blank=True)
    bill_address = models.TextField(blank=True)
    remark_template = models.ForeignKey(
        PurchaseRemarkTemplate,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="purchase_orders",
    )
    remark_template_code = models.CharField(max_length=64, blank=True, editable=False)
    remark_template_name = models.CharField(max_length=255, blank=True, editable=False)
    expected_date = models.DateField(blank=True, null=True)
    rental_duration = models.PositiveIntegerField(default=0)
    duration_unit = models.CharField(
        max_length=8,
        choices=DurationUnit.choices,
        default=DurationUnit.DAY,
    )
    is_multiple_expected_date = models.BooleanField(default=False)
    is_multiple_rental_duration = models.BooleanField(default=False)
    expected_return_date = models.DateField(blank=True, null=True, editable=False)
    total_rental_duration = models.PositiveIntegerField(default=0, editable=False)
    communication_history = models.TextField(blank=True)
    receiving_status = models.CharField(max_length=20, choices=ReceivingStatus.choices, default=ReceivingStatus.NOT_RECEIVED)
    accounting_status = models.CharField(max_length=20, choices=AccountingStatus.choices, default=AccountingStatus.NOT_BILLED)
    match_status = models.CharField(
        max_length=24,
        choices=ThreeWayMatchStatus.choices,
        blank=True,
        default="",
        editable=False,
    )
    purchase_limit_requires_approval = models.BooleanField(default=False, editable=False)
    purchase_limit_warning_message = models.TextField(blank=True, editable=False)
    purchase_limit_total = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=0,
        editable=False,
    )
    vendor_moq_warning_message = models.TextField(blank=True, editable=False)
    approval_reference = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-order__order_date"]

    def __str__(self):
        return str(self.order)


class PurchaseThreeWayMatch(TimeStampedModel):
    """Purchase-owned, persisted 3-way matching snapshot for one PO line."""

    order_line = models.OneToOneField(
        PurchaseOrderLine,
        on_delete=models.CASCADE,
        related_name="purchase_three_way_match",
    )
    matching_billed_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        editable=False,
    )
    remaining_to_receive = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        editable=False,
    )
    received_not_billed = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        editable=False,
    )
    over_received = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        editable=False,
    )
    over_billed = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        editable=False,
    )
    match_status = models.CharField(
        max_length=24,
        choices=ThreeWayMatchStatus.choices,
        blank=True,
        default="",
        editable=False,
    )

    class Meta:
        ordering = ["order_line_id"]

    def __str__(self):
        return f"{self.order_line} - {self.match_status or 'not applicable'}"


class PurchaseOrderMilestone(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DUE = "due", "Due"
        COMPLETED = "completed", "Completed"
        WAIVED = "waived", "Waived"

    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="milestones")
    sequence = models.PositiveIntegerField(default=10)
    name = models.CharField(max_length=120)
    due_date = models.DateField()
    percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["order", "sequence"]
        constraints = [models.UniqueConstraint(fields=["order", "sequence"], name="unique_purchase_order_milestone_sequence")]

    def __str__(self):
        return f"{self.order} - {self.name}"


class ProductBudgetGroup(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_product_budget_groups")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_product_budget_group_code")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ProductBudget(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_product_budgets")
    group = models.ForeignKey(ProductBudgetGroup, on_delete=models.PROTECT, related_name="budgets")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="purchase_budgets")
    year = models.PositiveIntegerField(default=current_year)
    planned_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    reserved_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    consumed_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    allow_overbudget = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-year", "product__name"]
        constraints = [models.UniqueConstraint(fields=["company", "product", "year"], name="unique_product_budget_per_year")]

    @property
    def available_amount(self):
        return self.planned_amount - self.reserved_amount - self.consumed_amount

    def __str__(self):
        return f"{self.product} ({self.year})"


class BudgetProductTransfer(TimeStampedModel):
    source_budget = models.ForeignKey(ProductBudget, on_delete=models.PROTECT, related_name="outgoing_transfers")
    target_budget = models.ForeignKey(ProductBudget, on_delete=models.PROTECT, related_name="incoming_transfers")
    transfer_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="purchase_budget_transfer_approvals")

    class Meta:
        ordering = ["-transfer_date"]

    def clean(self):
        if self.source_budget_id and self.source_budget_id == self.target_budget_id:
            raise ValidationError({"target_budget": "Target budget must differ from source budget."})

    def __str__(self):
        return f"{self.source_budget} to {self.target_budget}"


class BudgetProductCarryOver(TimeStampedModel):
    source_budget = models.ForeignKey(ProductBudget, on_delete=models.PROTECT, related_name="carry_overs")
    target_year = models.PositiveIntegerField()
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="purchase_budget_carry_over_approvals")

    class Meta:
        ordering = ["-target_year"]

    def __str__(self):
        return f"{self.source_budget} to {self.target_year}"


class BudgetProductChangeRequest(TimeStampedModel):
    budget = models.ForeignKey(ProductBudget, on_delete=models.PROTECT, related_name="change_requests")
    requested_amount = models.DecimalField(max_digits=18, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    requestor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_budget_change_requests")
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="purchase_budget_change_approvals")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.budget} - {self.requested_amount}"


class GoodsReceipt(TimeStampedModel):
    class ReceivingMode(models.TextChoices):
        FULL = "full", "Full Receipt"
        PARTIAL = "partial", "Partial Receipt"
        BLIND = "blind", "Blind Receipt"

    class DeliveryStatus(models.TextChoices):
        EARLY = "early", "Early"
        ON_TIME = "on_time", "On Time"
        LATE = "late", "Late"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        INSPECTING = "inspecting", "Inspecting"
        RECEIVED = "received", "Received"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_goods_receipts")
    number = models.CharField(max_length=40)
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="goods_receipts")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="purchase_goods_receipts")
    receipt_date = models.DateField(default=timezone.localdate)
    receiving_mode = models.CharField(max_length=16, choices=ReceivingMode.choices, default=ReceivingMode.FULL)
    delivery_status = models.CharField(max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.ON_TIME)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-receipt_date"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_goods_receipt_number_per_company")]

    def __str__(self):
        return self.number


class GoodsReceiptLine(TimeStampedModel):
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipt_lines")
    received_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    accepted_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    rejected_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    rejection_reason = models.CharField(max_length=240, blank=True)
    stock_move = models.OneToOneField(
        StockMove,
        on_delete=models.PROTECT,
        related_name="purchase_receipt_line",
        blank=True,
        null=True,
        editable=False,
    )
    returned_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0, editable=False)
    return_stock_move = models.OneToOneField(
        StockMove,
        on_delete=models.PROTECT,
        related_name="purchase_return_line",
        blank=True,
        null=True,
        editable=False,
    )

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["receipt", "order_line"], name="unique_goods_receipt_order_line")]

    def clean(self):
        errors = {}
        for field_name in ("received_quantity", "accepted_quantity", "rejected_quantity"):
            if getattr(self, field_name) < 0:
                errors[field_name] = "Quantities cannot be negative."
        if self.accepted_quantity + self.rejected_quantity > self.received_quantity:
            errors["accepted_quantity"] = "Accepted and rejected quantities cannot exceed received quantity."
        if self.receipt_id and self.order_line_id and self.order_line.order_id != self.receipt.order_id:
            errors["order_line"] = "Receipt lines must belong to the receipt purchase order."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.receipt} - {self.order_line}"


class PurchaseReturnRequest(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_return_requests")
    number = models.CharField(max_length=40)
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.PROTECT, related_name="return_requests")
    return_date = models.DateField(default=timezone.localdate)
    reason = models.TextField()
    replacement_required = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="purchase_return_approvals")

    class Meta:
        ordering = ["-return_date"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_purchase_return_number_per_company")]

    def __str__(self):
        return self.number


class PurchaseAccountingSetting(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="purchase_accounting_setting")
    payable_account_code = models.CharField(max_length=40)
    accrual_account_code = models.CharField(max_length=40, blank=True)
    default_journal = models.CharField(max_length=80)
    auto_create_vendor_bill = models.BooleanField(default=False)
    require_cost_center = models.BooleanField(default=False)

    class Meta:
        ordering = ["company__name"]

    def __str__(self):
        return f"Purchase Accounting - {self.company}"


class PurchaseAccountMapping(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_account_mappings")
    product_category = models.CharField(max_length=120)
    expense_account_code = models.CharField(max_length=40)
    asset_account_code = models.CharField(max_length=40, blank=True)
    tax_account_code = models.CharField(max_length=40, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["product_category"]
        constraints = [models.UniqueConstraint(fields=["company", "product_category"], name="unique_purchase_account_mapping")]

    def __str__(self):
        return self.product_category


class PurchasePeppolSubmission(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        PROCESSING = "processing", "Processing"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        INVALID = "invalid", "Invalid"
        RECEIVED = "received", "Received"
        CONVERTED = "converted", "Converted"

    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="peppol_submissions")
    message_id = models.CharField(max_length=100, unique=True)
    submitted_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    response_code = models.CharField(max_length=60, blank=True)
    response_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.message_id


class PurchaseInsight(TimeStampedModel):
    class InsightType(models.TextChoices):
        SPEND = "spend", "Spend Analysis"
        SUPPLIER = "supplier", "Supplier Performance"
        DEMAND = "demand", "Demand Analysis"
        SAVINGS = "savings", "Savings Opportunity"
        CONTRACT = "contract", "Contract Intelligence"
        RISK = "risk", "Risk Center"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_insights")
    insight_type = models.CharField(max_length=16, choices=InsightType.choices)
    title = models.CharField(max_length=180)
    summary = models.TextField()
    metric_value = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    severity = models.CharField(max_length=16, default="info")
    period_start = models.DateField(blank=True, null=True)
    period_end = models.DateField(blank=True, null=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


# Detailed procurement records for line-level sourcing, tender evidence,
# accounting, budget movements, inventory hand-off, and communication history.


class PurchaseQuotationLine(TimeStampedModel):
    class PriceSource(models.TextChoices):
        PRICELIST = "pricelist", "Pricelist"
        PURCHASE_REQUEST = "purchase_request", "Purchase Request"
        MANUAL = "manual", "Manual"
        NO_MATCH = "no_match", "No Match"

    quotation = models.ForeignKey(PurchaseQuotation, on_delete=models.CASCADE, related_name="lines")
    request_line = models.ForeignKey(PurchaseRequestLine, on_delete=models.SET_NULL, blank=True, null=True, related_name="quotation_lines")
    blanket_order_line = models.ForeignKey(
        "BlanketOrderLine",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="quotation_lines",
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="purchase_quotation_lines")
    description = models.CharField(max_length=240)
    specification = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1, validators=[MinValueValidator(Decimal("0.001"))])
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tax_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    tax_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    promised_date = models.DateField(blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)
    free_product = models.BooleanField(default=False, editable=False)
    free_product_source_line = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="free_product_benefit_lines",
        editable=False,
    )
    price_source = models.CharField(max_length=20, choices=PriceSource.choices, blank=True)
    applied_pricelist = models.ForeignKey(
        VendorPricelist,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="purchase_quotation_lines",
        editable=False,
    )
    applied_pricelist_tier = models.ForeignKey(
        VendorPricelistTier,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="purchase_quotation_lines",
        editable=False,
    )
    price_source_note = models.CharField(max_length=255, blank=True, editable=False)
    discount_formula = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        ordering = ["id"]

    @property
    def untaxed_amount(self):
        if self.tax_snapshot:
            return Decimal(str(self.tax_snapshot.get("untaxed_amount", "0")))
        if self.free_product:
            return Decimal("0")
        return self.quantity * self.unit_price * (
            Decimal("1") - self.discount_percent / Decimal("100")
        )

    @property
    def tax_amount(self):
        if self.tax_snapshot:
            return Decimal(str(self.tax_snapshot.get("taxes_amount", "0")))
        return self.untaxed_amount * self.tax_percent / Decimal("100")

    @property
    def total_amount(self):
        if self.tax_snapshot:
            return Decimal(str(self.tax_snapshot.get("total_amount", "0")))
        return self.untaxed_amount + self.tax_amount

    @property
    def subtotal(self):
        return self.total_amount

    def build_tax_snapshot(self):
        untaxed = self.untaxed_amount
        tax = untaxed * self.tax_percent / Decimal("100")
        discount = (
            Decimal("0")
            if self.free_product
            else self.quantity * self.unit_price * self.discount_percent / Decimal("100")
        )
        taxes = []
        if self.tax_percent:
            taxes.append(
                {
                    "name": f"Tax {self.tax_percent}%",
                    "amount_type": "percent",
                    "amount": str(self.tax_percent),
                    "price_include": False,
                }
            )
        return {
            "taxes": taxes,
            "subtotal": str(untaxed + tax),
            "discount_line_amount": str(discount),
            "global_discount_line_amount": "0",
            "total_discount_line_amount": str(discount),
            "untaxed_amount": str(untaxed),
            "taxes_amount": str(tax),
            "total_amount": str(untaxed + tax),
        }

    def clean(self):
        if not self.expiry_date and self.quotation_id:
            self.expiry_date = self.quotation.response_deadline
        errors = {}
        if self.request_line_id and self.quotation.request_id and self.request_line.request_id != self.quotation.request_id:
            errors["request_line"] = "The request line must belong to the quotation's purchase request."
        if self.product_id and self.product.company_id != self.quotation.company_id:
            errors["product"] = "Product must belong to the quotation company."
        if self.quantity <= 0:
            errors["quantity"] = "Quantity must be greater than zero."
        if self.unit_price < 0:
            errors["unit_price"] = "Unit price cannot be negative."
        if self.free_product and self.unit_price != 0:
            errors["unit_price"] = "Free-product RFQ lines must have a zero unit price."
        if self.free_product_source_line_id:
            source = self.free_product_source_line
            if self.pk and source.pk == self.pk:
                errors["free_product_source_line"] = "A free-product line cannot reference itself."
            elif source.quotation_id != self.quotation_id:
                errors["free_product_source_line"] = "Free-product source must belong to the same RFQ."
            elif source.free_product:
                errors["free_product_source_line"] = "Free-product benefits must reference a paid RFQ line."
            if not self.free_product:
                errors["free_product"] = "A line with a free-product source must be marked as free."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.expiry_date and self.quotation_id:
            self.expiry_date = self.quotation.response_deadline
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.quotation} - {self.description}"


class PurchaseOrderLinePricingProvenance(PurchaseCostCenterStructuredFields, TimeStampedModel):
    """Purchase-owned price and free-benefit trace for a core Purchase Order line."""

    order_line = models.OneToOneField(
        PurchaseOrderLine,
        on_delete=models.CASCADE,
        related_name="purchase_pricing_provenance",
    )
    quotation_line = models.OneToOneField(
        PurchaseQuotationLine,
        on_delete=models.PROTECT,
        related_name="purchase_order_pricing_provenance",
        blank=True,
        null=True,
    )
    blanket_order_line = models.ForeignKey(
        "BlanketOrderLine",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="purchase_order_pricing_provenance",
    )
    free_product = models.BooleanField(default=False, editable=False)
    free_product_source_order_line = models.ForeignKey(
        PurchaseOrderLine,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="free_product_benefit_provenance",
        editable=False,
    )
    price_source = models.CharField(
        max_length=20,
        choices=PurchaseQuotationLine.PriceSource.choices,
        blank=True,
    )
    applied_pricelist = models.ForeignKey(
        VendorPricelist,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="purchase_order_line_provenance",
        editable=False,
    )
    applied_pricelist_tier = models.ForeignKey(
        VendorPricelistTier,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="purchase_order_line_provenance",
        editable=False,
    )
    price_source_note = models.CharField(max_length=255, blank=True, editable=False)
    discount_formula = models.CharField(max_length=64, blank=True, editable=False)
    applicable_vendor_moq = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
        editable=False,
    )
    is_below_vendor_moq = models.BooleanField(default=False, editable=False)
    moq_warning_message = models.TextField(blank=True, editable=False)
    is_rental = models.BooleanField(default=False, editable=False)
    rent_duration = models.PositiveIntegerField(default=0)
    rental_period = models.CharField(
        max_length=8,
        choices=PurchaseOrderControl.DurationUnit.choices,
        default=PurchaseOrderControl.DurationUnit.DAY,
    )
    expected_date = models.DateField(blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)
    rental_end_date = models.DateField(blank=True, null=True, editable=False)
    total_rental_duration = models.PositiveIntegerField(default=0, editable=False)
    cost_center = models.CharField(max_length=80, blank=True)
    tax_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        editable=False,
    )
    discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        editable=False,
    )
    tax_snapshot = models.JSONField(default=dict, blank=True, editable=False)

    class Meta:
        ordering = ["order_line_id"]

    @property
    def untaxed_amount(self):
        if self.tax_snapshot:
            return Decimal(str(self.tax_snapshot.get("untaxed_amount", "0")))
        if self.free_product:
            return Decimal("0")
        rental_multiplier = Decimal(self.total_rental_duration or 1) if self.is_rental else Decimal("1")
        return self.order_line.quantity * self.order_line.unit_price * rental_multiplier * (
            Decimal("1") - self.discount_percent / Decimal("100")
        )

    @property
    def tax_amount(self):
        if self.tax_snapshot:
            return Decimal(str(self.tax_snapshot.get("taxes_amount", "0")))
        return self.untaxed_amount * self.tax_percent / Decimal("100")

    @property
    def total_amount(self):
        if self.tax_snapshot:
            return Decimal(str(self.tax_snapshot.get("total_amount", "0")))
        return self.untaxed_amount + self.tax_amount

    def build_tax_snapshot(self):
        untaxed = self.untaxed_amount
        tax = untaxed * self.tax_percent / Decimal("100")
        rental_multiplier = Decimal(self.total_rental_duration or 1) if self.is_rental else Decimal("1")
        discount = (
            Decimal("0")
            if self.free_product
            else self.order_line.quantity
            * self.order_line.unit_price
            * rental_multiplier
            * self.discount_percent
            / Decimal("100")
        )
        taxes = []
        if self.tax_percent:
            taxes.append(
                {
                    "name": f"Tax {self.tax_percent}%",
                    "amount_type": "percent",
                    "amount": str(self.tax_percent),
                    "price_include": False,
                }
            )
        return {
            "taxes": taxes,
            "subtotal": str(untaxed + tax),
            "discount_line_amount": str(discount),
            "global_discount_line_amount": "0",
            "total_discount_line_amount": str(discount),
            "untaxed_amount": str(untaxed),
            "taxes_amount": str(tax),
            "total_amount": str(untaxed + tax),
        }

    def clean(self):
        errors = {}
        if self.quotation_line_id and self.order_line_id:
            quotation = self.quotation_line.quotation
            if quotation.company_id != self.order_line.order.company_id:
                errors["quotation_line"] = "RFQ and Purchase Order line must belong to the same company."
        if self.blanket_order_line_id and self.quotation_line_id:
            if self.blanket_order_line_id != self.quotation_line.blanket_order_line_id:
                errors["blanket_order_line"] = "Blanket Order provenance must match the RFQ line."
        if self.free_product_source_order_line_id:
            if self.free_product_source_order_line.order_id != self.order_line.order_id:
                errors["free_product_source_order_line"] = (
                    "Free-product source must belong to the same Purchase Order."
                )
            if not self.free_product:
                errors["free_product"] = "A line with a free-product source must be marked as free."
        if self.free_product and self.order_line.unit_price != 0:
            errors["order_line"] = "Free-product Purchase Order lines must have a zero unit price."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.order_line} - {self.get_price_source_display() or 'Unspecified'}"


class DirectPurchaseLine(TimeStampedModel):
    direct_purchase = models.ForeignKey(DirectPurchase, on_delete=models.CASCADE, related_name="lines")
    request_line = models.ForeignKey(
        PurchaseRequestLine,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="direct_purchase_lines",
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="direct_purchase_lines")
    description = models.CharField(max_length=240)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1, validators=[MinValueValidator(Decimal("0.001"))])
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    cost_center = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["id"]

    @property
    def subtotal(self):
        return self.quantity * self.unit_price * (Decimal("1") + self.tax_percent / Decimal("100"))

    def __str__(self):
        return f"{self.direct_purchase} - {self.description}"


class ServiceWorkOrderLine(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In Progress"
        DONE = "done", "Done"

    work_order = models.ForeignKey(ServiceWorkOrder, on_delete=models.CASCADE, related_name="lines")
    purchase_order_line = models.ForeignKey(
        PurchaseOrderLine,
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="service_work_order_lines",
    )
    description = models.CharField(max_length=240)
    unit = models.CharField(max_length=40, default="service")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit_rate = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    cost_center = models.CharField(max_length=80, blank=True)
    milestone_name = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ["id"]

    @property
    def subtotal(self):
        return self.quantity * self.unit_rate * (Decimal("1") + self.tax_percent / Decimal("100"))

    def clean(self):
        errors = {}
        if self.purchase_order_line_id and self.work_order.source_order_id:
            if self.purchase_order_line.order_id != self.work_order.source_order_id:
                errors["purchase_order_line"] = "Service line must belong to the source Purchase Order."
            if self.purchase_order_line.product.product_type != Product.ProductType.SERVICE:
                errors["purchase_order_line"] = "Only service products can be attached to a Service Work Order."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.work_order} - {self.description}"


class PurchaseTenderLine(PurchaseCostCenterStructuredFields, TimeStampedModel):
    tender = models.ForeignKey(PurchaseTender, on_delete=models.CASCADE, related_name="lines")
    request_line = models.ForeignKey(PurchaseRequestLine, on_delete=models.SET_NULL, blank=True, null=True, related_name="tender_lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="purchase_tender_lines")
    description = models.CharField(max_length=240)
    specification = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    target_unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cost_center = models.CharField(max_length=80, blank=True)
    delivery_date = models.DateField(blank=True, null=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.tender} - {self.description}"


class TenderBidLine(TimeStampedModel):
    bid = models.ForeignKey(TenderBid, on_delete=models.CASCADE, related_name="lines")
    tender_line = models.ForeignKey(PurchaseTenderLine, on_delete=models.CASCADE, related_name="bid_lines")
    offered_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=0,
    )
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    delivery_days = models.PositiveIntegerField(default=0)
    compliant = models.BooleanField(default=True)
    no_bid = models.BooleanField(default=False)
    price_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, editable=False)
    delivery_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, editable=False)
    compliance_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, editable=False)
    term_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, editable=False)
    history_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, editable=False)
    total_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, editable=False)
    rank = models.PositiveIntegerField(default=0, editable=False)
    scored_at = models.DateTimeField(blank=True, null=True, editable=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["bid", "tender_line"]
        constraints = [
            models.UniqueConstraint(fields=["bid", "tender_line"], name="unique_tender_bid_line")
        ]

    @property
    def subtotal(self):
        return self.offered_quantity * self.unit_price

    def clean(self):
        errors = {}
        if self.bid_id and self.tender_line_id and self.bid.tender_id != self.tender_line.tender_id:
            errors["tender_line"] = "The bid line must belong to the same tender as the bid."
        if self.no_bid:
            if self.offered_quantity != 0:
                errors["offered_quantity"] = "A no-bid line must have zero offered quantity."
        elif self.offered_quantity <= 0:
            errors["offered_quantity"] = "Offered quantity must be greater than zero."
        if self.unit_price < 0:
            errors["unit_price"] = "Unit price cannot be negative."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.bid} - {self.tender_line}"


class TenderAwardAllocation(TimeStampedModel):
    class Strategy(models.TextChoices):
        SINGLE_RANK = "single_rank", "Single Rank"
        SPLIT_RATIO = "split_ratio", "Split Ratio"
        MANUAL = "manual", "Manual"

    tender = models.ForeignKey(PurchaseTender, on_delete=models.CASCADE, related_name="award_allocations")
    tender_line = models.ForeignKey(PurchaseTenderLine, on_delete=models.CASCADE, related_name="award_allocations")
    bid_line = models.ForeignKey(TenderBidLine, on_delete=models.PROTECT, related_name="award_allocations")
    allocated_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
    )
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    award_strategy = models.CharField(max_length=24, choices=Strategy.choices, default=Strategy.MANUAL)
    score = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    rank = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["tender_line", "rank", "bid_line"]
        constraints = [
            models.UniqueConstraint(
                fields=["tender_line", "bid_line"],
                name="unique_tender_award_allocation",
            )
        ]

    @property
    def subtotal(self):
        return self.allocated_quantity * self.unit_price

    def clean(self):
        errors = {}
        if self.tender_line_id and self.tender_id and self.tender_line.tender_id != self.tender_id:
            errors["tender_line"] = "The allocation line must belong to the selected tender."
        if self.bid_line_id and self.tender_id and self.bid_line.bid.tender_id != self.tender_id:
            errors["bid_line"] = "The bid line must belong to the selected tender."
        if self.bid_line_id and self.tender_line_id and self.bid_line.tender_line_id != self.tender_line_id:
            errors["bid_line"] = "The bid line and allocation must reference the same tender line."
        if self.allocated_quantity <= 0:
            errors["allocated_quantity"] = "Allocated quantity must be greater than zero."
        if self.bid_line_id and self.allocated_quantity > self.bid_line.offered_quantity:
            errors["allocated_quantity"] = "Allocated quantity cannot exceed the vendor offered quantity."
        if self.unit_price < 0:
            errors["unit_price"] = "Unit price cannot be negative."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.tender_line} - {self.bid_line.bid.vendor}"


class TenderAwardResponse(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        NO_RESPONSE = "no_response", "No Response"

    tender = models.ForeignKey(PurchaseTender, on_delete=models.CASCADE, related_name="award_responses")
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="tender_award_responses")
    award_cycle = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    awarded_at = models.DateTimeField(default=timezone.now, editable=False)
    deadline = models.DateTimeField(blank=True, null=True, editable=False)
    responded_at = models.DateTimeField(blank=True, null=True, editable=False)
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="tender_award_responses",
        editable=False,
    )
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["tender", "award_cycle", "vendor"]
        constraints = [
            models.UniqueConstraint(
                fields=["tender", "award_cycle", "vendor"],
                name="unique_tender_award_response_cycle_vendor",
            )
        ]

    def effective_status(self, at=None):
        if self.status == self.Status.PENDING and self.deadline and (at or timezone.now()) > self.deadline:
            return self.Status.NO_RESPONSE
        return self.status

    def __str__(self):
        return f"{self.tender} - {self.vendor} - {self.get_status_display()}"


class TenderInvitation(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"

    tender = models.ForeignKey(PurchaseTender, on_delete=models.CASCADE, related_name="invitations")
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="tender_invitations")
    invited_at = models.DateTimeField(blank=True, null=True)
    response_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    message = models.TextField(blank=True)

    class Meta:
        ordering = ["vendor__name"]
        constraints = [models.UniqueConstraint(fields=["tender", "vendor"], name="unique_tender_invited_vendor")]

    def __str__(self):
        return f"{self.tender} - {self.vendor}"


class TenderDocument(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        REVISION = "revision", "Revision Required"

    tender = models.ForeignKey(PurchaseTender, on_delete=models.CASCADE, related_name="documents")
    legal_document = models.ForeignKey(
        PurchaseLegalDocument,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="tender_requirements",
    )
    bid = models.ForeignKey(TenderBid, on_delete=models.CASCADE, blank=True, null=True, related_name="documents")
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="tender_documents")
    document_type = models.CharField(max_length=255)
    title = models.CharField(max_length=180)
    attachment_url = models.URLField(blank=True)
    mandatory = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    review_note = models.TextField(blank=True)

    class Meta:
        ordering = ["tender", "document_type", "title"]

    def clean(self):
        self.apply_legal_document_configuration()
        errors = {}
        if self.bid_id and self.bid.tender_id != self.tender_id:
            errors["bid"] = "The bid must belong to this tender."
        if self.bid_id and self.vendor_id and self.bid.vendor_id != self.vendor_id:
            errors["vendor"] = "The vendor must match the selected bid."
        if self.legal_document_id:
            if self.legal_document.company_id != self.tender.company_id:
                errors["legal_document"] = "Tender document requirement must belong to the Tender company."
            if self.legal_document.document_usage not in {
                PurchaseLegalDocument.DocumentUsage.TENDER_DOCUMENT,
                PurchaseLegalDocument.DocumentUsage.ADDITIONAL_VENDOR,
            }:
                errors["legal_document"] = "Tender requirements only accept Tender or Additional Vendor documents."
        if errors:
            raise ValidationError(errors)

    def apply_legal_document_configuration(self):
        if not self.legal_document_id:
            return
        self.document_type = self.legal_document.name
        if not self.title:
            self.title = self.legal_document.name
        self.mandatory = self.legal_document.requirement_type != PurchaseLegalDocument.RequirementType.OPTIONAL

    def save(self, *args, **kwargs):
        self.apply_legal_document_configuration()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and self.legal_document_id:
            kwargs["update_fields"] = set(update_fields) | {"document_type", "title", "mandatory"}
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tender} - {self.title}"


class TenderDocumentCompletionMatrix(TimeStampedModel):
    tender = models.OneToOneField(
        PurchaseTender,
        on_delete=models.CASCADE,
        related_name="document_completion_matrix",
    )
    name = models.CharField(max_length=255)
    document_columns = models.JSONField(default=list, blank=True)
    footer_totals = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return self.name


class TenderDocumentCompletionLine(TimeStampedModel):
    matrix = models.ForeignKey(
        TenderDocumentCompletionMatrix,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    tender = models.ForeignKey(
        PurchaseTender,
        on_delete=models.CASCADE,
        related_name="document_completion_lines",
    )
    vendor = models.ForeignKey(
        VendorProfile,
        on_delete=models.CASCADE,
        related_name="tender_document_completion_lines",
    )
    sequence = models.PositiveIntegerField(default=10)
    vendor_name = models.CharField(max_length=255)
    vendor_subtitle = models.CharField(max_length=255, blank=True)
    completion_summary = models.CharField(max_length=64, default="-")
    document_cells = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["sequence", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["tender", "vendor"],
                name="unique_tender_document_completion_vendor",
            )
        ]

    def clean(self):
        errors = {}
        if self.matrix_id and self.matrix.tender_id != self.tender_id:
            errors["matrix"] = "Document Completion matrix must belong to the selected Tender."
        if self.vendor_id and self.vendor.company_id != self.tender.company_id:
            errors["vendor"] = "Document Completion vendor must belong to the Tender company."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.tender} - {self.vendor_name}"


class TenderAuditLog(TimeStampedModel):
    tender = models.ForeignKey(PurchaseTender, on_delete=models.CASCADE, related_name="audit_logs")
    action = models.CharField(max_length=80)
    previous_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_tender_audits")
    occurred_at = models.DateTimeField(default=timezone.now)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at"]

    def __str__(self):
        return f"{self.tender} - {self.action}"


class BlanketOrderLine(PurchaseCostCenterStructuredFields, TimeStampedModel):
    class PriceSource(models.TextChoices):
        PRICELIST = "pricelist", "Pricelist"
        MANUAL = "manual", "Manual"
        NO_MATCH = "no_match", "No Match"

    blanket_order = models.ForeignKey(BlanketOrder, on_delete=models.CASCADE, related_name="lines")
    request_line = models.ForeignKey(
        PurchaseRequestLine,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="blanket_order_lines",
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="blanket_order_lines")
    description = models.CharField(max_length=240, blank=True)
    cost_center = models.CharField(max_length=80, blank=True)
    agreed_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    released_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tax_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    tax_snapshot = models.JSONField(default=dict, blank=True, editable=False)
    minimum_release_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    free_product = models.BooleanField(default=False, editable=False)
    free_product_source_line = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="free_product_benefit_lines",
        editable=False,
    )
    price_source = models.CharField(max_length=20, choices=PriceSource.choices, blank=True)
    applied_pricelist = models.ForeignKey(
        VendorPricelist,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="blanket_order_lines",
        editable=False,
    )
    applied_pricelist_tier = models.ForeignKey(
        VendorPricelistTier,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="blanket_order_lines",
        editable=False,
    )
    price_source_note = models.CharField(max_length=255, blank=True, editable=False)
    discount_formula = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        ordering = ["product__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["free_product_source_line"],
                condition=models.Q(free_product_source_line__isnull=False),
                name="unique_blanket_free_benefit_source",
            )
        ]

    @property
    def untaxed_amount(self):
        if self.tax_snapshot:
            return Decimal(str(self.tax_snapshot.get("untaxed_amount", "0")))
        if self.free_product:
            return Decimal("0")
        return self.agreed_quantity * self.unit_price

    @property
    def tax_amount(self):
        if self.tax_snapshot:
            return Decimal(str(self.tax_snapshot.get("taxes_amount", "0")))
        return self.untaxed_amount * self.tax_percent / Decimal("100")

    @property
    def total_amount(self):
        if self.tax_snapshot:
            return Decimal(str(self.tax_snapshot.get("total_amount", "0")))
        return self.untaxed_amount + self.tax_amount

    @property
    def subtotal(self):
        return self.total_amount

    def build_tax_snapshot(self):
        untaxed = self.untaxed_amount
        tax = untaxed * self.tax_percent / Decimal("100")
        taxes = []
        if self.tax_percent:
            taxes.append(
                {
                    "name": f"Tax {self.tax_percent}%",
                    "amount_type": "percent",
                    "amount": str(self.tax_percent),
                    "price_include": False,
                }
            )
        return {
            "taxes": taxes,
            "subtotal": str(untaxed + tax),
            "discount_line_amount": "0",
            "global_discount_line_amount": "0",
            "total_discount_line_amount": "0",
            "untaxed_amount": str(untaxed),
            "taxes_amount": str(tax),
            "total_amount": str(untaxed + tax),
        }

    def clean(self):
        errors = {}
        if (
            self.blanket_order_id
            and self.blanket_order.agreement_policy == BlanketOrder.AgreementPolicy.QUANTITY_PERIOD
            and self.released_quantity > self.agreed_quantity
        ):
            errors["released_quantity"] = "Released quantity cannot exceed agreed quantity."
        if self.product_id and self.blanket_order_id and self.product.company_id != self.blanket_order.company_id:
            errors["product"] = "Product must belong to the Blanket Order company."
        if self.free_product and self.unit_price != 0:
            errors["unit_price"] = "Free-product Blanket Order lines must have a zero unit price."
        if self.free_product_source_line_id:
            source = self.free_product_source_line
            if self.pk and source.pk == self.pk:
                errors["free_product_source_line"] = "A free-product line cannot reference itself."
            elif source.blanket_order_id != self.blanket_order_id:
                errors["free_product_source_line"] = "Free-product source must belong to the same Blanket Order."
            elif source.free_product:
                errors["free_product_source_line"] = "Free-product benefits must reference a paid Blanket Order line."
            if not self.free_product:
                errors["free_product"] = "A line with a free-product source must be marked as free."
            if self.request_line_id:
                errors["request_line"] = "Generated free-product lines cannot consume Purchase Request quantity."
        if self.applied_pricelist_tier_id and self.applied_pricelist_id:
            if self.applied_pricelist_tier.item.pricelist_id != self.applied_pricelist_id:
                errors["applied_pricelist_tier"] = "Applied tier must belong to the applied pricelist."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.blanket_order} - {self.product}"


class PurchaseProductCategory(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_product_categories")
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, blank=True, null=True, related_name="children")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_purchase_product_category")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class PurchaseProductVariant(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="purchase_variants")
    category = models.ForeignKey(PurchaseProductCategory, on_delete=models.SET_NULL, blank=True, null=True, related_name="variants")
    variant_code = models.CharField(max_length=60)
    vendor_reference = models.CharField(max_length=100, blank=True)
    attributes = models.JSONField(default=dict, blank=True)
    purchase_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["product__name", "variant_code"]
        constraints = [models.UniqueConstraint(fields=["product", "variant_code"], name="unique_purchase_product_variant")]

    def clean(self):
        if self.category_id and self.category.company_id != self.product.company_id:
            raise ValidationError({"category": "The category and product must belong to the same company."})

    def __str__(self):
        return f"{self.product} - {self.variant_code}"


class PurchaseProductRentalProfile(TimeStampedModel):
    """Purchase-owned rental eligibility for a shared core Product."""

    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name="purchase_rental_profile",
    )
    is_rental = models.BooleanField(default=False)

    class Meta:
        ordering = ["product__name"]

    def __str__(self):
        return f"{self.product} - {'Rental' if self.is_rental else 'Regular'}"


class VendorCommunication(TimeStampedModel):
    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        PHONE = "phone", "Phone"
        MEETING = "meeting", "Meeting"
        PORTAL = "portal", "Vendor Portal"

    vendor = models.ForeignKey(VendorProfile, on_delete=models.PROTECT, related_name="communications")
    channel = models.CharField(max_length=16, choices=Channel.choices)
    subject = models.CharField(max_length=180)
    message = models.TextField()
    communicated_at = models.DateTimeField(default=timezone.now)
    contact_person = models.CharField(max_length=120, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="vendor_communications")

    class Meta:
        ordering = ["-communicated_at"]

    def __str__(self):
        return f"{self.vendor} - {self.subject}"


class VendorTransactionHistory(TimeStampedModel):
    vendor = models.ForeignKey(VendorProfile, on_delete=models.PROTECT, related_name="transaction_history")
    order = models.ForeignKey(PurchaseOrder, on_delete=models.SET_NULL, blank=True, null=True, related_name="vendor_history_entries")
    transaction_type = models.CharField(max_length=40)
    reference = models.CharField(max_length=80)
    transaction_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=40, blank=True)
    notes = models.TextField(blank=True)
    source_document = models.CharField(max_length=255, blank=True, editable=False)
    source_model = models.CharField(max_length=128, blank=True, editable=False)
    source_record_id = models.PositiveBigIntegerField(blank=True, null=True, editable=False)
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="vendor_transaction_history",
        editable=False,
    )
    summary = models.TextField(blank=True, editable=False)
    value = models.DecimalField(max_digits=20, decimal_places=6, blank=True, null=True, editable=False)
    currency = models.CharField(max_length=3, blank=True, editable=False)
    ordered_quantity = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        blank=True,
        null=True,
        editable=False,
    )
    received_quantity = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        blank=True,
        null=True,
        editable=False,
    )
    remaining_quantity = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        blank=True,
        null=True,
        editable=False,
    )
    expected_date = models.DateField(blank=True, null=True, editable=False)
    actual_delivery_date = models.DateField(blank=True, null=True, editable=False)
    delivery_status = models.CharField(max_length=64, blank=True, editable=False)
    lead_time_variance = models.IntegerField(blank=True, null=True, editable=False)
    rejection_indicator = models.BooleanField(default=False, editable=False)
    rejection_reason = models.TextField(blank=True, editable=False)
    company_snapshot = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="vendor_transaction_history",
        editable=False,
    )
    branch_snapshot = models.ForeignKey(
        "organization.Branch",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="vendor_transaction_history",
        editable=False,
    )

    class Meta:
        ordering = ["-transaction_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["vendor", "transaction_type", "reference"], name="unique_vendor_transaction_reference")]
        indexes = [
            models.Index(
                fields=["vendor", "-transaction_date", "-id"],
                name="vendor_history_date_idx",
            )
        ]

    def __str__(self):
        return self.source_document or self.reference


class PurchaseOrderCommunication(TimeStampedModel):
    class Direction(models.TextChoices):
        OUTBOUND = "outbound", "Outbound"
        INBOUND = "inbound", "Inbound"
        INTERNAL = "internal", "Internal Note"

    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="communications")
    direction = models.CharField(max_length=16, choices=Direction.choices)
    subject = models.CharField(max_length=180)
    message = models.TextField()
    communicated_at = models.DateTimeField(default=timezone.now)
    sender = models.CharField(max_length=120, blank=True)
    recipient = models.CharField(max_length=120, blank=True)
    delivery_status = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["-communicated_at"]

    def __str__(self):
        return f"{self.order} - {self.subject}"


class PurchaseMailTemplate(TimeStampedModel):
    """Company-owned, editable RFQ mail template with stable seed identity."""

    class TemplateType(models.TextChoices):
        STANDARD = "standard_rfq", "Standard RFQ Request"
        URGENT = "urgent_rfq", "Urgent RFQ Request"
        FOLLOW_UP = "follow_up_rfq", "Follow-up RFQ"

    company = models.ForeignKey(
        Company,
        on_delete=models.PROTECT,
        related_name="purchase_mail_templates",
    )
    code = models.CharField(max_length=32, choices=TemplateType.choices)
    name = models.CharField(max_length=120)
    subject_template = models.CharField(max_length=255)
    body_html = models.TextField()
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="unique_purchase_mail_template_code",
            )
        ]

    def clean(self):
        super().clean()
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                "company_id", "code"
            ).first()
            if original and (
                original["company_id"] != self.company_id
                or original["code"] != self.code
            ):
                raise ValidationError(
                    {"code": "Mail Template company and code cannot be changed."}
                )

    def __str__(self):
        return f"{self.company.code} - {self.name}"


class PurchaseQuotationCommunication(TimeStampedModel):
    """Immutable delivery evidence for one RFQ email attempt."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    quotation = models.ForeignKey(
        PurchaseQuotation,
        on_delete=models.CASCADE,
        related_name="communications",
    )
    template = models.ForeignKey(
        PurchaseMailTemplate,
        on_delete=models.PROTECT,
        related_name="communications",
    )
    subject = models.CharField(max_length=255)
    email_from = models.EmailField()
    email_to = models.TextField()
    email_cc = models.TextField(blank=True)
    body_html = models.TextField()
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.QUEUED,
        editable=False,
    )
    failure_reason = models.TextField(blank=True, editable=False)
    sent_at = models.DateTimeField(blank=True, null=True, editable=False)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_quotation_communications",
    )

    class Meta:
        ordering = ["-created_at", "-pk"]

    def clean(self):
        super().clean()
        errors = {}
        if (
            self.quotation_id
            and self.template_id
            and self.quotation.company_id != self.template.company_id
        ):
            errors["template"] = "Mail Template must belong to the RFQ company."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.quotation.number} - {self.subject}"


class ProductBudgetMovement(TimeStampedModel):
    class MovementType(models.TextChoices):
        RESERVE = "reserve", "Reserve"
        RELEASE = "release", "Release"
        CONSUME = "consume", "Consume"
        REVERSE = "reverse", "Reverse Consume"
        ADJUST = "adjust", "Adjustment"

    budget = models.ForeignKey(ProductBudget, on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=16, choices=MovementType.choices)
    reference = models.CharField(max_length=80)
    movement_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_budget_movements")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="approved_purchase_budget_movements",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-movement_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["budget", "movement_type", "reference"], name="unique_budget_movement_reference")]

    def __str__(self):
        return self.reference


class PurchaseAccountingDocument(TimeStampedModel):
    class DocumentType(models.TextChoices):
        ACCRUAL = "accrual", "Purchase Accrual"
        VENDOR_BILL = "vendor_bill", "Vendor Bill"
        PAYMENT = "payment", "Vendor Payment"
        REVERSAL = "reversal", "Reversal"

    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="accounting_documents")
    invoice = models.ForeignKey("core.Invoice", on_delete=models.SET_NULL, blank=True, null=True, related_name="purchase_accounting_documents")
    number = models.CharField(max_length=60, unique=True)
    document_type = models.CharField(max_length=16, choices=DocumentType.choices)
    document_date = models.DateField(default=timezone.localdate)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    journal_reference = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)

    class Meta:
        ordering = ["-document_date", "-id"]

    @property
    def balanced(self):
        totals = self.lines.aggregate(debit=models.Sum("debit"), credit=models.Sum("credit"))
        debit = totals["debit"] or Decimal("0")
        credit = totals["credit"] or Decimal("0")
        return debit > 0 and debit == credit == self.total_amount

    def __str__(self):
        return self.number


class PurchaseOrderInvoiceAccounting(TimeStampedModel):
    class DownpaymentType(models.TextChoices):
        FIXED_AMOUNT = "fixed_amount", "Fixed Amount"
        PERCENTAGE = "percentage", "Percentage"

    class Status(models.TextChoices):
        READY = "ready", "Ready"
        DOWNPAYMENT_DRAFT = "downpayment_draft", "Down-Payment Draft"
        DOWNPAYMENT_POSTED = "downpayment_posted", "Down-Payment Posted"
        SETTLEMENT_DRAFT = "settlement_draft", "Settlement Draft"
        COMPLETED = "completed", "Completed"
        ACCOUNTING_ERROR = "accounting_error", "Accounting Error"

    order = models.OneToOneField(
        PurchaseOrder,
        on_delete=models.PROTECT,
        related_name="invoice_accounting",
    )
    downpayment_type = models.CharField(
        max_length=16,
        choices=DownpaymentType.choices,
        default=DownpaymentType.FIXED_AMOUNT,
    )
    downpayment_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    downpayment_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        validators=[MaxValueValidator(100)],
    )
    downpayment_sequence = models.PositiveIntegerField(default=1, editable=False)
    downpayment_invoice = models.OneToOneField(
        "core.Invoice",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        editable=False,
        related_name="purchase_downpayment_order_accounting",
    )
    settlement_invoice = models.OneToOneField(
        "core.Invoice",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        editable=False,
        related_name="purchase_settlement_order_accounting",
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.READY)
    error_message = models.TextField(blank=True, editable=False)

    def clean(self):
        errors = {}
        if self.downpayment_amount < 0:
            errors["downpayment_amount"] = "Purchase down payment cannot be negative."
        if self.downpayment_percentage < 0 or self.downpayment_percentage > 100:
            errors["downpayment_percentage"] = "Purchase down-payment percentage must be between 0 and 100."
        if (
            self.downpayment_type == self.DownpaymentType.FIXED_AMOUNT
            and self.downpayment_amount <= 0
        ):
            errors["downpayment_amount"] = "Set a positive fixed Purchase down-payment amount."
        if (
            self.downpayment_type == self.DownpaymentType.PERCENTAGE
            and self.downpayment_percentage <= 0
        ):
            errors["downpayment_percentage"] = "Set a positive Purchase down-payment percentage."
        if self.order_id and self.downpayment_amount > self.order.total:
            errors["downpayment_amount"] = "Purchase down payment cannot exceed the Purchase Order total."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.order.number} - vendor billing"


class PurchaseInvoiceAccounting(TimeStampedModel):
    class InvoiceKind(models.TextChoices):
        DOWNPAYMENT = "downpayment", "Purchase Down Payment"
        SETTLEMENT = "settlement", "Purchase Settlement"

    invoice = models.OneToOneField(
        "core.Invoice",
        on_delete=models.CASCADE,
        related_name="purchase_invoice_provenance",
    )
    order_accounting = models.ForeignKey(
        PurchaseOrderInvoiceAccounting,
        on_delete=models.PROTECT,
        related_name="invoice_provenance",
    )
    invoice_kind = models.CharField(max_length=16, choices=InvoiceKind.choices)
    source_downpayment_invoice = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="settlement_provenance",
    )

    def clean(self):
        order = self.order_accounting.order
        if self.invoice.company_id != order.company_id or self.invoice.partner_id != order.vendor_id:
            raise ValidationError("Purchase bill provenance must match the Purchase Order company and vendor.")

    def __str__(self):
        return f"{self.invoice.number} - {self.get_invoice_kind_display()}"


class PurchaseInvoiceLineProvenance(TimeStampedModel):
    invoice_line = models.OneToOneField(
        InvoiceLine,
        on_delete=models.CASCADE,
        related_name="purchase_invoice_provenance",
    )
    purchase_invoice = models.ForeignKey(
        PurchaseInvoiceAccounting,
        on_delete=models.CASCADE,
        related_name="line_provenance",
    )
    source_downpayment_line = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="settlement_applications",
    )
    order_line = models.ForeignKey(
        PurchaseOrderLine,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="purchase_invoice_line_provenance",
    )

    def clean(self):
        if self.invoice_line.invoice_id != self.purchase_invoice.invoice_id:
            raise ValidationError("Purchase invoice line provenance must belong to its invoice.")
        if self.source_downpayment_line_id and self.order_line_id:
            raise ValidationError("A settlement line cannot be both an order line and a down-payment application.")
        if self.order_line_id and self.order_line.order_id != self.purchase_invoice.order_accounting.order_id:
            raise ValidationError("Purchase bill line and provenance must belong to the same Purchase Order.")

    def __str__(self):
        return f"{self.purchase_invoice} - {self.invoice_line.description}"


class PurchaseAccountingLine(TimeStampedModel):
    document = models.ForeignKey(PurchaseAccountingDocument, on_delete=models.CASCADE, related_name="lines")
    account_code = models.CharField(max_length=40)
    description = models.CharField(max_length=240)
    debit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cost_center = models.CharField(max_length=80, blank=True)
    tax_code = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["id"]

    def clean(self):
        if self.debit < 0 or self.credit < 0:
            raise ValidationError("Debit and credit cannot be negative.")
        if self.debit and self.credit:
            raise ValidationError("An accounting line cannot have both debit and credit values.")

    def __str__(self):
        return f"{self.document} - {self.account_code}"


class PurchaseJournalLineAnalyticSnapshot(PurchaseCostCenterStructuredFields, TimeStampedModel):
    """Immutable Purchase assignment attached to the real Finance journal line."""

    journal_line = models.OneToOneField(
        "gsshop_finance.JournalLine",
        on_delete=models.CASCADE,
        related_name="purchase_analytic_snapshot",
    )
    order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.PROTECT,
        related_name="journal_line_analytic_snapshots",
    )
    order_line = models.ForeignKey(
        PurchaseOrderLine,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="purchase_journal_analytic_snapshots",
    )
    cost_center = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["journal_line_id"]

    def __str__(self):
        return f"{self.order.number} - journal line {self.journal_line_id}"


class PurchaseBillLineProvenance(TimeStampedModel):
    """Exact PO-line origin for one vendor-bill line."""

    accounting_document = models.ForeignKey(
        PurchaseAccountingDocument,
        on_delete=models.PROTECT,
        related_name="bill_line_provenance",
    )
    invoice_line = models.OneToOneField(
        InvoiceLine,
        on_delete=models.PROTECT,
        related_name="purchase_provenance",
    )
    order_line = models.ForeignKey(
        PurchaseOrderLine,
        on_delete=models.PROTECT,
        related_name="bill_line_provenance",
    )
    service_work_order = models.ForeignKey(
        ServiceWorkOrder,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="bill_line_provenance",
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        ordering = ["invoice_line_id"]

    def clean(self):
        errors = {}
        if self.quantity <= 0:
            errors["quantity"] = "Billed quantity must be greater than zero."
        if self.unit_price < 0:
            errors["unit_price"] = "Billed unit price cannot be negative."
        if self.accounting_document_id and self.order_line_id:
            if self.accounting_document.order_id != self.order_line.order_id:
                errors["order_line"] = "Bill provenance must belong to the accounting document purchase order."
        if self.service_work_order_id and self.order_line_id:
            if self.service_work_order.source_order_id != self.order_line.order_id:
                errors["service_work_order"] = "Service Work Order bill provenance must belong to the same Purchase Order."
        if self.accounting_document_id and self.invoice_line_id:
            if self.accounting_document.invoice_id != self.invoice_line.invoice_id:
                errors["invoice_line"] = "Bill provenance must belong to the accounting document invoice."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.accounting_document} - {self.order_line}"


class PurchaseStockDemand(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PLANNED = "planned", "Planned"
        PARTIAL = "partial", "Partially Fulfilled"
        FULFILLED = "fulfilled", "Fulfilled"
        CANCELLED = "cancelled", "Cancelled"

    request_line = models.ForeignKey(PurchaseRequestLine, on_delete=models.PROTECT, related_name="stock_demands")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="purchase_stock_demands")
    required_date = models.DateField()
    demanded_quantity = models.DecimalField(max_digits=18, decimal_places=3)
    fulfilled_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["required_date", "id"]

    def clean(self):
        if self.fulfilled_quantity > self.demanded_quantity:
            raise ValidationError({"fulfilled_quantity": "Fulfilled quantity cannot exceed demanded quantity."})

    def __str__(self):
        return f"{self.request_line} - {self.warehouse}"


class PurchaseInventoryTransfer(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready"
        IN_TRANSIT = "in_transit", "In Transit"
        DONE = "done", "Done"
        CANCELLED = "cancelled", "Cancelled"

    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.PROTECT, blank=True, null=True, related_name="inventory_transfers")
    reference = models.CharField(max_length=60, unique=True)
    source_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="outgoing_purchase_transfers")
    destination_warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="incoming_purchase_transfers")
    transfer_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-transfer_date"]

    def clean(self):
        if self.source_warehouse_id and self.source_warehouse_id == self.destination_warehouse_id:
            raise ValidationError({"destination_warehouse": "Destination warehouse must differ from source warehouse."})
        if (
            self.source_warehouse_id
            and self.destination_warehouse_id
            and self.source_warehouse.company_id != self.destination_warehouse.company_id
        ):
            raise ValidationError({"destination_warehouse": "Both warehouses must belong to the same company."})
        if self.receipt_id and self.source_warehouse_id and self.receipt.company_id != self.source_warehouse.company_id:
            raise ValidationError({"receipt": "The receipt and warehouses must belong to the same company."})

    def __str__(self):
        return self.reference


class PurchaseInventoryTransferLine(TimeStampedModel):
    transfer = models.ForeignKey(PurchaseInventoryTransfer, on_delete=models.CASCADE, related_name="lines")
    receipt_line = models.ForeignKey(GoodsReceiptLine, on_delete=models.SET_NULL, blank=True, null=True, related_name="transfer_lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="purchase_inventory_transfer_lines")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, validators=[MinValueValidator(Decimal("0.001"))])
    source_stock_move = models.OneToOneField(
        StockMove,
        on_delete=models.PROTECT,
        related_name="purchase_transfer_source_line",
        blank=True,
        null=True,
        editable=False,
    )
    destination_stock_move = models.OneToOneField(
        StockMove,
        on_delete=models.PROTECT,
        related_name="purchase_transfer_destination_line",
        blank=True,
        null=True,
        editable=False,
    )

    class Meta:
        ordering = ["id"]

    def clean(self):
        if self.product_id and self.transfer_id and self.product.company_id != self.transfer.source_warehouse.company_id:
            raise ValidationError({"product": "The product must belong to the transfer company."})
        if (
            self.receipt_line_id
            and self.transfer.receipt_id
            and self.receipt_line.receipt_id != self.transfer.receipt_id
        ):
            raise ValidationError({"receipt_line": "The receipt line must belong to the transfer receipt."})
        if self.receipt_line_id and self.product_id and self.receipt_line.order_line.product_id != self.product_id:
            raise ValidationError({"product": "The transfer product must match the selected receipt line."})

    def __str__(self):
        return f"{self.transfer} - {self.product}"


class PurchasePeppolEvent(TimeStampedModel):
    submission = models.ForeignKey(PurchasePeppolSubmission, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=16, choices=PurchasePeppolSubmission.Status.choices)
    occurred_at = models.DateTimeField(default=timezone.now)
    response_code = models.CharField(max_length=60, blank=True)
    message = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["occurred_at", "id"]

    def __str__(self):
        return f"{self.submission} - {self.status}"


class PurchaseContractTerm(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_contract_terms")
    order_template = models.ForeignKey(PurchaseOrderTemplate, on_delete=models.CASCADE, blank=True, null=True, related_name="contract_terms")
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, blank=True, null=True, related_name="contract_term_lines")
    sequence = models.PositiveIntegerField(default=10)
    term_type = models.CharField(max_length=60, default="general")
    title = models.CharField(max_length=180)
    body = models.TextField()
    mandatory = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "id"]

    def clean(self):
        if bool(self.order_template_id) == bool(self.order_id):
            raise ValidationError("Select exactly one order template or purchase order.")
        if self.order_template_id and self.order_template.company_id != self.company_id:
            raise ValidationError({"order_template": "The order template must belong to the selected company."})
        if self.order_id and self.order.company_id != self.company_id:
            raise ValidationError({"order": "The purchase order must belong to the selected company."})

    def __str__(self):
        return self.title


class PurchaseOrderRevision(TimeStampedModel):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="revisions")
    revised_order = models.OneToOneField(
        PurchaseOrder,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="revision_source",
    )
    original_order = models.ForeignKey(
        PurchaseOrder,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="revision_chain",
    )
    revision_number = models.PositiveIntegerField()
    reason = models.TextField()
    snapshot = models.JSONField(default=dict)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_order_revisions")
    effective_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["order", "-revision_number"]
        constraints = [models.UniqueConstraint(fields=["order", "revision_number"], name="unique_purchase_order_revision")]

    def __str__(self):
        return f"{self.order} rev.{self.revision_number}"
