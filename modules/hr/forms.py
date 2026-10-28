from pathlib import Path
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from decimal import Decimal, ROUND_HALF_UP

from django import forms
from django.forms.models import BaseInlineFormSet
from django.urls import reverse

from core.forms import SearchableMasterWidget, StyledModelForm

from .change_requests import clean_requested_value, serialize_employee_value
from .employee_change_fields import CHANGE_REQUEST_FIELDS
from .models import (
    AttendanceChangeRequest,
    CareerTransitionRequest,
    CashAdvance,
    CashAdvanceLine,
    ContractAllowance,
    ContractAttachment,
    ContractOvertimeComponent,
    EmployeeChangeRequest,
    EmployeeChangeRequestLine,
    EmployeeDocument,
    ExpenseProduct,
    EmploymentContract,
    LeaveAllocation,
    LeaveEncashment,
    LeaveRequest,
    RawAttendance,
    TrainingConduct,
    TrainingConductParticipant,
    TrainingConductSchedule,
)


class CashAdvanceForm(StyledModelForm):
    class Meta:
        model = CashAdvance
        fields = ["employee", "number", "request_date", "purpose", "settlement_due_date"]


class CashAdvanceLineForm(StyledModelForm):
    product = forms.ModelChoiceField(
        queryset=ExpenseProduct.objects.filter(active=True, activity_type=ExpenseProduct.ActivityType.ADVANCE),
        widget=SearchableMasterWidget(),
    )
    subtotal_display = forms.DecimalField(label="Subtotal", required=False, disabled=True, decimal_places=2)

    class Meta:
        model = CashAdvanceLine
        fields = ["sequence", "product", "quantity", "request_amount"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].widget.attrs.update({
            "data-master-search-url": reverse("expense_product_search"),
            "data-master-search-placeholder": "Search advance product...",
        })
        if self.instance and self.instance.product_id:
            product = self.instance.product
            self.fields["product"].widget.attrs["data-initial-label"] = f"{product.code} — {product.name}"
            self.fields["subtotal_display"].initial = self.instance.subtotal


class CashAdvanceLineFormSet(BaseInlineFormSet):
    total_amount = Decimal("0.00")

    def clean(self):
        super().clean()
        posted_ids = [
            value for form in self.forms
            if (value := form.data.get(form.add_prefix("id")))
        ]
        if len(posted_ids) != len(set(posted_ids)) or any(not value.isdecimal() for value in posted_ids):
            raise forms.ValidationError("Invalid advance line references.")
        allowed_ids = set(CashAdvanceLine.objects.filter(
            advance_id=self.instance.pk, pk__in=posted_ids
        ).values_list("pk", flat=True)) if self.instance.pk else set()
        if {int(value) for value in posted_ids} != allowed_ids:
            raise forms.ValidationError("Advance line does not belong to this document.")
        if any(self.errors):
            return
        active = [data for data in self.cleaned_data if data and not data.get("DELETE")]
        if not active:
            raise forms.ValidationError("Add at least one advance request line.")
        product_ids = [data["product"].pk for data in active]
        if len(product_ids) != len(set(product_ids)):
            raise forms.ValidationError("Use each advance product once; increase its quantity instead.")
        self.total_amount = sum(
            (
                (data["quantity"] * data["request_amount"]).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                for data in active
            ),
            Decimal("0.00"),
        )


class EmployeeDocumentForm(StyledModelForm):
    attachment = forms.FileField(
        required=False,
        help_text="PDF, PNG, JPEG, Word, or DOCX file; maximum 10 MB. An existing file remains unless replaced.",
    )

    class Meta:
        model = EmployeeDocument
        fields = [
            "employee", "document_type", "document_number", "issued_date",
            "expiry_date", "attachment_url", "notes",
        ]

    def clean_attachment(self):
        attachment = self.cleaned_data.get("attachment")
        if not attachment:
            return attachment
        if attachment.size == 0 or attachment.size > 10 * 1024 * 1024:
            raise forms.ValidationError("Attachment must be between 1 byte and 10 MB.")
        suffix = Path(attachment.name).suffix.lower()
        if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx"}:
            raise forms.ValidationError("Attachment type is not allowed.")
        header = attachment.read(8)
        attachment.seek(0)
        valid = {
            ".pdf": header.startswith(b"%PDF-"),
            ".png": header == b"\x89PNG\r\n\x1a\n",
            ".jpg": header.startswith(b"\xff\xd8\xff"),
            ".jpeg": header.startswith(b"\xff\xd8\xff"),
            ".doc": header == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
        }
        if suffix == ".docx":
            try:
                with ZipFile(BytesIO(attachment.read())) as archive:
                    names = set(archive.namelist())
                valid[".docx"] = "[Content_Types].xml" in names and "word/document.xml" in names
            except BadZipFile:
                valid[".docx"] = False
            finally:
                attachment.seek(0)
        if not valid.get(suffix, False):
            raise forms.ValidationError("Attachment content does not match its file type.")
        return attachment

    def save(self, commit=True):
        instance = super().save(commit=False)
        attachment = self.cleaned_data.get("attachment")
        if attachment:
            instance.attachment_name = Path(attachment.name).name[:255]
            instance.attachment_content_type = {
                ".pdf": "application/pdf",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".doc": "application/msword",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }[Path(attachment.name).suffix.lower()]
            attachment.seek(0)
            instance.attachment_content = attachment.read()
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class LeaveRequestForm(StyledModelForm):
    attachment = forms.FileField(
        required=False,
        help_text="PDF, image, Word, or Excel file; maximum 10 MB.",
    )

    class Meta:
        model = LeaveRequest
        fields = ["employee", "leave_type_record", "start_date", "end_date", "portion", "reason"]

    def clean_leave_type_record(self):
        leave_type = self.cleaned_data["leave_type_record"]
        if not leave_type:
            raise forms.ValidationError("Select a leave type.")
        if not leave_type.active:
            raise forms.ValidationError("Select an active leave type.")
        return leave_type

    def clean_attachment(self):
        attachment = self.cleaned_data.get("attachment")
        if not attachment:
            return attachment
        if Path(attachment.name).suffix.lower() not in {
            ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".doc", ".docx", ".xls", ".xlsx"
        }:
            raise forms.ValidationError("Attachment type is not allowed.")
        leave_type = self.cleaned_data.get("leave_type_record")
        maximum_mb = leave_type.max_file_size_mb if leave_type else 5
        if attachment.size == 0 or attachment.size > maximum_mb * 1024 * 1024:
            raise forms.ValidationError(f"Attachment must be between 1 byte and {maximum_mb} MB.")
        return attachment

    def clean(self):
        cleaned = super().clean()
        leave_type = cleaned.get("leave_type_record")
        if leave_type and leave_type.requires_attachment and leave_type.attachment_trigger == "always" and not (
            cleaned.get("attachment") or self.instance.attachment_content
        ):
            self.add_error("attachment", "Evidence is required for this leave type.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.leave_type = self.cleaned_data["leave_type_record"].name
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


class LeaveAllocationForm(StyledModelForm):
    class Meta:
        model = LeaveAllocation
        fields = ["employee", "leave_type", "year", "allocated_days", "valid_from", "expires_on"]


class LeaveEncashmentForm(StyledModelForm):
    class Meta:
        model = LeaveEncashment
        fields = ["employee", "leave_type", "request_date", "days", "reason"]

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("Enter a reason for the encashment request.")
        return reason


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
            "daily_wage",
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
        contract = cleaned.get("contract") or (
            self.instance.contract if self.instance and self.instance.contract_id else None
        )
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


class ContractAllowanceWorkspaceForm(ContractAllowanceForm):
    """Allowance entry scoped by the Contract workspace instead of a free FK."""

    class Meta(ContractAllowanceForm.Meta):
        fields = ["component", "amount", "effective_date", "end_date", "active"]


class ContractOvertimeComponentForm(StyledModelForm):
    class Meta:
        model = ContractOvertimeComponent
        fields = ["component", "rate_multiplier", "effective_date", "end_date", "active", "notes"]


class ContractAttachmentForm(StyledModelForm):
    attachment = forms.FileField(
        required=False,
        help_text="PDF, image, Word, or Excel file; maximum 10 MB. Alternatively, add an external document URL.",
    )

    class Meta:
        model = ContractAttachment
        fields = ["title", "category", "file_url", "issued_date", "expiry_date", "verified", "notes"]

    def clean(self):
        cleaned = super().clean()
        attachment = cleaned.get("attachment")
        file_url = (cleaned.get("file_url") or "").strip()
        if attachment and file_url:
            self.add_error("file_url", "Use either an upload or an external URL, not both.")
        if not attachment and not file_url:
            self.add_error("attachment", "Upload a file or provide an external document URL.")
        if attachment:
            allowed_extensions = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".doc", ".docx", ".xls", ".xlsx"}
            extension = Path(attachment.name).suffix.lower()
            if extension not in allowed_extensions:
                self.add_error("attachment", "Attachment type is not allowed.")
            if attachment.size == 0:
                self.add_error("attachment", "Attachment cannot be empty.")
            if attachment.size > 10 * 1024 * 1024:
                self.add_error("attachment", "Attachment cannot exceed 10 MB.")
            if not self.errors.get("attachment"):
                self.instance.attachment_name = Path(attachment.name).name[:255]
                self.instance.attachment_content_type = (attachment.content_type or "application/octet-stream")[:120]
                attachment.seek(0)
                self.instance.attachment_content = attachment.read()
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

    field_name = forms.ChoiceField(
        choices=[("", "Relationship details only"), *CHANGE_REQUEST_FIELDS.items()],
        required=False,
    )

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
        if self.instance.pk and employee and employee.pk != self.instance.employee_id:
            self.add_error("employee", "The employee cannot be changed after the request is created.")
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
        elif not field_name:
            cleaned["current_value"] = ""
            cleaned["requested_value"] = ""
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
