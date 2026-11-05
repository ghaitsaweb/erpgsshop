import re

from django import forms
from django.core.exceptions import ValidationError

from core.views import ENTITY_CONFIG
from modules.organization.models import Branch

from .models import (
    AIKnowledgeCategory,
    AIKnowledgeBusinessFlow,
    AIKnowledgeCompanyProfile,
    AIKnowledgeDocument,
    AIKnowledgeRecordModelMapping,
    AssistantConfiguration,
    HashyOSConnection,
)


class AssistantConfigurationForm(forms.ModelForm):
    class Meta:
        model = AssistantConfiguration
        fields = [
            "display_name",
            "provider",
            "api_base_url",
            "credential_env_var",
            "model_name",
            "system_prompt",
            "max_output_tokens",
            "temperature",
            "active",
            "allow_erp_context",
            "allow_file_content",
            "allow_knowledge_context",
        ]
        widgets = {
            "system_prompt": forms.Textarea(attrs={"rows": 5}),
            "temperature": forms.NumberInput(attrs={"step": "0.05", "min": "0", "max": "1"}),
        }


class HashyOSConnectionForm(forms.ModelForm):
    class Meta:
        model = HashyOSConnection
        fields = ["account_email", "workspace_identifier"]


class AIKnowledgeDocumentForm(forms.ModelForm):
    """Small local equivalent of the HMX base-knowledge upload flow."""

    def __init__(self, *args, company_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].widget.attrs["list"] = "knowledge-categories"
        self.fields["branch"].queryset = Branch.objects.filter(company_id=company_id, active=True).order_by("sequence", "name") if company_id else Branch.objects.none()
        self.fields["branch"].required = False

    class Meta:
        model = AIKnowledgeDocument
        fields = ["title", "branch", "category", "extracted_text", "file"]
        widgets = {
            "extracted_text": forms.Textarea(
                attrs={
                    "rows": 8,
                    "placeholder": "Paste approved company knowledge here, or attach a supported plain-text file.",
                }
            ),
        }

    def clean(self):
        cleaned_data = super().clean()
        extracted_text = (cleaned_data.get("extracted_text") or "").strip()
        uploaded_file = cleaned_data.get("file")
        if not extracted_text and not uploaded_file:
            raise ValidationError("Provide either approved text or a source file.")
        if len(extracted_text) > 200_000:
            self.add_error("extracted_text", "Knowledge text must be at most 200,000 characters.")
        if uploaded_file and uploaded_file.size > 10 * 1024 * 1024:
            self.add_error("file", "Knowledge files must be at most 10 MB.")
        return cleaned_data


class AIKnowledgeCompanyProfileForm(forms.ModelForm):
    class Meta:
        model = AIKnowledgeCompanyProfile
        fields = ["summary", "operating_context", "active"]
        widgets = {
            "summary": forms.Textarea(attrs={"rows": 4, "placeholder": "Describe the company, services, customers, and operating scope."}),
            "operating_context": forms.Textarea(attrs={"rows": 4, "placeholder": "Add stable policies or operating context that should guide answers."}),
        }


class AIKnowledgeCategoryForm(forms.ModelForm):
    class Meta:
        model = AIKnowledgeCategory
        fields = ["name", "description", "active"]


class AIKnowledgeBusinessFlowForm(forms.ModelForm):
    steps = forms.JSONField(required=False, widget=forms.HiddenInput)
    edges = forms.JSONField(required=False, widget=forms.HiddenInput)
    steps_text = forms.CharField(
        label="Ordered steps",
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "One reviewed operational step per line."}),
        help_text="Enter 1 to 20 steps. These remain reference material, not executable automation.",
    )
    edges_text = forms.CharField(
        required=False,
        label="Directed edges",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "1 -> 2 : next\n2 -> 3 : approved"}),
        help_text="Optional branch or transition lines: source step -> target step : label. Leave empty for a linear flow.",
    )

    class Meta:
        model = AIKnowledgeBusinessFlow
        fields = ["name", "trigger", "description", "steps", "edges", "status"]
        widgets = {
            "description": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Explain purpose, scope, handoffs, and any important constraints."}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            self.fields["steps_text"].initial = "\n".join(
                str(step.get("description", "")).strip()
                for step in (self.instance.steps or [])
                if isinstance(step, dict) and str(step.get("description", "")).strip()
            )
            self.fields["edges_text"].initial = "\n".join(
                f"{edge.get('from_sequence')} -> {edge.get('to_sequence')}"
                + (f" : {edge.get('label')}" if edge.get("label") else "")
                for edge in (self.instance.edges or [])
                if isinstance(edge, dict)
            )

    def clean(self):
        cleaned_data = super().clean()
        steps = [line.strip() for line in (cleaned_data.get("steps_text") or "").splitlines() if line.strip()]
        if not 1 <= len(steps) <= 20:
            self.add_error("steps_text", "Enter from 1 to 20 non-empty ordered steps.")
        elif any(len(step) > 500 for step in steps):
            self.add_error("steps_text", "Each step must be at most 500 characters.")
        cleaned_data["steps"] = [
            {"sequence": index, "description": description}
            for index, description in enumerate(steps, start=1)
        ]
        edges = []
        edge_pattern = re.compile(r"^(\d{1,2})\s*->\s*(\d{1,2})(?:\s*:\s*(.{1,160}))?$")
        for line_number, raw_line in enumerate((cleaned_data.get("edges_text") or "").splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            match = edge_pattern.fullmatch(line)
            if not match:
                self.add_error("edges_text", f"Edge line {line_number} must use '1 -> 2 : label'.")
                continue
            from_sequence, to_sequence = int(match.group(1)), int(match.group(2))
            if not 1 <= from_sequence <= len(steps) or not 1 <= to_sequence <= len(steps):
                self.add_error("edges_text", f"Edge line {line_number} references a missing step.")
                continue
            edges.append(
                {
                    "from_sequence": from_sequence,
                    "to_sequence": to_sequence,
                    "label": (match.group(3) or "next").strip(),
                }
            )
        if len(edges) > 40:
            self.add_error("edges_text", "Enter at most 40 directed edges.")
        if not edges and steps:
            edges = [
                {"from_sequence": index, "to_sequence": index + 1, "label": "next"}
                for index in range(1, len(steps))
            ]
        cleaned_data["edges"] = edges
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.steps = self.cleaned_data["steps"]
        instance.edges = self.cleaned_data["edges"]
        if commit:
            instance.full_clean()
            instance.save()
        return instance


class AIKnowledgeRecordModelMappingForm(forms.ModelForm):
    business_flow = forms.ChoiceField(required=False, choices=(), label="Business flow")
    feature_tags = forms.JSONField(required=False, widget=forms.HiddenInput)
    feature_tags_text = forms.CharField(
        required=False,
        label="Feature tags",
        help_text="Optional comma-separated labels, at most 20.",
        widget=forms.TextInput(attrs={"maxlength": 1300, "placeholder": "approval, reimbursement, finance"}),
    )

    class Meta:
        model = AIKnowledgeRecordModelMapping
        fields = ["record_model", "display_name", "branch", "category", "module_name", "feature_tags", "business_flow", "active"]

    def __init__(self, *args, company_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.company_id = company_id
        choices = [(slug, config["title"]) for slug, config in sorted(ENTITY_CONFIG.items(), key=lambda item: item[1]["title"])]
        self.fields["record_model"].widget = forms.Select(choices=choices)
        self.fields["record_model"].choices = choices
        self.fields["branch"].queryset = (
            Branch.objects.filter(company_id=company_id, active=True).order_by("sequence", "name")
            if company_id
            else Branch.objects.none()
        )
        self.fields["branch"].required = False
        visible_flows = list(
            AIKnowledgeBusinessFlow.objects.filter(company_id=company_id).order_by("name", "id").values_list("pk", "name")[:100]
        )
        self.fields["business_flow"].choices = [("", "No linked business flow"), *[(str(pk), name) for pk, name in visible_flows]]
        self.fields["category"].widget.attrs["list"] = "knowledge-categories"
        if self.instance and self.instance.pk and not self.is_bound:
            self.fields["feature_tags_text"].initial = ", ".join(self.instance.feature_tags or [])
            self.fields["business_flow"].initial = str(self.instance.business_flow_id or "")

    def clean_record_model(self):
        value = self.cleaned_data["record_model"]
        if value not in ENTITY_CONFIG:
            raise ValidationError("Choose an exposed ERP entity.")
        return value

    def clean_business_flow(self):
        raw_value = self.cleaned_data.get("business_flow")
        if not raw_value:
            return None
        try:
            flow_id = int(raw_value)
        except (TypeError, ValueError):
            raise ValidationError("Choose a company business flow.")
        flow = AIKnowledgeBusinessFlow.objects.filter(company_id=self.company_id, pk=flow_id).first()
        if flow is None:
            raise ValidationError("Choose a company business flow.")
        return flow

    def clean(self):
        cleaned_data = super().clean()
        tags = [tag.strip() for tag in (cleaned_data.get("feature_tags_text") or "").split(",") if tag.strip()]
        if len(tags) > 20 or len({tag.casefold() for tag in tags}) != len(tags) or any(len(tag) > 64 for tag in tags):
            self.add_error("feature_tags_text", "Use up to 20 unique tags of 64 characters or fewer.")
        cleaned_data["feature_tags"] = tags
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.feature_tags = self.cleaned_data["feature_tags"]
        if commit:
            instance.full_clean()
            instance.save()
        return instance
