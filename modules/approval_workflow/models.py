from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import Company, TimeStampedModel


class ApprovalWorkflow(TimeStampedModel):
    """Structural approval configuration for one transactional model."""

    content_type = models.OneToOneField(
        ContentType,
        on_delete=models.PROTECT,
        related_name="gsshop_approval_workflow",
    )
    description = models.TextField(blank=True)
    status_field = models.CharField(max_length=80, default="status")
    primary_perimeter = models.JSONField(default=list, blank=True)
    optional_perimeters = models.JSONField(default=list, blank=True)
    enable_mass_approval = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["content_type__app_label", "content_type__model"]

    @property
    def name(self):
        return self.content_type.name.title()

    def clean(self):
        super().clean()
        model = self.content_type.model_class() if self.content_type_id else None
        if model is None:
            raise ValidationError({"content_type": "Select a concrete transactional model."})
        field_names = {field.name for field in model._meta.get_fields()}
        if self.status_field not in field_names:
            raise ValidationError({"status_field": "The status field does not exist on the selected model."})
        if not isinstance(self.primary_perimeter, list) or not self.primary_perimeter:
            raise ValidationError({"primary_perimeter": "Define at least one primary perimeter field."})
        unknown = sorted(set(self.primary_perimeter) - field_names)
        if unknown:
            raise ValidationError({"primary_perimeter": f"Unknown model fields: {', '.join(unknown)}."})

    def __str__(self):
        return f"{self.content_type.app_label}.{self.content_type.model}"


class ApprovalWorkflowPerimeter(TimeStampedModel):
    class PerimeterType(models.TextChoices):
        PRIMARY = "primary", "Primary"
        OPTIONAL = "optional", "Optional"

    workflow = models.ForeignKey(ApprovalWorkflow, on_delete=models.PROTECT, related_name="perimeters")
    name = models.CharField(max_length=160)
    perimeter_type = models.CharField(max_length=16, choices=PerimeterType.choices)
    field_names = models.JSONField(default=list)
    priority = models.PositiveSmallIntegerField(default=10)
    is_override = models.BooleanField(default=False)

    class Meta:
        ordering = ["workflow", "priority", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["workflow", "name"], name="unique_approval_perimeter_name"),
            models.UniqueConstraint(
                fields=["workflow"],
                condition=Q(perimeter_type="primary"),
                name="one_primary_approval_perimeter",
            ),
        ]

    def clean(self):
        super().clean()
        if not isinstance(self.field_names, list) or not self.field_names:
            raise ValidationError({"field_names": "Choose at least one field."})
        allowed = set(self.workflow.primary_perimeter or [])
        allowed.update(
            field_name
            for perimeter in self.workflow.optional_perimeters or []
            if isinstance(perimeter, dict)
            for field_name in perimeter.get("field_names", [])
        )
        unknown = sorted(set(self.field_names) - allowed)
        if unknown:
            raise ValidationError({"field_names": f"Fields are outside the workflow perimeter: {', '.join(unknown)}."})

    def __str__(self):
        return f"{self.workflow} · {self.name}"


class ApprovalRule(TimeStampedModel):
    class State(models.TextChoices):
        UNASSOCIATED = "unassociated", "Unassociated"
        ASSOCIATED = "associated", "Associated"
        ARCHIVED = "archived", "Archived"

    class TimeoutAction(models.TextChoices):
        NONE = "none", "No Action"
        APPROVE = "approve", "Auto Approve"
        REJECT = "reject", "Auto Reject"

    class Period(models.TextChoices):
        MINUTES = "minutes", "Minutes"
        DAYS = "days", "Days"
        WEEKS = "weeks", "Weeks"

    workflow = models.ForeignKey(ApprovalWorkflow, on_delete=models.PROTECT, related_name="rules")
    perimeter = models.ForeignKey(
        ApprovalWorkflowPerimeter,
        on_delete=models.PROTECT,
        related_name="rules",
        blank=True,
        null=True,
    )
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    companies = models.ManyToManyField(Company, blank=True, related_name="approval_rules")
    state = models.CharField(max_length=16, choices=State.choices, default=State.UNASSOCIATED)
    priority = models.PositiveSmallIntegerField(default=100)
    trigger_conditions = models.JSONField(default=list, blank=True)
    approval_levels = models.JSONField(default=list)
    # The JSON fields remain as a read-compatible fallback for rules created
    # before line configuration was introduced. New rules use the relational
    # configuration below, while pending instances always use their snapshot.
    use_relational_conditions = models.BooleanField(default=True)
    use_relational_levels = models.BooleanField(default=True)
    master_approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="master_approval_rules",
    )
    use_delegate = models.BooleanField(default=False)
    delegate_after = models.PositiveIntegerField(blank=True, null=True)
    delegate_period = models.CharField(max_length=10, choices=Period.choices, blank=True)
    require_approve_reason = models.BooleanField(default=False)
    timeout_action = models.CharField(max_length=16, choices=TimeoutAction.choices, default=TimeoutAction.NONE)
    timeout_value = models.PositiveIntegerField(blank=True, null=True)
    timeout_period = models.CharField(max_length=10, choices=Period.choices, blank=True)
    notification_settings = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["priority", "pk"]
        indexes = [models.Index(fields=["workflow", "state", "priority"], name="approval_rule_resolution_idx")]

    def clean(self):
        super().clean()
        if self.perimeter_id and self.perimeter.workflow_id != self.workflow_id:
            raise ValidationError({"perimeter": "The perimeter must belong to the selected workflow."})
        if not self.use_relational_levels:
            if not isinstance(self.approval_levels, list) or not self.approval_levels:
                raise ValidationError({"approval_levels": "Define at least one approval level."})
            for index, level in enumerate(self.approval_levels, start=1):
                if not isinstance(level, dict):
                    raise ValidationError({"approval_levels": f"Level {index} must be an object."})
                minimum = level.get("minimum_approvals", level.get("min_approval", 1))
                try:
                    minimum = int(minimum)
                except (TypeError, ValueError) as exc:
                    raise ValidationError({"approval_levels": f"Level {index} has an invalid minimum approval count."}) from exc
                if minimum < 1:
                    raise ValidationError({"approval_levels": f"Level {index} requires at least one approval."})
                if not level.get("approvers") and not level.get("user_ids") and not level.get("group_names"):
                    raise ValidationError({"approval_levels": f"Level {index} has no approver source."})
        if self.use_delegate and (not self.delegate_after or not self.delegate_period):
            raise ValidationError("Delegate timing is required when delegation is enabled.")
        if self.timeout_action != self.TimeoutAction.NONE and (not self.timeout_value or not self.timeout_period):
            raise ValidationError("Timeout timing is required for automatic timeout actions.")
        if (
            self.timeout_action != self.TimeoutAction.NONE
            and self.workflow_id
            and not ApprovalTimeoutWhitelist.objects.filter(content_type_id=self.workflow.content_type_id).exists()
        ):
            raise ValidationError(
                {"timeout_action": "Add this workflow model to the Approval Timeout Whitelist first."}
            )

    def save(self, *args, **kwargs):
        """Keep older integrations working when they explicitly update JSON."""
        update_fields = kwargs.get("update_fields")
        if update_fields:
            update_fields = set(update_fields)
            if "approval_levels" in update_fields and "use_relational_levels" not in update_fields:
                self.use_relational_levels = False
                update_fields.add("use_relational_levels")
            if "trigger_conditions" in update_fields and "use_relational_conditions" not in update_fields:
                self.use_relational_conditions = False
                update_fields.add("use_relational_conditions")
            kwargs["update_fields"] = update_fields
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ApprovalRuleConditionGroup(TimeStampedModel):
    """A relational AND/OR group used when selecting an approval rule."""

    class Operator(models.TextChoices):
        AND = "and", "All conditions"
        OR = "or", "Any condition"

    rule = models.ForeignKey(ApprovalRule, on_delete=models.CASCADE, related_name="condition_groups")
    name = models.CharField(max_length=120, blank=True)
    sequence = models.PositiveSmallIntegerField(default=10)
    operator = models.CharField(max_length=3, choices=Operator.choices, default=Operator.AND)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["rule", "sequence"], name="approval_condition_group_sequence"),
        ]

    def __str__(self):
        return self.name or f"{self.rule} · condition group {self.sequence}"


class ApprovalRuleConditionLine(TimeStampedModel):
    """One typed comparison inside an approval-rule condition group."""

    group = models.ForeignKey(ApprovalRuleConditionGroup, on_delete=models.CASCADE, related_name="lines")
    field_name = models.CharField(max_length=160)
    operator = models.CharField(max_length=20, default="eq")
    value = models.JSONField(blank=True, null=True)
    sequence = models.PositiveSmallIntegerField(default=10)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["group", "sequence"], name="approval_condition_line_sequence"),
        ]

    def clean(self):
        super().clean()
        if not self.group_id or not self.field_name:
            return
        workflow = self.group.rule.workflow
        allowed = set(workflow.primary_perimeter or [])
        allowed.update(
            field_name
            for perimeter in workflow.optional_perimeters or []
            if isinstance(perimeter, dict)
            for field_name in perimeter.get("field_names", [])
        )
        if self.field_name not in allowed:
            raise ValidationError({"field_name": "Choose a field from the rule workflow perimeter."})

    def __str__(self):
        return f"{self.field_name} {self.operator}"


class ApprovalRuleLevel(TimeStampedModel):
    """A separately addressable approval layer belonging to one rule."""

    class ApprovalType(models.TextChoices):
        USER = "user", "Users"
        ACCESS_PROFILE = "access_profile", "Access Profile"
        HIERARCHY = "hierarchy", "Hierarchy"
        FUNCTIONAL_HIERARCHY = "functional_hierarchy", "Functional Hierarchy"

    rule = models.ForeignKey(ApprovalRule, on_delete=models.CASCADE, related_name="levels")
    sequence = models.PositiveSmallIntegerField(default=10)
    name = models.CharField(max_length=120, blank=True)
    approval_type = models.CharField(max_length=24, choices=ApprovalType.choices, default=ApprovalType.USER)
    minimum_approvals = models.PositiveSmallIntegerField(default=1)
    maximum_hierarchy = models.PositiveSmallIntegerField(blank=True, null=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["rule", "sequence"], name="approval_rule_level_sequence"),
        ]

    def clean(self):
        super().clean()
        if self.minimum_approvals < 1:
            raise ValidationError({"minimum_approvals": "A level requires at least one approval."})
        if self.maximum_hierarchy and self.maximum_hierarchy > 5:
            raise ValidationError({"maximum_hierarchy": "At most five hierarchy levels are supported."})

    def __str__(self):
        return self.name or f"{self.rule} · level {self.sequence}"


class ApprovalRuleLevelAssignee(TimeStampedModel):
    """One approver or delegate source attached to a relational approval level."""

    class Role(models.TextChoices):
        APPROVER = "approver", "Approver"
        DELEGATE = "delegate", "Delegate"

    class Source(models.TextChoices):
        USER = "user", "Specific User"
        GROUP = "group", "Access Profile / Group"
        RECORD_FIELD = "record_field", "Record User Field"
        RECORD_SUPERVISOR = "record_supervisor", "Employee Supervisor"

    level = models.ForeignKey(ApprovalRuleLevel, on_delete=models.CASCADE, related_name="assignees")
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.APPROVER)
    source = models.CharField(max_length=20, choices=Source.choices)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approval_rule_level_assignments",
        blank=True,
        null=True,
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.PROTECT,
        related_name="approval_rule_level_assignments",
        blank=True,
        null=True,
    )
    # Kept for migrations and configurations that reference a group by its
    # business name before the group has been provisioned in Django.
    group_name = models.CharField(max_length=150, blank=True)
    field_path = models.CharField(max_length=160, blank=True)
    sequence = models.PositiveSmallIntegerField(default=10)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "pk"]

    def clean(self):
        super().clean()
        errors = {}
        if self.source == self.Source.USER and not self.user_id:
            errors["user"] = "Choose a user for this source."
        if self.source == self.Source.GROUP and not (self.group_id or self.group_name):
            errors["group"] = "Choose a group or provide its configured group name."
        if self.source == self.Source.RECORD_FIELD and not self.field_path:
            errors["field_path"] = "Provide the record user-field path."
        if self.source in {self.Source.RECORD_FIELD, self.Source.RECORD_SUPERVISOR} and self.role == self.Role.DELEGATE:
            errors["role"] = "Delegates must be users or groups."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.level} · {self.get_role_display()}"


class ApprovalDelegation(TimeStampedModel):
    delegator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="approval_delegations")
    delegate = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="delegated_approvals")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, blank=True, null=True, related_name="approval_delegations")
    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField(blank=True, null=True)
    while_away = models.BooleanField(default=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-valid_from", "pk"]
        constraints = [
            models.CheckConstraint(condition=~Q(delegator=models.F("delegate")), name="approval_delegate_not_self"),
        ]
        indexes = [models.Index(fields=["delegate", "active", "valid_from"], name="approval_delegate_lookup_idx")]

    def clean(self):
        super().clean()
        if self.delegator_id and self.delegator_id == self.delegate_id:
            raise ValidationError({"delegate": "An approver cannot delegate to themselves."})
        if self.valid_until and self.valid_until <= self.valid_from:
            raise ValidationError({"valid_until": "Delegation end must be after its start."})

    def __str__(self):
        return f"{self.delegator} → {self.delegate}"


class ApprovalInstance(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    workflow = models.ForeignKey(ApprovalWorkflow, on_delete=models.PROTECT, related_name="instances")
    rule = models.ForeignKey(ApprovalRule, on_delete=models.PROTECT, related_name="instances")
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="approval_instances")
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="approval_instances")
    entity = models.CharField(max_length=100)
    reference = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    current_level = models.PositiveSmallIntegerField(default=0)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="submitted_approvals")
    submitted_at = models.DateTimeField(default=timezone.now)
    level_started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(blank=True, null=True)
    approval_levels_snapshot = models.JSONField(default=list)
    trigger_snapshot = models.JSONField(default=dict)
    notification_snapshot = models.JSONField(default=dict)
    timeout_action_snapshot = models.CharField(
        max_length=16,
        choices=ApprovalRule.TimeoutAction.choices,
        default=ApprovalRule.TimeoutAction.NONE,
    )
    timeout_value_snapshot = models.PositiveIntegerField(blank=True, null=True)
    timeout_period_snapshot = models.CharField(
        max_length=10,
        choices=ApprovalRule.Period.choices,
        blank=True,
    )
    timeout_due_at = models.DateTimeField(blank=True, null=True)
    delegate_after_snapshot = models.PositiveIntegerField(blank=True, null=True)
    delegate_period_snapshot = models.CharField(
        max_length=10,
        choices=ApprovalRule.Period.choices,
        blank=True,
    )
    delegate_due_at = models.DateTimeField(blank=True, null=True)
    delegate_activated_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-submitted_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id"],
                condition=Q(status="pending"),
                name="one_pending_approval_per_record",
            )
        ]
        indexes = [
            models.Index(fields=["company", "status", "submitted_at"], name="approval_inbox_idx"),
            models.Index(fields=["content_type", "object_id"], name="approval_target_idx"),
            models.Index(fields=["status", "timeout_due_at"], name="approval_timeout_due_idx"),
            models.Index(fields=["status", "delegate_due_at"], name="approval_delegate_due_idx"),
        ]

    @property
    def active_level(self):
        levels = self.approval_levels_snapshot or []
        if self.current_level >= len(levels):
            return None
        return levels[self.current_level]

    def __str__(self):
        return f"{self.reference} · {self.get_status_display()}"


class ApprovalAction(TimeStampedModel):
    class Action(models.TextChoices):
        SUBMIT = "submit", "Submit"
        APPROVE = "approve", "Approve"
        REJECT = "reject", "Reject"
        MASTER_APPROVE = "master_approve", "Master Approve"
        MASTER_REJECT = "master_reject", "Master Reject"
        AUTO_APPROVE = "auto_approve", "Auto Approve"
        AUTO_REJECT = "auto_reject", "Auto Reject"
        AUTO_DELEGATE = "auto_delegate", "Auto Delegate"
        CANCEL = "cancel", "Cancel"

    instance = models.ForeignKey(ApprovalInstance, on_delete=models.PROTECT, related_name="actions")
    action = models.CharField(max_length=24, choices=Action.choices)
    level = models.PositiveSmallIntegerField(blank=True, null=True)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="approval_actions")
    effective_approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="effective_approval_actions",
        blank=True,
        null=True,
    )
    delegated_from = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="delegated_from_approval_actions",
        blank=True,
        null=True,
    )
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    performed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["performed_at", "pk"]
        indexes = [models.Index(fields=["instance", "level", "action"], name="approval_action_level_idx")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Approval history is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Approval history cannot be deleted.")

    def __str__(self):
        return f"{self.instance.reference} · {self.get_action_display()}"


class ApprovalOutbox(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    instance = models.ForeignKey(ApprovalInstance, on_delete=models.PROTECT, related_name="outbox_messages")
    event = models.CharField(max_length=80)
    recipient_ids = models.JSONField(default=list)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["available_at", "pk"]
        indexes = [models.Index(fields=["status", "available_at"], name="approval_outbox_due_idx")]


class ApprovalNotification(TimeStampedModel):
    """Durable in-app delivery produced from the approval outbox."""

    outbox = models.ForeignKey(ApprovalOutbox, on_delete=models.PROTECT, related_name="notifications")
    instance = models.ForeignKey(ApprovalInstance, on_delete=models.PROTECT, related_name="notifications")
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approval_notifications",
    )
    event = models.CharField(max_length=80)
    title = models.CharField(max_length=255)
    message = models.TextField()
    read_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["outbox", "recipient"], name="unique_approval_outbox_recipient"),
        ]
        indexes = [models.Index(fields=["recipient", "read_at", "created_at"], name="appr_notification_inbox_idx")]

    def __str__(self):
        return self.title


class ApprovalTimeoutWhitelist(TimeStampedModel):
    content_type = models.OneToOneField(ContentType, on_delete=models.CASCADE, related_name="approval_timeout_whitelist")
    reason = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.content_type.name


def timeout_delta(value, period):
    if not value:
        return None
    if period == ApprovalRule.Period.MINUTES:
        return timedelta(minutes=value)
    if period == ApprovalRule.Period.DAYS:
        return timedelta(days=value)
    if period == ApprovalRule.Period.WEEKS:
        return timedelta(weeks=value)
    return None
