from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import (
    Company,
    Contact,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    TimeStampedModel,
    Warehouse,
)


def current_year():
    return timezone.localdate().year


class ApprovalStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class PurchaseRequest(TimeStampedModel):
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
    number = models.CharField(max_length=40)
    requestor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_requests")
    request_date = models.DateField(default=timezone.localdate)
    required_date = models.DateField()
    justification = models.TextField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    rejection_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-request_date", "number"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="unique_purchase_request_number_per_company")
        ]

    def clean(self):
        if self.required_date and self.required_date < self.request_date:
            raise ValidationError({"required_date": "Required date must be on or after request date."})

    def __str__(self):
        return self.number


class PurchaseRequestLine(TimeStampedModel):
    request = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="purchase_request_lines")
    description = models.CharField(max_length=240)
    specification = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1, validators=[MinValueValidator(Decimal("0.001"))])
    approved_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    ordered_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    estimated_unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]

    def clean(self):
        if self.approved_quantity > self.quantity:
            raise ValidationError({"approved_quantity": "Approved quantity cannot exceed requested quantity."})
        if self.ordered_quantity > self.approved_quantity:
            raise ValidationError({"ordered_quantity": "Ordered quantity cannot exceed approved quantity."})

    @property
    def estimated_total(self):
        return self.quantity * self.estimated_unit_price

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
    number = models.CharField(max_length=40)
    request = models.ForeignKey(PurchaseRequest, on_delete=models.SET_NULL, blank=True, null=True, related_name="quotations")
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="purchase_quotations")
    issue_date = models.DateField(default=timezone.localdate)
    response_deadline = models.DateField()
    currency = models.CharField(max_length=3, default="IDR")
    quoted_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-issue_date"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="unique_purchase_quotation_number_per_company")
        ]

    def __str__(self):
        return self.number


class DirectPurchase(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="direct_purchases")
    number = models.CharField(max_length=40)
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="direct_purchases")
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


class ServiceWorkOrder(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="service_work_orders")
    number = models.CharField(max_length=40)
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="service_work_orders")
    service_description = models.TextField()
    date_start = models.DateField()
    date_end = models.DateField(blank=True, null=True)
    contract_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="service_work_order_approvals")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_start"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_service_work_order_number_per_company")]

    def clean(self):
        if self.date_end and self.date_end < self.date_start:
            raise ValidationError({"date_end": "End date must be on or after start date."})

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
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_award_strategy_per_company")]

    def __str__(self):
        return self.name


class PurchaseTender(TimeStampedModel):
    class Category(models.TextChoices):
        OPEX = "opex", "OPEX"
        CAPEX = "capex", "CAPEX"

    class TenderType(models.TextChoices):
        OPEN = "open", "Open Tender"
        INVITED = "invited", "Invited Tender"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        SUBMISSION = "submission", "Bid Submission"
        EVALUATION = "evaluation", "Evaluation"
        AWARDED = "awarded", "Awarded"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_tenders")
    number = models.CharField(max_length=40)
    title = models.CharField(max_length=180)
    category = models.CharField(max_length=10, choices=Category.choices)
    tender_type = models.CharField(max_length=12, choices=TenderType.choices)
    award_strategy = models.ForeignKey(AwardStrategy, on_delete=models.PROTECT, related_name="tenders")
    publish_date = models.DateField(blank=True, null=True)
    close_date = models.DateField()
    budget_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_purchase_tender_number_per_company")]

    def __str__(self):
        return self.number


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
    allocation_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["bid_amount", "-technical_score"]
        constraints = [models.UniqueConstraint(fields=["tender", "vendor"], name="unique_vendor_bid_per_tender")]

    def __str__(self):
        return f"{self.tender} - {self.vendor}"


class BlanketOrder(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        EXHAUSTED = "exhausted", "Exhausted"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="blanket_orders")
    number = models.CharField(max_length=40)
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="blanket_orders")
    date_start = models.DateField()
    date_end = models.DateField()
    agreement_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    consumed_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    terms = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-date_start"]
        constraints = [models.UniqueConstraint(fields=["company", "number"], name="unique_blanket_order_number_per_company")]

    def clean(self):
        if self.date_end < self.date_start:
            raise ValidationError({"date_end": "End date must be on or after start date."})
        if self.consumed_value > self.agreement_value:
            raise ValidationError({"consumed_value": "Consumed value cannot exceed agreement value."})

    def __str__(self):
        return self.number


class VendorCategory(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="vendor_categories")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    risk_level = models.CharField(max_length=20, default="standard")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_vendor_category_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class VendorProfile(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        BLACKLISTED = "blacklisted", "Blacklisted"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_vendor_profiles")
    partner = models.OneToOneField(Contact, on_delete=models.PROTECT, related_name="purchase_vendor_profile")
    category = models.ForeignKey(VendorCategory, on_delete=models.SET_NULL, blank=True, null=True, related_name="vendors")
    registration_number = models.CharField(max_length=80, blank=True)
    tax_number = models.CharField(max_length=80, blank=True)
    rating = models.DecimalField(max_digits=4, decimal_places=2, default=0, validators=[MaxValueValidator(5)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["partner__name"]

    def __str__(self):
        return str(self.partner)


class VendorLegalDocument(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        VALID = "valid", "Valid"
        EXPIRED = "expired", "Expired"
        REJECTED = "rejected", "Rejected"

    vendor = models.ForeignKey(VendorProfile, on_delete=models.CASCADE, related_name="legal_documents")
    document_type = models.CharField(max_length=100)
    document_number = models.CharField(max_length=100, blank=True)
    issue_date = models.DateField(blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)
    attachment_url = models.URLField(blank=True)
    mandatory = models.BooleanField(default=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["vendor__partner__name", "document_type"]

    def __str__(self):
        return f"{self.vendor} - {self.document_type}"


class VendorPrequalification(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending"
        UNDER_REVIEW = "under_review", "Under Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    vendor = models.ForeignKey(VendorProfile, on_delete=models.PROTECT, related_name="prequalifications")
    number = models.CharField(max_length=40, unique=True)
    submitted_date = models.DateField(blank=True, null=True)
    review_due_date = models.DateField(blank=True, null=True)
    score = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="vendor_prequalification_reviews")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number


class VendorDocumentReview(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        REVISION = "revision", "Revision Required"

    prequalification = models.ForeignKey(VendorPrequalification, on_delete=models.CASCADE, related_name="document_reviews")
    document = models.ForeignKey(VendorLegalDocument, on_delete=models.PROTECT, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="vendor_document_reviews")
    reviewed_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.prequalification} - {self.document}"


class VendorPricelist(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="vendor_pricelists")
    vendor = models.ForeignKey(VendorProfile, on_delete=models.PROTECT, related_name="pricelists")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="vendor_pricelists")
    minimum_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="IDR")
    date_start = models.DateField(default=timezone.localdate)
    date_end = models.DateField(blank=True, null=True)
    lead_time_days = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["product__name", "unit_price"]
        constraints = [
            models.UniqueConstraint(
                fields=["vendor", "product", "minimum_quantity", "date_start"],
                name="unique_vendor_pricelist_effective_tier",
            )
        ]

    def __str__(self):
        return f"{self.vendor} - {self.product}"


class VendorPricelistAmendment(TimeStampedModel):
    pricelist = models.ForeignKey(VendorPricelist, on_delete=models.PROTECT, related_name="amendments")
    effective_date = models.DateField(default=timezone.localdate)
    previous_price = models.DecimalField(max_digits=18, decimal_places=2)
    new_price = models.DecimalField(max_digits=18, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="vendor_pricelist_approvals")

    class Meta:
        ordering = ["-effective_date"]

    def __str__(self):
        return f"{self.pricelist} - {self.effective_date}"


class VendorPerformanceTemplate(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="vendor_performance_templates")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    quality_weight = models.DecimalField(max_digits=5, decimal_places=2, default=40)
    delivery_weight = models.DecimalField(max_digits=5, decimal_places=2, default=30)
    service_weight = models.DecimalField(max_digits=5, decimal_places=2, default=30)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_vendor_performance_template_code")]

    def clean(self):
        total = self.quality_weight + self.delivery_weight + self.service_weight
        if total != Decimal("100"):
            raise ValidationError("Vendor performance weights must total 100%.")

    def __str__(self):
        return f"{self.code} - {self.name}"


class VendorPerformanceEvaluation(TimeStampedModel):
    class Rating(models.TextChoices):
        VERY_POOR = "very_poor", "Very Poor"
        POOR = "poor", "Poor"
        FAIR = "fair", "Fair"
        GOOD = "good", "Good"
        EXCELLENT = "excellent", "Excellent"

    vendor = models.ForeignKey(VendorProfile, on_delete=models.PROTECT, related_name="performance_evaluations")
    template = models.ForeignKey(VendorPerformanceTemplate, on_delete=models.PROTECT, related_name="evaluations")
    order = models.ForeignKey(PurchaseOrder, on_delete=models.SET_NULL, blank=True, null=True, related_name="vendor_evaluations")
    period_start = models.DateField()
    period_end = models.DateField()
    quality_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    delivery_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    service_score = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    overall_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    rating = models.CharField(max_length=16, choices=Rating.choices, default=Rating.FAIR)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-period_end"]

    def clean(self):
        if self.period_end < self.period_start:
            raise ValidationError({"period_end": "Period end must be on or after period start."})

    def __str__(self):
        return f"{self.vendor} - {self.period_end}"


class PurchaseTeam(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_teams")
    name = models.CharField(max_length=120)
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="managed_purchase_teams")
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_purchase_team_per_company")]

    def __str__(self):
        return self.name


class PurchaseTeamMember(TimeStampedModel):
    team = models.ForeignKey(PurchaseTeam, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="purchase_team_memberships")
    role = models.CharField(max_length=80, default="Buyer")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["team__name", "user__username"]
        constraints = [models.UniqueConstraint(fields=["team", "user"], name="unique_purchase_team_member")]

    def __str__(self):
        return f"{self.team} - {self.user}"


class PurchaseKPITemplate(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_kpi_templates")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    unit = models.CharField(max_length=40, default="percent")
    direction = models.CharField(max_length=20, default="higher_is_better")
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_purchase_kpi_template_code")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class PurchaseKPITarget(TimeStampedModel):
    team = models.ForeignKey(PurchaseTeam, on_delete=models.PROTECT, related_name="kpi_targets")
    template = models.ForeignKey(PurchaseKPITemplate, on_delete=models.PROTECT, related_name="targets")
    period_start = models.DateField()
    period_end = models.DateField()
    target_value = models.DecimalField(max_digits=18, decimal_places=4)
    actual_value = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)

    class Meta:
        ordering = ["-period_start"]
        constraints = [
            models.UniqueConstraint(fields=["team", "template", "period_start"], name="unique_purchase_kpi_target_period")
        ]

    def __str__(self):
        return f"{self.team} - {self.template}"


class PurchaseOrderType(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_order_types")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    sequence_prefix = models.CharField(max_length=20, default="PO")
    requires_approval = models.BooleanField(default=True)
    allow_direct_purchase = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_purchase_order_type_code")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class PurchaseOrderTemplate(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="purchase_order_templates")
    order_type = models.ForeignKey(PurchaseOrderType, on_delete=models.PROTECT, related_name="templates")
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


class PurchaseMilestoneTemplate(TimeStampedModel):
    order_template = models.ForeignKey(PurchaseOrderTemplate, on_delete=models.CASCADE, related_name="milestone_templates")
    sequence = models.PositiveIntegerField(default=10)
    name = models.CharField(max_length=120)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    due_days = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order_template", "sequence"]
        constraints = [
            models.UniqueConstraint(fields=["order_template", "sequence"], name="unique_purchase_milestone_template_sequence")
        ]

    def __str__(self):
        return f"{self.order_template} - {self.name}"


class PurchaseSetting(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="purchase_setting")
    default_currency = models.CharField(max_length=3, default="IDR")
    request_approval_levels = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(5)])
    order_approval_levels = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(5)])
    allow_overbudget = models.BooleanField(default=False)
    receipt_tolerance_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    require_vendor_prequalification = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__name"]

    def __str__(self):
        return f"Purchase Settings - {self.company}"


class PurchaseOrderControl(TimeStampedModel):
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
    vendor_pricelist = models.ForeignKey(
        VendorPricelist,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="orders",
    )
    team = models.ForeignKey(PurchaseTeam, on_delete=models.SET_NULL, blank=True, null=True, related_name="orders")
    revision_number = models.PositiveIntegerField(default=0)
    expiry_date = models.DateField(blank=True, null=True)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    cost_center = models.CharField(max_length=80, blank=True)
    contract_terms = models.TextField(blank=True)
    communication_history = models.TextField(blank=True)
    receiving_status = models.CharField(max_length=20, choices=ReceivingStatus.choices, default=ReceivingStatus.NOT_RECEIVED)
    accounting_status = models.CharField(max_length=20, choices=AccountingStatus.choices, default=AccountingStatus.NOT_BILLED)
    approval_reference = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-order__order_date"]

    def __str__(self):
        return str(self.order)


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

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["receipt", "order_line"], name="unique_goods_receipt_order_line")]

    def clean(self):
        if self.accepted_quantity + self.rejected_quantity > self.received_quantity:
            raise ValidationError("Accepted and rejected quantities cannot exceed received quantity.")

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
    quotation = models.ForeignKey(PurchaseQuotation, on_delete=models.CASCADE, related_name="lines")
    request_line = models.ForeignKey(PurchaseRequestLine, on_delete=models.SET_NULL, blank=True, null=True, related_name="quotation_lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="purchase_quotation_lines")
    description = models.CharField(max_length=240)
    specification = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1, validators=[MinValueValidator(Decimal("0.001"))])
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    promised_date = models.DateField(blank=True, null=True)

    class Meta:
        ordering = ["id"]

    @property
    def subtotal(self):
        discounted = self.quantity * self.unit_price * (Decimal("1") - self.discount_percent / Decimal("100"))
        return discounted * (Decimal("1") + self.tax_percent / Decimal("100"))

    def clean(self):
        if self.request_line_id and self.quotation.request_id and self.request_line.request_id != self.quotation.request_id:
            raise ValidationError({"request_line": "The request line must belong to the quotation's purchase request."})

    def __str__(self):
        return f"{self.quotation} - {self.description}"


class DirectPurchaseLine(TimeStampedModel):
    direct_purchase = models.ForeignKey(DirectPurchase, on_delete=models.CASCADE, related_name="lines")
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
    work_order = models.ForeignKey(ServiceWorkOrder, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=240)
    unit = models.CharField(max_length=40, default="service")
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    unit_rate = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, validators=[MaxValueValidator(100)])
    cost_center = models.CharField(max_length=80, blank=True)
    milestone_name = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["id"]

    @property
    def subtotal(self):
        return self.quantity * self.unit_rate * (Decimal("1") + self.tax_percent / Decimal("100"))

    def __str__(self):
        return f"{self.work_order} - {self.description}"


class PurchaseTenderLine(TimeStampedModel):
    tender = models.ForeignKey(PurchaseTender, on_delete=models.CASCADE, related_name="lines")
    request_line = models.ForeignKey(PurchaseRequestLine, on_delete=models.SET_NULL, blank=True, null=True, related_name="tender_lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, blank=True, null=True, related_name="purchase_tender_lines")
    description = models.CharField(max_length=240)
    specification = models.TextField(blank=True)
    quantity = models.DecimalField(max_digits=18, decimal_places=3, default=1)
    target_unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    delivery_date = models.DateField(blank=True, null=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.tender} - {self.description}"


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
    bid = models.ForeignKey(TenderBid, on_delete=models.CASCADE, blank=True, null=True, related_name="documents")
    vendor = models.ForeignKey(Contact, on_delete=models.PROTECT, blank=True, null=True, related_name="tender_documents")
    document_type = models.CharField(max_length=100)
    title = models.CharField(max_length=180)
    attachment_url = models.URLField()
    mandatory = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    review_note = models.TextField(blank=True)

    class Meta:
        ordering = ["tender", "document_type", "title"]

    def clean(self):
        if self.bid_id and self.bid.tender_id != self.tender_id:
            raise ValidationError({"bid": "The bid must belong to this tender."})
        if self.bid_id and self.vendor_id and self.bid.vendor_id != self.vendor_id:
            raise ValidationError({"vendor": "The vendor must match the selected bid."})

    def __str__(self):
        return f"{self.tender} - {self.title}"


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


class BlanketOrderLine(TimeStampedModel):
    blanket_order = models.ForeignKey(BlanketOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="blanket_order_lines")
    description = models.CharField(max_length=240, blank=True)
    agreed_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    released_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    unit_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    minimum_release_quantity = models.DecimalField(max_digits=18, decimal_places=3, default=0)

    class Meta:
        ordering = ["product__name"]
        constraints = [models.UniqueConstraint(fields=["blanket_order", "product"], name="unique_blanket_order_product")]

    def clean(self):
        if self.released_quantity > self.agreed_quantity:
            raise ValidationError({"released_quantity": "Released quantity cannot exceed agreed quantity."})

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

    class Meta:
        ordering = ["-transaction_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["vendor", "transaction_type", "reference"], name="unique_vendor_transaction_reference")]

    def __str__(self):
        return self.reference


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
