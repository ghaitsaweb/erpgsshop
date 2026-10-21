from decimal import Decimal
import re
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import Company, Employee, TimeStampedModel


def current_year():
    return timezone.localdate().year


class ApprovalStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class Department(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="departments")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    manager = models.ForeignKey(Employee, on_delete=models.SET_NULL, blank=True, null=True, related_name="managed_departments")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_department_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class EmploymentContract(TimeStampedModel):
    class ContractType(models.TextChoices):
        PERMANENT = "permanent", "Permanent"
        FIXED_TERM = "fixed_term", "Fixed Term"
        CONTRACTOR = "contractor", "Contractor"
        INTERN = "intern", "Intern"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        TERMINATED = "terminated", "Terminated"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="contracts")
    salary_package = models.ForeignKey(
        "SalaryPackage",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="contracts",
    )
    letter_template = models.ForeignKey(
        "ContractLetterTemplate",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="contracts",
    )
    number = models.CharField(max_length=40, unique=True)
    contract_type = models.CharField(max_length=20, choices=ContractType.choices)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    base_salary = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "number"]

    def clean(self):
        super().clean()
        errors = {}
        if self.end_date and self.start_date and self.end_date < self.start_date:
            errors["end_date"] = "End date must be on or after start date."
        if self.contract_type == self.ContractType.FIXED_TERM and not self.end_date:
            errors["end_date"] = "A fixed-term contract requires an end date."
        if self.base_salary is not None and self.base_salary < 0:
            errors["base_salary"] = "Base salary cannot be negative."
        if self.salary_package_id and self.employee_id:
            if self.salary_package.company_id != self.employee.company_id:
                errors["salary_package"] = "The salary package must belong to the employee company."
            if not self.salary_package.active:
                errors["salary_package"] = "Select an active salary package."
        if self.letter_template_id and self.employee_id:
            if self.letter_template.company_id != self.employee.company_id:
                errors["letter_template"] = "The letter template must belong to the employee company."
            if not self.letter_template.active:
                errors["letter_template"] = "Select an active contract letter template."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.number


class LeaveRequest(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leave_requests")
    leave_type = models.CharField(max_length=60, default="Annual Leave")
    start_date = models.DateField()
    end_date = models.DateField()
    days = models.DecimalField(max_digits=6, decimal_places=2, default=1)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="leave_approvals")

    class Meta:
        ordering = ["-start_date", "employee__name"]

    def __str__(self):
        return f"{self.employee} · {self.leave_type}"


class OvertimeRequest(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="overtime_requests")
    overtime_date = models.DateField(default=timezone.localdate)
    hours = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(Decimal("0.25"))])
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=LeaveRequest.Status.choices, default=LeaveRequest.Status.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="overtime_approvals")

    class Meta:
        ordering = ["-overtime_date", "employee__name"]

    def __str__(self):
        return f"{self.employee} · {self.overtime_date}"


class Payslip(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payslips")
    number = models.CharField(max_length=40, unique=True)
    period_start = models.DateField()
    period_end = models.DateField()
    basic_salary = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    allowances = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    net_salary = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-period_end", "employee__name"]

    def save(self, *args, **kwargs):
        self.net_salary = self.basic_salary + self.allowances - self.deductions
        super().save(*args, **kwargs)

    def __str__(self):
        return self.number


class Candidate(TimeStampedModel):
    class Status(models.TextChoices):
        APPLIED = "applied", "Applied"
        SCREENING = "screening", "Screening"
        INTERVIEW = "interview", "Interview"
        OFFER = "offer", "Offer"
        HIRED = "hired", "Hired"
        REJECTED = "rejected", "Rejected"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="candidates")
    job_title = models.CharField(max_length=140)
    name = models.CharField(max_length=160)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True)
    source = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.APPLIED)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} · {self.job_title}"


class Appraisal(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        IN_REVIEW = "in_review", "In Review"
        COMPLETED = "completed", "Completed"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="appraisals")
    review_date = models.DateField(default=timezone.localdate)
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="employee_appraisals")
    score = models.DecimalField(max_digits=4, decimal_places=2, default=0, validators=[MinValueValidator(0), MaxValueValidator(5)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    strengths = models.TextField(blank=True)
    development_plan = models.TextField(blank=True)

    class Meta:
        ordering = ["-review_date", "employee__name"]

    def __str__(self):
        return f"{self.employee} · {self.review_date}"


class BusinessUnit(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="business_units")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    manager = models.ForeignKey(Employee, on_delete=models.SET_NULL, blank=True, null=True, related_name="managed_business_units")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_business_unit_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class WorkLocation(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="work_locations")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    address = models.TextField(blank=True)
    timezone = models.CharField(max_length=60, default="Asia/Jakarta")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_work_location_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class JobPosition(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="job_positions")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, blank=True, null=True, related_name="job_positions")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    planned_headcount = models.PositiveIntegerField(default=1)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_job_position_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class EmployeeGrade(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employee_grades")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    rank = models.PositiveIntegerField(default=1)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["rank", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_employee_grade_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Skill(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="hr_skills")
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_hr_skill_name_per_company")]

    def __str__(self):
        return self.name


class DocumentType(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employee_document_types")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    mandatory = models.BooleanField(default=False)
    has_expiry = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_document_type_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class HRYear(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="hr_years")
    name = models.CharField(max_length=80)
    date_start = models.DateField()
    date_end = models.DateField()
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-date_start"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_hr_year_name_per_company")]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.date_end < self.date_start:
            raise ValidationError({"date_end": "End date must be on or after start date."})

    def __str__(self):
        return self.name


class CompanyHoliday(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="company_holidays")
    name = models.CharField(max_length=120)
    holiday_date = models.DateField()
    recurring = models.BooleanField(default=False)
    paid = models.BooleanField(default=True)

    class Meta:
        ordering = ["-holiday_date", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "holiday_date", "name"], name="unique_company_holiday")]

    def __str__(self):
        return f"{self.holiday_date} - {self.name}"


class EmployeeChangeRequest(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="change_requests")
    field_name = models.CharField(max_length=80)
    current_value = models.TextField(blank=True)
    requested_value = models.TextField()
    effective_date = models.DateField(default=timezone.localdate)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="employee_change_approvals")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.employee} - {self.field_name}"


class EmployeeDocument(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        REPLACED = "replaced", "Replaced"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="documents")
    document_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT, related_name="employee_documents")
    document_number = models.CharField(max_length=80, blank=True)
    issued_date = models.DateField(blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)
    attachment_url = models.URLField(blank=True)
    verified = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    replaced_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="replaces",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["employee__name", "document_type__name"]

    def __str__(self):
        return f"{self.employee} - {self.document_type}"


class EmployeeDisciplinary(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        REVIEW = "review", "Under Review"
        DECIDED = "decided", "Decision Issued"
        CLOSED = "closed", "Closed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="disciplinary_cases")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="disciplinary_cases")
    reference = models.CharField(max_length=40)
    incident_date = models.DateField()
    violation = models.CharField(max_length=160)
    stage = models.ForeignKey(
        "DisciplinaryStage",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="cases",
    )
    expiry_date = models.DateField(blank=True, null=True)
    action_taken = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-incident_date"]
        constraints = [models.UniqueConstraint(fields=["company", "reference"], name="unique_disciplinary_reference_per_company")]

    def __str__(self):
        return self.reference


class EmployeeChecklist(TimeStampedModel):
    class Lifecycle(models.TextChoices):
        ONBOARDING = "onboarding", "Onboarding"
        OFFBOARDING = "offboarding", "Offboarding"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employee_checklists")
    name = models.CharField(max_length=120)
    lifecycle = models.CharField(max_length=16, choices=Lifecycle.choices)
    description = models.TextField(blank=True)
    responsible_role = models.CharField(max_length=100, blank=True)
    sequence = models.PositiveIntegerField(default=10)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["lifecycle", "sequence", "name"]

    def __str__(self):
        return f"{self.get_lifecycle_display()} - {self.name}"


class EmployeeOnboarding(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="onboarding_processes")
    start_date = models.DateField(default=timezone.localdate)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_onboarding_processes")
    progress = models.PositiveSmallIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "employee__name"]

    def __str__(self):
        return f"Onboarding - {self.employee}"


class EmployeeOffboarding(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="offboarding_processes")
    last_working_date = models.DateField()
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_offboarding_processes")
    reason = models.TextField(blank=True)
    progress = models.PositiveSmallIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-last_working_date", "employee__name"]

    def __str__(self):
        return f"Offboarding - {self.employee}"


class RawAttendance(TimeStampedModel):
    class EventType(models.TextChoices):
        CHECK_IN = "check_in", "Check In"
        CHECK_OUT = "check_out", "Check Out"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="raw_attendance_events")
    captured_at = models.DateTimeField(default=timezone.now)
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    source = models.CharField(max_length=40, default="manual")
    device_identifier = models.CharField(max_length=100, blank=True)
    processed = models.BooleanField(default=False)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-captured_at"]
        indexes = [models.Index(fields=["employee", "captured_at"], name="hr_raw_emp_captured_idx")]

    def __str__(self):
        return f"{self.employee} - {self.captured_at}"


class Shift(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="hr_shifts")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    start_time = models.TimeField()
    end_time = models.TimeField()
    break_minutes = models.PositiveIntegerField(default=60)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_shift_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class WorkSchedule(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="work_schedules")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="work_schedules")
    working_days = models.CharField(max_length=80, default="Mon,Tue,Wed,Thu,Fri")
    hours_per_day = models.DecimalField(max_digits=4, decimal_places=2, default=8)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_work_schedule_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ScheduleAssignment(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="schedule_assignments")
    schedule = models.ForeignKey(WorkSchedule, on_delete=models.PROTECT, related_name="assignments")
    date_start = models.DateField()
    date_end = models.DateField(blank=True, null=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-date_start", "employee__name"]
        indexes = [models.Index(fields=["employee", "-date_start"], name="hr_assignment_emp_start_idx")]

    def clean(self):
        errors = {}
        if self.date_start and self.date_end and self.date_end < self.date_start:
            errors["date_end"] = "End date must be on or after start date."
        if self.employee_id and self.schedule_id:
            employee_company_id = Employee.objects.filter(pk=self.employee_id).values_list("company_id", flat=True).first()
            schedule_companies = WorkSchedule.objects.filter(pk=self.schedule_id).values_list(
                "company_id", "shift__company_id"
            ).first()
            if employee_company_id and schedule_companies and (
                schedule_companies[0] != employee_company_id or schedule_companies[1] != employee_company_id
            ):
                errors["schedule"] = "Schedule and shift must belong to the employee company."
        if self.active and self.employee_id and self.date_start and "date_end" not in errors:
            overlapping = ScheduleAssignment.objects.filter(employee_id=self.employee_id, active=True)
            if self.date_end:
                overlapping = overlapping.filter(date_start__lte=self.date_end)
            overlapping = overlapping.filter(models.Q(date_end__isnull=True) | models.Q(date_end__gte=self.date_start))
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)
            if overlapping.exists():
                errors["date_start"] = "An active schedule assignment already covers part of this period."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.employee} - {self.schedule}"


class LeaveType(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="leave_types")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    annual_entitlement = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    paid = models.BooleanField(default=True)
    requires_attachment = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_leave_type_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class LeaveAllocation(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leave_allocations")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="allocations")
    year = models.PositiveIntegerField(default=current_year)
    allocated_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    used_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)

    class Meta:
        ordering = ["-year", "employee__name"]
        constraints = [models.UniqueConstraint(fields=["employee", "leave_type", "year"], name="unique_leave_allocation_per_year")]

    @property
    def balance_days(self):
        return self.allocated_days - self.used_days

    def __str__(self):
        return f"{self.employee} - {self.leave_type} ({self.year})"


class OvertimeRule(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="overtime_rules")
    name = models.CharField(max_length=120)
    weekday_multiplier = models.DecimalField(max_digits=5, decimal_places=2, default=1.5)
    weekend_multiplier = models.DecimalField(max_digits=5, decimal_places=2, default=2)
    maximum_hours = models.DecimalField(max_digits=5, decimal_places=2, default=4)
    minimum_minutes = models.PositiveIntegerField(default=30)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_overtime_rule_name_per_company")]

    def __str__(self):
        return self.name


class PayrollPeriod(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PROCESSING = "processing", "Processing"
        CLOSED = "closed", "Closed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="payroll_periods")
    name = models.CharField(max_length=100)
    date_start = models.DateField()
    date_end = models.DateField()
    cutoff_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["-date_start"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_payroll_period_name_per_company")]

    def __str__(self):
        return self.name


class SalaryComponent(TimeStampedModel):
    class ComponentType(models.TextChoices):
        EARNING = "earning", "Earning"
        DEDUCTION = "deduction", "Deduction"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="salary_components")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    component_type = models.CharField(max_length=16, choices=ComponentType.choices)
    default_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    taxable = models.BooleanField(default=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["component_type", "name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_salary_component_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class TravelRequest(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="travel_requests")
    number = models.CharField(max_length=40, unique=True)
    destination = models.CharField(max_length=160)
    date_start = models.DateField()
    date_end = models.DateField()
    purpose = models.TextField()
    estimated_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="travel_approvals")

    class Meta:
        ordering = ["-date_start"]

    def __str__(self):
        return self.number


class TrainingProgram(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SCHEDULED = "scheduled", "Scheduled"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="training_programs")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=140)
    trainer = models.CharField(max_length=140, blank=True)
    date_start = models.DateField()
    date_end = models.DateField()
    capacity = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["-date_start"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_training_program_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class CashAdvance(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="cash_advances")
    number = models.CharField(max_length=40, unique=True)
    request_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    purpose = models.TextField()
    settlement_due_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="cash_advance_approvals")

    class Meta:
        ordering = ["-request_date"]

    def __str__(self):
        return self.number


class HRSetting(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="hr_setting")
    standard_hours_per_day = models.DecimalField(max_digits=4, decimal_places=2, default=8)
    attendance_tolerance_minutes = models.PositiveIntegerField(default=15)
    leave_approval_levels = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(5)])
    overtime_approval_required = models.BooleanField(default=True)
    payroll_currency = models.CharField(max_length=3, default="IDR")

    class Meta:
        ordering = ["company__name"]

    def __str__(self):
        return f"HR Settings - {self.company}"


class LoanType(TimeStampedModel):
    class InterestMethod(models.TextChoices):
        FLAT = "flat", "Flat"
        DECLINING = "declining", "Declining Balance"
        NONE = "none", "No Interest"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employee_loan_types")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    interest_method = models.CharField(max_length=16, choices=InterestMethod.choices, default=InterestMethod.FLAT)
    default_interest_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    maximum_term_months = models.PositiveIntegerField(default=12)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_loan_type_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class LoanPolicy(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employee_loan_policies")
    loan_type = models.ForeignKey(LoanType, on_delete=models.PROTECT, related_name="policies")
    name = models.CharField(max_length=120)
    minimum_service_months = models.PositiveIntegerField(default=0)
    maximum_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    maximum_installment_ratio = models.DecimalField(max_digits=5, decimal_places=2, default=30, help_text="Maximum percentage of salary.")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_loan_policy_name_per_company")]

    def __str__(self):
        return self.name


class CareerTransitionType(TimeStampedModel):
    class Category(models.TextChoices):
        PROMOTION = "promotion", "Promotion"
        TRANSFER = "transfer", "Transfer"
        DEMOTION = "demotion", "Demotion"
        RESIGNATION = "resignation", "Resignation"
        TERMINATION = "termination", "Termination"
        RETIREMENT = "retirement", "Retirement"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="career_transition_types")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=20, choices=Category.choices)
    requires_clearance = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_transition_type_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class CareerTransitionLetter(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="career_transition_letters")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    subject = models.CharField(max_length=180)
    template_body = models.TextField(help_text="Use plain text placeholders such as employee_name and effective_date.")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_transition_letter_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class CareerTransitionRequest(TimeStampedModel):
    organization_branch_lookup = "employee__branch"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="career_transition_requests")
    number = models.CharField(max_length=40, unique=True)
    transition_type = models.ForeignKey(CareerTransitionType, on_delete=models.PROTECT, related_name="requests")
    effective_date = models.DateField()
    current_position = models.CharField(max_length=120, blank=True)
    new_position = models.CharField(max_length=120, blank=True)
    target_branch = models.ForeignKey(
        "organization.Branch",
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="career_transition_requests",
    )
    target_department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="career_transition_requests",
    )
    target_job_position = models.ForeignKey(
        JobPosition,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="career_transition_requests",
    )
    target_supervisor = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="incoming_career_transition_requests",
    )
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="career_transition_approvals")
    applied_at = models.DateTimeField(blank=True, null=True, editable=False)
    applied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        editable=False,
        related_name="applied_career_transitions",
    )

    class Meta:
        ordering = ["-effective_date", "number"]

    def clean(self):
        super().clean()
        errors = {}
        if self.employee_id and self.transition_type_id:
            company_id = self.employee.company_id
            if self.transition_type.company_id != company_id:
                errors["transition_type"] = "Transition type must belong to the employee company."
            elif not self.transition_type.active:
                errors["transition_type"] = "Select an active transition type."
            for field_name, label in (
                ("target_branch", "Branch"),
                ("target_department", "Department"),
                ("target_job_position", "Job position"),
                ("target_supervisor", "Supervisor"),
            ):
                related_id = getattr(self, f"{field_name}_id")
                if related_id and getattr(self, field_name).company_id != company_id:
                    errors[field_name] = f"{label} must belong to the employee company."
            if self.target_supervisor_id and self.target_supervisor_id == self.employee_id:
                errors["target_supervisor"] = "An employee cannot supervise themselves."
            internal_categories = {
                CareerTransitionType.Category.PROMOTION,
                CareerTransitionType.Category.TRANSFER,
                CareerTransitionType.Category.DEMOTION,
            }
            if self.transition_type.category in internal_categories and not any(
                (self.target_branch_id, self.target_department_id, self.target_job_position_id, self.target_supervisor_id)
            ):
                errors["target_job_position"] = "Choose at least one target organization value."
            if self.transition_type.category not in internal_categories and any(
                (self.target_branch_id, self.target_department_id, self.target_job_position_id, self.target_supervisor_id)
            ):
                errors["target_branch"] = "Exit transitions cannot contain target organization values."
            if self.target_job_position_id and self.target_department_id:
                position_department_id = self.target_job_position.department_id
                if position_department_id and position_department_id != self.target_department_id:
                    errors["target_job_position"] = "Job position must belong to the target department."
            if self.effective_date and self.effective_date < self.employee.join_date:
                errors["effective_date"] = "Effective date cannot be before the employee join date."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.number


class TravelPackage(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="travel_packages")
    name = models.CharField(max_length=120)
    destination_group = models.CharField(max_length=120)
    daily_allowance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    lodging_limit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    transport_limit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_travel_package_name_per_company")]

    def __str__(self):
        return self.name


class TrainingCourse(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="hr_training_courses")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=140)
    category = models.CharField(max_length=100, blank=True)
    duration_hours = models.DecimalField(max_digits=7, decimal_places=2, default=1)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_training_course_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class TrainingTrainer(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="hr_training_trainers")
    name = models.CharField(max_length=140)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    specialties = models.TextField(blank=True)
    external = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class TrainingConduct(TimeStampedModel):
    class TrainingType(models.TextChoices):
        INTERNAL = "internal", "Internal Training"
        PUBLIC = "public", "Public Training"

    class TrainingSource(models.TextChoices):
        COURSE = "course", "Course"
        PROGRAM = "program", "Program"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        UPCOMING = "upcoming", "Upcoming"
        ON_GOING = "on_going", "On Going"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="training_conducts", blank=True, null=True)
    name = models.CharField(max_length=255, blank=True)
    training_type = models.CharField(max_length=20, choices=TrainingType.choices, default=TrainingType.INTERNAL)
    training_source = models.CharField(max_length=20, choices=TrainingSource.choices, default=TrainingSource.COURSE)
    course = models.ForeignKey(TrainingCourse, on_delete=models.PROTECT, related_name="conducts", blank=True, null=True)
    program = models.ForeignKey(TrainingProgram, on_delete=models.PROTECT, related_name="conducts", blank=True, null=True)
    pic = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="managed_training_conducts", blank=True, null=True)
    trainer = models.ForeignKey(TrainingTrainer, on_delete=models.SET_NULL, blank=True, null=True, related_name="conducts")
    reference = models.CharField(max_length=40, unique=True, blank=True)
    date_start = models.DateField(blank=True, null=True)
    date_end = models.DateField(blank=True, null=True)
    location = models.CharField(max_length=160, blank=True)
    participant_count = models.PositiveIntegerField(default=0)
    request_count = models.PositiveIntegerField(default=0)
    use_minimum_participant = models.BooleanField(default=False)
    minimum_participants = models.PositiveIntegerField(blank=True, null=True)
    use_maximum_participant = models.BooleanField(default=False)
    maximum_participants = models.PositiveIntegerField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-date_start", "name"]

    def clean(self):
        super().clean()
        if self.training_source == self.TrainingSource.COURSE and not self.course_id:
            raise ValidationError({"course": "Training Course is required for Course source."})
        if self.training_source == self.TrainingSource.PROGRAM and not self.program_id:
            raise ValidationError({"program": "Training Program is required for Program source."})
        if self.training_type != self.TrainingType.PUBLIC:
            self.use_minimum_participant = False
            self.minimum_participants = None
            self.use_maximum_participant = False
            self.maximum_participants = None
        if self.use_minimum_participant and not self.minimum_participants:
            raise ValidationError({"minimum_participants": "Minimum Participants must be greater than zero."})
        if self.use_maximum_participant and not self.maximum_participants:
            raise ValidationError({"maximum_participants": "Maximum Participants must be greater than zero."})
        if (
            self.use_minimum_participant
            and self.use_maximum_participant
            and self.maximum_participants < self.minimum_participants
        ):
            raise ValidationError({"maximum_participants": "Maximum Participants cannot be lower than Minimum Participants."})

    def save(self, *args, **kwargs):
        if self.training_source == self.TrainingSource.COURSE:
            self.program = None
        else:
            self.course = None
        if not self.reference:
            prefix = "PUB" if self.training_type == self.TrainingType.PUBLIC else "INT"
            self.reference = f"TRN/{prefix}/{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name or self.reference


class TrainingConductSchedule(TimeStampedModel):
    conduct = models.ForeignKey(TrainingConduct, on_delete=models.CASCADE, related_name="schedule_lines")
    sequence = models.PositiveIntegerField(default=1)
    course = models.ForeignKey(TrainingCourse, on_delete=models.PROTECT, related_name="conduct_schedule_lines")
    trainer = models.ForeignKey(TrainingTrainer, on_delete=models.SET_NULL, blank=True, null=True, related_name="conduct_schedule_lines")
    notes = models.CharField(max_length=240, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()

    class Meta:
        ordering = ["sequence", "start_date", "start_time"]

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End Date cannot be earlier than Start Date."})
        if self.start_date and self.end_date and self.start_time and self.end_time and self.start_date == self.end_date and self.end_time <= self.start_time:
            raise ValidationError({"end_time": "End Time must be later than Start Time."})

    def __str__(self):
        return f"{self.conduct} · {self.course}"


class TrainingConductParticipant(TimeStampedModel):
    class Status(models.TextChoices):
        ENROLLED = "enrolled", "Enrolled"
        ATTENDED = "attended", "Attended"
        ABSENT = "absent", "Absent"
        CANCELLED = "cancelled", "Cancelled"

    conduct = models.ForeignKey(TrainingConduct, on_delete=models.CASCADE, related_name="participant_lines")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="training_participations")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ENROLLED)
    notes = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["employee__name"]
        constraints = [
            models.UniqueConstraint(fields=["conduct", "employee"], name="unique_training_conduct_participant")
        ]

    def __str__(self):
        return f"{self.conduct} · {self.employee}"


class ExpenseProduct(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employee_expense_products")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=100, blank=True)
    maximum_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    receipt_required = models.BooleanField(default=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_expense_product_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class PayrollRun(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CALCULATING = "calculating", "Calculating"
        READY = "ready", "Ready"
        POSTED = "posted", "Posted"
        CANCELLED = "cancelled", "Cancelled"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="payroll_runs")
    period = models.ForeignKey(PayrollPeriod, on_delete=models.PROTECT, related_name="payroll_runs")
    number = models.CharField(max_length=40, unique=True)
    employee_count = models.PositiveIntegerField(default=0)
    gross_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    deduction_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    net_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number


class CompensationBenefit(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="compensation_benefits")
    contract = models.ForeignKey(
        EmploymentContract,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_name="compensation_snapshots",
    )
    effective_date = models.DateField(default=timezone.localdate)
    basic_salary = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    allowance_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    deduction_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="compensation_approvals")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-effective_date", "employee__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["contract", "effective_date"],
                name="unique_contract_compensation_effective_date",
            )
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.contract_id and self.employee_id and self.contract.employee_id != self.employee_id:
            errors["contract"] = "The compensation contract must belong to the selected employee."
        for field_name in ("basic_salary", "allowance_amount", "deduction_amount"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                errors[field_name] = "Compensation amounts cannot be negative."
        if errors:
            raise ValidationError(errors)

    @property
    def total_compensation(self):
        return self.basic_salary + self.allowance_amount - self.deduction_amount

    def __str__(self):
        return f"{self.employee} - {self.effective_date}"


# Extended HR foundation and operational surfaces audited from the HMX HR PRD.
# These models are intentionally additive so existing GS Shop demo data and URLs
# remain compatible while the missing domains become usable through generic CRUD.


class JobClassification(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="job_classifications")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_job_classification_code_per_company")
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class JobExperienceLevel(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="job_experience_levels")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    minimum_years = models.PositiveSmallIntegerField(default=0)
    maximum_years = models.PositiveSmallIntegerField(blank=True, null=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["minimum_years", "name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_job_experience_code_per_company")
        ]

    def clean(self):
        if self.maximum_years is not None and self.maximum_years < self.minimum_years:
            raise ValidationError({"maximum_years": "Maximum years cannot be lower than minimum years."})

    def __str__(self):
        return f"{self.code} - {self.name}"


class EmployeeCategory(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employee_categories")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_employee_category_code_per_company")
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class EmployeeAddress(TimeStampedModel):
    class AddressType(models.TextChoices):
        HOME = "home", "Home"
        DOMICILE = "domicile", "Domicile"
        EMERGENCY = "emergency", "Emergency"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="hr_addresses")
    address_type = models.CharField(max_length=16, choices=AddressType.choices, default=AddressType.HOME)
    address = models.TextField()
    city = models.CharField(max_length=100, blank=True)
    province = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    primary = models.BooleanField(default=False)

    class Meta:
        ordering = ["employee__name", "address_type"]

    def __str__(self):
        return f"{self.employee} - {self.get_address_type_display()}"


class EmployeeRelation(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="hr_relations")
    name = models.CharField(max_length=140)
    relationship = models.CharField(max_length=80)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    emergency_contact = models.BooleanField(default=False)

    class Meta:
        ordering = ["employee__name", "name"]

    def __str__(self):
        return f"{self.employee} - {self.name}"


class WorkingCalendar(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="working_calendars")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    timezone = models.CharField(max_length=60, default="Asia/Jakarta")
    hours_per_week = models.DecimalField(max_digits=5, decimal_places=2, default=40)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_working_calendar_code_per_company")
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class WorkPattern(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="work_patterns")
    calendar = models.ForeignKey(WorkingCalendar, on_delete=models.PROTECT, related_name="patterns")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    pattern = models.CharField(max_length=160, help_text="Comma-separated shift or rest-day pattern.")
    cycle_days = models.PositiveSmallIntegerField(default=7, validators=[MinValueValidator(1)])
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_work_pattern_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ScheduleExchange(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="schedule_exchange_requests")
    exchange_with = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="schedule_exchange_offers")
    original_date = models.DateField()
    requested_date = models.DateField()
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="schedule_exchange_approvals",
    )

    class Meta:
        ordering = ["-original_date"]

    def clean(self):
        if self.employee_id and self.employee_id == self.exchange_with_id:
            raise ValidationError({"exchange_with": "Choose a different employee for the exchange."})

    def __str__(self):
        return f"{self.employee} - {self.original_date}"


class ContractType(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="contract_types")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    default_duration_months = models.PositiveIntegerField(blank=True, null=True)
    renewable = models.BooleanField(default=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_contract_type_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


CONTRACT_LETTER_VARIABLES = {
    "company_name",
    "contract_number",
    "employee_id",
    "employee_name",
    "start_date",
    "end_date",
    "base_salary",
}


class ContractLetterTemplate(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="contract_letter_templates")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    letter_type = models.CharField(max_length=80, default="Employment Contract")
    content = models.TextField(
        help_text=(
            "Supported variables: {{ company_name }}, {{ contract_number }}, {{ employee_id }}, "
            "{{ employee_name }}, {{ start_date }}, {{ end_date }}, and {{ base_salary }}."
        )
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"],
                name="unique_contract_letter_template_code",
            )
        ]

    def clean(self):
        super().clean()
        variables = set(re.findall(r"{{\s*([a-z_][a-z0-9_]*)\s*}}", self.content or ""))
        unsupported = sorted(variables - CONTRACT_LETTER_VARIABLES)
        if unsupported:
            raise ValidationError(
                {"content": f"Unsupported contract letter variables: {', '.join(unsupported)}."}
            )

    def __str__(self):
        return f"{self.code} - {self.name}"


class ContractAllowance(TimeStampedModel):
    contract = models.ForeignKey(EmploymentContract, on_delete=models.CASCADE, related_name="allowance_lines")
    component = models.ForeignKey(SalaryComponent, on_delete=models.PROTECT, related_name="contract_allowances")
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    effective_date = models.DateField(default=timezone.localdate)
    end_date = models.DateField(blank=True, null=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-effective_date", "component__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["contract", "component", "effective_date"],
                name="unique_contract_allowance_effective_date",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name="contract_allowance_amount_nonnegative",
            ),
        ]
        indexes = [
            models.Index(
                fields=["contract", "active", "effective_date"],
                name="hr_contract_allow_active_idx",
            )
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.end_date and self.end_date < self.effective_date:
            errors["end_date"] = "End date must be on or after effective date."
        if self.amount is not None and self.amount < 0:
            errors["amount"] = "Allowance amount cannot be negative."
        if self.contract_id and self.component_id:
            employee_company_id = self.contract.employee.company_id
            if self.component.company_id != employee_company_id:
                errors["component"] = "The salary component must belong to the contract company."
            if self.component.component_type != SalaryComponent.ComponentType.EARNING:
                errors["component"] = "Contract allowances require an earning salary component."
            if self.effective_date < self.contract.start_date:
                errors["effective_date"] = "Allowance cannot start before the contract."
            if self.contract.end_date and self.effective_date > self.contract.end_date:
                errors["effective_date"] = "Allowance cannot start after the contract ends."
            if self.end_date and self.contract.end_date and self.end_date > self.contract.end_date:
                errors["end_date"] = "Allowance cannot end after the contract."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.contract} - {self.component}"


class ContractLetter(TimeStampedModel):
    contract = models.ForeignKey(EmploymentContract, on_delete=models.CASCADE, related_name="letters")
    template = models.ForeignKey(
        ContractLetterTemplate,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="generated_letters",
    )
    letter_number = models.CharField(max_length=50, unique=True)
    letter_type = models.CharField(max_length=80, default="Employment Contract")
    issued_date = models.DateField(default=timezone.localdate)
    content = models.TextField(blank=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="generated_contract_letters",
    )
    attachment_url = models.URLField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-issued_date"]

    def clean(self):
        super().clean()
        if self.contract_id and self.template_id:
            if self.contract.employee.company_id != self.template.company_id:
                raise ValidationError({"template": "The template must belong to the contract company."})

    def __str__(self):
        return self.letter_number


class DisciplinaryStage(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="disciplinary_stages")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    sequence = models.PositiveIntegerField(default=10)
    validity_days = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sequence", "name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_disciplinary_stage_code_per_company")
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class AttendanceChangeRequest(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="attendance_change_requests")
    attendance_date = models.DateField()
    requested_check_in = models.TimeField(blank=True, null=True)
    requested_check_out = models.TimeField(blank=True, null=True)
    snapshot_captured = models.BooleanField(default=False)
    had_attendance = models.BooleanField(default=False)
    original_check_in = models.TimeField(blank=True, null=True)
    original_check_out = models.TimeField(blank=True, null=True)
    original_check_in_at = models.DateTimeField(blank=True, null=True)
    original_check_out_at = models.DateTimeField(blank=True, null=True)
    original_from_raw_attendance = models.BooleanField(default=False)
    original_status = models.CharField(max_length=16, blank=True)
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="attendance_change_approvals",
    )

    class Meta:
        ordering = ["-attendance_date", "employee__name"]
        indexes = [
            models.Index(
                fields=["employee", "attendance_date"],
                condition=models.Q(status__in=[ApprovalStatus.DRAFT, ApprovalStatus.SUBMITTED, ApprovalStatus.APPROVED]),
                name="hr_acr_active_day_idx",
            ),
        ]

    def __str__(self):
        return f"{self.employee} - {self.attendance_date}"


class LiveTrackingSession(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        RUNNING = "running", "Running"
        STOPPED = "stopped", "Stopped"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="live_tracking_sessions")
    started_at = models.DateTimeField(blank=True, null=True)
    stopped_at = models.DateTimeField(blank=True, null=True)
    last_latitude = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)
    last_longitude = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.employee} - {self.get_status_display()}"


class AttendanceRecompute(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready for Review"
        APPLIED = "applied", "Applied"
        REJECTED = "rejected", "Rejected"
        FAILED = "failed", "Failed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="attendance_recomputes")
    date_start = models.DateField()
    date_end = models.DateField()
    employee_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attendance_recomputes")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        if self.date_end < self.date_start:
            raise ValidationError({"date_end": "End date must be on or after start date."})

    def __str__(self):
        return f"{self.company} - {self.date_start} to {self.date_end}"


class LeaveStructure(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="leave_structures")
    name = models.CharField(max_length=120)
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="structures")
    entitlement_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    carry_forward_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    minimum_service_months = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_leave_structure_per_company")]

    def __str__(self):
        return self.name


class LeaveOpeningBalance(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        FINALIZED = "finalized", "Finalized"
        LOCKED = "locked", "Locked"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leave_opening_balances")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="opening_balances")
    year = models.PositiveIntegerField(default=current_year)
    balance_days = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-year", "employee__name"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "leave_type", "year"], name="unique_leave_opening_balance")
        ]

    def __str__(self):
        return f"{self.employee} - {self.leave_type} ({self.year})"


class LeaveMovement(TimeStampedModel):
    class MovementType(models.TextChoices):
        OPENING = "opening", "Opening Balance"
        ALLOCATION = "allocation", "Allocation"
        USAGE = "usage", "Usage"
        ADJUSTMENT = "adjustment", "Adjustment"
        ENCASHMENT = "encashment", "Encashment"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leave_movements")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="movements")
    movement_date = models.DateField(default=timezone.localdate)
    movement_type = models.CharField(max_length=16, choices=MovementType.choices)
    days = models.DecimalField(max_digits=7, decimal_places=2)
    reference = models.CharField(max_length=80, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-movement_date", "-created_at"]

    def __str__(self):
        return f"{self.employee} - {self.days} days"


class LeaveEncashment(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leave_encashments")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="encashments")
    request_date = models.DateField(default=timezone.localdate)
    days = models.DecimalField(max_digits=6, decimal_places=2, validators=[MinValueValidator(Decimal("0.25"))])
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="leave_encashment_approvals",
    )

    class Meta:
        ordering = ["-request_date"]

    def __str__(self):
        return f"{self.employee} - {self.days} days"


class PayrollVariable(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="payroll_variables")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    value = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    effective_date = models.DateField(default=timezone.localdate)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code", "-effective_date"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code", "effective_date"], name="unique_payroll_variable_effective_date")
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class SalaryPackage(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="salary_packages")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    grade = models.ForeignKey(EmployeeGrade, on_delete=models.SET_NULL, blank=True, null=True, related_name="salary_packages")
    base_salary = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_salary_package_code_per_company")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class PayrollReportTemplate(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="payroll_report_templates")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    columns = models.TextField(help_text="Comma-separated payroll report columns.")
    file_format = models.CharField(max_length=12, default="xlsx")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_payroll_report_template_code_per_company")
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class EmployeeLoanRequest(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        WAITING = "waiting", "Waiting for Approval"
        CONFIRMED = "confirmed", "Confirmed"
        DISBURSED = "disbursed", "Disbursed"
        ACTIVE = "active", "Active"
        SETTLED = "settled", "Settled"
        REFUSED = "refused", "Refused"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="employee_loan_requests")
    loan_type = models.ForeignKey(LoanType, on_delete=models.PROTECT, related_name="requests")
    number = models.CharField(max_length=40, unique=True)
    request_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    term_months = models.PositiveIntegerField(default=12, validators=[MinValueValidator(1)])
    interest_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    purpose = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="employee_loan_approvals",
    )

    class Meta:
        ordering = ["-request_date"]

    def __str__(self):
        return self.number


class LoanInstallment(TimeStampedModel):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        DUE = "due", "Due"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"
        CANCELLED = "cancelled", "Cancelled"

    loan = models.ForeignKey(EmployeeLoanRequest, on_delete=models.CASCADE, related_name="installments")
    sequence = models.PositiveIntegerField()
    due_date = models.DateField()
    principal = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    interest = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SCHEDULED)

    class Meta:
        ordering = ["loan", "sequence"]
        constraints = [models.UniqueConstraint(fields=["loan", "sequence"], name="unique_loan_installment_sequence")]

    @property
    def amount(self):
        return self.principal + self.interest

    def __str__(self):
        return f"{self.loan} - {self.sequence}"


class LoanRepayment(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"
        SETTLED = "settled", "Settled"
        CANCELLED = "cancelled", "Cancelled"

    loan = models.ForeignKey(EmployeeLoanRequest, on_delete=models.PROTECT, related_name="repayments")
    installment = models.ForeignKey(
        LoanInstallment,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="repayments",
    )
    payment_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    reference = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-payment_date", "-created_at"]

    def __str__(self):
        return f"{self.loan} - {self.payment_date}"


class LoanEligibilityAssignment(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="loan_eligibility_assignments")
    policy = models.ForeignKey(LoanPolicy, on_delete=models.PROTECT, related_name="eligibility_assignments")
    date_start = models.DateField(default=timezone.localdate)
    date_end = models.DateField(blank=True, null=True)
    maximum_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-date_start"]
        constraints = [
            models.UniqueConstraint(fields=["employee", "policy", "date_start"], name="unique_loan_eligibility_start")
        ]

    def clean(self):
        if self.date_end and self.date_end < self.date_start:
            raise ValidationError({"date_end": "End date must be on or after start date."})

    def __str__(self):
        return f"{self.employee} - {self.policy}"


class TrainingRequest(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="training_requests")
    course = models.ForeignKey(TrainingCourse, on_delete=models.PROTECT, related_name="requests")
    requested_date = models.DateField(default=timezone.localdate)
    preferred_start_date = models.DateField(blank=True, null=True)
    business_reason = models.TextField()
    estimated_cost = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="training_request_approvals",
    )

    class Meta:
        ordering = ["-requested_date"]

    def __str__(self):
        return f"{self.employee} - {self.course}"


class TrainingProgress(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="training_progress")
    conduct = models.ForeignKey(TrainingConduct, on_delete=models.PROTECT, related_name="progress_records")
    completion_percent = models.PositiveSmallIntegerField(default=0, validators=[MinValueValidator(0), MaxValueValidator(100)])
    score = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    completed_date = models.DateField(blank=True, null=True)
    certificate_url = models.URLField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [models.UniqueConstraint(fields=["employee", "conduct"], name="unique_training_progress")]

    def __str__(self):
        return f"{self.employee} - {self.conduct}"


class AdvanceCycle(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="advance_cycles")
    name = models.CharField(max_length=120)
    date_start = models.DateField()
    date_end = models.DateField()
    limit_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-date_start"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_advance_cycle_per_company")]

    def clean(self):
        if self.date_end < self.date_start:
            raise ValidationError({"date_end": "End date must be on or after start date."})

    def __str__(self):
        return self.name


class ExpenseCycle(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="expense_cycles")
    name = models.CharField(max_length=120)
    date_start = models.DateField()
    date_end = models.DateField()
    limit_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-date_start"]
        constraints = [models.UniqueConstraint(fields=["company", "name"], name="unique_expense_cycle_per_company")]

    def clean(self):
        if self.date_end < self.date_start:
            raise ValidationError({"date_end": "End date must be on or after start date."})

    def __str__(self):
        return self.name


class AdvanceLimitConsumption(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="advance_limit_consumptions")
    cycle = models.ForeignKey(AdvanceCycle, on_delete=models.PROTECT, related_name="consumptions")
    transaction_reference = models.CharField(max_length=80)
    transaction_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-transaction_date"]
        constraints = [
            models.UniqueConstraint(fields=["cycle", "transaction_reference"], name="unique_advance_consumption_reference")
        ]

    def __str__(self):
        return self.transaction_reference


class ExpenseLimitConsumption(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="expense_limit_consumptions")
    cycle = models.ForeignKey(ExpenseCycle, on_delete=models.PROTECT, related_name="consumptions")
    transaction_reference = models.CharField(max_length=80)
    transaction_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-transaction_date"]
        constraints = [
            models.UniqueConstraint(fields=["cycle", "transaction_reference"], name="unique_expense_consumption_reference")
        ]

    def __str__(self):
        return self.transaction_reference


# Detailed HR records required by the audited HMX menus, wizards, and history
# surfaces. They stay additive so existing GS Shop records remain compatible.


class EmployeeIDCardTemplate(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employee_id_card_templates")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    validity_months = models.PositiveIntegerField(default=12)
    front_template_url = models.URLField(blank=True)
    back_template_url = models.URLField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_employee_id_card_template")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class EmployeeIDCard(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"
        REPLACED = "replaced", "Replaced"

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="id_cards")
    template = models.ForeignKey(EmployeeIDCardTemplate, on_delete=models.PROTECT, related_name="cards")
    card_number = models.CharField(max_length=60, unique=True)
    issued_date = models.DateField(default=timezone.localdate)
    expiry_date = models.DateField(blank=True, null=True)
    rendered_card_url = models.URLField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["employee__name", "-issued_date"]

    def clean(self):
        if self.expiry_date and self.expiry_date < self.issued_date:
            raise ValidationError({"expiry_date": "Expiry date must be on or after issued date."})
        if self.template_id and self.employee_id and self.template.company_id != self.employee.company_id:
            raise ValidationError({"template": "The ID card template must belong to the employee company."})

    def __str__(self):
        return f"{self.card_number} - {self.employee}"


class MultipleJobAssignment(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="multiple_job_assignments")
    job_position = models.ForeignKey(JobPosition, on_delete=models.PROTECT, related_name="employee_assignments")
    date_start = models.DateField(default=timezone.localdate)
    date_end = models.DateField(blank=True, null=True)
    allocation_percent = models.DecimalField(max_digits=5, decimal_places=2, default=100, validators=[MaxValueValidator(100)])
    primary = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["employee__name", "-primary", "job_position__name"]
        constraints = [models.UniqueConstraint(fields=["employee", "job_position", "date_start"], name="unique_multiple_job_period")]

    def clean(self):
        if self.date_end and self.date_end < self.date_start:
            raise ValidationError({"date_end": "End date must be on or after start date."})
        if self.employee_id and self.job_position_id and self.employee.company_id != self.job_position.company_id:
            raise ValidationError({"job_position": "The job position must belong to the employee company."})

    def __str__(self):
        return f"{self.employee} - {self.job_position}"


class OrganizationConfiguration(TimeStampedModel):
    company = models.OneToOneField(Company, on_delete=models.PROTECT, related_name="hr_organization_configuration")
    organization_chart_enabled = models.BooleanField(default=True)
    allow_multiple_jobs = models.BooleanField(default=False)
    require_branch_scope = models.BooleanField(default=False)
    default_working_calendar = models.ForeignKey(
        WorkingCalendar,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="default_for_companies",
    )
    announcement_notifications = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__name"]

    def clean(self):
        if (
            self.default_working_calendar_id
            and self.default_working_calendar.company_id != self.company_id
        ):
            raise ValidationError(
                {"default_working_calendar": "The working calendar must belong to the selected company."}
            )

    def __str__(self):
        return f"Organization Configuration - {self.company}"


class HRAnnouncement(TimeStampedModel):
    class Audience(models.TextChoices):
        ALL = "all", "All Employees"
        MANAGERS = "managers", "Managers"
        DEPARTMENT = "department", "Department"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="hr_announcements")
    title = models.CharField(max_length=180)
    body = models.TextField()
    audience = models.CharField(max_length=16, choices=Audience.choices, default=Audience.ALL)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, blank=True, null=True, related_name="announcements")
    publish_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(blank=True, null=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-publish_at"]

    def clean(self):
        if self.expires_at and self.expires_at < self.publish_at:
            raise ValidationError({"expires_at": "Expiry must be on or after publication."})
        if self.audience == self.Audience.DEPARTMENT and not self.department_id:
            raise ValidationError({"department": "A department is required for department announcements."})
        if self.department_id and self.department.company_id != self.company_id:
            raise ValidationError({"department": "The department must belong to the selected company."})

    def __str__(self):
        return self.title


class ContractAllowanceHistory(TimeStampedModel):
    contract = models.ForeignKey(EmploymentContract, on_delete=models.PROTECT, related_name="allowance_history")
    allowance = models.ForeignKey(ContractAllowance, on_delete=models.PROTECT, related_name="history")
    previous_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    new_amount = models.DecimalField(max_digits=18, decimal_places=2)
    effective_date = models.DateField(default=timezone.localdate)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="contract_allowance_changes")
    reason = models.TextField()

    class Meta:
        ordering = ["-effective_date", "-id"]

    def __str__(self):
        return f"{self.contract} - {self.allowance}"


class EmployeeDocumentBulkUpload(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Partially Completed"
        FAILED = "failed", "Failed"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="employee_document_bulk_uploads")
    document_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT, related_name="bulk_uploads")
    source_file_url = models.URLField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    total_rows = models.PositiveIntegerField(default=0)
    successful_rows = models.PositiveIntegerField(default=0)
    failed_rows = models.PositiveIntegerField(default=0)
    processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="employee_document_bulk_uploads")
    error_log = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.document_type} - {self.created_at:%Y-%m-%d}"


class EmployeeDocumentHistory(TimeStampedModel):
    document = models.ForeignKey(EmployeeDocument, on_delete=models.CASCADE, related_name="history")
    action = models.CharField(max_length=40)
    previous_status = models.CharField(max_length=16, blank=True)
    new_status = models.CharField(max_length=16)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="employee_document_history")
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.document} - {self.action}"


class AttendanceRecomputeTransaction(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPLIED = "applied", "Applied"
        REJECTED = "rejected", "Rejected"
        FAILED = "failed", "Failed"

    recompute = models.ForeignKey(AttendanceRecompute, on_delete=models.CASCADE, related_name="transactions")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="attendance_recompute_transactions")
    attendance_date = models.DateField()
    previous_values = models.JSONField(default=dict, blank=True)
    proposed_values = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["attendance_date", "employee__name"]
        constraints = [models.UniqueConstraint(fields=["recompute", "employee", "attendance_date"], name="unique_recompute_employee_date")]

    def __str__(self):
        return f"{self.employee} - {self.attendance_date}"


class LeaveBulkOperation(TimeStampedModel):
    class Operation(models.TextChoices):
        ALLOCATE = "allocate", "Generate Allocation"
        IMPORT = "import", "Bulk Import"
        ADJUST = "adjust", "Balance Adjustment"

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="leave_bulk_operations")
    operation = models.CharField(max_length=16, choices=Operation.choices)
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, blank=True, null=True, related_name="bulk_operations")
    period_start = models.DateField()
    period_end = models.DateField()
    employee_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="leave_bulk_operations")
    source_file_url = models.URLField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        if self.period_end < self.period_start:
            raise ValidationError({"period_end": "Period end must be on or after period start."})

    def __str__(self):
        return f"{self.get_operation_display()} - {self.period_start}"


class PayrollDisbursement(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        GENERATED = "generated", "Bank File Generated"
        SUBMITTED = "submitted", "Submitted"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name="disbursements")
    reference = models.CharField(max_length=80, unique=True)
    disbursement_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    bank_file_url = models.URLField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    response_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-disbursement_date"]

    def __str__(self):
        return self.reference


class PayrollAccountingEvent(TimeStampedModel):
    class EventType(models.TextChoices):
        ACCRUAL = "accrual", "Payroll Accrual"
        PAYMENT = "payment", "Payroll Payment"
        REVERSAL = "reversal", "Reversal"

    payroll_run = models.ForeignKey(PayrollRun, on_delete=models.PROTECT, related_name="accounting_events")
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    reference = models.CharField(max_length=80)
    event_date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    journal_reference = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=16, default="pending")
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-event_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["payroll_run", "event_type", "reference"], name="unique_payroll_accounting_event")]

    def __str__(self):
        return self.reference


class EmployeeLoanOffer(TimeStampedModel):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="loan_offers")
    loan_type = models.ForeignKey(LoanType, on_delete=models.PROTECT, related_name="offers")
    offered_amount = models.DecimalField(max_digits=18, decimal_places=2)
    interest_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    term_months = models.PositiveIntegerField(default=12)
    offered_date = models.DateField(default=timezone.localdate)
    expiry_date = models.DateField()
    accepted = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-offered_date"]

    def clean(self):
        if self.expiry_date < self.offered_date:
            raise ValidationError({"expiry_date": "Expiry date must be on or after offered date."})

    def __str__(self):
        return f"{self.employee} - {self.loan_type}"


class CareerTransitionChangeHistory(TimeStampedModel):
    request = models.ForeignKey(CareerTransitionRequest, on_delete=models.CASCADE, related_name="change_history")
    field_name = models.CharField(max_length=80)
    previous_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    effective_date = models.DateField(default=timezone.localdate)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="career_transition_changes")
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-effective_date", "-id"]

    def __str__(self):
        return f"{self.request} - {self.field_name}"


class TrainingCategory(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="training_categories")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_training_category_code")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class FaceRecognitionProfile(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ENROLLED = "enrolled", "Enrolled"
        SUSPENDED = "suspended", "Suspended"
        REVOKED = "revoked", "Revoked"

    employee = models.OneToOneField(Employee, on_delete=models.PROTECT, related_name="face_recognition_profile")
    provider = models.CharField(max_length=80, blank=True)
    external_reference = models.CharField(max_length=120, blank=True)
    consent_at = models.DateTimeField(blank=True, null=True)
    enrolled_at = models.DateTimeField(blank=True, null=True)
    last_verified_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["employee__name"]

    def __str__(self):
        return str(self.employee)


class ExpenseProductCategory(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="expense_product_categories")
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=120)
    default_limit = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    receipt_required = models.BooleanField(default=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="unique_expense_product_category")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class HRWorkflowAudit(TimeStampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="hr_workflow_audits")
    entity = models.CharField(max_length=80)
    record_id = models.PositiveBigIntegerField()
    action = models.CharField(max_length=60)
    previous_status = models.CharField(max_length=40, blank=True)
    new_status = models.CharField(max_length=40, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="hr_workflow_audits")
    occurred_at = models.DateTimeField(default=timezone.now)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["company", "entity", "record_id"], name="hr_audit_record_idx")]

    def __str__(self):
        return f"{self.entity}#{self.record_id} - {self.action}"
