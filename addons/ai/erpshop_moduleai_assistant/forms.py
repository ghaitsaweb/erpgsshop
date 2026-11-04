from django import forms
from django.core.exceptions import ValidationError

from .models import AIKnowledgeDocument, AssistantConfiguration, HashyOSConnection


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

    class Meta:
        model = AIKnowledgeDocument
        fields = ["title", "category", "extracted_text", "file"]
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
