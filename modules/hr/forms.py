from pathlib import Path

from django import forms

from core.forms import StyledModelForm

from .change_requests import clean_requested_value, serialize_employee_value
from .employee_change_fields import CHANGE_REQUEST_FIELDS
from .models import (
    AttendanceChangeRequest,
    CareerTransitionRequest,
    ContractAllowance,
    EmployeeChangeRequest,
    EmployeeChangeRequestLine,
    EmploymentContract,
    RawAttendance,
    TrainingConduct,
    TrainingConductParticipant,
    TrainingConductSchedule,
)


class RawAttendanceForm(StyledModelForm):
    class Meta:
        model = RawAttendance
        fields = ["employee", "captured_at", "event_type", "source", "device_identifier", "note"]


class AttendanceChangeRequestForm(StyledModelForm):
    class Meta:
        model = AttendanceChangeRequest
        fields = ["employee", "attendance_date", "requested_check_in", "requested_check_out", "reason"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("requested_check_in") is None and cleaned.get("requested_check_out") is None:
            self.add_error("requested_check_in", "Enter a corrected check-in or check-out time.")
        return cleaned


class EmploymentContractForm(StyledModelForm):
    class Meta:
        model = EmploymentContract
        fields = [
            "employee",
            "number",
            "contract_type",
            "start_date",
            "end_date",
            "salary_package",
            "base_salary",
            "letter_template",
            "notes",
        ]

    def clean(self):
        cleaned = super().clean()
        salary_package = cleaned.get("salary_package")
        if salary_package and cleaned.get("base_salary") == 0:
            cleaned["base_salary"] = salary_package.base_salary
            self.instance.base_salary = salary_package.base_salary
        return cleaned


class ContractAllowanceForm(StyledModelForm):
    change_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Required when adding a new effective allowance version to an active contract.",
    )

    class Meta:
        model = ContractAllowance
        fields = ["contract", "component", "amount", "effective_date", "end_date", "active"]

    def clean(self):
        cleaned = super().clean()
        contract = cleaned.get("contract")
        reason = (cleaned.get("change_reason") or "").strip()
        if contract and contract.status == EmploymentContract.Status.ACTIVE:
            if self.instance.pk:
                raise forms.ValidationError(
                    "Active-contract allowance versions are immutable. Add a new allowance version instead."
                )
            if not cleaned.get("active"):
                self.add_error("active", "A new active-contract allowance version must be active.")
            if len(reason) < 8:
                self.add_error("change_reason", "Enter a concrete change reason of at least 8 characters.")
        cleaned["change_reason"] = reason
        return cleaned


class CareerTransitionRequestForm(StyledModelForm):
    class Meta:
        model = CareerTransitionRequest
        fields = [
            "employee",
            "number",
            "transition_type",
            "effective_date",
            "target_branch",
            "target_department",
            "target_job_position",
            "target_supervisor",
            "reason",
        ]

    def clean(self):
        cleaned = super().clean()
        employee = cleaned.get("employee")
        if employee:
            self.instance.current_position = employee.position
            target_job = cleaned.get("target_job_position")
            self.instance.new_position = target_job.name if target_job else employee.position
        return cleaned


class EmployeeChangeRequestForm(StyledModelForm):
    attachment = forms.FileField(
        required=False,
        help_text="PDF, image, Word, or Excel file; maximum 10 MB.",
    )

    field_name = forms.ChoiceField(choices=list(CHANGE_REQUEST_FIELDS.items()))

    class Meta:
        model = EmployeeChangeRequest
        fields = ["employee", "field_name", "current_value", "requested_value", "effective_date", "reason"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["current_value"].disabled = True
        self.fields["current_value"].help_text = "Captured from the selected employee when this draft is saved."
        self.fields["requested_value"].required = False
        if self.instance and self.instance.attachment_name:
            self.fields["attachment"].help_text = (
                f"Current evidence: {self.instance.attachment_name}. Upload a file to replace it."
            )

    def clean(self):
        cleaned = super().clean()
        employee = cleaned.get("employee")
        field_name = cleaned.get("field_name")
        if employee is not None and field_name in CHANGE_REQUEST_FIELDS:
            current_value = serialize_employee_value(getattr(employee, field_name))
            cleaned["current_value"] = current_value
            if "requested_value" in cleaned:
                try:
                    requested = clean_requested_value(employee, field_name, cleaned["requested_value"])
                except forms.ValidationError as exc:
                    self.add_error("requested_value", exc)
                else:
                    if serialize_employee_value(requested) == current_value:
                        self.add_error("requested_value", "Enter a value different from the employee profile.")
        attachment = cleaned.get("attachment")
        if attachment:
            allowed_extensions = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".doc", ".docx", ".xls", ".xlsx"}
            extension = Path(attachment.name).suffix.lower()
            if extension not in allowed_extensions:
                self.add_error("attachment", "Attachment type is not allowed.")
            if attachment.size == 0:
                self.add_error("attachment", "Attachment cannot be empty.")
            if attachment.size > 10 * 1024 * 1024:
                self.add_error("attachment", "Attachment cannot exceed 10 MB.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        attachment = self.cleaned_data.get("attachment")
        if attachment:
            instance.attachment_name = Path(attachment.name).name[:255]
            instance.attachment_content_type = (attachment.content_type or "application/octet-stream")[:120]
            attachment.seek(0)
            instance.attachment_content = attachment.read()
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class EmployeeChangeRequestLineForm(StyledModelForm):
    field_name = forms.ChoiceField(choices=list(CHANGE_REQUEST_FIELDS.items()))

    class Meta:
        model = EmployeeChangeRequestLine
        fields = ["request", "field_name", "current_value", "requested_value"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["current_value"].disabled = True
        self.fields["requested_value"].required = False
        self.fields["request"].queryset = self.fields["request"].queryset.filter(status="draft")

    def clean(self):
        cleaned = super().clean()
        request = cleaned.get("request")
        field_name = cleaned.get("field_name")
        if request and field_name in CHANGE_REQUEST_FIELDS:
            if field_name == request.field_name:
                self.add_error("field_name", "This field is already the primary request change.")
            current_value = serialize_employee_value(getattr(request.employee, field_name))
            cleaned["current_value"] = current_value
            if "requested_value" in cleaned:
                try:
                    requested = clean_requested_value(request.employee, field_name, cleaned["requested_value"])
                except forms.ValidationError as exc:
                    self.add_error("requested_value", exc)
                else:
                    if serialize_employee_value(requested) == current_value:
                        self.add_error("requested_value", "Enter a value different from the employee profile.")
        return cleaned


class TrainingConductForm(StyledModelForm):
    class Meta:
        model = TrainingConduct
        fields = [
            "company",
            "name",
            "training_type",
            "training_source",
            "course",
            "program",
            "pic",
            "location",
            "use_minimum_participant",
            "minimum_participants",
            "use_maximum_participant",
            "maximum_participants",
            "active",
        ]
        labels = {
            "name": "Conduct Name",
            "course": "Training Course",
            "program": "Training Program",
            "pic": "PIC",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["company"].required = True
        self.fields["name"].required = True
        self.fields["pic"].required = True
        self.fields["training_type"].widget = forms.RadioSelect(
            choices=TrainingConduct.TrainingType.choices,
            attrs={"class": "conduct-type-options"},
        )

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("training_source")
        if source == TrainingConduct.TrainingSource.COURSE:
            cleaned["program"] = None
            if not cleaned.get("course"):
                self.add_error("course", "Select a Training Course.")
        elif source == TrainingConduct.TrainingSource.PROGRAM:
            cleaned["course"] = None
            if not cleaned.get("program"):
                self.add_error("program", "Select a Training Program.")
        return cleaned


class TrainingConductScheduleForm(StyledModelForm):
    class Meta:
        model = TrainingConductSchedule
        fields = ["sequence", "course", "trainer", "notes", "start_date", "end_date", "start_time", "end_time"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
        }


class TrainingConductParticipantForm(StyledModelForm):
    class Meta:
        model = TrainingConductParticipant
        fields = ["employee", "status", "notes"]
