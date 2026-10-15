from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import Company, Contact, TimeStampedModel


class Lead(TimeStampedModel):
    class Stage(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        QUALIFIED = "qualified", "Qualified"
        WON = "won", "Won"
        LOST = "lost", "Lost"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="leads")
    name = models.CharField(max_length=160)
    organization = models.CharField(max_length=160, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    source = models.CharField(max_length=80, blank=True)
    stage = models.CharField(max_length=16, choices=Stage.choices, default=Stage.NEW)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="assigned_leads")
    expected_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class Opportunity(TimeStampedModel):
    class Stage(models.TextChoices):
        DISCOVERY = "discovery", "Discovery"
        PROPOSAL = "proposal", "Proposal"
        NEGOTIATION = "negotiation", "Negotiation"
        WON = "won", "Won"
        LOST = "lost", "Lost"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="opportunities")
    lead = models.ForeignKey(Lead, on_delete=models.SET_NULL, blank=True, null=True, related_name="opportunities")
    customer = models.ForeignKey(Contact, on_delete=models.SET_NULL, blank=True, null=True, related_name="opportunities")
    name = models.CharField(max_length=180)
    stage = models.CharField(max_length=20, choices=Stage.choices, default=Stage.DISCOVERY)
    probability = models.PositiveSmallIntegerField(default=10, validators=[MinValueValidator(0), MaxValueValidator(100)])
    value = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal("0"))
    expected_close = models.DateField(blank=True, null=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="owned_opportunities")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class CRMActivity(TimeStampedModel):
    class ActivityType(models.TextChoices):
        CALL = "call", "Call"
        MEETING = "meeting", "Meeting"
        EMAIL = "email", "Email"
        FOLLOW_UP = "follow_up", "Follow Up"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, blank=True, null=True, related_name="activities")
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, blank=True, null=True, related_name="activities")
    activity_type = models.CharField(max_length=16, choices=ActivityType.choices)
    subject = models.CharField(max_length=180)
    due_at = models.DateTimeField(default=timezone.now)
    completed = models.BooleanField(default=False)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="crm_activities")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["completed", "due_at"]

    def __str__(self):
        return self.subject

