from decimal import Decimal
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
    number = models.CharField(max_length=40, unique=True)
    contract_type = models.CharField(max_length=20, choices=ContractType.choices)
    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    base_salary = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-start_date", "number"]

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
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="documents")
    document_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT, related_name="employee_documents")
    document_number = models.CharField(max_length=80, blank=True)
    issued_date = models.DateField(blank=True, null=True)
    expiry_date = models.DateField(blank=True, null=True)
    attachment_url = models.URLField(blank=True)
    verified = models.BooleanField(default=False)
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

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.date_end and self.date_end < self.date_start:
            raise ValidationError({"date_end": "End date must be on or after start date."})

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
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="career_transition_requests")
    number = models.CharField(max_length=40, unique=True)
    transition_type = models.ForeignKey(CareerTransitionType, on_delete=models.PROTECT, related_name="requests")
    effective_date = models.DateField()
    current_position = models.CharField(max_length=120, blank=True)
    new_position = models.CharField(max_length=120, blank=True)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="career_transition_approvals")

    class Meta:
        ordering = ["-effective_date", "number"]

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
    effective_date = models.DateField(default=timezone.localdate)
    basic_salary = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    allowance_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    deduction_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT)
    approver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True, related_name="compensation_approvals")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-effective_date", "employee__name"]

    @property
    def total_compensation(self):
        return self.basic_salary + self.allowance_amount - self.deduction_amount

    def __str__(self):
        return f"{self.employee} - {self.effective_date}"
