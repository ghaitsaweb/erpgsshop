from django import forms

from .models import AssistantConfiguration, HashyOSConnection


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
        ]
        widgets = {
            "system_prompt": forms.Textarea(attrs={"rows": 5}),
            "temperature": forms.NumberInput(attrs={"step": "0.05", "min": "0", "max": "1"}),
        }


class HashyOSConnectionForm(forms.ModelForm):
    class Meta:
        model = HashyOSConnection
        fields = ["account_email", "workspace_identifier", "state", "status_detail"]
