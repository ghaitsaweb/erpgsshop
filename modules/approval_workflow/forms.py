from django import forms

from core.forms import StyledModelForm

from .models import ApprovalDelegation, ApprovalRule, ApprovalWorkflow, ApprovalWorkflowPerimeter


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
            "trigger_conditions",
            "approval_levels",
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
