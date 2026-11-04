import json

from django import forms
from django.core.exceptions import ValidationError

from addons.ai.erpshop_hashy_bridge import api as bridge_api
from core.views import ENTITY_CONFIG, _can

from .models import (
    AIAgent,
    AIChannelConfiguration,
    AICoworkerChannel,
    AICoworkerCron,
    AICoworkerHeartbeat,
    AICoworkerSchedule,
    AICompanyCredit,
    AICreditLedger,
    AIModelPricing,
    AIModelTemplate,
    AIProvider,
    AIWebhookEndpoint,
    AITask,
)


class CoworkerForm(forms.ModelForm):
    allow_erp_context = forms.BooleanField(
        required=False,
        initial=True,
        label="Allow governed ERP reads",
        help_text="Allows bounded read-only context through Hashy Bridge. Reads run automatically only within the active company and branch.",
    )
    allowed_tools = forms.MultipleChoiceField(
        required=False,
        choices=(
            ("web_search", "Request web search (approval required)"),
            ("fetch", "Request URL retrieval (approval required)"),
            ("filesystem", "Request workspace file operation (approval required)"),
            ("mcp", "Request MCP operation (approval required)"),
            ("sandbox", "Request sandboxed computation (approval required)"),
            ("erp_write", "Request ERP write (approval required)"),
            ("message", "Request outbound message (approval required)"),
        ),
        widget=forms.CheckboxSelectMultiple,
        label="Additional governed tools",
        help_text="A tool request is recorded for human approval. It is never executed automatically.",
    )
    max_inbound_queue_size = forms.IntegerField(
        min_value=0,
        required=False,
        label="Maximum inbound channel queue",
        help_text="Maximum queued or running channel tasks. Set 0 to pause new inbound channel work.",
    )
    max_concurrent_dispatches = forms.IntegerField(
        min_value=1,
        required=False,
        label="Maximum concurrent dispatches",
        help_text="Maximum simultaneous provider turns for this coworker; excess work is retried safely.",
    )

    class Meta:
        model = AIAgent
        fields = ["name", "slug", "role", "model_template", "model_name", "system_prompt", "max_context_records", "max_inbound_queue_size", "max_concurrent_dispatches", "active"]
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
            self.fields["allowed_tools"].initial = [
                name for name in (self.instance.allowed_tools or []) if name != "erp_read"
            ]
        self.fields["max_inbound_queue_size"].initial = self.instance.max_inbound_queue_size if self.instance and self.instance.pk else 500
        self.fields["max_concurrent_dispatches"].initial = self.instance.max_concurrent_dispatches if self.instance and self.instance.pk else 10

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

    def clean_max_inbound_queue_size(self):
        value = self.cleaned_data.get("max_inbound_queue_size")
        if value is not None:
            return value
        return self.instance.max_inbound_queue_size if self.instance and self.instance.pk else 500

    def clean_max_concurrent_dispatches(self):
        value = self.cleaned_data.get("max_concurrent_dispatches")
        if value is not None:
            return value
        return self.instance.max_concurrent_dispatches if self.instance and self.instance.pk else 10

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.max_inbound_queue_size = self.cleaned_data["max_inbound_queue_size"]
        instance.max_concurrent_dispatches = self.cleaned_data["max_concurrent_dispatches"]
        tools = set(self.cleaned_data.get("allowed_tools") or [])
        if self.cleaned_data.get("allow_erp_context"):
            tools.add("erp_read")
        instance.allowed_tools = sorted(tools)
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


class _CompanyAgentForm(forms.ModelForm):
    """Shared company scoping for administrator-only runtime forms."""

    def __init__(self, *args, company_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.company_id = company_id
        self.fields["agent"].queryset = (
            AIAgent.objects.filter(workspace__company_id=company_id, workspace__active=True, active=True)
            .select_related("workspace")
            .order_by("name", "pk")
        )

    def clean_agent(self):
        agent = self.cleaned_data["agent"]
        if agent.workspace.company_id != self.company_id:
            raise ValidationError("Coworker is outside the active company.")
        return agent


class CoworkerMemoryForm(forms.Form):
    agent = forms.ModelChoiceField(queryset=AIAgent.objects.none(), label="Coworker")
    content = forms.CharField(
        max_length=20000,
        widget=forms.Textarea(attrs={"rows": 8}),
        help_text="Administrator-maintained durable context. Do not store credentials or untrusted instructions.",
    )

    def __init__(self, *args, company_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.company_id = company_id
        self.fields["agent"].queryset = (
            AIAgent.objects.filter(workspace__company_id=company_id, workspace__active=True)
            .select_related("workspace")
            .order_by("name", "pk")
        )

    def clean_agent(self):
        agent = self.cleaned_data["agent"]
        if agent.workspace.company_id != self.company_id:
            raise ValidationError("Coworker is outside the active company.")
        return agent

    def clean_content(self):
        value = self.cleaned_data["content"].strip()
        if not value:
            raise ValidationError("Memory content is required.")
        return value


class CoworkerSubagentForm(forms.Form):
    agent = forms.ModelChoiceField(queryset=AIAgent.objects.none(), label="Coworker")
    description = forms.CharField(max_length=255)
    prompt = forms.CharField(max_length=12000, widget=forms.Textarea(attrs={"rows": 6}))

    def __init__(self, *args, company_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.company_id = company_id
        self.fields["agent"].queryset = (
            AIAgent.objects.filter(workspace__company_id=company_id, workspace__active=True, active=True)
            .select_related("workspace", "owner_user", "run_as_user")
            .order_by("name", "pk")
        )

    def clean_agent(self):
        agent = self.cleaned_data["agent"]
        if agent.workspace.company_id != self.company_id:
            raise ValidationError("Coworker is outside the active company.")
        if not (agent.run_as_user_id or agent.owner_user_id):
            raise ValidationError("Set an active run-as user or owner before starting a subagent.")
        return agent


class CoworkerCronForm(_CompanyAgentForm):
    class Meta:
        model = AICoworkerCron
        fields = [
            "name", "agent", "schedule_type", "at_datetime", "interval_seconds", "crontab_expr",
            "timezone_name", "prompt", "active",
        ]
        widgets = {
            "at_datetime": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "prompt": forms.Textarea(attrs={"rows": 5, "maxlength": 12000}),
            "crontab_expr": forms.TextInput(attrs={"maxlength": 80, "placeholder": "0 9 * * 1-5"}),
        }

    def clean_prompt(self):
        return self.cleaned_data["prompt"].strip()


class CoworkerHeartbeatForm(_CompanyAgentForm):
    class Meta:
        model = AICoworkerHeartbeat
        fields = ["name", "agent", "instruction", "interval_seconds", "notify_policy", "enabled"]
        widgets = {"instruction": forms.Textarea(attrs={"rows": 5, "maxlength": 12000})}

    def clean_instruction(self):
        return self.cleaned_data["instruction"].strip()


class ChannelConfigurationForm(forms.ModelForm):
    agent = forms.ModelChoiceField(queryset=AIAgent.objects.none(), label="Coworker")
    config_json = forms.CharField(
        required=False,
        initial="{}",
        label="Channel configuration (JSON)",
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="Telegram: no secrets here. WhatsApp Cloud: phone_number_id, verify_token_env_var. WhatsApp Web: bridge_url (HTTPS).",
    )

    class Meta:
        model = AIChannelConfiguration
        fields = ["name", "channel_type", "credential_env_var", "app_secret_env_var", "enabled"]

    def __init__(self, *args, company_id, **kwargs):
        super().__init__(*args, **kwargs)
        self.company_id = company_id
        self.fields["agent"].queryset = (
            AIAgent.objects.filter(workspace__company_id=company_id, workspace__active=True)
            .select_related("workspace", "owner_user")
            .order_by("name", "pk")
        )
        if self.instance and self.instance.pk:
            self.fields["agent"].initial = getattr(self.instance.coworker_link, "agent", None)
            self.fields["config_json"].initial = json.dumps(self.instance.config or {}, ensure_ascii=False, indent=2)

    def clean_agent(self):
        agent = self.cleaned_data["agent"]
        if agent.workspace.company_id != self.company_id:
            raise ValidationError("Coworker is outside the active company.")
        return agent

    def clean_config_json(self):
        raw = self.cleaned_data["config_json"] or "{}"
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValidationError("Channel configuration must be valid JSON.") from exc
        if not isinstance(value, dict) or len(value) > 30:
            raise ValidationError("Channel configuration must be a JSON object with at most 30 keys.")
        if len(json.dumps(value, ensure_ascii=False)) > 12000:
            raise ValidationError("Channel configuration is too large.")
        return value

    def clean(self):
        cleaned = super().clean()
        channel_type = cleaned.get("channel_type")
        config = cleaned.get("config_json") or {}
        if "secret_token" in config or "verify_token" in config:
            self.add_error("config_json", "Store callback secrets in an environment variable and use the matching *_env_var key.")
        for key, value in config.items():
            if key.endswith("_env_var") and (not isinstance(value, str) or not value or not value.replace("_", "").isalnum()):
                self.add_error("config_json", f"{key} must contain an environment variable name.")
        if channel_type == AIChannelConfiguration.ChannelType.WHATSAPP_WEB:
            bridge_url = str(config.get("bridge_url", "")).strip()
            if cleaned.get("enabled") and not bridge_url:
                self.add_error("config_json", "An HTTPS bridge_url is required before enabling WhatsApp Web.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.config = self.cleaned_data["config_json"]
        if commit:
            instance.full_clean()
            instance.save()
        return instance


class WebhookEndpointForm(forms.ModelForm):
    class Meta:
        model = AIWebhookEndpoint
        fields = [
            "name", "hmac_secret_env_var", "hmac_header", "allowed_ips", "rate_limit_per_minute",
            "max_body_bytes", "active", "expires_at",
        ]
        widgets = {"expires_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}

    def __init__(self, *args, company_id, agent, **kwargs):
        super().__init__(*args, **kwargs)
        self.company_id = company_id
        self.agent = agent

    def clean(self):
        cleaned = super().clean()
        if self.agent.workspace.company_id != self.company_id:
            raise ValidationError("Coworker is outside the active company.")
        return cleaned


class ProviderRegistryForm(forms.ModelForm):
    class Meta:
        model = AIProvider
        fields = ["name", "display_name", "backend", "api_base_url", "credential_env_var", "active"]
        widgets = {
            "api_base_url": forms.URLInput(attrs={"placeholder": "https://provider.example/v1"}),
            "credential_env_var": forms.TextInput(attrs={"placeholder": "AI_PROVIDER_API_KEY"}),
        }
        help_texts = {
            "credential_env_var": "Environment-variable name only; the key itself is never stored here.",
            "api_base_url": "HTTPS endpoint only. Leave blank only when the company fallback is intentionally used.",
        }


class ModelTemplateRegistryForm(forms.ModelForm):
    class Meta:
        model = AIModelTemplate
        fields = [
            "name", "description", "provider", "model_name", "max_output_tokens", "context_window_tokens",
            "temperature", "reasoning_effort", "max_tool_iterations", "max_tool_result_chars",
            "max_concurrent_interactive", "max_concurrent_background", "concurrent_queue_timeout_seconds",
            "workspace_retention_days", "subagent_max_depth", "subagent_max_children_per_parent",
            "subagent_timeout_seconds", "active",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3, "maxlength": 2000}),
            "temperature": forms.NumberInput(attrs={"step": "0.05", "min": "0", "max": "2"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["provider"].queryset = AIProvider.objects.order_by("name", "pk")


class ModelPricingRegistryForm(forms.ModelForm):
    class Meta:
        model = AIModelPricing
        fields = [
            "template", "min_context_tokens", "max_context_tokens", "input_price_per_mtok", "output_price_per_mtok",
            "cache_read_price_per_mtok", "cache_write_5m_price_per_mtok", "cache_write_1h_price_per_mtok",
            "currency", "effective_from", "effective_to", "source", "active",
        ]
        widgets = {
            "effective_from": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "effective_to": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "source": forms.TextInput(attrs={"maxlength": 255, "placeholder": "Provider pricing reference"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template"].queryset = AIModelTemplate.objects.select_related("provider").order_by("name", "pk")


class CompanyCreditPolicyForm(forms.ModelForm):
    class Meta:
        model = AICompanyCredit
        fields = ["credit_limit", "enforce_credits", "currency", "low_balance_threshold"]
        widgets = {
            "credit_limit": forms.NumberInput(attrs={"step": "0.000001"}),
            "low_balance_threshold": forms.NumberInput(attrs={"step": "0.000001"}),
        }
        help_texts = {
            "credit_limit": "New provider turns stop once the balance is at or below this safety limit when enforcement is enabled.",
            "low_balance_threshold": "Observed threshold for operations review; it does not send notifications by itself.",
        }

    def clean_currency(self):
        value = self.cleaned_data["currency"].strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValidationError("Use a three-letter ISO-style currency code.")
        return value


class CreditAdjustmentForm(forms.Form):
    entry_type = forms.ChoiceField(
        choices=(
            (AICreditLedger.EntryType.GRANT, "Grant credit"),
            (AICreditLedger.EntryType.REFUND, "Refund credit"),
            (AICreditLedger.EntryType.ADJUSTMENT, "Manual adjustment"),
        )
    )
    amount = forms.DecimalField(max_digits=16, decimal_places=8, help_text="Use a positive amount for grants/refunds. Adjustments may be signed.")
    description = forms.CharField(max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get("amount")
        entry_type = cleaned.get("entry_type")
        if amount is None:
            return cleaned
        if amount == 0:
            self.add_error("amount", "Amount must not be zero.")
        elif entry_type in {AICreditLedger.EntryType.GRANT, AICreditLedger.EntryType.REFUND} and amount < 0:
            self.add_error("amount", "Grant and refund amounts must be positive.")
        return cleaned

    def clean_description(self):
        return self.cleaned_data["description"].strip()
