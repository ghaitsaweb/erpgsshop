import json

from django import forms
from django.core.exceptions import ValidationError

from addons.ai.erpshop_hashy_bridge import api as bridge_api
from core.views import ENTITY_CONFIG, _can

from .models import AIAgent, AITask


class CoworkerForm(forms.ModelForm):
    allow_erp_context = forms.BooleanField(
        required=False,
        initial=True,
        label="Allow governed ERP reads",
        help_text="Allows bounded read-only context through Hashy Bridge. No write or delete tool is available.",
    )

    class Meta:
        model = AIAgent
        fields = ["name", "role", "system_prompt", "max_context_records", "active"]
        widgets = {
            "system_prompt": forms.Textarea(attrs={"rows": 7, "maxlength": 12000}),
        }

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.workspace = workspace or (self.instance.workspace if self.instance and self.instance.pk else None)
        if self.instance and self.instance.pk:
            self.fields["allow_erp_context"].initial = "erp_read" in (self.instance.allowed_tools or [])

    def clean_name(self):
        value = self.cleaned_data["name"].strip()
        if self.workspace:
            duplicate = AIAgent.objects.filter(workspace=self.workspace, name=value)
            if self.instance and self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                raise ValidationError("A coworker with this name already exists in the company workspace.")
        return value

    def clean_system_prompt(self):
        value = self.cleaned_data["system_prompt"].strip()
        if len(value) > 12000:
            raise ValidationError("System prompt must be at most 12,000 characters.")
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.allowed_tools = ["erp_read"] if self.cleaned_data.get("allow_erp_context") else []
        if commit:
            instance.full_clean()
            instance.save()
        return instance


class CoworkerTaskForm(forms.Form):
    agent = forms.ModelChoiceField(queryset=AIAgent.objects.none(), label="Coworker")
    task_type = forms.ChoiceField(choices=AITask.TaskType.choices, label="Task")
    title = forms.CharField(max_length=180)
    instruction = forms.CharField(
        max_length=12000,
        widget=forms.Textarea(attrs={"rows": 6}),
        help_text="Describe the desired analysis. AI Coworker cannot change ERP records.",
    )
    entity = forms.ChoiceField(required=False, choices=(), label="ERP entity")
    field_names = forms.CharField(
        required=False,
        max_length=500,
        label="Fields",
        help_text="Optional comma-separated field names; at most 12.",
    )
    filters_json = forms.CharField(
        required=False,
        initial="[]",
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Filters (JSON)",
        help_text='Optional list, e.g. [{"field":"status","op":"eq","value":"draft"}].',
    )

    def __init__(self, *args, company_id, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.company_id = company_id
        self.user = user
        self.fields["agent"].queryset = (
            AIAgent.objects.filter(workspace__company_id=company_id, workspace__active=True, active=True)
            .select_related("workspace")
            .order_by("name", "pk")
        )
        entity_choices = [("", "No ERP context")]
        for slug, config in sorted(ENTITY_CONFIG.items(), key=lambda item: item[1]["title"]):
            if _can(user, "view", config["model"], slug) and bridge_api.exposed_fields(config):
                entity_choices.append((slug, config["title"]))
        self.fields["entity"].choices = entity_choices

    def clean_instruction(self):
        value = self.cleaned_data["instruction"].strip()
        if not value:
            raise ValidationError("Instruction is required.")
        return value

    def clean(self):
        cleaned = super().clean()
        agent = cleaned.get("agent")
        task_type = cleaned.get("task_type")
        entity = cleaned.get("entity") or ""
        if agent and agent.workspace.company_id != self.company_id:
            self.add_error("agent", "Coworker is outside the active company.")
        if task_type == AITask.TaskType.PLAN:
            if entity:
                self.add_error("entity", "Planning tasks do not accept ERP record context.")
            entity = ""
        elif not entity:
            self.add_error("entity", "Choose an ERP entity for this task.")
        elif agent and "erp_read" not in (agent.allowed_tools or []):
            self.add_error("agent", "This coworker is not allowed to read ERP context.")

        field_names = [item.strip() for item in (cleaned.get("field_names") or "").split(",") if item.strip()]
        if len(field_names) > 12 or len(field_names) != len(set(field_names)):
            self.add_error("field_names", "Choose at most 12 unique field names.")

        raw_filters = cleaned.get("filters_json") or "[]"
        try:
            filters = json.loads(raw_filters)
        except (TypeError, json.JSONDecodeError):
            self.add_error("filters_json", "Filters must be valid JSON.")
            filters = []
        if not isinstance(filters, list) or len(filters) > bridge_api.MAX_FILTERS:
            self.add_error("filters_json", f"Filters must be a list of at most {bridge_api.MAX_FILTERS} items.")
            filters = []

        if entity in ENTITY_CONFIG:
            config = ENTITY_CONFIG[entity]
            available = bridge_api.exposed_fields(config)
            if field_names and any(name not in available for name in field_names):
                self.add_error("field_names", "One or more fields are not exposed for this entity.")
            try:
                bridge_api._apply_filters(config["model"].objects.none(), config, filters)
            except Exception as exc:
                self.add_error("filters_json", str(exc))

        cleaned["entity"] = entity
        cleaned["selected_fields"] = field_names
        cleaned["filters"] = filters
        return cleaned
