from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Prefetch, Q, Sum
from django.forms import inlineformset_factory, modelform_factory
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .forms import (
    AttendanceForm,
    CompanyForm,
    ContactForm,
    EmployeeForm,
    InvoiceForm,
    ProductForm,
    PurchaseOrderForm,
    SaleOrderDocumentForm,
    SaleOrderForm,
    SaleOrderLineForm,
    StockMoveForm,
    WarehouseForm,
    StyledModelForm,
)
from .models import (
    Attendance,
    Company,
    Contact,
    Employee,
    Invoice,
    InvoiceLine,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    SaleOrder,
    SaleOrderLine,
    StockMove,
    Warehouse,
)
from modules.ai_workspace.models import AIAgent, AIConversation, AIMessage, AITask, AIWorkspace
from modules.crm.models import Lead, Opportunity
from modules.education.models import BookLoan, Course, Enrollment, Lesson, LibraryBook, School, Student
from modules.finance.models import Account, Budget, ExpenseClaim, JournalEntry, JournalLine, Loan
from modules.hr.models import (
    Appraisal,
    BusinessUnit,
    Candidate,
    CareerTransitionLetter,
    CareerTransitionRequest,
    CareerTransitionType,
    CashAdvance,
    CompanyHoliday,
    Department,
    DocumentType,
    EmployeeChangeRequest,
    EmployeeChecklist,
    EmployeeDisciplinary,
    EmployeeDocument,
    EmployeeGrade,
    EmployeeOffboarding,
    EmployeeOnboarding,
    ExpenseProduct,
    EmploymentContract,
    HRYear,
    HRSetting,
    JobPosition,
    LeaveAllocation,
    LeaveRequest,
    LeaveType,
    LoanPolicy,
    LoanType,
    OvertimeRequest,
    OvertimeRule,
    PayrollRun,
    PayrollPeriod,
    Payslip,
    RawAttendance,
    CompensationBenefit,
    SalaryComponent,
    ScheduleAssignment,
    Shift,
    Skill,
    TrainingConduct,
    TrainingCourse,
    TrainingProgram,
    TrainingTrainer,
    TravelPackage,
    TravelRequest,
    WorkLocation,
    WorkSchedule,
)
from modules.localization.models import DocumentSequence, LocalizationPack, PayrollParameter, TaxRule
from modules.operations.models import (
    Asset,
    BillOfMaterial,
    BillOfMaterialLine,
    ConstructionProject,
    MaintenanceRequest,
    ManufacturingOrder,
    Project,
    ProjectTask,
    QualityInspection,
    WorkPackage,
)
from modules.pos.models import KitchenTicket, POSOrder, POSOrderLine, POSRegister, POSSession, RestaurantTable
from modules.apphub.models import ERPApp
from modules.apphub.workspaces import ENTITY_APP_MAP
from modules.hr.access import can_hr_entity_action, can_hr_workflow_action, scope_hr_form, scope_hr_queryset
from modules.hr.training_views import training_conduct_form, training_conduct_list
from .rental_views import rental_detail_list
from .document_pdf import REPORT_CONFIG, render_document_pdf


APPROVAL_WORKFLOW = {
    "submit": {"label": "Submit", "from": ["draft"], "to": "submitted", "primary": True},
    "approve": {"label": "Approve", "from": ["submitted"], "to": "approved", "primary": True},
    "reject": {"label": "Reject", "from": ["submitted"], "to": "rejected"},
    "cancel": {"label": "Cancel", "from": ["draft", "submitted"], "to": "cancelled"},
    "reset": {"label": "Reset to Draft", "from": ["rejected", "cancelled"], "to": "draft"},
}

PAYROLL_RUN_WORKFLOW = {
    "calculate": {"label": "Start Calculation", "from": ["draft"], "to": "calculating", "primary": True},
    "mark-ready": {"label": "Mark Ready", "from": ["calculating"], "to": "ready", "primary": True},
    "post": {"label": "Post Payroll", "from": ["ready"], "to": "posted", "primary": True},
    "cancel": {"label": "Cancel", "from": ["draft", "calculating", "ready"], "to": "cancelled"},
    "reset": {"label": "Reset to Draft", "from": ["cancelled"], "to": "draft"},
}

TRAINING_WORKFLOW = {
    "open": {"label": "Open", "from": ["draft"], "to": "open", "primary": True},
    "set-upcoming": {"label": "Set Upcoming", "from": ["open"], "to": "upcoming", "primary": True},
    "start": {"label": "Start", "from": ["upcoming"], "to": "on_going", "primary": True},
    "cancel": {"label": "Cancel", "from": ["draft", "open", "upcoming", "on_going"], "to": "cancelled"},
}


ENTITY_CONFIG = {
    "companies": {
        "model": Company,
        "form": CompanyForm,
        "title": "Companies",
        "singular": "Company",
        "columns": [("code", "Code"), ("name", "Company"), ("currency", "Currency"), ("active", "Active")],
        "search": ["code", "name", "email"],
    },
    "contacts": {
        "model": Contact,
        "form": ContactForm,
        "title": "Partners",
        "singular": "Partner",
        "columns": [("code", "Code"), ("name", "Partner"), ("contact_type", "Type"), ("company", "Company"), ("active", "Active")],
        "search": ["code", "name", "email", "phone"],
    },
    "products": {
        "model": Product,
        "form": ProductForm,
        "title": "Products",
        "singular": "Product",
        "columns": [("sku", "SKU"), ("name", "Product"), ("product_type", "Type"), ("sale_price", "Sale Price"), ("current_stock", "Stock")],
        "search": ["sku", "name"],
    },
    "warehouses": {
        "model": Warehouse,
        "form": WarehouseForm,
        "title": "Warehouses",
        "singular": "Warehouse",
        "columns": [("code", "Code"), ("name", "Warehouse"), ("company", "Company"), ("active", "Active")],
        "search": ["code", "name"],
    },
    "stock-moves": {
        "model": StockMove,
        "form": StockMoveForm,
        "title": "Stock Ledger",
        "singular": "Stock Movement",
        "columns": [("movement_date", "Date"), ("product", "Product"), ("warehouse", "Warehouse"), ("direction", "Direction"), ("quantity", "Quantity"), ("reference", "Reference")],
        "search": ["product__sku", "product__name", "reference"],
        "editable": False,
        "deletable": False,
    },
    "sales": {
        "model": SaleOrder,
        "form": SaleOrderForm,
        "title": "Sales Orders",
        "singular": "Sales Order",
        "columns": [("number", "Number"), ("order_date", "Date"), ("customer", "Customer"), ("status", "Status"), ("total", "Total")],
        "search": ["number", "customer__name"],
        "admin_detail": True,
    },
    "rental-details": {
        "model": SaleOrderLine,
        "title": "Detail Sewa",
        "singular": "Rental Detail",
        "columns": [("order", "Sales Order"), ("description", "Description"), ("quantity", "Qty"), ("rental_start_date", "Tanggal Sewa"), ("rental_return_date", "Tanggal Kembali"), ("rental_status", "Status Barang")],
        "search": ["order__number", "order__customer__name", "description", "product__name"],
        "editable": False,
        "deletable": False,
    },
    "purchases": {
        "model": PurchaseOrder,
        "form": PurchaseOrderForm,
        "title": "Purchase Orders",
        "singular": "Purchase Order",
        "columns": [("number", "Number"), ("order_date", "Date"), ("vendor", "Vendor"), ("status", "Status"), ("total", "Total")],
        "search": ["number", "vendor__name"],
        "admin_detail": True,
    },
    "invoices": {
        "model": Invoice,
        "form": InvoiceForm,
        "title": "Invoices & Bills",
        "singular": "Invoice",
        "columns": [("number", "Number"), ("invoice_date", "Date"), ("partner", "Partner"), ("invoice_type", "Type"), ("status", "Status"), ("total", "Total")],
        "search": ["number", "partner__name"],
        "admin_detail": True,
    },
    "employees": {
        "model": Employee,
        "form": EmployeeForm,
        "title": "Employees",
        "singular": "Employee",
        "columns": [("employee_id", "Employee ID"), ("name", "Name"), ("department", "Department"), ("position", "Position"), ("status", "Status")],
        "search": ["employee_id", "name", "department", "position"],
    },
    "attendance": {
        "model": Attendance,
        "form": AttendanceForm,
        "title": "Attendance",
        "singular": "Attendance",
        "columns": [("attendance_date", "Date"), ("employee", "Employee"), ("check_in", "Check In"), ("check_out", "Check Out"), ("status", "Status")],
        "search": ["employee__employee_id", "employee__name"],
    },
    # Human Resources
    "departments": {
        "model": Department,
        "title": "Departments",
        "singular": "Department",
        "columns": [("code", "Code"), ("name", "Department"), ("company", "Company"), ("manager", "Manager"), ("active", "Active")],
        "search": ["code", "name"],
        "form_fields": ["company", "code", "name", "manager", "active"],
        "select_related": ["company", "manager"],
    },
    "contracts": {
        "model": EmploymentContract,
        "title": "Employment Contracts",
        "singular": "Employment Contract",
        "columns": [("number", "Number"), ("employee", "Employee"), ("contract_type", "Type"), ("start_date", "Start"), ("end_date", "End"), ("status", "Status")],
        "search": ["number", "employee__employee_id", "employee__name"],
        "form_fields": ["employee", "number", "contract_type", "start_date", "end_date", "base_salary", "status", "notes"],
        "select_related": ["employee"],
    },
    "leave-requests": {
        "model": LeaveRequest,
        "title": "Leave Requests",
        "singular": "Leave Request",
        "columns": [("employee", "Employee"), ("leave_type", "Type"), ("start_date", "Start"), ("end_date", "End"), ("days", "Days"), ("status", "Status")],
        "search": ["employee__employee_id", "employee__name", "leave_type"],
        "form_fields": ["employee", "leave_type", "start_date", "end_date", "days", "reason"],
        "select_related": ["employee", "approver"],
        "workflow": APPROVAL_WORKFLOW,
    },
    "overtime": {
        "model": OvertimeRequest,
        "title": "Overtime Requests",
        "singular": "Overtime Request",
        "columns": [("employee", "Employee"), ("overtime_date", "Date"), ("hours", "Hours"), ("status", "Status"), ("approver", "Approver")],
        "search": ["employee__employee_id", "employee__name", "reason"],
        "form_fields": ["employee", "overtime_date", "hours", "reason"],
        "select_related": ["employee", "approver"],
        "workflow": APPROVAL_WORKFLOW,
    },
    "payslips": {
        "model": Payslip,
        "title": "Payslips",
        "singular": "Payslip",
        "columns": [("number", "Number"), ("employee", "Employee"), ("period_start", "Period Start"), ("period_end", "Period End"), ("net_salary", "Net Salary"), ("status", "Status")],
        "search": ["number", "employee__employee_id", "employee__name"],
        "form_fields": ["employee", "number", "period_start", "period_end", "basic_salary", "allowances", "deductions", "status"],
        "select_related": ["employee"],
    },
    "candidates": {
        "model": Candidate,
        "title": "Recruitment Candidates",
        "singular": "Candidate",
        "columns": [("name", "Candidate"), ("job_title", "Job"), ("company", "Company"), ("email", "Email"), ("source", "Source"), ("status", "Status")],
        "search": ["name", "job_title", "email", "source"],
        "form_fields": ["company", "job_title", "name", "email", "phone", "source", "status", "notes"],
        "select_related": ["company"],
    },
    "appraisals": {
        "model": Appraisal,
        "title": "Employee Appraisals",
        "singular": "Appraisal",
        "columns": [("employee", "Employee"), ("review_date", "Review Date"), ("reviewer", "Reviewer"), ("score", "Score"), ("status", "Status")],
        "search": ["employee__employee_id", "employee__name", "reviewer__username"],
        "form_fields": ["employee", "review_date", "reviewer", "score", "status", "strengths", "development_plan"],
        "select_related": ["employee", "reviewer"],
    },
    "business-units": {
        "model": BusinessUnit,
        "title": "Business Units",
        "singular": "Business Unit",
        "columns": [("code", "Code"), ("name", "Business Unit"), ("company", "Company"), ("manager", "Manager"), ("active", "Active")],
        "search": ["code", "name", "manager__name"],
        "form_fields": ["company", "code", "name", "manager", "active"],
        "select_related": ["company", "manager"],
    },
    "work-locations": {
        "model": WorkLocation,
        "title": "Work Locations",
        "singular": "Work Location",
        "columns": [("code", "Code"), ("name", "Location"), ("company", "Company"), ("timezone", "Timezone"), ("active", "Active")],
        "search": ["code", "name", "address", "timezone"],
        "form_fields": ["company", "code", "name", "address", "timezone", "active"],
        "select_related": ["company"],
    },
    "job-positions": {
        "model": JobPosition,
        "title": "Job Positions",
        "singular": "Job Position",
        "columns": [("code", "Code"), ("name", "Position"), ("department", "Department"), ("planned_headcount", "Headcount"), ("active", "Active")],
        "search": ["code", "name", "department__name"],
        "form_fields": ["company", "department", "code", "name", "planned_headcount", "description", "active"],
        "select_related": ["company", "department"],
    },
    "employee-change-requests": {
        "model": EmployeeChangeRequest,
        "title": "Employee Change Requests",
        "singular": "Employee Change Request",
        "columns": [("employee", "Employee"), ("field_name", "Field"), ("effective_date", "Effective Date"), ("status", "Status"), ("approver", "Approver")],
        "search": ["employee__employee_id", "employee__name", "field_name", "requested_value"],
        "form_fields": ["employee", "field_name", "current_value", "requested_value", "effective_date", "reason", "approver"],
        "select_related": ["employee", "approver"],
        "workflow": APPROVAL_WORKFLOW,
    },
    "employee-documents": {
        "model": EmployeeDocument,
        "title": "Employee Documents",
        "singular": "Employee Document",
        "columns": [("employee", "Employee"), ("document_type", "Document Type"), ("document_number", "Number"), ("expiry_date", "Expiry"), ("verified", "Verified")],
        "search": ["employee__employee_id", "employee__name", "document_type__name", "document_number"],
        "form_fields": ["employee", "document_type", "document_number", "issued_date", "expiry_date", "attachment_url", "verified", "notes"],
        "select_related": ["employee", "document_type"],
    },
    "employee-disciplinary": {
        "model": EmployeeDisciplinary,
        "title": "Employee Disciplinary",
        "singular": "Disciplinary Case",
        "columns": [("reference", "Reference"), ("employee", "Employee"), ("incident_date", "Incident"), ("violation", "Violation"), ("status", "Status")],
        "search": ["reference", "employee__employee_id", "employee__name", "violation"],
        "form_fields": ["company", "employee", "reference", "incident_date", "violation", "action_taken", "status", "notes"],
        "select_related": ["company", "employee"],
    },
    "employee-checklists": {
        "model": EmployeeChecklist,
        "title": "Employee Checklists",
        "singular": "Employee Checklist",
        "columns": [("name", "Checklist"), ("lifecycle", "Lifecycle"), ("responsible_role", "Responsible Role"), ("sequence", "Sequence"), ("active", "Active")],
        "search": ["name", "description", "responsible_role"],
        "form_fields": ["company", "name", "lifecycle", "description", "responsible_role", "sequence", "active"],
        "select_related": ["company"],
    },
    "employee-onboarding": {
        "model": EmployeeOnboarding,
        "title": "Employee Onboarding",
        "singular": "Onboarding Process",
        "columns": [("employee", "Employee"), ("start_date", "Start Date"), ("owner", "Owner"), ("progress", "Progress %"), ("status", "Status")],
        "search": ["employee__employee_id", "employee__name", "owner__username"],
        "form_fields": ["employee", "start_date", "owner", "progress", "notes"],
        "select_related": ["employee", "owner"],
        "workflow": APPROVAL_WORKFLOW,
    },
    "employee-offboarding": {
        "model": EmployeeOffboarding,
        "title": "Employee Offboarding",
        "singular": "Offboarding Process",
        "columns": [("employee", "Employee"), ("last_working_date", "Last Working Date"), ("owner", "Owner"), ("progress", "Progress %"), ("status", "Status")],
        "search": ["employee__employee_id", "employee__name", "owner__username", "reason"],
        "form_fields": ["employee", "last_working_date", "owner", "reason", "progress", "notes"],
        "select_related": ["employee", "owner"],
        "workflow": APPROVAL_WORKFLOW,
    },
    "raw-attendance": {
        "model": RawAttendance,
        "title": "Raw Attendance",
        "singular": "Raw Attendance Event",
        "columns": [("captured_at", "Captured At"), ("employee", "Employee"), ("event_type", "Event"), ("source", "Source"), ("processed", "Processed")],
        "search": ["employee__employee_id", "employee__name", "source", "device_identifier"],
        "form_fields": ["employee", "captured_at", "event_type", "source", "device_identifier", "processed", "note"],
        "select_related": ["employee"],
    },
    "shifts": {
        "model": Shift,
        "title": "Shift Master",
        "singular": "Shift",
        "columns": [("code", "Code"), ("name", "Shift"), ("start_time", "Start"), ("end_time", "End"), ("break_minutes", "Break Minutes"), ("active", "Active")],
        "search": ["code", "name"],
        "form_fields": ["company", "code", "name", "start_time", "end_time", "break_minutes", "active"],
        "select_related": ["company"],
    },
    "work-schedules": {
        "model": WorkSchedule,
        "title": "Work Schedules",
        "singular": "Work Schedule",
        "columns": [("code", "Code"), ("name", "Schedule"), ("shift", "Shift"), ("working_days", "Working Days"), ("hours_per_day", "Hours/Day"), ("active", "Active")],
        "search": ["code", "name", "shift__name", "working_days"],
        "form_fields": ["company", "code", "name", "shift", "working_days", "hours_per_day", "active"],
        "select_related": ["company", "shift"],
    },
    "schedule-assignments": {
        "model": ScheduleAssignment,
        "title": "Schedule Assignments",
        "singular": "Schedule Assignment",
        "columns": [("employee", "Employee"), ("schedule", "Schedule"), ("date_start", "Start"), ("date_end", "End"), ("active", "Active")],
        "search": ["employee__employee_id", "employee__name", "schedule__name"],
        "form_fields": ["employee", "schedule", "date_start", "date_end", "active"],
        "select_related": ["employee", "schedule"],
    },
    "leave-types": {
        "model": LeaveType,
        "title": "Leave Types",
        "singular": "Leave Type",
        "columns": [("code", "Code"), ("name", "Leave Type"), ("annual_entitlement", "Annual Entitlement"), ("paid", "Paid"), ("active", "Active")],
        "search": ["code", "name"],
        "form_fields": ["company", "code", "name", "annual_entitlement", "paid", "requires_attachment", "active"],
        "select_related": ["company"],
    },
    "leave-allocations": {
        "model": LeaveAllocation,
        "title": "Leave Allocations",
        "singular": "Leave Allocation",
        "columns": [("employee", "Employee"), ("leave_type", "Leave Type"), ("year", "Year"), ("allocated_days", "Allocated"), ("used_days", "Used"), ("balance_days", "Balance"), ("status", "Status")],
        "search": ["employee__employee_id", "employee__name", "leave_type__name"],
        "form_fields": ["employee", "leave_type", "year", "allocated_days", "used_days"],
        "select_related": ["employee", "leave_type"],
        "workflow": APPROVAL_WORKFLOW,
    },
    "overtime-rules": {
        "model": OvertimeRule,
        "title": "Overtime Rules",
        "singular": "Overtime Rule",
        "columns": [("name", "Rule"), ("company", "Company"), ("weekday_multiplier", "Weekday"), ("weekend_multiplier", "Weekend"), ("maximum_hours", "Max Hours"), ("active", "Active")],
        "search": ["name"],
        "form_fields": ["company", "name", "weekday_multiplier", "weekend_multiplier", "maximum_hours", "minimum_minutes", "active"],
        "select_related": ["company"],
    },
    "payroll-periods": {
        "model": PayrollPeriod,
        "title": "Payslip Periods",
        "singular": "Payroll Period",
        "columns": [("name", "Period"), ("company", "Company"), ("date_start", "Start"), ("date_end", "End"), ("cutoff_date", "Cutoff"), ("status", "Status")],
        "search": ["name"],
        "form_fields": ["company", "name", "date_start", "date_end", "cutoff_date", "status"],
        "select_related": ["company"],
    },
    "salary-components": {
        "model": SalaryComponent,
        "title": "Salary Components",
        "singular": "Salary Component",
        "columns": [("code", "Code"), ("name", "Component"), ("component_type", "Type"), ("default_amount", "Default Amount"), ("taxable", "Taxable"), ("active", "Active")],
        "search": ["code", "name"],
        "form_fields": ["company", "code", "name", "component_type", "default_amount", "taxable", "active"],
        "select_related": ["company"],
    },
    "travel-requests": {
        "model": TravelRequest,
        "title": "Travel Requests",
        "singular": "Travel Request",
        "columns": [("number", "Number"), ("employee", "Employee"), ("destination", "Destination"), ("date_start", "Start"), ("estimated_amount", "Estimated Amount"), ("status", "Status")],
        "search": ["number", "employee__employee_id", "employee__name", "destination", "purpose"],
        "form_fields": ["employee", "number", "destination", "date_start", "date_end", "purpose", "estimated_amount", "approver"],
        "select_related": ["employee", "approver"],
        "workflow": APPROVAL_WORKFLOW,
    },
    "training-programs": {
        "model": TrainingProgram,
        "title": "Training Programs",
        "singular": "Training Program",
        "columns": [("code", "Code"), ("name", "Program"), ("trainer", "Trainer"), ("date_start", "Start"), ("capacity", "Capacity"), ("status", "Status")],
        "search": ["code", "name", "trainer", "description"],
        "form_fields": ["company", "code", "name", "trainer", "date_start", "date_end", "capacity", "status", "description"],
        "select_related": ["company"],
    },
    "cash-advances": {
        "model": CashAdvance,
        "title": "Cash Advances",
        "singular": "Cash Advance",
        "columns": [("number", "Number"), ("employee", "Employee"), ("request_date", "Request Date"), ("amount", "Amount"), ("settlement_due_date", "Settlement Due"), ("status", "Status")],
        "search": ["number", "employee__employee_id", "employee__name", "purpose"],
        "form_fields": ["employee", "number", "request_date", "amount", "purpose", "settlement_due_date", "approver"],
        "select_related": ["employee", "approver"],
        "workflow": APPROVAL_WORKFLOW,
    },
    "hr-years": {
        "model": HRYear,
        "title": "HR Years",
        "singular": "HR Year",
        "columns": [("name", "HR Year"), ("company", "Company"), ("date_start", "Start"), ("date_end", "End"), ("active", "Active")],
        "search": ["name"],
        "form_fields": ["company", "name", "date_start", "date_end", "active"],
        "select_related": ["company"],
    },
    "company-holidays": {
        "model": CompanyHoliday,
        "title": "Company Public Holidays",
        "singular": "Company Holiday",
        "columns": [("holiday_date", "Date"), ("name", "Holiday"), ("company", "Company"), ("recurring", "Recurring"), ("paid", "Paid")],
        "search": ["name"],
        "form_fields": ["company", "name", "holiday_date", "recurring", "paid"],
        "select_related": ["company"],
    },
    "employee-grades": {
        "model": EmployeeGrade,
        "title": "Employee Grades",
        "singular": "Employee Grade",
        "columns": [("code", "Code"), ("name", "Grade"), ("company", "Company"), ("rank", "Rank"), ("active", "Active")],
        "search": ["code", "name"],
        "form_fields": ["company", "code", "name", "rank", "active"],
        "select_related": ["company"],
    },
    "skills": {
        "model": Skill,
        "title": "Skills",
        "singular": "Skill",
        "columns": [("name", "Skill"), ("category", "Category"), ("company", "Company"), ("active", "Active")],
        "search": ["name", "category", "description"],
        "form_fields": ["company", "name", "category", "description", "active"],
        "select_related": ["company"],
    },
    "document-types": {
        "model": DocumentType,
        "title": "Document Types",
        "singular": "Document Type",
        "columns": [("code", "Code"), ("name", "Document Type"), ("mandatory", "Mandatory"), ("has_expiry", "Has Expiry"), ("active", "Active")],
        "search": ["code", "name"],
        "form_fields": ["company", "code", "name", "mandatory", "has_expiry", "active"],
        "select_related": ["company"],
    },
    "hr-settings": {
        "model": HRSetting,
        "title": "HR Settings",
        "singular": "HR Settings",
        "columns": [("company", "Company"), ("standard_hours_per_day", "Hours/Day"), ("attendance_tolerance_minutes", "Tolerance Minutes"), ("leave_approval_levels", "Leave Levels"), ("payroll_currency", "Currency")],
        "search": ["company__name", "payroll_currency"],
        "form_fields": ["company", "standard_hours_per_day", "attendance_tolerance_minutes", "leave_approval_levels", "overtime_approval_required", "payroll_currency"],
        "select_related": ["company"],
    },
    "loan-types": {
        "model": LoanType,
        "title": "Loan Types",
        "singular": "Loan Type",
        "columns": [("code", "Code"), ("name", "Loan Type"), ("interest_method", "Interest Method"), ("default_interest_rate", "Rate"), ("maximum_term_months", "Max Term"), ("active", "Active")],
        "search": ["code", "name"],
        "form_fields": ["company", "code", "name", "interest_method", "default_interest_rate", "maximum_term_months", "active"],
        "select_related": ["company"],
    },
    "loan-policies": {
        "model": LoanPolicy,
        "title": "Loan Policies",
        "singular": "Loan Policy",
        "columns": [("name", "Policy"), ("loan_type", "Loan Type"), ("minimum_service_months", "Min Service"), ("maximum_amount", "Maximum Amount"), ("maximum_installment_ratio", "Installment %"), ("active", "Active")],
        "search": ["name", "loan_type__name"],
        "form_fields": ["company", "loan_type", "name", "minimum_service_months", "maximum_amount", "maximum_installment_ratio", "active"],
        "select_related": ["company", "loan_type"],
    },
    "career-transition-types": {
        "model": CareerTransitionType,
        "title": "Career Transition Types",
        "singular": "Career Transition Type",
        "columns": [("code", "Code"), ("name", "Transition Type"), ("category", "Category"), ("requires_clearance", "Clearance"), ("active", "Active")],
        "search": ["code", "name", "category"],
        "form_fields": ["company", "code", "name", "category", "requires_clearance", "active"],
        "select_related": ["company"],
    },
    "career-transition-letters": {
        "model": CareerTransitionLetter,
        "title": "Career Transition Letters",
        "singular": "Career Transition Letter",
        "columns": [("code", "Code"), ("name", "Letter"), ("subject", "Subject"), ("company", "Company"), ("active", "Active")],
        "search": ["code", "name", "subject"],
        "form_fields": ["company", "code", "name", "subject", "template_body", "active"],
        "select_related": ["company"],
    },
    "career-transition-requests": {
        "model": CareerTransitionRequest,
        "title": "Career Transition Requests",
        "singular": "Career Transition Request",
        "columns": [("number", "Number"), ("employee", "Employee"), ("transition_type", "Type"), ("effective_date", "Effective Date"), ("new_position", "New Position"), ("status", "Status")],
        "search": ["number", "employee__employee_id", "employee__name", "transition_type__name", "new_position"],
        "form_fields": ["employee", "number", "transition_type", "effective_date", "current_position", "new_position", "reason", "approver"],
        "select_related": ["employee", "transition_type", "approver"],
        "workflow": APPROVAL_WORKFLOW,
    },
    "travel-packages": {
        "model": TravelPackage,
        "title": "Travel Packages",
        "singular": "Travel Package",
        "columns": [("name", "Package"), ("destination_group", "Destination Group"), ("daily_allowance", "Daily Allowance"), ("lodging_limit", "Lodging"), ("transport_limit", "Transport"), ("active", "Active")],
        "search": ["name", "destination_group"],
        "form_fields": ["company", "name", "destination_group", "daily_allowance", "lodging_limit", "transport_limit", "active"],
        "select_related": ["company"],
    },
    "training-courses": {
        "model": TrainingCourse,
        "title": "Training Courses",
        "singular": "Training Course",
        "columns": [("code", "Code"), ("name", "Course"), ("category", "Category"), ("duration_hours", "Duration Hours"), ("active", "Active")],
        "search": ["code", "name", "category"],
        "form_fields": ["company", "code", "name", "category", "duration_hours", "description", "active"],
        "select_related": ["company"],
    },
    "training-trainers": {
        "model": TrainingTrainer,
        "title": "Training Trainers",
        "singular": "Trainer",
        "columns": [("name", "Trainer"), ("email", "Email"), ("phone", "Phone"), ("external", "External"), ("active", "Active")],
        "search": ["name", "email", "phone", "specialties"],
        "form_fields": ["company", "name", "email", "phone", "specialties", "external", "active"],
        "select_related": ["company"],
    },
    "training-conducts": {
        "model": TrainingConduct,
        "title": "Training Conduct",
        "singular": "Training Conduct",
        "columns": [("reference", "Reference"), ("name", "Conduct Name"), ("training_type", "Type"), ("date_start", "Start"), ("participant_count", "Participants"), ("status", "Status")],
        "search": ["reference", "name", "course__code", "course__name", "program__code", "program__name", "pic__name", "location"],
        "select_related": ["company", "course", "program", "pic"],
        "workflow": TRAINING_WORKFLOW,
        "workflow_locked_statuses": {"cancelled"},
    },
    "expense-products": {
        "model": ExpenseProduct,
        "title": "Expense Products",
        "singular": "Expense Product",
        "columns": [("code", "Code"), ("name", "Expense Product"), ("category", "Category"), ("maximum_amount", "Maximum Amount"), ("receipt_required", "Receipt"), ("active", "Active")],
        "search": ["code", "name", "category"],
        "form_fields": ["company", "code", "name", "category", "maximum_amount", "receipt_required", "active"],
        "select_related": ["company"],
    },
    "payroll-runs": {
        "model": PayrollRun,
        "title": "Payroll Runs",
        "singular": "Payroll Run",
        "columns": [("number", "Number"), ("period", "Period"), ("company", "Company"), ("employee_count", "Employees"), ("net_amount", "Net Amount"), ("status", "Status")],
        "search": ["number", "period__name", "company__name"],
        "form_fields": ["company", "period", "number", "employee_count", "gross_amount", "deduction_amount", "net_amount", "notes"],
        "select_related": ["company", "period"],
        "workflow": PAYROLL_RUN_WORKFLOW,
        "workflow_locked_statuses": {"posted", "cancelled"},
    },
    "compensation-benefits": {
        "model": CompensationBenefit,
        "title": "Compensation & Benefits",
        "singular": "Compensation & Benefit",
        "columns": [("employee", "Employee"), ("effective_date", "Effective Date"), ("basic_salary", "Basic Salary"), ("allowance_amount", "Allowance"), ("deduction_amount", "Deduction"), ("total_compensation", "Total"), ("status", "Status")],
        "search": ["employee__employee_id", "employee__name"],
        "form_fields": ["employee", "effective_date", "basic_salary", "allowance_amount", "deduction_amount", "approver", "notes"],
        "select_related": ["employee", "approver"],
        "workflow": APPROVAL_WORKFLOW,
    },
    # Finance
    "accounts": {
        "model": Account,
        "title": "Chart of Accounts",
        "singular": "Account",
        "columns": [("code", "Code"), ("name", "Account"), ("account_type", "Type"), ("company", "Company"), ("active", "Active")],
        "search": ["code", "name"],
        "form_fields": ["company", "code", "name", "account_type", "parent", "active"],
        "select_related": ["company", "parent"],
    },
    "journal-entries": {
        "model": JournalEntry,
        "title": "Journal Entries",
        "singular": "Journal Entry",
        "columns": [("number", "Number"), ("entry_date", "Date"), ("company", "Company"), ("reference", "Reference"), ("status", "Status"), ("total_debit", "Debit"), ("total_credit", "Credit")],
        "search": ["number", "reference", "description"],
        "form_fields": ["company", "number", "entry_date", "reference", "description", "status"],
        "select_related": ["company"],
        "admin_detail": True,
    },
    "budgets": {
        "model": Budget,
        "title": "Budgets",
        "singular": "Budget",
        "columns": [("name", "Budget"), ("company", "Company"), ("start_date", "Start"), ("end_date", "End"), ("amount", "Amount"), ("status", "Status")],
        "search": ["name"],
        "form_fields": ["company", "name", "start_date", "end_date", "amount", "committed", "actual", "status"],
        "select_related": ["company"],
    },
    "expenses": {
        "model": ExpenseClaim,
        "title": "Expense Claims",
        "singular": "Expense Claim",
        "columns": [("reference", "Reference"), ("claim_date", "Date"), ("employee", "Employee"), ("category", "Category"), ("amount", "Amount"), ("status", "Status")],
        "search": ["reference", "employee__employee_id", "employee__name", "category"],
        "form_fields": ["company", "employee", "reference", "claim_date", "category", "amount", "description", "status"],
        "select_related": ["company", "employee"],
    },
    "loans": {
        "model": Loan,
        "title": "Employee Loans",
        "singular": "Loan",
        "columns": [("number", "Number"), ("employee", "Employee"), ("principal", "Principal"), ("balance", "Balance"), ("term_months", "Term"), ("status", "Status")],
        "search": ["number", "employee__employee_id", "employee__name"],
        "form_fields": ["employee", "number", "principal", "balance", "term_months", "start_date", "status"],
        "select_related": ["employee"],
    },
    # CRM
    "leads": {
        "model": Lead,
        "title": "CRM Leads",
        "singular": "Lead",
        "columns": [("name", "Lead"), ("organization", "Organization"), ("stage", "Stage"), ("assigned_to", "Owner"), ("expected_revenue", "Expected Revenue")],
        "search": ["name", "organization", "email", "phone"],
        "form_fields": ["company", "name", "organization", "email", "phone", "source", "stage", "assigned_to", "expected_revenue", "notes"],
        "select_related": ["company", "assigned_to"],
    },
    "opportunities": {
        "model": Opportunity,
        "title": "Opportunities",
        "singular": "Opportunity",
        "columns": [("name", "Opportunity"), ("stage", "Stage"), ("customer", "Customer"), ("probability", "Probability"), ("value", "Value"), ("expected_close", "Close Date")],
        "search": ["name", "lead__name", "customer__name"],
        "form_fields": ["company", "lead", "customer", "name", "stage", "probability", "value", "expected_close", "owner"],
        "select_related": ["company", "lead", "customer", "owner"],
    },
    # Manufacturing, projects, assets, and construction
    "boms": {
        "model": BillOfMaterial,
        "title": "Bills of Material",
        "singular": "Bill of Material",
        "columns": [("code", "Code"), ("product", "Output Product"), ("output_quantity", "Output Qty"), ("company", "Company"), ("active", "Active")],
        "search": ["code", "product__sku", "product__name"],
        "form_fields": ["company", "code", "product", "output_quantity", "active", "notes"],
        "select_related": ["company", "product"],
        "admin_detail": True,
    },
    "manufacturing-orders": {
        "model": ManufacturingOrder,
        "title": "Manufacturing Orders",
        "singular": "Manufacturing Order",
        "columns": [("number", "Number"), ("bom", "BOM"), ("quantity", "Quantity"), ("warehouse", "Warehouse"), ("planned_start", "Planned Start"), ("status", "Status")],
        "search": ["number", "bom__code", "bom__product__name"],
        "form_fields": ["company", "number", "bom", "quantity", "warehouse", "planned_start", "planned_end", "status", "responsible"],
        "select_related": ["company", "bom", "bom__product", "warehouse", "responsible"],
    },
    "quality-inspections": {
        "model": QualityInspection,
        "title": "Quality Inspections",
        "singular": "Quality Inspection",
        "columns": [("number", "Number"), ("inspection_date", "Date"), ("product", "Product"), ("reference", "Reference"), ("result", "Result"), ("inspector", "Inspector")],
        "search": ["number", "product__sku", "product__name", "reference"],
        "form_fields": ["company", "number", "product", "inspection_date", "reference", "result", "inspector", "notes"],
        "select_related": ["company", "product", "inspector"],
    },
    "projects": {
        "model": Project,
        "title": "Projects",
        "singular": "Project",
        "columns": [("code", "Code"), ("name", "Project"), ("customer", "Customer"), ("manager", "Manager"), ("progress", "Progress %"), ("status", "Status")],
        "search": ["code", "name", "customer__name"],
        "form_fields": ["company", "code", "name", "customer", "manager", "start_date", "end_date", "budget", "progress", "status"],
        "select_related": ["company", "customer", "manager"],
        "admin_detail": True,
    },
    "project-tasks": {
        "model": ProjectTask,
        "title": "Project Tasks",
        "singular": "Project Task",
        "columns": [("title", "Task"), ("project", "Project"), ("assignee", "Assignee"), ("due_date", "Due Date"), ("progress", "Progress %"), ("status", "Status")],
        "search": ["title", "project__code", "project__name", "assignee__username"],
        "form_fields": ["project", "title", "assignee", "due_date", "status", "progress", "description"],
        "select_related": ["project", "assignee"],
    },
    "assets": {
        "model": Asset,
        "title": "Assets",
        "singular": "Asset",
        "columns": [("code", "Code"), ("name", "Asset"), ("company", "Company"), ("assigned_employee", "Assigned To"), ("location", "Location"), ("status", "Status")],
        "search": ["code", "name", "assigned_employee__name", "location"],
        "form_fields": ["company", "code", "name", "product", "purchase_date", "acquisition_value", "assigned_employee", "location", "status"],
        "select_related": ["company", "product", "assigned_employee"],
    },
    "maintenance": {
        "model": MaintenanceRequest,
        "title": "Maintenance Requests",
        "singular": "Maintenance Request",
        "columns": [("number", "Number"), ("asset", "Asset"), ("request_date", "Request Date"), ("scheduled_date", "Scheduled"), ("assigned_to", "Assigned To"), ("status", "Status")],
        "search": ["number", "asset__code", "asset__name", "description"],
        "form_fields": ["asset", "number", "request_date", "scheduled_date", "description", "assigned_to", "estimated_cost", "actual_cost", "status"],
        "select_related": ["asset", "assigned_to"],
    },
    "construction-projects": {
        "model": ConstructionProject,
        "title": "Construction Projects",
        "singular": "Construction Project",
        "columns": [("code", "Code"), ("name", "Project"), ("customer", "Customer"), ("contract_value", "Contract Value"), ("progress", "Progress %"), ("status", "Status")],
        "search": ["code", "name", "customer__name", "site_address"],
        "form_fields": ["company", "code", "name", "customer", "site_address", "contract_value", "start_date", "end_date", "progress", "status"],
        "select_related": ["company", "customer"],
        "admin_detail": True,
    },
    "work-packages": {
        "model": WorkPackage,
        "title": "Construction Work Packages",
        "singular": "Work Package",
        "columns": [("code", "Code"), ("name", "Work Package"), ("construction_project", "Project"), ("budget", "Budget"), ("actual_cost", "Actual"), ("progress", "Progress %"), ("status", "Status")],
        "search": ["code", "name", "construction_project__code", "construction_project__name"],
        "form_fields": ["construction_project", "code", "name", "budget", "actual_cost", "progress", "status"],
        "select_related": ["construction_project"],
    },
    # Education and LMS
    "schools": {
        "model": School,
        "title": "Schools",
        "singular": "School",
        "columns": [("code", "Code"), ("name", "School"), ("company", "Company"), ("email", "Email"), ("active", "Active")],
        "search": ["code", "name", "email"],
        "form_fields": ["company", "code", "name", "address", "email", "phone", "active"],
        "select_related": ["company"],
    },
    "students": {
        "model": Student,
        "title": "Students",
        "singular": "Student",
        "columns": [("student_number", "Student No."), ("name", "Student"), ("school", "School"), ("email", "Email"), ("guardian_name", "Guardian"), ("status", "Status")],
        "search": ["student_number", "name", "email", "guardian_name"],
        "form_fields": ["school", "student_number", "name", "email", "date_of_birth", "guardian_name", "guardian_phone", "status"],
        "select_related": ["school"],
    },
    "courses": {
        "model": Course,
        "title": "Courses",
        "singular": "Course",
        "columns": [("code", "Code"), ("title", "Course"), ("school", "School"), ("instructor", "Instructor"), ("active", "Active")],
        "search": ["code", "title", "description"],
        "form_fields": ["school", "code", "title", "description", "instructor", "active"],
        "select_related": ["school", "instructor"],
        "admin_detail": True,
    },
    "enrollments": {
        "model": Enrollment,
        "title": "Course Enrollments",
        "singular": "Enrollment",
        "columns": [("student", "Student"), ("course", "Course"), ("enrollment_date", "Date"), ("progress", "Progress %"), ("status", "Status")],
        "search": ["student__student_number", "student__name", "course__code", "course__title"],
        "form_fields": ["student", "course", "enrollment_date", "status", "progress"],
        "select_related": ["student", "course"],
    },
    "library-books": {
        "model": LibraryBook,
        "title": "Library Books",
        "singular": "Library Book",
        "columns": [("title", "Title"), ("author", "Author"), ("isbn", "ISBN"), ("school", "School"), ("copies_total", "Total"), ("copies_available", "Available")],
        "search": ["title", "author", "isbn", "category"],
        "form_fields": ["school", "isbn", "title", "author", "category", "copies_total", "copies_available"],
        "select_related": ["school"],
    },
    "book-loans": {
        "model": BookLoan,
        "title": "Book Loans",
        "singular": "Book Loan",
        "columns": [("book", "Book"), ("student", "Student"), ("borrowed_date", "Borrowed"), ("due_date", "Due"), ("returned_date", "Returned"), ("status", "Status")],
        "search": ["book__title", "student__student_number", "student__name"],
        "form_fields": ["book", "student", "borrowed_date", "due_date", "returned_date", "status"],
        "select_related": ["book", "student"],
    },
    # POS and F&B
    "pos-registers": {
        "model": POSRegister,
        "title": "POS Registers",
        "singular": "POS Register",
        "columns": [("code", "Code"), ("name", "Register"), ("company", "Company"), ("warehouse", "Warehouse"), ("active", "Active")],
        "search": ["code", "name"],
        "form_fields": ["company", "code", "name", "warehouse", "active"],
        "select_related": ["company", "warehouse"],
    },
    "pos-sessions": {
        "model": POSSession,
        "title": "POS Sessions",
        "singular": "POS Session",
        "columns": [("register", "Register"), ("opened_by", "Opened By"), ("opened_at", "Opened"), ("closed_at", "Closed"), ("opening_cash", "Opening Cash"), ("status", "Status")],
        "search": ["register__code", "register__name", "opened_by__username"],
        "form_fields": ["register", "opened_by", "opened_at", "closed_at", "opening_cash", "closing_cash", "status"],
        "select_related": ["register", "opened_by"],
    },
    "pos-orders": {
        "model": POSOrder,
        "title": "POS Orders",
        "singular": "POS Order",
        "columns": [("number", "Number"), ("session", "Session"), ("order_type", "Type"), ("customer", "Customer"), ("total", "Total"), ("status", "Status")],
        "search": ["number", "customer__name", "session__register__name"],
        "form_fields": ["session", "number", "customer", "order_type", "restaurant_table", "paid_amount", "payment_method", "status"],
        "select_related": ["session", "session__register", "customer", "restaurant_table"],
        "admin_detail": True,
    },
    "restaurant-tables": {
        "model": RestaurantTable,
        "title": "Restaurant Tables",
        "singular": "Restaurant Table",
        "columns": [("name", "Table"), ("area", "Area"), ("register", "Register"), ("seats", "Seats"), ("status", "Status")],
        "search": ["name", "area", "register__name"],
        "form_fields": ["register", "name", "area", "seats", "status"],
        "select_related": ["register"],
    },
    "kitchen-tickets": {
        "model": KitchenTicket,
        "title": "Kitchen Tickets",
        "singular": "Kitchen Ticket",
        "columns": [("order", "Order"), ("station", "Station"), ("status", "Status"), ("created_at", "Created")],
        "search": ["order__number", "station", "notes"],
        "form_fields": ["order", "station", "status", "notes"],
        "select_related": ["order"],
    },
    # AI Coworker
    "ai-workspaces": {
        "model": AIWorkspace,
        "title": "AI Workspaces",
        "singular": "AI Workspace",
        "columns": [("name", "Workspace"), ("company", "Company"), ("active", "Active"), ("created_at", "Created")],
        "search": ["name", "description"],
        "form_fields": ["company", "name", "description", "active"],
        "select_related": ["company"],
        "admin_detail": True,
    },
    "ai-agents": {
        "model": AIAgent,
        "title": "AI Coworkers",
        "singular": "AI Coworker",
        "columns": [("name", "Agent"), ("workspace", "Workspace"), ("role", "Role"), ("provider", "Provider"), ("model_name", "Model"), ("active", "Active")],
        "search": ["name", "role", "model_name", "workspace__name"],
        "form_fields": ["workspace", "name", "role", "provider", "model_name", "system_prompt", "allowed_tools", "active"],
        "select_related": ["workspace"],
    },
    "ai-conversations": {
        "model": AIConversation,
        "title": "AI Conversations",
        "singular": "AI Conversation",
        "columns": [("title", "Conversation"), ("agent", "Agent"), ("user", "User"), ("status", "Status"), ("last_activity_at", "Last Activity")],
        "search": ["title", "agent__name", "user__username"],
        "form_fields": ["agent", "user", "title", "status", "last_activity_at"],
        "select_related": ["agent", "user"],
        "admin_detail": True,
    },
    "ai-tasks": {
        "model": AITask,
        "title": "AI Tasks",
        "singular": "AI Task",
        "columns": [("title", "Task"), ("agent", "Agent"), ("task_type", "Type"), ("status", "Status"), ("created_at", "Created")],
        "search": ["title", "task_type", "agent__name"],
        "form_fields": ["agent", "title", "task_type", "status", "input_data", "result_data", "error_message", "started_at", "completed_at"],
        "select_related": ["agent"],
    },
    # Localization
    "localization-packs": {
        "model": LocalizationPack,
        "title": "Localization Packs",
        "singular": "Localization Pack",
        "columns": [("country_code", "Country"), ("country_name", "Name"), ("currency", "Currency"), ("tax_label", "Tax"), ("language_code", "Language"), ("enabled", "Enabled")],
        "search": ["country_code", "country_name", "currency", "tax_label"],
        "form_fields": ["country_code", "country_name", "currency", "tax_label", "language_code", "version", "enabled"],
        "admin_detail": True,
    },
    "tax-rules": {
        "model": TaxRule,
        "title": "Tax Rules",
        "singular": "Tax Rule",
        "columns": [("code", "Code"), ("name", "Tax Rule"), ("localization", "Localization"), ("tax_type", "Type"), ("rate", "Rate"), ("active", "Active")],
        "search": ["code", "name", "localization__country_name"],
        "form_fields": ["localization", "code", "name", "tax_type", "rate", "effective_from", "effective_until", "active"],
        "select_related": ["localization"],
    },
    "payroll-parameters": {
        "model": PayrollParameter,
        "title": "Payroll Parameters",
        "singular": "Payroll Parameter",
        "columns": [("code", "Code"), ("name", "Parameter"), ("localization", "Localization"), ("value", "Value"), ("unit", "Unit"), ("active", "Active")],
        "search": ["code", "name", "localization__country_name"],
        "form_fields": ["localization", "code", "name", "value", "unit", "effective_from", "active"],
        "select_related": ["localization"],
    },
    "document-sequences": {
        "model": DocumentSequence,
        "title": "Document Sequences",
        "singular": "Document Sequence",
        "columns": [("document_type", "Document"), ("localization", "Localization"), ("prefix", "Prefix"), ("padding", "Padding"), ("next_number", "Next"), ("reset_annually", "Annual Reset")],
        "search": ["document_type", "prefix", "localization__country_name"],
        "form_fields": ["localization", "document_type", "prefix", "padding", "next_number", "reset_annually"],
        "select_related": ["localization"],
    },
}


DOCUMENT_CONFIG = {
    "sales": {
        "line_model": SaleOrderLine,
        "line_relation": "lines",
        "header_fields": ["company", "number", "customer", "validity_date", "order_date", "price_list", "payment_terms", "salesperson_name", "customer_reference", "notes"],
        "line_fields": ["product", "description", "quantity", "unit_price", "discount_percent", "rental_start_date", "rental_return_date", "rental_status"],
        "line_title": "Order Lines & Rental Period",
        "total_fields": [("Total", "total")],
        "editable_statuses": [SaleOrder.Status.DRAFT],
        "actions": {
            "confirm": {"label": "Confirm", "from": [SaleOrder.Status.DRAFT], "to": SaleOrder.Status.CONFIRMED, "primary": True},
            "complete": {"label": "Complete", "from": [SaleOrder.Status.CONFIRMED], "to": SaleOrder.Status.COMPLETED, "primary": True},
            "cancel": {"label": "Cancel", "from": [SaleOrder.Status.DRAFT, SaleOrder.Status.CONFIRMED], "to": SaleOrder.Status.CANCELLED},
        },
    },
    "purchases": {
        "line_model": PurchaseOrderLine,
        "line_relation": "lines",
        "header_fields": ["company", "number", "vendor", "order_date", "notes"],
        "line_fields": ["product", "description", "quantity", "unit_price"],
        "line_title": "Order Lines",
        "total_fields": [("Total", "total")],
        "editable_statuses": [PurchaseOrder.Status.DRAFT],
        "actions": {
            "confirm": {"label": "Confirm", "from": [PurchaseOrder.Status.DRAFT], "to": PurchaseOrder.Status.CONFIRMED, "primary": True},
            "complete": {"label": "Complete", "from": [PurchaseOrder.Status.CONFIRMED], "to": PurchaseOrder.Status.COMPLETED, "primary": True},
            "cancel": {"label": "Cancel", "from": [PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.CONFIRMED], "to": PurchaseOrder.Status.CANCELLED},
        },
    },
    "invoices": {
        "line_model": InvoiceLine,
        "line_relation": "lines",
        "header_fields": ["company", "number", "invoice_type", "partner", "invoice_date", "due_date", "paid_amount", "notes"],
        "line_fields": ["product", "description", "quantity", "unit_price"],
        "line_title": "Invoice Lines",
        "total_fields": [("Total", "total"), ("Paid", "paid_amount"), ("Balance", "balance")],
        "editable_statuses": [Invoice.Status.DRAFT],
        "actions": {
            "post": {"label": "Post", "from": [Invoice.Status.DRAFT], "to": Invoice.Status.POSTED, "primary": True},
            "mark_paid": {"label": "Mark Paid", "from": [Invoice.Status.POSTED], "to": Invoice.Status.PAID, "primary": True, "set_paid": True},
            "cancel": {"label": "Cancel", "from": [Invoice.Status.DRAFT, Invoice.Status.POSTED], "to": Invoice.Status.CANCELLED},
        },
    },
    "journal-entries": {
        "line_model": JournalLine,
        "line_relation": "lines",
        "header_fields": ["company", "number", "entry_date", "reference", "description"],
        "line_fields": ["account", "description", "debit", "credit"],
        "line_title": "Journal Items",
        "total_fields": [("Debit", "total_debit"), ("Credit", "total_credit")],
        "editable_statuses": [JournalEntry.Status.DRAFT],
        "actions": {
            "post": {"label": "Post", "from": [JournalEntry.Status.DRAFT], "to": JournalEntry.Status.POSTED, "primary": True, "requires_balance": True},
            "reverse": {"label": "Reverse", "from": [JournalEntry.Status.POSTED], "to": JournalEntry.Status.REVERSED},
        },
    },
    "boms": {
        "line_model": BillOfMaterialLine,
        "line_relation": "components",
        "header_fields": ["company", "code", "product", "output_quantity", "active", "notes"],
        "line_fields": ["component", "quantity", "warehouse"],
        "line_title": "Components",
        "total_fields": [],
        "editable_statuses": None,
        "actions": {},
    },
    "pos-orders": {
        "line_model": POSOrderLine,
        "line_relation": "lines",
        "header_fields": ["session", "number", "customer", "order_type", "restaurant_table", "paid_amount", "payment_method"],
        "line_fields": ["product", "quantity", "unit_price", "notes"],
        "line_title": "Order Lines",
        "total_fields": [("Total", "total"), ("Paid", "paid_amount")],
        "editable_statuses": [POSOrder.Status.OPEN],
        "actions": {
            "pay": {"label": "Pay", "from": [POSOrder.Status.OPEN], "to": POSOrder.Status.PAID, "primary": True, "set_paid": True},
            "cancel": {"label": "Cancel", "from": [POSOrder.Status.OPEN], "to": POSOrder.Status.CANCELLED},
        },
    },
    "projects": {
        "line_model": ProjectTask,
        "line_relation": "tasks",
        "header_fields": ["company", "code", "name", "customer", "manager", "start_date", "end_date", "budget", "progress"],
        "line_fields": ["title", "assignee", "due_date", "status", "progress", "description"],
        "line_title": "Project Tasks",
        "total_fields": [("Progress", "progress")],
        "editable_statuses": [Project.Status.PLANNING, Project.Status.ACTIVE],
        "actions": {
            "activate": {"label": "Start Project", "from": [Project.Status.PLANNING], "to": Project.Status.ACTIVE, "primary": True},
            "complete": {"label": "Complete", "from": [Project.Status.ACTIVE], "to": Project.Status.COMPLETED, "primary": True},
            "hold": {"label": "Put On Hold", "from": [Project.Status.ACTIVE], "to": Project.Status.ON_HOLD},
            "resume": {"label": "Resume", "from": [Project.Status.ON_HOLD], "to": Project.Status.ACTIVE},
        },
    },
    "construction-projects": {
        "line_model": WorkPackage,
        "line_relation": "work_packages",
        "header_fields": ["company", "code", "name", "customer", "site_address", "contract_value", "start_date", "end_date", "progress"],
        "line_fields": ["code", "name", "budget", "actual_cost", "progress", "status"],
        "line_title": "Work Packages",
        "total_fields": [("Contract Value", "contract_value"), ("Progress", "progress")],
        "editable_statuses": [ConstructionProject.Status.TENDER, ConstructionProject.Status.ACTIVE],
        "actions": {
            "activate": {"label": "Start Project", "from": [ConstructionProject.Status.TENDER], "to": ConstructionProject.Status.ACTIVE, "primary": True},
            "complete": {"label": "Complete", "from": [ConstructionProject.Status.ACTIVE], "to": ConstructionProject.Status.COMPLETED, "primary": True},
            "hold": {"label": "Put On Hold", "from": [ConstructionProject.Status.ACTIVE], "to": ConstructionProject.Status.ON_HOLD},
            "resume": {"label": "Resume", "from": [ConstructionProject.Status.ON_HOLD], "to": ConstructionProject.Status.ACTIVE},
        },
    },
    "courses": {
        "line_model": Lesson,
        "line_relation": "lessons",
        "header_fields": ["school", "code", "title", "description", "instructor", "active"],
        "line_fields": ["sequence", "title", "content", "published"],
        "line_title": "Lessons",
        "total_fields": [],
        "editable_statuses": None,
        "actions": {},
    },
    "ai-conversations": {
        "line_model": AIMessage,
        "line_relation": "messages",
        "header_fields": ["agent", "user", "title", "last_activity_at"],
        "line_fields": ["role", "content", "metadata"],
        "line_title": "Messages",
        "total_fields": [],
        "editable_statuses": [AIConversation.Status.ACTIVE],
        "actions": {
            "archive": {"label": "Archive", "from": [AIConversation.Status.ACTIVE], "to": AIConversation.Status.ARCHIVED},
        },
    },
}


def health(request):
    return JsonResponse({"status": "ok", "service": "erp-gsshop"})


def _config(entity):
    try:
        return ENTITY_CONFIG[entity]
    except KeyError as exc:
        raise Http404("Unknown ERP module") from exc


def _can(user, action, model, entity=None):
    return user.has_perm(f"{model._meta.app_label}.{action}_{model._meta.model_name}") and can_hr_entity_action(
        user,
        entity,
        action,
    )


def _unavailable_application(request, entity):
    app_slug = ENTITY_APP_MAP.get(entity)
    if not app_slug:
        return None
    app = ERPApp.objects.filter(slug=app_slug).only("slug", "name", "enabled").first()
    if app is None or app.enabled:
        return None
    messages.error(request, f"Install {app.name} from Apps before opening this menu.")
    return redirect("app_workspace", slug=app.slug)


def _display_value(obj, field_name):
    display_method = getattr(obj, f"get_{field_name}_display", None)
    value = display_method() if display_method else getattr(obj, field_name)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value is None or value == "":
        return "—"
    return value


def _form_class(config):
    if "form" in config:
        return config["form"]
    return modelform_factory(config["model"], form=StyledModelForm, fields=config["form_fields"])


def _document_context(request, entity, config, instance=None, mode="Create"):
    document = DOCUMENT_CONFIG[entity]
    model = config["model"]
    header_form_class = (
        SaleOrderDocumentForm
        if entity == "sales"
        else modelform_factory(model, form=StyledModelForm, fields=document["header_fields"])
    )
    line_form_class = SaleOrderLineForm if entity == "sales" else StyledModelForm
    formset_class = inlineformset_factory(
        model,
        document["line_model"],
        form=line_form_class,
        fields=document["line_fields"],
        extra=1,
        can_delete=True,
    )
    form = header_form_class(request.POST or None, instance=instance)
    line_queryset = document["line_model"].objects.all()
    if entity == "sales":
        line_queryset = line_queryset.select_related("product")
    formset = formset_class(
        request.POST or None,
        instance=instance,
        prefix="document_lines",
        queryset=line_queryset,
    )
    current_status = getattr(instance, "status", None) if instance else None
    editable_statuses = document.get("editable_statuses")
    editable = instance is None or editable_statuses is None or current_status in editable_statuses

    if request.method == "POST" and not editable:
        messages.error(request, "This document is locked by its workflow status.")
        return None, redirect("entity_edit", entity=entity, pk=instance.pk)

    if editable and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            saved = form.save()
            formset.instance = saved
            formset.save()
        saved.refresh_from_db()
        messages.success(request, f"{config['singular']} saved with its detail lines.")
        return None, redirect("entity_edit", entity=entity, pk=saved.pk)

    if not editable:
        for field in form.fields.values():
            field.disabled = True
        for line_form in formset.forms:
            for field in line_form.fields.values():
                field.disabled = True

    status_choices = []
    if hasattr(model, "Status"):
        status_choices = list(model.Status.choices)
    available_actions = [
        {"key": key, **definition}
        for key, definition in document.get("actions", {}).items()
        if instance and current_status in definition["from"]
    ]
    totals = []
    if instance:
        totals = [{"label": label, "value": getattr(instance, field)} for label, field in document.get("total_fields", [])]
    context = {
        "entity": entity,
        "config": config,
        "document": document,
        "object": instance,
        "form": form,
        "formset": formset,
        "mode": mode,
        "editable": editable,
        "current_status": current_status,
        "status_choices": status_choices,
        "available_actions": available_actions,
        "totals": totals,
    }
    return context, None


@require_GET
@login_required
def product_search(request):
    if not _can(request.user, "view", Product, "products"):
        return JsonResponse({"detail": "Anda tidak memiliki akses ke master produk."}, status=403)

    products = Product.objects.filter(active=True)
    product_id = request.GET.get("id", "").strip()
    query = request.GET.get("q", "").strip()[:100]
    if product_id.isdigit():
        products = products.filter(pk=int(product_id))
    elif query:
        products = products.filter(Q(sku__icontains=query) | Q(name__icontains=query))

    results = [
        {"id": product["id"], "text": f"{product['sku']} — {product['name']}"}
        for product in products.order_by("name", "sku").values("id", "sku", "name")[:20]
    ]
    return JsonResponse({"results": results})


@require_GET
@login_required
def customer_search(request):
    if not _can(request.user, "view", Contact, "contacts"):
        return JsonResponse({"detail": "Anda tidak memiliki akses ke master customer."}, status=403)

    customers = Contact.objects.filter(
        active=True,
        contact_type__in=[Contact.ContactType.CUSTOMER, Contact.ContactType.BOTH],
    )
    customer_id = request.GET.get("id", "").strip()
    query = request.GET.get("q", "").strip()[:100]
    if customer_id.isdigit():
        customers = customers.filter(pk=int(customer_id))
    elif query:
        customers = customers.filter(Q(code__icontains=query) | Q(name__icontains=query))

    results = [
        {"id": customer["id"], "text": f"{customer['code']} — {customer['name']}"}
        for customer in customers.order_by("name", "code").values("id", "code", "name")[:20]
    ]
    return JsonResponse({"results": results})


@login_required
def dashboard(request):
    today = timezone.localdate()
    visible_employees = scope_hr_queryset(request.user, "employees", Employee.objects.all())
    visible_attendance = scope_hr_queryset(request.user, "attendance", Attendance.objects.all())
    sales_total = SaleOrder.objects.exclude(status=SaleOrder.Status.CANCELLED).aggregate(value=Sum("total"))["value"] or 0
    receivables = (
        Invoice.objects.filter(invoice_type=Invoice.InvoiceType.CUSTOMER)
        .exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED])
        .aggregate(value=Sum(F("total") - F("paid_amount")))["value"]
        or 0
    )
    context = {
        "today": today,
        "metrics": [
            {"label": "Sales Value", "value": sales_total, "kind": "currency", "tone": "emerald"},
            {"label": "Receivables", "value": receivables, "kind": "currency", "tone": "amber"},
            {"label": "Active Products", "value": Product.objects.filter(active=True).count(), "tone": "blue"},
            {"label": "Employees", "value": visible_employees.filter(status=Employee.Status.ACTIVE).count(), "tone": "violet"},
        ],
        "recent_sales": SaleOrder.objects.select_related("customer").all()[:6],
        "low_stock": Product.objects.filter(active=True, product_type=Product.ProductType.STOCKABLE, current_stock__lte=F("reorder_level"))[:6],
        "today_attendance": visible_attendance.filter(attendance_date=today).count(),
        "company_count": Company.objects.filter(active=True).count(),
    }
    return render(request, "core/dashboard.html", context)


@login_required
def entity_list(request, entity):
    config = _config(entity)
    unavailable = _unavailable_application(request, entity)
    if unavailable:
        return unavailable
    model = config["model"]
    if not _can(request.user, "view", model, entity):
        messages.error(request, "You do not have permission to view this module.")
        return redirect("dashboard")
    if entity == "training-conducts":
        return training_conduct_list(
            request,
            config,
            can_add=_can(request.user, "add", model, entity),
            can_change=_can(request.user, "change", model, entity),
        )
    if entity == "rental-details":
        return rental_detail_list(
            request,
            config,
            can_change=_can(request.user, "change", model, entity),
            can_delete=_can(request.user, "delete", model, entity),
        )
    queryset = scope_hr_queryset(request.user, entity, model.objects.all())
    if config.get("select_related"):
        queryset = queryset.select_related(*config["select_related"])
    query = request.GET.get("q", "").strip()
    if query:
        filters = Q()
        for field in config["search"]:
            filters |= Q(**{f"{field}__icontains": query})
        queryset = queryset.filter(filters)
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    rows = [{"object": obj, "values": [_display_value(obj, field) for field, _ in config["columns"]]} for obj in page.object_list]
    context = {
        "entity": entity,
        "config": config,
        "headers": [label for _, label in config["columns"]],
        "rows": rows,
        "page": page,
        "query": query,
        "can_add": _can(request.user, "add", model, entity),
        "can_change": config.get("editable", True) and _can(request.user, "change", model, entity),
        "can_delete": config.get("deletable", True) and _can(request.user, "delete", model, entity),
    }
    return render(request, "core/entity_list.html", context)


@login_required
def entity_create(request, entity):
    config = _config(entity)
    unavailable = _unavailable_application(request, entity)
    if unavailable:
        return unavailable
    model = config["model"]
    if entity == "rental-details":
        return entity_create(request, "sales")
    if not _can(request.user, "add", model, entity):
        messages.error(request, "You do not have permission to add this record.")
        return redirect("entity_list", entity=entity)
    if entity == "training-conducts":
        return training_conduct_form(request, config, can_change=True)
    if entity in DOCUMENT_CONFIG:
        context, response = _document_context(request, entity, config)
        return response or render(request, "core/document_form.html", context)
    form = _form_class(config)(request.POST or None)
    scope_hr_form(form, request.user, entity)
    if form.is_valid():
        instance = form.save(commit=False)
        if isinstance(instance, StockMove):
            instance.created_by = request.user
        instance.save()
        messages.success(request, f"{config['singular']} created successfully.")
        return redirect("entity_list", entity=entity)
    return render(request, "core/entity_form.html", {"form": form, "config": config, "entity": entity, "mode": "Create"})


@login_required
def entity_edit(request, entity, pk):
    config = _config(entity)
    unavailable = _unavailable_application(request, entity)
    if unavailable:
        return unavailable
    model = config["model"]
    if entity == "rental-details":
        line = get_object_or_404(SaleOrderLine.objects.select_related("order"), pk=pk)
        return redirect("entity_edit", entity="sales", pk=line.order_id)
    if entity == "training-conducts":
        if not _can(request.user, "view", model, entity):
            messages.error(request, "You do not have permission to view this record.")
            return redirect("entity_list", entity=entity)
        scoped_queryset = scope_hr_queryset(request.user, entity, model.objects.all())
        instance = get_object_or_404(scoped_queryset, pk=pk)
        return training_conduct_form(
            request,
            config,
            instance=instance,
            can_change=_can(request.user, "change", model, entity),
        )
    if not config.get("editable", True) or not _can(request.user, "change", model, entity):
        messages.error(request, "This record is immutable or you do not have permission to edit it.")
        return redirect("entity_list", entity=entity)
    scoped_queryset = scope_hr_queryset(request.user, entity, model.objects.all())
    instance = get_object_or_404(scoped_queryset, pk=pk)
    if entity in DOCUMENT_CONFIG:
        context, response = _document_context(request, entity, config, instance=instance, mode="Edit")
        return response or render(request, "core/document_form.html", context)
    current_status = getattr(instance, "status", None)
    locked_statuses = set(config.get("workflow_locked_statuses", {"approved", "cancelled"}))
    workflow_locked = bool(config.get("workflow")) and current_status in locked_statuses
    if request.method == "POST" and workflow_locked:
        messages.error(request, "This record is locked by its workflow status.")
        return redirect("entity_edit", entity=entity, pk=pk)
    form = _form_class(config)(request.POST or None, instance=instance)
    scope_hr_form(form, request.user, entity)
    if workflow_locked:
        for field in form.fields.values():
            field.disabled = True
    if form.is_valid():
        form.save()
        messages.success(request, f"{config['singular']} updated successfully.")
        return redirect("entity_list", entity=entity)
    workflow_actions = [
        {"key": key, **definition}
        for key, definition in config.get("workflow", {}).items()
        if current_status in definition["from"] and can_hr_workflow_action(request.user, entity, key, instance)
    ]
    return render(
        request,
        "core/entity_form.html",
        {
            "form": form,
            "config": config,
            "entity": entity,
            "mode": "Edit",
            "object": instance,
            "current_status": current_status,
            "workflow_actions": workflow_actions,
            "workflow_locked": workflow_locked,
        },
    )


@login_required
def entity_delete(request, entity, pk):
    config = _config(entity)
    unavailable = _unavailable_application(request, entity)
    if unavailable:
        return unavailable
    model = config["model"]
    if not config.get("deletable", True) or not _can(request.user, "delete", model, entity):
        messages.error(request, "This record cannot be deleted or you do not have permission.")
        return redirect("entity_list", entity=entity)
    scoped_queryset = scope_hr_queryset(request.user, entity, model.objects.all())
    instance = get_object_or_404(scoped_queryset, pk=pk)
    if request.method == "POST":
        instance.delete()
        messages.success(request, f"{config['singular']} deleted.")
        return redirect("entity_list", entity=entity)
    return render(request, "core/entity_confirm_delete.html", {"object": instance, "config": config, "entity": entity})


@require_GET
@login_required
def document_pdf(request, entity, pk):
    config = _config(entity)
    unavailable = _unavailable_application(request, entity)
    if unavailable:
        return unavailable
    document = DOCUMENT_CONFIG.get(entity)
    report = REPORT_CONFIG.get(entity)
    if document is None or report is None:
        raise Http404("This menu does not provide a document PDF.")
    model = config["model"]
    if not _can(request.user, "view", model, entity):
        messages.error(request, "You do not have permission to print this document.")
        return redirect("entity_list", entity=entity)
    queryset = scope_hr_queryset(request.user, entity, model.objects.all()).select_related(*report["select_related"])
    line_queryset = document["line_model"].objects.all()
    if report["line_select_related"]:
        line_queryset = line_queryset.select_related(*report["line_select_related"])
    queryset = queryset.prefetch_related(Prefetch(document["line_relation"], queryset=line_queryset))
    instance = get_object_or_404(queryset, pk=pk)
    lines = list(getattr(instance, document["line_relation"]).all())
    pdf = render_document_pdf(entity, instance, lines, document)
    reference = str(getattr(instance, report["reference"], pk)).replace("/", "-").replace("\\", "-")
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{entity}-{reference}.pdf"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _rental_detail_redirect(request):
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect("entity_list", entity="rental-details")


@require_POST
@login_required
def rental_line_status(request, pk):
    if not _can(request.user, "change", SaleOrderLine, "rental-details"):
        messages.error(request, "You do not have permission to change rental status.")
        return _rental_detail_redirect(request)
    line = get_object_or_404(SaleOrderLine.objects.select_related("order"), pk=pk)
    if line.display_type:
        messages.error(request, "Section and note lines do not have a rental status.")
        return _rental_detail_redirect(request)
    status = request.POST.get("rental_status", "")
    valid_statuses = {"", *(value for value, _label in SaleOrderLine.RentalStatus.choices)}
    if status not in valid_statuses:
        messages.error(request, "Invalid rental status.")
        return _rental_detail_redirect(request)
    if line.rental_status != status:
        line.rental_status = status
        line.save(update_fields=["rental_status", "updated_at"])
    messages.success(request, f"Status Barang for {line.order.number} updated.")
    return _rental_detail_redirect(request)


@require_POST
@login_required
def rental_line_delete(request, pk):
    if not _can(request.user, "delete", SaleOrderLine, "rental-details"):
        messages.error(request, "You do not have permission to delete rental lines.")
        return _rental_detail_redirect(request)
    line = get_object_or_404(SaleOrderLine.objects.select_related("order"), pk=pk)
    if line.odoo_source_id is None and line.order.status != SaleOrder.Status.DRAFT:
        messages.error(request, "Manual lines on confirmed orders cannot be deleted. Cancel or reverse them instead.")
        return _rental_detail_redirect(request)
    order_number = line.order.number
    line.delete()
    messages.success(request, f"Rental line from {order_number} deleted.")
    return _rental_detail_redirect(request)


@require_POST
@login_required
def document_action(request, entity, pk, action):
    config = _config(entity)
    unavailable = _unavailable_application(request, entity)
    if unavailable:
        return unavailable
    document = DOCUMENT_CONFIG.get(entity)
    if document is None:
        workflow = config.get("workflow", {})
        definition = workflow.get(action)
        if definition is None:
            raise Http404("This module does not use that workflow action.")
        model = config["model"]
        if not _can(request.user, "change", model, entity):
            messages.error(request, "You do not have permission to update this workflow.")
            return redirect("entity_list", entity=entity)
        scoped_queryset = scope_hr_queryset(request.user, entity, model.objects.all())
        instance = get_object_or_404(scoped_queryset, pk=pk)
        if not can_hr_workflow_action(request.user, entity, action, instance):
            messages.error(request, "Your role cannot perform that workflow approval action.")
            return redirect("entity_edit", entity=entity, pk=pk)
        current_status = getattr(instance, "status", None)
        if current_status not in definition["from"]:
            messages.error(request, "That workflow action is not available for the current status.")
            return redirect("entity_edit", entity=entity, pk=pk)
        update_values = {"status": definition["to"], "updated_at": timezone.now()}
        if action in {"approve", "reject"} and hasattr(instance, "approver_id"):
            update_values["approver_id"] = request.user.pk
        updated = scoped_queryset.filter(pk=pk, status=current_status).update(**update_values)
        if updated != 1:
            messages.error(request, "The record changed while this action was running. Please review it again.")
        else:
            messages.success(request, f"{config['singular']} moved to {definition['to'].replace('_', ' ').title()}.")
        return redirect("entity_edit", entity=entity, pk=pk)
    model = config["model"]
    if not _can(request.user, "change", model, entity):
        messages.error(request, "You do not have permission to update this document.")
        return redirect("entity_list", entity=entity)
    instance = get_object_or_404(model, pk=pk)
    definition = document.get("actions", {}).get(action)
    if definition is None or instance.status not in definition["from"]:
        messages.error(request, "That workflow action is not available for the current status.")
        return redirect("entity_edit", entity=entity, pk=pk)
    if definition.get("requires_balance") and not instance.balanced:
        messages.error(request, "A journal entry must have equal non-zero debit and credit totals before posting.")
        return redirect("entity_edit", entity=entity, pk=pk)
    update_fields = ["status", "updated_at"]
    instance.status = definition["to"]
    if definition.get("set_paid"):
        instance.paid_amount = instance.total
        update_fields.append("paid_amount")
    instance.save(update_fields=update_fields)
    messages.success(request, f"{config['singular']} moved to {instance.get_status_display()}.")
    return redirect("entity_edit", entity=entity, pk=pk)
