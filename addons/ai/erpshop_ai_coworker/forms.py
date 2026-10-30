import json

from django import forms
from django.core.exceptions import ValidationError

from addons.ai.erpshop_hashy_bridge import api as bridge_api
from core.views import ENTITY_CONFIG, _can

from .models import AIAgent, AICoworkerSchedule, AIModelTemplate, AITask


class CoworkerForm(forms.ModelForm):
    allow_erp_context = forms.BooleanField(
        required=False,
        initial=True,
        label="Allow governed ERP reads",
        help_text="Allows bounded read-only context through Hashy Bridge. No write or delete tool is available.",
    )

    class Meta:
        model = AIAgent
        fields = ["name", "slug", "role", "model_template", "model_name", "system_prompt", "max_context_records", "active"]
        widgets = {
            "model_name": forms.TextInput(
                attrs={"maxlength": 100, "placeholder": "Leave blank to use the company default model"}
            ),
            "system_prompt": forms.Textarea(attrs={"rows": 7, "maxlength": 12000}),
        }

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.workspace = workspace or (self.instance.workspace if self.instance and self.instance.pk else None)
        self.fields["model_template"].queryset = AIModelTemplate.objects.filter(active=True).select_related("provider").order_by("name")
        self.fields["model_template"].required = False
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

    def clean_model_name(self):
        return self.cleaned_data["model_name"].strip()

    def clean_slug(self):
        return self.cleaned_data["slug"].strip()

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


class CoworkerScheduleForm(CoworkerTaskForm):
    WEEKDAYS = (
        ("0", "Monday"),
        ("1", "Tuesday"),
        ("2", "Wednesday"),
        ("3", "Thursday"),
        ("4", "Friday"),
        ("5", "Saturday"),
        ("6", "Sunday"),
    )

    name = forms.CharField(max_length=180, label="Schedule name")
    frequency = forms.ChoiceField(choices=AICoworkerSchedule.Frequency.choices)
    weekday = forms.ChoiceField(required=False, choices=(("", "Choose a weekday"), *WEEKDAYS))
    run_at = forms.TimeField(
        label="Run at (Asia/Jakarta)",
        widget=forms.TimeInput(attrs={"type": "time"}),
        help_text="The scheduler checks due jobs every minute.",
    )
    active = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, company_id, branch_id, user, schedule=None, **kwargs):
        self.schedule = schedule
        self.branch_id = branch_id
        super().__init__(*args, company_id=company_id, user=user, **kwargs)
        if schedule and not self.is_bound:
            self.initial.update(
                {
                    "name": schedule.name,
                    "agent": schedule.agent_id,
                    "task_type": schedule.task_type,
                    "instruction": schedule.instruction,
                    "entity": schedule.entity,
                    "field_names": ",".join(schedule.selected_fields or []),
                    "filters_json": json.dumps(schedule.filters or []),
                    "frequency": schedule.frequency,
                    "weekday": str(schedule.weekday) if schedule.weekday is not None else "",
                    "run_at": schedule.run_at,
                    "active": schedule.active,
                }
            )

    def clean_name(self):
        value = self.cleaned_data["name"].strip()
        schedules = AICoworkerSchedule.objects.filter(company_id=self.company_id, name=value)
        if self.schedule:
            schedules = schedules.exclude(pk=self.schedule.pk)
        if schedules.exists():
            raise ValidationError("A schedule with this name already exists in the active company.")
        return value

    def clean(self):
        cleaned = super().clean()
        task_type = cleaned.get("task_type")
        entity = cleaned.get("entity") or ""
        frequency = cleaned.get("frequency")
        weekday = cleaned.get("weekday")
        if task_type != AITask.TaskType.PLAN and not self.branch_id:
            self.add_error("entity", "Select an active branch before scheduling ERP record context.")
        if frequency == AICoworkerSchedule.Frequency.WEEKLY:
            if not weekday:
                self.add_error("weekday", "Choose a weekday for a weekly schedule.")
            else:
                cleaned["weekday"] = int(weekday)
        else:
            cleaned["weekday"] = None
        if entity and not cleaned.get("selected_fields"):
            # The worker will use the same bounded default fields as an ad-hoc task.
            cleaned["selected_fields"] = []
        return cleaned
