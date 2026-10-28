from django import forms

from core.forms import StyledModelForm

from .models import (
    ApprovalDelegation,
    ApprovalRule,
    ApprovalRuleConditionGroup,
    ApprovalRuleConditionLine,
    ApprovalRuleLevel,
    ApprovalRuleLevelAssignee,
    ApprovalWorkflow,
    ApprovalWorkflowPerimeter,
)


class ApprovalWorkflowForm(StyledModelForm):
    class Meta:
        model = ApprovalWorkflow
        fields = [
            "content_type",
            "description",
            "status_field",
            "primary_perimeter",
            "optional_perimeters",
            "enable_mass_approval",
            "active",
        ]


class ApprovalWorkflowPerimeterForm(StyledModelForm):
    class Meta:
        model = ApprovalWorkflowPerimeter
        fields = ["workflow", "name", "perimeter_type", "field_names", "priority", "is_override"]


class ApprovalRuleForm(StyledModelForm):
    class Meta:
        model = ApprovalRule
        fields = [
            "workflow",
            "perimeter",
            "name",
            "description",
            "companies",
            "state",
            "priority",
            "use_relational_conditions",
            "use_relational_levels",
            "master_approver",
            "use_delegate",
            "delegate_after",
            "delegate_period",
            "require_approve_reason",
            "timeout_action",
            "timeout_value",
            "timeout_period",
            "notification_settings",
        ]


class ApprovalRuleConditionGroupForm(StyledModelForm):
    class Meta:
        model = ApprovalRuleConditionGroup
        fields = ["rule", "name", "sequence", "operator", "active"]


class ApprovalRuleConditionLineForm(StyledModelForm):
    class Meta:
        model = ApprovalRuleConditionLine
        fields = ["group", "field_name", "operator", "value", "sequence", "active"]


class ApprovalRuleLevelForm(StyledModelForm):
    class Meta:
        model = ApprovalRuleLevel
        fields = [
            "rule",
            "sequence",
            "name",
            "approval_type",
            "minimum_approvals",
            "maximum_hierarchy",
            "active",
        ]


class ApprovalRuleLevelAssigneeForm(StyledModelForm):
    class Meta:
        model = ApprovalRuleLevelAssignee
        fields = [
            "level",
            "role",
            "source",
            "user",
            "group",
            "group_name",
            "field_path",
            "sequence",
            "active",
        ]


class ApprovalDelegationForm(StyledModelForm):
    class Meta:
        model = ApprovalDelegation
        fields = ["delegator", "delegate", "company", "valid_from", "valid_until", "while_away", "active"]
        widgets = {
            "valid_from": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "valid_until": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }


class ApprovalActionForm(forms.Form):
    action = forms.ChoiceField(choices=(("approve", "Approve"), ("reject", "Reject")), widget=forms.HiddenInput())
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
