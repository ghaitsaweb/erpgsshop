from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Max, Prefetch, Q, Sum
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
from .developer import developer_field_metadata, developer_metadata
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
from modules.apphub.workflow_hooks import run_before_workflow_hooks
from modules.crm.models import Lead, Opportunity
from modules.construction.access import (
    can_construction_entity_action,
    can_construction_workflow_action,
    scope_construction_form,
    scope_construction_queryset,
)
from modules.construction import models as construction_models
from modules.construction.entity_config import (
    BOQ_WORKFLOW,
    CLAIM_WORKFLOW,
    CONSTRUCTION_ENTITY_CONFIG,
    CONTRACT_WORKFLOW,
    EXECUTION_WORKFLOW,
)
from modules.construction.services import handle_document_action as handle_construction_document_action
from modules.construction.services import handle_document_save as handle_construction_document_save
from modules.education.models import BookLoan, Course, Enrollment, Lesson, LibraryBook, School, Student
from modules.finance.access import (
    can_accounting_entity_action,
    can_accounting_workflow_action,
    scope_accounting_form,
    scope_accounting_queryset,
)
from modules.finance.entity_config import ACCOUNTING_DOCUMENT_CONFIG, ACCOUNTING_ENTITY_CONFIG
from modules.finance.ecosystem_config import ECOSYSTEM_DOCUMENT_CONFIG, ECOSYSTEM_ENTITY_CONFIG
from modules.finance.core_parity_config import CORE_PARITY_DOCUMENT_CONFIG, CORE_PARITY_ENTITY_CONFIG
from modules.finance.localization_config import LOCALIZATION_ENTITY_CONFIG
from modules.finance.parity_config import PARITY_DOCUMENT_CONFIG, PARITY_ENTITY_CONFIG
from modules.finance.models import Account, Budget, ExpenseClaim, JournalEntry, JournalLine, Loan
from modules.finance.services import (
    cancel_invoice,
    handle_accounting_workflow,
    handle_external_accounting_workflow,
    post_invoice,
    post_journal_entry,
    reverse_journal_entry,
)
from modules.hr.models import (
    AdvanceCycle,
    AdvanceLimitConsumption,
    Appraisal,
    AttendanceChangeRequest,
    AttendanceRecompute,
    BusinessUnit,
    Candidate,
    CareerTransitionLetter,
    CareerTransitionRequest,
    CareerTransitionType,
    CashAdvance,
    CompanyHoliday,
    CompensationBenefit,
    ContractAllowance,
    ContractLetter,
    ContractType,
    Department,
    DisciplinaryStage,
    DocumentType,
    EmployeeAddress,
    EmployeeCategory,
    EmployeeChangeRequest,
    EmployeeChecklist,
    EmployeeDisciplinary,
    EmployeeDocument,
    EmployeeGrade,
    EmployeeLoanRequest,
    EmployeeOffboarding,
    EmployeeOnboarding,
    EmployeeRelation,
    EmploymentContract,
    ExpenseCycle,
    ExpenseLimitConsumption,
    ExpenseProduct,
    HRSetting,
    HRWorkflowAudit,
    HRYear,
    JobClassification,
    JobExperienceLevel,
    JobPosition,
    LeaveAllocation,
    LeaveEncashment,
    LeaveMovement,
    LeaveOpeningBalance,
    LeaveRequest,
    LeaveStructure,
    LeaveType,
    LiveTrackingSession,
    LoanEligibilityAssignment,
    LoanInstallment,
    LoanPolicy,
    LoanRepayment,
    LoanType,
    OvertimeRequest,
    OvertimeRule,
    PayrollPeriod,
    PayrollReportTemplate,
    PayrollRun,
    PayrollVariable,
    Payslip,
    RawAttendance,
    SalaryComponent,
    SalaryPackage,
    ScheduleAssignment,
    ScheduleExchange,
    Shift,
    Skill,
    TrainingConduct,
    TrainingCourse,
    TrainingProgress,
    TrainingProgram,
    TrainingRequest,
    TrainingTrainer,
    TravelPackage,
    TravelRequest,
    WorkLocation,
    WorkingCalendar,
    WorkPattern,
    WorkSchedule,
)
from modules.hr.change_requests import transition_employee_change_request
from modules.hr.attendance_changes import transition_attendance_change_request
from modules.hr.contracts import transition_employment_contract
from modules.hr.forms import AttendanceChangeRequestForm, EmployeeChangeRequestForm, EmploymentContractForm, RawAttendanceForm
from modules.hr.raw_attendance import process_raw_attendance
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
from modules.pos.entity_config import POS_DOCUMENT_CONFIG, POS_ENTITY_CONFIG
from modules.pos.access import can_pos_entity_action, can_pos_workflow_action, scope_pos_form, scope_pos_queryset
from modules.organization.context import scope_form as scope_organization_form
from modules.organization.context import scope_queryset as scope_organization_queryset
from modules.organization.forms import BranchForm
from modules.organization.models import Branch
from modules.apphub.models import ERPApp
from modules.apphub.workspaces import ENTITY_APP_MAP
from modules.hr.access import can_hr_entity_action, can_hr_workflow_action, scope_hr_form, scope_hr_queryset
from modules.hr.detail_config import HR_DETAIL_ENTITY_CONFIG
from modules.hr.training_views import training_conduct_form, training_conduct_list
from modules.purchase.access import (
    can_purchase_entity_action,
    can_purchase_workflow_action,
    scope_purchase_form,
    scope_purchase_queryset,
)
from modules.purchase.entity_config import PURCHASE_ENTITY_CONFIG
from modules.purchase.models import (
    BlanketOrder,
    BlanketOrderLine,
    DirectPurchase,
    DirectPurchaseLine,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseAccountingDocument,
    PurchaseAccountingLine,
    PurchaseInventoryTransfer,
    PurchaseInventoryTransferLine,
    PurchaseOrderRevision,
    PurchasePeppolEvent,
    PurchaseQuotation,
    PurchaseQuotationLine,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseTender,
    PurchaseTenderLine,
    ServiceWorkOrder,
    ServiceWorkOrderLine,
    TenderAuditLog,
)
from modules.purchase.services import approve_budget_movement
from .rental_views import rental_detail_list
from .document_pdf import REPORT_CONFIG, render_document_pdf


APPROVAL_WORKFLOW = {
    "submit": {"label": "Submit", "from": ["draft"], "to": "submitted", "primary": True},
    "approve": {"label": "Approve", "from": ["submitted"], "to": "approved", "primary": True},
    "reject": {"label": "Reject", "from": ["submitted"], "to": "rejected"},
    "cancel": {"label": "Cancel", "from": ["draft", "submitted"], "to": "cancelled"},
    "reset": {"label": "Reset to Draft", "from": ["rejected", "cancelled"], "to": "draft"},
}

EMPLOYMENT_CONTRACT_WORKFLOW = {
    "activate": {"label": "Activate", "from": ["draft"], "to": "active", "primary": True},
    "expire": {"label": "Expire", "from": ["active"], "to": "expired"},
    "terminate": {"label": "Terminate", "from": ["active"], "to": "terminated"},
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

LOAN_REQUEST_WORKFLOW = {
    "submit": {"label": "Submit", "from": ["draft"], "to": "waiting", "primary": True},
    "confirm": {"label": "Approve", "from": ["waiting"], "to": "confirmed", "primary": True},
    "refuse": {"label": "Refuse", "from": ["waiting"], "to": "refused"},
    "disburse": {"label": "Disburse", "from": ["confirmed"], "to": "disbursed", "primary": True},
    "activate": {"label": "Activate", "from": ["disbursed"], "to": "active", "primary": True},
    "settle": {"label": "Settle", "from": ["active"], "to": "settled", "primary": True},
    "cancel": {"label": "Cancel", "from": ["draft", "waiting", "confirmed"], "to": "cancelled"},
}

LIVE_TRACKING_WORKFLOW = {
    "start": {"label": "Start", "from": ["draft", "stopped"], "to": "running", "primary": True},
    "stop": {"label": "Stop", "from": ["running"], "to": "stopped", "primary": True},
    "cancel": {"label": "Cancel", "from": ["draft", "running"], "to": "cancelled"},
}

ATTENDANCE_RECOMPUTE_WORKFLOW = {
    "process": {"label": "Process", "from": ["draft"], "to": "processing", "primary": True},
    "review": {"label": "Ready for Review", "from": ["processing"], "to": "ready", "primary": True},
    "apply": {"label": "Apply", "from": ["ready"], "to": "applied", "primary": True},
    "reject": {"label": "Reject", "from": ["ready"], "to": "rejected"},
    "fail": {"label": "Mark Failed", "from": ["processing"], "to": "failed"},
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
    "branches": {
        "model": Branch,
        "form": BranchForm,
        "title": "Branches",
        "singular": "Branch",
        "columns": [
            ("code", "Code"),
            ("name", "Branch"),
            ("company", "Company"),
            ("parent", "Parent"),
            ("is_main", "Main"),
            ("active", "Active"),
        ],
        "search": ["code", "name", "company__name", "email", "phone"],
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
        "columns": [("code", "Code"), ("name", "Warehouse"), ("company", "Company"), ("branch", "Branch"), ("active", "Active")],
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
        "columns": [
            ("attendance_date", "Work Date"), ("employee", "Employee"),
            ("check_in", "Check In"), ("check_out", "Check Out"),
            ("check_in_at", "Raw Check In At"), ("check_out_at", "Raw Check Out At"),
            ("status", "Status"),
        ],
        "search": ["employee__employee_id", "employee__name"],
        "select_related": ["employee"],
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
        "form": EmploymentContractForm,
        "select_related": ["employee"],
        "workflow": EMPLOYMENT_CONTRACT_WORKFLOW,
        "workflow_locked_statuses": {"active", "expired", "terminated"},
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
        "form": EmployeeChangeRequestForm,
        "select_related": ["employee", "approver"],
        "workflow": {key: value for key, value in APPROVAL_WORKFLOW.items() if key != "reset"},
        "workflow_locked_statuses": {"submitted", "approved", "rejected", "cancelled"},
    },
    "employee-documents": {
        "model": EmployeeDocument,
        "title": "Employee Documents",
        "singular": "Employee Document",
        "columns": [("employee", "Employee"), ("document_type", "Document Type"), ("document_number", "Number"), ("expiry_date", "Expiry"), ("status", "Status"), ("verified", "Verified")],
        "search": ["employee__employee_id", "employee__name", "document_type__name", "document_number"],
        "form_fields": ["employee", "document_type", "document_number", "issued_date", "expiry_date", "attachment_url", "verified", "status", "replaced_by", "notes"],
        "select_related": ["employee", "document_type", "replaced_by"],
    },
    "employee-disciplinary": {
        "model": EmployeeDisciplinary,
        "title": "Employee Disciplinary",
        "singular": "Disciplinary Case",
        "columns": [("reference", "Reference"), ("employee", "Employee"), ("incident_date", "Incident"), ("violation", "Violation"), ("status", "Status")],
        "search": ["reference", "employee__employee_id", "employee__name", "violation"],
        "form_fields": ["company", "employee", "reference", "incident_date", "violation", "stage", "expiry_date", "action_taken", "status", "notes"],
        "select_related": ["company", "employee", "stage"],
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
        "form": RawAttendanceForm,
        "deletable": False,
        "title": "Raw Attendance",
        "singular": "Raw Attendance Event",
        "columns": [("captured_at", "Captured At"), ("employee", "Employee"), ("event_type", "Event"), ("source", "Source"), ("processed", "Processed")],
        "search": ["employee__employee_id", "employee__name", "source", "device_identifier"],
        "select_related": ["employee"],
        "workflow_field": "processed",
        "workflow_locked_statuses": {True},
        "workflow": {"process": {"label": "Process Day", "from": [False], "to": True, "primary": True}},
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
        "workflow": {
            "submit": {"label": "Submit", "from": ["draft"], "to": "submitted", "primary": True},
            "approve": {"label": "Approve & Disburse", "from": ["submitted"], "to": "approved", "primary": True, "service_controls_state": True},
            "reject": {"label": "Reject", "from": ["submitted"], "to": "rejected"},
            "cancel": {"label": "Cancel", "from": ["draft", "submitted"], "to": "cancelled"},
        },
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
        "form_fields": ["company", "code", "name", "account_type", "parent", "currency", "allow_reconciliation", "deprecated", "active"],
        "select_related": ["company", "parent"],
    },
    "journal-entries": {
        "model": JournalEntry,
        "title": "Journal Entries",
        "singular": "Journal Entry",
        "columns": [("number", "Number"), ("entry_date", "Date"), ("company", "Company"), ("reference", "Reference"), ("status", "Status"), ("total_debit", "Debit"), ("total_credit", "Credit")],
        "search": ["number", "reference", "description"],
        "form_fields": ["company", "journal", "number", "entry_date", "reference", "description", "currency", "exchange_rate", "source_type", "source_reference", "auto_post"],
        "select_related": ["company", "journal", "posted_by", "reversal_of"],
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
        "form_fields": ["company", "employee", "product", "cash_advance", "reference", "claim_date", "category", "amount", "description", "status"],
        "select_related": ["company", "employee", "product", "cash_advance"],
        "workflow": {
            "submit": {"label": "Submit", "from": ["draft"], "to": "submitted", "primary": True},
            "approve": {"label": "Approve & Recognize", "from": ["submitted"], "to": "approved", "primary": True, "service_controls_state": True},
            "pay": {"label": "Reimburse", "from": ["approved"], "to": "closed", "primary": True, "service_controls_state": True},
            "return": {"label": "Record Advance Return", "from": ["return_due"], "to": "closed", "primary": True, "service_controls_state": True},
            "reverse": {"label": "Reverse Accounting", "from": ["approved", "return_due", "paid", "closed"], "to": "reversed", "service_controls_state": True},
            "reject": {"label": "Reject", "from": ["submitted"], "to": "rejected"},
        },
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


ENTITY_CONFIG.update(
    {
        "job-classifications": {
            "model": JobClassification,
            "title": "Job Classifications",
            "singular": "Job Classification",
            "columns": [("code", "Code"), ("name", "Classification"), ("company", "Company"), ("active", "Active")],
            "search": ["code", "name", "description"],
            "form_fields": ["company", "code", "name", "description", "active"],
            "select_related": ["company"],
        },
        "job-experience-levels": {
            "model": JobExperienceLevel,
            "title": "Job Experience Levels",
            "singular": "Job Experience Level",
            "columns": [("code", "Code"), ("name", "Level"), ("minimum_years", "Min Years"), ("maximum_years", "Max Years"), ("active", "Active")],
            "search": ["code", "name"],
            "form_fields": ["company", "code", "name", "minimum_years", "maximum_years", "active"],
            "select_related": ["company"],
        },
        "employee-categories": {
            "model": EmployeeCategory,
            "title": "Employee Categories",
            "singular": "Employee Category",
            "columns": [("code", "Code"), ("name", "Category"), ("company", "Company"), ("active", "Active")],
            "search": ["code", "name", "description"],
            "form_fields": ["company", "code", "name", "description", "active"],
            "select_related": ["company"],
        },
        "employee-addresses": {
            "model": EmployeeAddress,
            "title": "Employee Addresses",
            "singular": "Employee Address",
            "columns": [("employee", "Employee"), ("address_type", "Type"), ("city", "City"), ("province", "Province"), ("primary", "Primary")],
            "search": ["employee__employee_id", "employee__name", "address", "city", "province"],
            "form_fields": ["employee", "address_type", "address", "city", "province", "postal_code", "primary"],
            "select_related": ["employee"],
        },
        "employee-relations": {
            "model": EmployeeRelation,
            "title": "Employee Relations",
            "singular": "Employee Relation",
            "columns": [("employee", "Employee"), ("name", "Contact"), ("relationship", "Relationship"), ("phone", "Phone"), ("emergency_contact", "Emergency")],
            "search": ["employee__employee_id", "employee__name", "name", "relationship", "phone"],
            "form_fields": ["employee", "name", "relationship", "phone", "email", "emergency_contact"],
            "select_related": ["employee"],
        },
        "working-calendars": {
            "model": WorkingCalendar,
            "title": "Working Calendars",
            "singular": "Working Calendar",
            "columns": [("code", "Code"), ("name", "Calendar"), ("timezone", "Timezone"), ("hours_per_week", "Hours/Week"), ("active", "Active")],
            "search": ["code", "name", "timezone"],
            "form_fields": ["company", "code", "name", "timezone", "hours_per_week", "active"],
            "select_related": ["company"],
        },
        "work-patterns": {
            "model": WorkPattern,
            "title": "Work Patterns",
            "singular": "Work Pattern",
            "columns": [("code", "Code"), ("name", "Pattern"), ("calendar", "Calendar"), ("cycle_days", "Cycle Days"), ("active", "Active")],
            "search": ["code", "name", "calendar__name", "pattern"],
            "form_fields": ["company", "calendar", "code", "name", "pattern", "cycle_days", "active"],
            "select_related": ["company", "calendar"],
        },
        "schedule-exchanges": {
            "model": ScheduleExchange,
            "title": "Schedule Exchanges",
            "singular": "Schedule Exchange",
            "columns": [("employee", "Employee"), ("exchange_with", "Exchange With"), ("original_date", "Original Date"), ("requested_date", "Requested Date"), ("status", "Status")],
            "search": ["employee__employee_id", "employee__name", "exchange_with__name", "reason"],
            "form_fields": ["employee", "exchange_with", "original_date", "requested_date", "reason", "approver"],
            "select_related": ["employee", "exchange_with", "approver"],
            "workflow": APPROVAL_WORKFLOW,
        },
        "contract-types": {
            "model": ContractType,
            "title": "Contract Types",
            "singular": "Contract Type",
            "columns": [("code", "Code"), ("name", "Type"), ("default_duration_months", "Duration Months"), ("renewable", "Renewable"), ("active", "Active")],
            "search": ["code", "name"],
            "form_fields": ["company", "code", "name", "default_duration_months", "renewable", "active"],
            "select_related": ["company"],
        },
        "contract-allowances": {
            "model": ContractAllowance,
            "title": "Contract Allowances",
            "singular": "Contract Allowance",
            "columns": [("contract", "Contract"), ("component", "Component"), ("amount", "Amount"), ("effective_date", "Effective"), ("active", "Active")],
            "search": ["contract__number", "contract__employee__name", "component__name"],
            "form_fields": ["contract", "component", "amount", "effective_date", "end_date", "active"],
            "select_related": ["contract", "contract__employee", "component"],
        },
        "contract-letters": {
            "model": ContractLetter,
            "title": "Contract Letters",
            "singular": "Contract Letter",
            "columns": [("letter_number", "Letter Number"), ("contract", "Contract"), ("letter_type", "Type"), ("issued_date", "Issued Date")],
            "search": ["letter_number", "contract__number", "contract__employee__name", "letter_type"],
            "form_fields": ["contract", "letter_number", "letter_type", "issued_date", "attachment_url", "notes"],
            "select_related": ["contract", "contract__employee"],
        },
        "disciplinary-stages": {
            "model": DisciplinaryStage,
            "title": "Disciplinary Stages",
            "singular": "Disciplinary Stage",
            "columns": [("code", "Code"), ("name", "Stage"), ("sequence", "Sequence"), ("validity_days", "Validity Days"), ("active", "Active")],
            "search": ["code", "name"],
            "form_fields": ["company", "code", "name", "sequence", "validity_days", "active"],
            "select_related": ["company"],
        },
        "attendance-change-requests": {
            "model": AttendanceChangeRequest,
            "title": "Attendance Change Requests",
            "singular": "Attendance Change Request",
            "columns": [("employee", "Employee"), ("attendance_date", "Date"), ("requested_check_in", "Check In"), ("requested_check_out", "Check Out"), ("status", "Status")],
            "search": ["employee__employee_id", "employee__name", "reason"],
            "form": AttendanceChangeRequestForm,
            "select_related": ["employee", "approver"],
            "workflow": {
                **{key: APPROVAL_WORKFLOW[key] for key in ("submit", "approve", "reject")},
                "cancel": {"label": "Cancel", "from": ["submitted"], "to": "cancelled"},
            },
            "workflow_locked_statuses": {"submitted", "approved", "rejected", "cancelled"},
        },
        "live-tracking-sessions": {
            "model": LiveTrackingSession,
            "title": "Live Tracking Sessions",
            "singular": "Live Tracking Session",
            "columns": [("employee", "Employee"), ("started_at", "Started"), ("stopped_at", "Stopped"), ("status", "Status")],
            "search": ["employee__employee_id", "employee__name", "notes"],
            "form_fields": ["employee", "started_at", "stopped_at", "last_latitude", "last_longitude", "notes"],
            "select_related": ["employee"],
            "workflow": LIVE_TRACKING_WORKFLOW,
        },
        "attendance-recomputes": {
            "model": AttendanceRecompute,
            "title": "Attendance Recompute",
            "singular": "Attendance Recompute",
            "columns": [("company", "Company"), ("date_start", "Start"), ("date_end", "End"), ("employee_count", "Employees"), ("status", "Status")],
            "search": ["company__name", "notes", "requested_by__username"],
            "form_fields": ["company", "date_start", "date_end", "employee_count", "requested_by", "notes"],
            "select_related": ["company", "requested_by"],
            "workflow": ATTENDANCE_RECOMPUTE_WORKFLOW,
        },
        "leave-structures": {
            "model": LeaveStructure,
            "title": "Leave Structures",
            "singular": "Leave Structure",
            "columns": [("name", "Structure"), ("leave_type", "Leave Type"), ("entitlement_days", "Entitlement"), ("carry_forward_days", "Carry Forward"), ("active", "Active")],
            "search": ["name", "leave_type__name"],
            "form_fields": ["company", "name", "leave_type", "entitlement_days", "carry_forward_days", "minimum_service_months", "active"],
            "select_related": ["company", "leave_type"],
        },
        "leave-opening-balances": {
            "model": LeaveOpeningBalance,
            "title": "Leave Opening Balances",
            "singular": "Leave Opening Balance",
            "columns": [("employee", "Employee"), ("leave_type", "Leave Type"), ("year", "Year"), ("balance_days", "Balance"), ("status", "Status")],
            "search": ["employee__employee_id", "employee__name", "leave_type__name"],
            "form_fields": ["employee", "leave_type", "year", "balance_days", "status", "notes"],
            "select_related": ["employee", "leave_type"],
        },
        "leave-movements": {
            "model": LeaveMovement,
            "title": "Leave Movements",
            "singular": "Leave Movement",
            "columns": [("movement_date", "Date"), ("employee", "Employee"), ("leave_type", "Leave Type"), ("movement_type", "Type"), ("days", "Days")],
            "search": ["employee__employee_id", "employee__name", "leave_type__name", "reference"],
            "form_fields": ["employee", "leave_type", "movement_date", "movement_type", "days", "reference", "notes"],
            "select_related": ["employee", "leave_type"],
        },
        "leave-encashments": {
            "model": LeaveEncashment,
            "title": "Leave Encashments",
            "singular": "Leave Encashment",
            "columns": [("request_date", "Date"), ("employee", "Employee"), ("leave_type", "Leave Type"), ("days", "Days"), ("amount", "Amount"), ("status", "Status")],
            "search": ["employee__employee_id", "employee__name", "leave_type__name"],
            "form_fields": ["employee", "leave_type", "request_date", "days", "amount", "approver"],
            "select_related": ["employee", "leave_type", "approver"],
            "workflow": APPROVAL_WORKFLOW,
        },
        "payroll-variables": {
            "model": PayrollVariable,
            "title": "Payroll Variables",
            "singular": "Payroll Variable",
            "columns": [("code", "Code"), ("name", "Variable"), ("value", "Value"), ("effective_date", "Effective"), ("active", "Active")],
            "search": ["code", "name"],
            "form_fields": ["company", "code", "name", "value", "effective_date", "active"],
            "select_related": ["company"],
        },
        "salary-packages": {
            "model": SalaryPackage,
            "title": "Salary Packages",
            "singular": "Salary Package",
            "columns": [("code", "Code"), ("name", "Package"), ("grade", "Grade"), ("base_salary", "Base Salary"), ("active", "Active")],
            "search": ["code", "name", "grade__name"],
            "form_fields": ["company", "code", "name", "grade", "base_salary", "description", "active"],
            "select_related": ["company", "grade"],
        },
        "payroll-report-templates": {
            "model": PayrollReportTemplate,
            "title": "Payroll Report Templates",
            "singular": "Payroll Report Template",
            "columns": [("code", "Code"), ("name", "Template"), ("file_format", "Format"), ("active", "Active")],
            "search": ["code", "name", "columns"],
            "form_fields": ["company", "code", "name", "columns", "file_format", "active"],
            "select_related": ["company"],
        },
        "employee-loan-requests": {
            "model": EmployeeLoanRequest,
            "title": "Employee Loan Requests",
            "singular": "Employee Loan Request",
            "columns": [("number", "Number"), ("employee", "Employee"), ("loan_type", "Loan Type"), ("amount", "Amount"), ("term_months", "Term"), ("status", "Status")],
            "search": ["number", "employee__employee_id", "employee__name", "loan_type__name", "purpose"],
            "form_fields": ["employee", "loan_type", "number", "request_date", "amount", "term_months", "interest_rate", "purpose", "approver"],
            "select_related": ["employee", "loan_type", "approver"],
            "workflow": LOAN_REQUEST_WORKFLOW,
        },
        "loan-installments": {
            "model": LoanInstallment,
            "title": "Loan Installments",
            "singular": "Loan Installment",
            "columns": [("loan", "Loan"), ("sequence", "Sequence"), ("due_date", "Due Date"), ("principal", "Principal"), ("interest", "Interest"), ("status", "Status")],
            "search": ["loan__number", "loan__employee__name"],
            "form_fields": ["loan", "sequence", "due_date", "principal", "interest", "paid_amount", "status"],
            "select_related": ["loan", "loan__employee"],
        },
        "loan-repayments": {
            "model": LoanRepayment,
            "title": "Loan Repayments",
            "singular": "Loan Repayment",
            "columns": [("payment_date", "Date"), ("loan", "Loan"), ("installment", "Installment"), ("amount", "Amount"), ("reference", "Reference"), ("status", "Status")],
            "search": ["loan__number", "loan__employee__name", "reference"],
            "form_fields": ["loan", "installment", "payment_date", "amount", "reference", "status", "notes"],
            "select_related": ["loan", "loan__employee", "installment"],
        },
        "loan-eligibility": {
            "model": LoanEligibilityAssignment,
            "title": "Loan Eligibility Assignments",
            "singular": "Loan Eligibility Assignment",
            "columns": [("employee", "Employee"), ("policy", "Policy"), ("date_start", "Start"), ("date_end", "End"), ("maximum_amount", "Maximum"), ("active", "Active")],
            "search": ["employee__employee_id", "employee__name", "policy__name"],
            "form_fields": ["employee", "policy", "date_start", "date_end", "maximum_amount", "active"],
            "select_related": ["employee", "policy"],
        },
        "training-requests": {
            "model": TrainingRequest,
            "title": "Training Requests",
            "singular": "Training Request",
            "columns": [("requested_date", "Date"), ("employee", "Employee"), ("course", "Course"), ("preferred_start_date", "Preferred Start"), ("estimated_cost", "Estimated Cost"), ("status", "Status")],
            "search": ["employee__employee_id", "employee__name", "course__name", "business_reason"],
            "form_fields": ["employee", "course", "requested_date", "preferred_start_date", "business_reason", "estimated_cost", "approver"],
            "select_related": ["employee", "course", "approver"],
            "workflow": APPROVAL_WORKFLOW,
        },
        "training-progress": {
            "model": TrainingProgress,
            "title": "Training Progress",
            "singular": "Training Progress",
            "columns": [("employee", "Employee"), ("conduct", "Training Conduct"), ("completion_percent", "Completion %"), ("score", "Score"), ("completed_date", "Completed")],
            "search": ["employee__employee_id", "employee__name", "conduct__name", "conduct__reference"],
            "form_fields": ["employee", "conduct", "completion_percent", "score", "completed_date", "certificate_url", "notes"],
            "select_related": ["employee", "conduct"],
        },
        "advance-cycles": {
            "model": AdvanceCycle,
            "title": "Cash Advance Cycles",
            "singular": "Cash Advance Cycle",
            "columns": [("name", "Cycle"), ("company", "Company"), ("date_start", "Start"), ("date_end", "End"), ("limit_amount", "Limit"), ("active", "Active")],
            "search": ["name", "company__name"],
            "form_fields": ["company", "name", "date_start", "date_end", "limit_amount", "active"],
            "select_related": ["company"],
        },
        "expense-cycles": {
            "model": ExpenseCycle,
            "title": "Expense Cycles",
            "singular": "Expense Cycle",
            "columns": [("name", "Cycle"), ("company", "Company"), ("date_start", "Start"), ("date_end", "End"), ("limit_amount", "Limit"), ("active", "Active")],
            "search": ["name", "company__name"],
            "form_fields": ["company", "name", "date_start", "date_end", "limit_amount", "active"],
            "select_related": ["company"],
        },
        "advance-limit-consumptions": {
            "model": AdvanceLimitConsumption,
            "title": "Advance Limit Consumption",
            "singular": "Advance Limit Consumption",
            "columns": [("transaction_date", "Date"), ("employee", "Employee"), ("cycle", "Cycle"), ("transaction_reference", "Reference"), ("amount", "Amount"), ("active", "Active")],
            "search": ["employee__employee_id", "employee__name", "cycle__name", "transaction_reference"],
            "form_fields": ["employee", "cycle", "transaction_reference", "transaction_date", "amount", "active"],
            "select_related": ["employee", "cycle"],
        },
        "expense-limit-consumptions": {
            "model": ExpenseLimitConsumption,
            "title": "Expense Limit Consumption",
            "singular": "Expense Limit Consumption",
            "columns": [("transaction_date", "Date"), ("employee", "Employee"), ("cycle", "Cycle"), ("transaction_reference", "Reference"), ("amount", "Amount"), ("active", "Active")],
            "search": ["employee__employee_id", "employee__name", "cycle__name", "transaction_reference"],
            "form_fields": ["employee", "cycle", "transaction_reference", "transaction_date", "amount", "active"],
            "select_related": ["employee", "cycle"],
        },
    }
)

ENTITY_CONFIG.update(PURCHASE_ENTITY_CONFIG)
ENTITY_CONFIG.update(HR_DETAIL_ENTITY_CONFIG)
ENTITY_CONFIG.update(CONSTRUCTION_ENTITY_CONFIG)
ENTITY_CONFIG.update(POS_ENTITY_CONFIG)
ENTITY_CONFIG.update(ACCOUNTING_ENTITY_CONFIG)
ENTITY_CONFIG.update(PARITY_ENTITY_CONFIG)
ENTITY_CONFIG.update(ECOSYSTEM_ENTITY_CONFIG)
ENTITY_CONFIG.update(CORE_PARITY_ENTITY_CONFIG)
ENTITY_CONFIG.update(LOCALIZATION_ENTITY_CONFIG)


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
            "complete": {"label": "Complete & Post Accounting", "from": [SaleOrder.Status.CONFIRMED], "to": SaleOrder.Status.COMPLETED, "primary": True, "service_controls_state": True},
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
        "editable_statuses": [PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.REVISED],
        "actions": {
            "send": {
                "label": "Send to Vendor",
                "from": [PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.REVISED],
                "to": PurchaseOrder.Status.SENT,
                "primary": True,
            },
            "request-approval": {
                "label": "Request Approval",
                "from": [PurchaseOrder.Status.SENT],
                "to": PurchaseOrder.Status.WAITING_APPROVAL,
                "primary": True,
            },
            "confirm": {
                "label": "Approve",
                "from": [PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.WAITING_APPROVAL],
                "to": PurchaseOrder.Status.CONFIRMED,
                "primary": True,
            },
            "reject": {
                "label": "Reject",
                "from": [PurchaseOrder.Status.WAITING_APPROVAL],
                "to": PurchaseOrder.Status.REJECTED,
            },
            "revise": {
                "label": "Revise",
                "from": [PurchaseOrder.Status.REJECTED, PurchaseOrder.Status.EXPIRED],
                "to": PurchaseOrder.Status.REVISED,
            },
            "complete": {"label": "Complete", "from": [PurchaseOrder.Status.CONFIRMED], "to": PurchaseOrder.Status.COMPLETED, "primary": True},
            "close": {
                "label": "Close",
                "from": [PurchaseOrder.Status.CONFIRMED, PurchaseOrder.Status.COMPLETED],
                "to": PurchaseOrder.Status.CLOSED,
                "primary": True,
            },
            "expire": {
                "label": "Mark Expired",
                "from": [PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.SENT, PurchaseOrder.Status.WAITING_APPROVAL],
                "to": PurchaseOrder.Status.EXPIRED,
            },
            "cancel": {
                "label": "Cancel",
                "from": [
                    PurchaseOrder.Status.DRAFT,
                    PurchaseOrder.Status.SENT,
                    PurchaseOrder.Status.WAITING_APPROVAL,
                    PurchaseOrder.Status.CONFIRMED,
                    PurchaseOrder.Status.REVISED,
                ],
                "to": PurchaseOrder.Status.CANCELLED,
            },
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
        "header_fields": ["company", "journal", "number", "entry_date", "reference", "description", "currency", "exchange_rate", "source_type", "source_reference", "auto_post"],
        "line_fields": ["account", "partner", "cost_center", "description", "debit", "credit", "currency", "amount_currency", "due_date"],
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

DOCUMENT_CONFIG.update(ACCOUNTING_DOCUMENT_CONFIG)
DOCUMENT_CONFIG.update(PARITY_DOCUMENT_CONFIG)
DOCUMENT_CONFIG.update(ECOSYSTEM_DOCUMENT_CONFIG)
DOCUMENT_CONFIG.update(CORE_PARITY_DOCUMENT_CONFIG)

DOCUMENT_CONFIG.update(POS_DOCUMENT_CONFIG)

DOCUMENT_CONFIG.update(
    {
        "purchase-requests": {
            "line_model": PurchaseRequestLine,
            "line_relation": "lines",
            "header_fields": [
                "company",
                "number",
                "requestor",
                "request_date",
                "required_date",
                "justification",
                "rejection_reason",
                "notes",
            ],
            "line_fields": [
                "product",
                "description",
                "specification",
                "quantity",
                "approved_quantity",
                "ordered_quantity",
                "estimated_unit_price",
            ],
            "line_title": "Requested Products and Services",
            "total_fields": [],
            "editable_statuses": [PurchaseRequest.Status.DRAFT, PurchaseRequest.Status.REJECTED],
            "actions": {
                "submit": {"label": "Submit", "from": [PurchaseRequest.Status.DRAFT], "to": PurchaseRequest.Status.PENDING, "primary": True},
                "approve": {"label": "Approve", "from": [PurchaseRequest.Status.PENDING, PurchaseRequest.Status.PARTIALLY_APPROVED], "to": PurchaseRequest.Status.APPROVED, "primary": True},
                "reject": {"label": "Reject", "from": [PurchaseRequest.Status.PENDING, PurchaseRequest.Status.PARTIALLY_APPROVED], "to": PurchaseRequest.Status.REJECTED},
                "reset": {"label": "Reset to Draft", "from": [PurchaseRequest.Status.REJECTED], "to": PurchaseRequest.Status.DRAFT},
                "create-rfq": {"label": "Create RFQ", "from": [PurchaseRequest.Status.APPROVED, PurchaseRequest.Status.PARTIALLY_RFQ], "to": PurchaseRequest.Status.RFQ, "primary": True},
                "mark-ordered": {"label": "Mark Fully Ordered", "from": [PurchaseRequest.Status.RFQ, PurchaseRequest.Status.PARTIALLY_ORDERED], "to": PurchaseRequest.Status.FULLY_ORDERED, "primary": True},
                "start": {"label": "Start Fulfilment", "from": [PurchaseRequest.Status.FULLY_ORDERED], "to": PurchaseRequest.Status.IN_PROGRESS, "primary": True},
                "close": {"label": "Close", "from": [PurchaseRequest.Status.IN_PROGRESS], "to": PurchaseRequest.Status.CLOSED, "primary": True},
            },
        },
        "purchase-quotations": {
            "line_model": PurchaseQuotationLine,
            "line_relation": "lines",
            "header_fields": ["company", "number", "request", "vendor", "issue_date", "response_deadline", "currency", "quoted_amount", "notes"],
            "line_fields": ["request_line", "product", "description", "specification", "quantity", "unit_price", "tax_percent", "discount_percent", "promised_date"],
            "line_title": "Quotation Lines",
            "total_fields": [("Quoted Amount", "quoted_amount")],
            "editable_statuses": [PurchaseQuotation.Status.DRAFT, PurchaseQuotation.Status.RECEIVED],
            "actions": {
                "send": {"label": "Send to Vendor", "from": [PurchaseQuotation.Status.DRAFT], "to": PurchaseQuotation.Status.SENT, "primary": True},
                "receive": {"label": "Record Response", "from": [PurchaseQuotation.Status.SENT], "to": PurchaseQuotation.Status.RECEIVED, "primary": True},
                "select": {"label": "Select Quote", "from": [PurchaseQuotation.Status.RECEIVED], "to": PurchaseQuotation.Status.SELECTED, "primary": True},
                "cancel": {"label": "Cancel", "from": [PurchaseQuotation.Status.DRAFT, PurchaseQuotation.Status.SENT, PurchaseQuotation.Status.RECEIVED], "to": PurchaseQuotation.Status.CANCELLED},
            },
        },
        "direct-purchases": {
            "line_model": DirectPurchaseLine,
            "line_relation": "lines",
            "header_fields": ["company", "number", "vendor", "purchase_date", "description", "amount", "justification"],
            "line_fields": ["product", "description", "quantity", "unit_price", "tax_percent", "cost_center"],
            "line_title": "Direct Purchase Lines",
            "total_fields": [("Total", "amount")],
            "editable_statuses": ["draft", "rejected"],
            "actions": APPROVAL_WORKFLOW,
        },
        "service-work-orders": {
            "line_model": ServiceWorkOrderLine,
            "line_relation": "lines",
            "header_fields": ["company", "number", "vendor", "service_description", "date_start", "date_end", "contract_amount", "notes"],
            "line_fields": ["description", "unit", "quantity", "unit_rate", "tax_percent", "cost_center", "milestone_name"],
            "line_title": "Service Scope Lines",
            "total_fields": [("Contract Value", "contract_amount")],
            "editable_statuses": ["draft", "rejected"],
            "actions": APPROVAL_WORKFLOW,
        },
        "purchase-tenders": {
            "line_model": PurchaseTenderLine,
            "line_relation": "lines",
            "header_fields": ["company", "number", "title", "category", "tender_type", "award_strategy", "publish_date", "close_date", "budget_amount", "description"],
            "line_fields": ["request_line", "product", "description", "specification", "quantity", "target_unit_price", "delivery_date"],
            "line_title": "Tender Scope Lines",
            "total_fields": [("Budget", "budget_amount")],
            "editable_statuses": [PurchaseTender.Status.DRAFT],
            "actions": {
                "publish": {"label": "Publish", "from": [PurchaseTender.Status.DRAFT], "to": PurchaseTender.Status.PUBLISHED, "primary": True},
                "open-submission": {"label": "Open Submission", "from": [PurchaseTender.Status.PUBLISHED], "to": PurchaseTender.Status.SUBMISSION, "primary": True},
                "evaluate": {"label": "Start Evaluation", "from": [PurchaseTender.Status.SUBMISSION], "to": PurchaseTender.Status.EVALUATION, "primary": True},
                "award": {"label": "Award", "from": [PurchaseTender.Status.EVALUATION], "to": PurchaseTender.Status.AWARDED, "primary": True},
                "close": {"label": "Close", "from": [PurchaseTender.Status.AWARDED], "to": PurchaseTender.Status.CLOSED, "primary": True},
                "cancel": {"label": "Cancel", "from": [PurchaseTender.Status.DRAFT, PurchaseTender.Status.PUBLISHED, PurchaseTender.Status.SUBMISSION, PurchaseTender.Status.EVALUATION], "to": PurchaseTender.Status.CANCELLED},
            },
        },
        "blanket-orders": {
            "line_model": BlanketOrderLine,
            "line_relation": "lines",
            "header_fields": ["company", "number", "vendor", "date_start", "date_end", "agreement_value", "consumed_value", "terms"],
            "line_fields": ["product", "description", "agreed_quantity", "released_quantity", "unit_price", "minimum_release_quantity"],
            "line_title": "Agreement Lines",
            "total_fields": [("Agreement Value", "agreement_value"), ("Consumed", "consumed_value")],
            "editable_statuses": [BlanketOrder.Status.DRAFT],
            "actions": {
                "activate": {"label": "Activate", "from": [BlanketOrder.Status.DRAFT], "to": BlanketOrder.Status.ACTIVE, "primary": True},
                "exhaust": {"label": "Mark Exhausted", "from": [BlanketOrder.Status.ACTIVE], "to": BlanketOrder.Status.EXHAUSTED},
                "expire": {"label": "Mark Expired", "from": [BlanketOrder.Status.ACTIVE], "to": BlanketOrder.Status.EXPIRED},
                "cancel": {"label": "Cancel", "from": [BlanketOrder.Status.DRAFT, BlanketOrder.Status.ACTIVE], "to": BlanketOrder.Status.CANCELLED},
            },
        },
        "goods-receipts": {
            "line_model": GoodsReceiptLine,
            "line_relation": "lines",
            "header_fields": ["company", "number", "order", "warehouse", "receipt_date", "receiving_mode", "delivery_status", "notes"],
            "line_fields": ["order_line", "received_quantity", "accepted_quantity", "rejected_quantity", "rejection_reason"],
            "line_title": "Received Lines and Inspection",
            "total_fields": [],
            "editable_statuses": [GoodsReceipt.Status.DRAFT, GoodsReceipt.Status.INSPECTING],
            "actions": {
                "inspect": {"label": "Start Inspection", "from": [GoodsReceipt.Status.DRAFT], "to": GoodsReceipt.Status.INSPECTING, "primary": True},
                "receive": {"label": "Accept Receipt", "from": [GoodsReceipt.Status.INSPECTING], "to": GoodsReceipt.Status.RECEIVED, "primary": True},
                "reject": {"label": "Reject Receipt", "from": [GoodsReceipt.Status.INSPECTING], "to": GoodsReceipt.Status.REJECTED},
                "cancel": {"label": "Cancel", "from": [GoodsReceipt.Status.DRAFT], "to": GoodsReceipt.Status.CANCELLED},
            },
        },
        "purchase-accounting-documents": {
            "line_model": PurchaseAccountingLine,
            "line_relation": "lines",
            "header_fields": ["order", "invoice", "number", "document_type", "document_date", "total_amount", "journal_reference"],
            "line_fields": ["account_code", "description", "debit", "credit", "cost_center", "tax_code"],
            "line_title": "Accounting Lines",
            "total_fields": [("Total", "total_amount")],
            "editable_statuses": ["draft", "rejected"],
            "actions": {
                **APPROVAL_WORKFLOW,
                "approve": {**APPROVAL_WORKFLOW["approve"], "requires_balance": True},
            },
        },
        "purchase-inventory-transfers": {
            "line_model": PurchaseInventoryTransferLine,
            "line_relation": "lines",
            "header_fields": ["receipt", "reference", "source_warehouse", "destination_warehouse", "transfer_date", "notes"],
            "line_fields": ["receipt_line", "product", "quantity"],
            "line_title": "Transfer Lines",
            "total_fields": [],
            "editable_statuses": [PurchaseInventoryTransfer.Status.DRAFT, PurchaseInventoryTransfer.Status.READY],
            "actions": {
                "ready": {"label": "Mark Ready", "from": [PurchaseInventoryTransfer.Status.DRAFT], "to": PurchaseInventoryTransfer.Status.READY, "primary": True},
                "dispatch": {"label": "Dispatch", "from": [PurchaseInventoryTransfer.Status.READY], "to": PurchaseInventoryTransfer.Status.IN_TRANSIT, "primary": True},
                "complete": {"label": "Complete", "from": [PurchaseInventoryTransfer.Status.IN_TRANSIT], "to": PurchaseInventoryTransfer.Status.DONE, "primary": True},
                "cancel": {"label": "Cancel", "from": [PurchaseInventoryTransfer.Status.DRAFT, PurchaseInventoryTransfer.Status.READY], "to": PurchaseInventoryTransfer.Status.CANCELLED},
            },
        },
    }
)

DOCUMENT_CONFIG.update(
    {
        "construction-boq-templates": {
            "line_model": construction_models.ConstructionBOQTemplateItem,
            "line_relation": "items",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-boq-templates"]["form_fields"],
            "line_fields": ["sequence", "parent_item", "code", "name", "description", "level", "is_leaf_node", "is_direct_amount", "direct_amount_info", "product_group", "product", "unit", "quantity", "unit_cost"],
            "line_title": "WBS Template Lines",
            "total_fields": [], "editable_statuses": None, "actions": {},
        },
        "construction-boq-template-items": {
            "line_model": construction_models.ConstructionBOQTemplateCostLine,
            "line_relation": "cost_lines",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-boq-template-items"]["form_fields"],
            "line_fields": ["sequence", "input_type", "cost_category", "product", "job_position", "vendor", "work_package", "description", "ownership", "quantity", "unit", "unit_rate", "notes"],
            "line_title": "Template Resource Lines",
            "total_fields": [], "editable_statuses": None, "actions": {},
        },
        "construction-contracts": {
            "line_model": construction_models.ConstructionContractDPLine,
            "line_relation": "down_payment_lines",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-contracts"]["form_fields"],
            "line_fields": ["sequence", "invoice", "invoice_date", "portion_percent", "invoice_amount", "payment_status"],
            "line_title": "Down Payment Schedule",
            "line_sections": [
                {"line_model": construction_models.ConstructionContractDPLine, "line_relation": "down_payment_lines", "line_title": "Down Payment Schedule", "line_fields": ["sequence", "invoice", "invoice_date", "portion_percent", "invoice_amount", "payment_status"], "line_select_related": ("invoice",)},
                {"line_model": construction_models.ConstructionContractMilestone, "line_relation": "milestones", "line_title": "Contract Milestones", "line_fields": ["sequence", "name", "description", "due_date", "percentage", "amount", "based_on_work_item", "linked_boq_items", "status", "achieved", "achieved_date"]},
            ],
            "total_fields": [("Contract Value", "contract_value"), ("Down Payment", "down_payment_amount")],
            "editable_statuses": ["draft", "active", "on_hold"], "actions": CONTRACT_WORKFLOW,
        },
        "construction-boqs": {
            "line_model": construction_models.ConstructionBOQItem,
            "line_relation": "items",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-boqs"]["form_fields"],
            "line_fields": ["sequence", "parent_item", "wbs_code", "level", "is_leaf_node", "is_direct_amount", "work_package", "product_group", "product", "description", "unit", "quantity", "unit_price", "budget_cost", "procurement_method", "commercial_markup_type", "commercial_markup_value", "commercial_discount_type", "commercial_discount_value", "commercial_tax_percent"],
            "line_title": "WBS / BOQ Lines",
            "total_fields": [("Cost", "total_cost"), ("Revenue Before Tax", "total_revenue_before_tax"), ("Tax", "total_tax_amount"), ("Revenue Including Tax", "total_revenue_including_tax"), ("Margin", "total_margin")],
            "editable_statuses": ["draft", "review", "revised"], "actions": BOQ_WORKFLOW,
        },
        "construction-boq-items": {
            "line_model": construction_models.ConstructionBOQCostLine,
            "line_relation": "cost_lines",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-boq-items"]["form_fields"],
            "line_fields": ["sequence", "input_type", "cost_category", "product", "job_position", "vendor", "work_package", "description", "ownership", "quantity", "unit", "unit_rate", "markup_type", "markup_value", "discount_type", "discount_value", "tax_percent", "notes"],
            "line_title": "Resource Cost Lines",
            "total_fields": [("Sales Amount", "sales_amount"), ("Budget Amount", "budget_amount")],
            "editable_statuses": None, "actions": {},
        },
        "construction-cost-sheets": {
            "line_model": construction_models.ConstructionCostSheetItem,
            "line_relation": "items",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-cost-sheets"]["form_fields"],
            "line_fields": ["sequence", "boq_item", "parent_item", "wbs_code", "level", "is_leaf_node", "is_cost_sheet_only", "description", "product", "quantity", "unit_cost", "revenue_adjustment", "budget_cost_adjustment", "quantity_adjustment", "unit_rate_adjustment", "utilised_budget", "planned_start", "planned_end"],
            "line_title": "Cost Sheet WBS Lines",
            "total_fields": [("Revised Revenue", "revised_revenue"), ("Revised Cost", "revised_cost"), ("Utilised", "utilised_amount"), ("Estimated Margin", "estimated_margin")],
            "editable_statuses": ["draft", "submitted", "rejected"], "actions": APPROVAL_WORKFLOW,
        },
        "construction-cost-sheet-items": {
            "line_model": construction_models.ConstructionCostSheetResource,
            "line_relation": "resources",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-cost-sheet-items"]["form_fields"],
            "line_fields": ["sequence", "boq_resource", "cost_category", "input_type", "product", "job_position", "vendor", "work_package", "description", "unit", "quantity", "unit_rate", "budget_cost_adjustment", "revised_cost", "ownership", "reserved_quantity", "delivered_quantity", "utilised_quantity", "utilised_budget", "onsite_stock_quantity", "notes"],
            "line_title": "Cost Resources",
            "total_fields": [("Planned Amount", "planned_amount")], "editable_statuses": None, "actions": {},
        },
        "construction-budget-changes": {
            "line_model": construction_models.ConstructionBudgetChangeLine,
            "line_relation": "lines",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-budget-changes"]["form_fields"],
            "line_fields": ["sequence", "line_type", "change_type", "cost_sheet_item", "cost_sheet_resource", "description", "previous_amount", "proposed_amount", "current_quantity", "revised_quantity", "current_unit_rate", "revised_unit_rate", "new_wbs_code", "new_wbs_name", "new_description", "new_plan_start", "new_plan_end", "cost_category", "product", "job_position", "vendor", "work_package", "zeroed"],
            "line_title": "Budget Change Lines",
            "total_fields": [("Increase", "total_increase"), ("Decrease", "total_decrease"), ("Net Impact", "amount"), ("Revised Cost", "revised_total_cost")],
            "editable_statuses": ["draft", "rejected"], "actions": APPROVAL_WORKFLOW,
        },
        "construction-job-orders": {
            "line_model": construction_models.ConstructionProgressLog,
            "line_relation": "progress_logs",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-job-orders"]["form_fields"],
            "line_fields": ["log_date", "progress", "completed_quantity", "cumulative_quantity", "weather", "notes", "evidence", "recorded_by"],
            "line_title": "Progress Logs",
            "line_sections": [
                {"line_model": construction_models.ConstructionProgressLog, "line_relation": "progress_logs", "line_title": "Progress Logs", "line_fields": ["log_date", "progress", "completed_quantity", "cumulative_quantity", "weather", "notes", "evidence", "recorded_by"]},
                {"line_model": construction_models.ConstructionTaskWorker, "line_relation": "workers", "line_title": "Task Workers", "line_fields": ["employee", "role", "assigned_date", "released_date", "planned_hours", "actual_hours"]},
                {"line_model": construction_models.ConstructionEquipmentFacility, "line_relation": "equipment_facilities", "line_title": "Equipment & Facilities", "line_fields": ["asset", "category", "start_date", "end_date", "planned_hours", "actual_hours", "notes"]},
            ],
            "total_fields": [("Progress", "progress"), ("Completed Quantity", "completed_quantity")],
            "editable_statuses": ["draft", "planned", "in_progress", "on_hold"], "actions": EXECUTION_WORKFLOW,
        },
        "construction-quantity-surveys": {
            "line_model": construction_models.ConstructionQuantitySurveyLine,
            "line_relation": "lines",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-quantity-surveys"]["form_fields"],
            "line_fields": ["sequence", "boq_item", "previous_quantity", "measured_quantity", "accepted_quantity", "contract_quantity", "unit_rate", "location", "remarks"],
            "line_title": "Quantity Survey Lines", "total_fields": [],
            "editable_statuses": ["draft", "rejected"], "actions": APPROVAL_WORKFLOW,
        },
        "construction-quantity-survey-lines": {
            "line_model": construction_models.ConstructionQuantitySurveyJOSource,
            "line_relation": "job_order_sources",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-quantity-survey-lines"]["form_fields"],
            "line_fields": ["progress_log", "previous_approved_quantity", "approved_quantity", "notes"],
            "line_title": "Job Order Progress Sources", "total_fields": [("Amount", "amount"), ("Progress", "progress_percent")],
            "editable_statuses": None, "actions": {},
        },
        "construction-resource-actualizations": {
            "line_model": construction_models.ConstructionResourceActualizationLine,
            "line_relation": "lines",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-resource-actualizations"]["form_fields"],
            "line_fields": ["sequence", "cost_sheet_resource", "job_order", "resource_type", "product", "employee", "asset", "description", "quantity", "unit_cost", "revised_quantity_snapshot", "revised_cost_snapshot", "remaining_quantity", "remaining_budget", "source_warehouse", "reference_document", "remarks"],
            "line_title": "Actualized Resources",
            "total_fields": [("Budget", "budget_cost"), ("Actual", "total_actual_amount"), ("Variance", "variance_amount")],
            "editable_statuses": ["draft", "rejected"], "actions": APPROVAL_WORKFLOW,
        },
        "construction-timesheets": {
            "line_model": construction_models.ConstructionTimesheetLine,
            "line_relation": "lines",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-timesheets"]["form_fields"],
            "line_fields": ["employee", "job_order", "work_date", "hours", "description"],
            "line_title": "Construction Time Logs", "total_fields": [],
            "editable_statuses": ["draft", "rejected"], "actions": APPROVAL_WORKFLOW,
        },
        "construction-progressive-claims": {
            "line_model": construction_models.ConstructionProgressiveClaimLine,
            "line_relation": "lines",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-progressive-claims"]["form_fields"],
            "line_fields": ["sequence", "boq_item", "quantity_survey_line", "job_orders", "previous_quantity", "current_quantity", "certified_quantity", "contract_quantity", "unit_price", "source"],
            "line_title": "Claim Lines",
            "line_sections": [
                {"line_model": construction_models.ConstructionProgressiveClaimLine, "line_relation": "lines", "line_title": "Claim Lines", "line_fields": ["sequence", "boq_item", "quantity_survey_line", "job_orders", "previous_quantity", "current_quantity", "certified_quantity", "contract_quantity", "unit_price", "source"]},
                {"line_model": construction_models.ConstructionProgressiveClaimAttachment, "line_relation": "attachments", "line_title": "Supporting Attachments", "line_fields": ["sequence", "document_name", "document_type", "document_date", "uploaded_by", "file"]},
            ],
            "total_fields": [("Gross", "gross_amount"), ("Retention", "retention_amount"), ("Advance Recovery", "advance_recovery"), ("Net", "net_amount")],
            "editable_statuses": ["draft", "rejected"], "actions": CLAIM_WORKFLOW,
        },
        "construction-material-demands": {
            "line_model": construction_models.ConstructionMaterialDemandLine,
            "line_relation": "lines",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-material-demands"]["form_fields"],
            "line_fields": ["product", "description", "requested_quantity", "allocated_quantity", "delivered_quantity"],
            "line_title": "Material Demand Lines", "total_fields": [],
            "editable_statuses": ["draft", "rejected"], "actions": APPROVAL_WORKFLOW,
        },
        "construction-account-settings": {
            "line_model": construction_models.ConstructionProjectAccountSettingsLine,
            "line_relation": "cip_lines",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-account-settings"]["form_fields"],
            "line_fields": ["resource_category", "account", "usage_help"],
            "line_title": "CIP Accounts by Resource Category", "total_fields": [],
            "editable_statuses": None, "actions": {},
        },
        "construction-subcontract-job-orders": {
            "line_model": construction_models.ConstructionSubcontractProgressLine,
            "line_relation": "progress_lines",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-subcontract-job-orders"]["form_fields"],
            "line_fields": ["progress_date", "quantity", "cumulative_quantity", "progress_percent", "amount", "evidence", "notes"],
            "line_title": "Subcontract Progress",
            "line_sections": [
                {"line_model": construction_models.ConstructionSubcontractProgressLine, "line_relation": "progress_lines", "line_title": "Subcontract Progress", "line_fields": ["progress_date", "quantity", "cumulative_quantity", "progress_percent", "amount", "evidence", "notes"]},
                {"line_model": construction_models.ConstructionSubcontractMilestone, "line_relation": "milestones", "line_title": "Payment Milestones", "line_fields": ["sequence", "name", "percentage", "amount", "due_date", "achieved", "achieved_date", "invoice"]},
            ],
            "total_fields": [("Contract Amount", "contract_amount"), ("Progress", "progress")],
            "editable_statuses": ["draft", "planned", "in_progress", "on_hold"], "actions": EXECUTION_WORKFLOW,
        },
        "construction-inventory-transfers": {
            "line_model": construction_models.ConstructionInventoryTransferLine,
            "line_relation": "lines",
            "header_fields": CONSTRUCTION_ENTITY_CONFIG["construction-inventory-transfers"]["form_fields"],
            "line_fields": ["material_demand_line", "cost_sheet_resource", "product", "quantity", "notes"],
            "line_title": "Transfer Lines", "total_fields": [],
            "editable_statuses": ["draft", "ready"],
            "actions": {
                "ready": {"label": "Mark Ready", "from": ["draft"], "to": "ready", "primary": True},
                "dispatch": {"label": "Dispatch", "from": ["ready"], "to": "in_transit", "primary": True},
                "complete": {"label": "Complete", "from": ["in_transit"], "to": "done", "primary": True},
                "cancel": {"label": "Cancel", "from": ["draft", "ready"], "to": "cancelled"},
            },
        },
    }
)

DOCUMENT_CONFIG["construction-contracts"]["actions"]["create-dp-invoices"] = {
    "label": "Create DP Invoices", "from": ["draft"], "to": "draft", "preserve_status": True,
}
DOCUMENT_CONFIG["construction-contracts"]["actions"]["release-retention-1"] = {
    "label": "Release Retention 1", "from": ["active", "completed"], "to": "active", "preserve_status": True,
}
DOCUMENT_CONFIG["construction-contracts"]["actions"]["release-retention-2"] = {
    "label": "Release Retention 2", "from": ["active", "completed"], "to": "active", "preserve_status": True,
}
DOCUMENT_CONFIG["construction-boqs"]["actions"]["recompute"] = {
    "label": "Recompute Commercials", "from": ["draft", "review", "revised"], "to": "draft", "preserve_status": True,
}
DOCUMENT_CONFIG["construction-cost-sheets"]["actions"].update(
    {
        "load-boq": {"label": "Load BOQ", "from": ["draft"], "to": "draft", "primary": True, "preserve_status": True},
        "recompute": {"label": "Recompute", "from": ["draft", "rejected"], "to": "draft", "preserve_status": True},
    }
)
DOCUMENT_CONFIG["construction-quantity-surveys"]["actions"]["refresh"] = {
    "label": "Refresh from Job Orders", "from": ["draft", "rejected"], "to": "draft", "preserve_status": True,
}
DOCUMENT_CONFIG["construction-progressive-claims"]["actions"]["recompute"] = {
    "label": "Recompute Claim", "from": ["draft", "rejected"], "to": "draft", "preserve_status": True,
}
DOCUMENT_CONFIG["construction-material-demands"]["actions"]["create-purchase-request"] = {
    "label": "Create Purchase Request", "from": ["approved"], "to": "approved", "primary": True, "preserve_status": True,
}


def health(request):
    return JsonResponse({"status": "ok", "service": "erp-gsshop"})


def _config(entity):
    try:
        return ENTITY_CONFIG[entity]
    except KeyError as exc:
        raise Http404("Unknown ERP module") from exc


def _can(user, action, model, entity=None):
    return (
        user.has_perm(f"{model._meta.app_label}.{action}_{model._meta.model_name}")
        and can_hr_entity_action(user, entity, action)
        and can_purchase_entity_action(user, entity, action)
        and can_construction_entity_action(user, entity, action)
        and can_pos_entity_action(user, entity, action)
        and can_accounting_entity_action(user, entity, action)
    )


def _scope_queryset(user, entity, queryset):
    queryset = scope_hr_queryset(user, entity, queryset)
    queryset = scope_purchase_queryset(user, entity, queryset)
    queryset = scope_construction_queryset(user, entity, queryset)
    queryset = scope_pos_queryset(user, entity, queryset)
    queryset = scope_accounting_queryset(user, entity, queryset)
    return scope_organization_queryset(
        queryset,
        include_branch=entity != "branches",
        use_allowed_companies=entity == "companies",
    )


def _scope_form(form, user, entity):
    scope_hr_form(form, user, entity)
    scope_purchase_form(form, user, entity)
    scope_construction_form(form, user, entity)
    scope_pos_form(form, user, entity)
    scope_accounting_form(form, user, entity)
    return scope_organization_form(form, include_branch=entity != "branches")


def _can_workflow(user, entity, action, instance):
    return (
        can_hr_workflow_action(user, entity, action, instance)
        and can_purchase_workflow_action(user, entity, action)
        and can_construction_workflow_action(user, entity, action)
        and can_pos_workflow_action(user, entity, action)
        and can_accounting_workflow_action(user, entity, action)
    )


def _hr_audit_company_id(instance):
    """Resolve the company for one HR workflow record without recordset loops."""
    if getattr(instance, "company_id", None):
        return instance.company_id
    relation_paths = (
        "employee.company_id",
        "contract.employee.company_id",
        "payroll_run.company_id",
        "request.employee.company_id",
        "document.employee.company_id",
    )
    for path in relation_paths:
        value = instance
        for part in path.split("."):
            value = getattr(value, part, None)
            if value is None:
                break
        if value:
            return value
    return None


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
    form = header_form_class(request.POST or None, request.FILES or None, instance=instance)
    section_definitions = document.get("line_sections") or [{
        "line_model": document["line_model"],
        "line_relation": document["line_relation"],
        "line_fields": document["line_fields"],
        "line_title": document["line_title"],
        "line_select_related": document.get("line_select_related", ()),
        "fk_name": document.get("fk_name"),
    }]
    line_sections = []
    for index, section in enumerate(section_definitions):
        prefix = f"document_lines_{index}" if document.get("line_sections") else "document_lines"
        line_form_class = SaleOrderLineForm if entity == "sales" and index == 0 else StyledModelForm
        formset_class = inlineformset_factory(
            model,
            section["line_model"],
            form=line_form_class,
            fields=section["line_fields"],
            extra=section.get("extra", 1),
            can_delete=True,
            fk_name=section.get("fk_name"),
        )
        line_queryset = section["line_model"].objects.all()
        related = section.get("line_select_related", ())
        if entity == "sales" and index == 0:
            related = ("product",)
        if related:
            line_queryset = line_queryset.select_related(*related)
        formset = formset_class(
            request.POST or None,
            request.FILES or None,
            instance=instance,
            prefix=prefix,
            queryset=line_queryset,
        )
        line_sections.append(
            {
                **section,
                "index": index,
                "prefix": prefix,
                "formset": formset,
                "developer_meta": developer_metadata(
                    section["line_model"],
                    entity=f"{entity}:{section['line_relation']}",
                ),
            }
        )
    formset = line_sections[0]["formset"]
    _scope_form(form, request.user, entity)
    for section in line_sections:
        for line_form in section["formset"].forms:
            _scope_form(line_form, request.user, entity)
    workflow_field = config.get("workflow_field", "status")
    current_status = getattr(instance, workflow_field, None) if instance else None
    editable_statuses = document.get("editable_statuses")
    editable = instance is None or editable_statuses is None or current_status in editable_statuses

    if request.method == "POST" and not editable:
        messages.error(request, "This document is locked by its workflow status.")
        return None, redirect("entity_edit", entity=entity, pk=instance.pk)

    formsets_valid = all(section["formset"].is_valid() for section in line_sections)
    if editable and form.is_valid() and formsets_valid:
        with transaction.atomic():
            saved = form.save()
            for section in line_sections:
                section["formset"].instance = saved
                section["formset"].save()
            if model._meta.app_label == "gsshop_construction":
                handle_construction_document_save(entity, saved, request.user)
        saved.refresh_from_db()
        messages.success(request, f"{config['singular']} saved with its detail lines.")
        return None, redirect("entity_edit", entity=entity, pk=saved.pk)

    if not editable:
        for field in form.fields.values():
            field.disabled = True
        for section in line_sections:
            for line_form in section["formset"].forms:
                for field in line_form.fields.values():
                    field.disabled = True

    status_choices = list(model._meta.get_field(workflow_field).choices or ()) if hasattr(model, workflow_field) else []
    available_actions = [
        {"key": key, **definition}
        for key, definition in document.get("actions", {}).items()
        if instance
        and current_status in definition["from"]
        and _can_workflow(request.user, entity, key, instance)
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
        "line_sections": line_sections,
        "mode": mode,
        "editable": editable,
        "current_status": current_status,
        "status_choices": status_choices,
        "available_actions": available_actions,
        "totals": totals,
        "developer_meta": developer_metadata(model, entity=entity, instance=instance),
    }
    return context, None


@require_GET
@login_required
def product_search(request):
    if not _can(request.user, "view", Product, "products"):
        return JsonResponse({"detail": "Anda tidak memiliki akses ke master produk."}, status=403)

    products = _scope_queryset(request.user, "products", Product.objects.filter(active=True))
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

    customers = _scope_queryset(request.user, "contacts", Contact.objects.filter(
        active=True,
        contact_type__in=[Contact.ContactType.CUSTOMER, Contact.ContactType.BOTH],
    ))
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
    def visible(entity, model):
        if not _can(request.user, "view", model, entity):
            return model.objects.none()
        return _scope_queryset(request.user, entity, model.objects.all())

    visible_employees = visible("employees", Employee)
    visible_attendance = visible("attendance", Attendance)
    visible_sales = visible("sales", SaleOrder)
    visible_invoices = visible("invoices", Invoice)
    visible_products = visible("products", Product)
    sales_total = visible_sales.exclude(status=SaleOrder.Status.CANCELLED).aggregate(value=Sum("total"))["value"] or 0
    receivables = (
        visible_invoices.filter(invoice_type=Invoice.InvoiceType.CUSTOMER)
        .exclude(status__in=[Invoice.Status.PAID, Invoice.Status.CANCELLED])
        .aggregate(value=Sum(F("total") - F("paid_amount")))["value"]
        or 0
    )
    context = {
        "today": today,
        "metrics": [
            {"label": "Sales Value", "value": sales_total, "kind": "currency", "tone": "emerald"},
            {"label": "Receivables", "value": receivables, "kind": "currency", "tone": "amber"},
            {"label": "Active Products", "value": visible_products.filter(active=True).count(), "tone": "blue"},
            {"label": "Employees", "value": visible_employees.filter(status=Employee.Status.ACTIVE).count(), "tone": "violet"},
        ],
        "recent_sales": visible_sales.select_related("customer")[:6],
        "low_stock": visible_products.filter(active=True, product_type=Product.ProductType.STOCKABLE, current_stock__lte=F("reorder_level"))[:6],
        "today_attendance": visible_attendance.filter(attendance_date=today).count(),
        "company_count": len(request.organization_context.allowed_company_ids),
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
    queryset = _scope_queryset(request.user, entity, model.objects.all())
    if config.get("select_related"):
        queryset = queryset.select_related(*config["select_related"])
    query = request.GET.get("q", "").strip()
    if query:
        filters = Q()
        for field in config["search"]:
            filters |= Q(**{f"{field}__icontains": query})
        queryset = queryset.filter(filters)
    if not queryset.ordered:
        queryset = queryset.order_by("pk")
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    columns = [
        {"label": label, **developer_field_metadata(model, field)}
        for field, label in config["columns"]
    ]
    rows = [
        {
            "object": obj,
            "developer_meta": developer_metadata(model, entity=entity, instance=obj),
            "cells": [
                {"value": _display_value(obj, column["name"]), **column}
                for column in columns
            ],
        }
        for obj in page.object_list
    ]
    context = {
        "entity": entity,
        "config": config,
        "columns": columns,
        "rows": rows,
        "page": page,
        "query": query,
        "developer_meta": developer_metadata(model, entity=entity),
        "can_add": config.get("creatable", True) and _can(request.user, "add", model, entity),
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
    if not config.get("creatable", True):
        messages.error(request, "This history or audit record is generated by the system and is read-only.")
        return redirect("entity_list", entity=entity)
    if not _can(request.user, "add", model, entity):
        messages.error(request, "You do not have permission to add this record.")
        return redirect("entity_list", entity=entity)
    if entity == "training-conducts":
        return training_conduct_form(request, config, can_change=True)
    if entity in DOCUMENT_CONFIG:
        context, response = _document_context(request, entity, config)
        return response or render(request, "core/document_form.html", context)
    form = _form_class(config)(request.POST or None, request.FILES or None)
    _scope_form(form, request.user, entity)
    if form.is_valid():
        instance = form.save(commit=False)
        if isinstance(instance, StockMove):
            instance.created_by = request.user
        instance.save()
        form.save_m2m()
        messages.success(request, f"{config['singular']} created successfully.")
        return redirect("entity_list", entity=entity)
    return render(
        request,
        "core/entity_form.html",
        {
            "form": form,
            "config": config,
            "entity": entity,
            "mode": "Create",
            "developer_meta": developer_metadata(model, entity=entity),
        },
    )


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
        scoped_queryset = _scope_queryset(request.user, entity, model.objects.all())
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
    scoped_queryset = _scope_queryset(request.user, entity, model.objects.all())
    if entity == "raw-attendance" and request.method == "POST":
        return _edit_raw_attendance(request, config, scoped_queryset, pk)
    if entity == "attendance-change-requests" and request.method == "POST":
        return _edit_attendance_change_request(request, config, scoped_queryset, pk)
    if entity == "contracts" and request.method == "POST":
        return _edit_employment_contract(request, config, scoped_queryset, pk)
    if entity == "employee-change-requests" and request.method == "POST":
        return _edit_employee_change_request(request, config, scoped_queryset, pk)
    instance = get_object_or_404(scoped_queryset, pk=pk)
    if entity in DOCUMENT_CONFIG:
        context, response = _document_context(request, entity, config, instance=instance, mode="Edit")
        return response or render(request, "core/document_form.html", context)
    current_status = getattr(instance, config.get("workflow_field", "status"), None)
    locked_statuses = set(config.get("workflow_locked_statuses", {"approved", "cancelled"}))
    workflow_locked = (entity == "attendance" and instance.from_raw_attendance) or (
        bool(config.get("workflow")) and current_status in locked_statuses
    )
    if request.method == "POST" and workflow_locked:
        messages.error(request, "This record is locked by its workflow status.")
        return redirect("entity_edit", entity=entity, pk=pk)
    form = _form_class(config)(request.POST or None, request.FILES or None, instance=instance)
    _scope_form(form, request.user, entity)
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
        if current_status in definition["from"] and _can_workflow(request.user, entity, key, instance)
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
            "developer_meta": developer_metadata(model, entity=entity, instance=instance),
        },
    )


@transaction.atomic
def _edit_raw_attendance(request, config, scoped_queryset, pk):
    """Serialize edits with processing and keep consumed punches immutable."""
    original = get_object_or_404(scoped_queryset, pk=pk)
    Employee.objects.select_for_update().get(pk=original.employee_id)
    instance = get_object_or_404(scoped_queryset.select_for_update(), pk=pk)
    if instance.employee_id != original.employee_id or instance.processed:
        messages.error(request, "This raw punch changed or was already processed. Reload the record.")
        return redirect("entity_edit", entity="raw-attendance", pk=pk)
    form = RawAttendanceForm(request.POST, request.FILES, instance=instance)
    _scope_form(form, request.user, "raw-attendance")
    if form.is_valid():
        form.save()
        messages.success(request, "Raw Attendance Event updated successfully.")
        return redirect("entity_list", entity="raw-attendance")
    return render(
        request,
        "core/entity_form.html",
        {
            "form": form,
            "config": config,
            "entity": "raw-attendance",
            "mode": "Edit",
            "object": instance,
            "current_status": instance.processed,
            "workflow_actions": [{"key": "process", **config["workflow"]["process"]}],
            "workflow_locked": False,
            "developer_meta": developer_metadata(RawAttendance, entity="raw-attendance", instance=instance),
        },
    )


@transaction.atomic
def _edit_attendance_change_request(request, config, scoped_queryset, pk):
    """Keep submitted correction values immutable under concurrent approval."""
    instance = get_object_or_404(scoped_queryset.select_for_update(), pk=pk)
    if instance.status != "draft":
        messages.error(request, "This record is locked by its workflow status.")
        return redirect("entity_edit", entity="attendance-change-requests", pk=pk)
    form = AttendanceChangeRequestForm(request.POST, request.FILES, instance=instance)
    _scope_form(form, request.user, "attendance-change-requests")
    if form.is_valid():
        form.save()
        messages.success(request, "Attendance Change Request updated successfully.")
        return redirect("entity_list", entity="attendance-change-requests")
    return render(
        request,
        "core/entity_form.html",
        {
            "form": form,
            "config": config,
            "entity": "attendance-change-requests",
            "mode": "Edit",
            "object": instance,
            "current_status": instance.status,
            "workflow_actions": [{"key": "submit", **APPROVAL_WORKFLOW["submit"]}],
            "workflow_locked": False,
            "developer_meta": developer_metadata(AttendanceChangeRequest, entity="attendance-change-requests", instance=instance),
        },
    )


@transaction.atomic
def _edit_employment_contract(request, config, scoped_queryset, pk):
    """Reject a draft edit if another actor activated the contract first."""
    instance = get_object_or_404(scoped_queryset.select_for_update(), pk=pk)
    if instance.status != EmploymentContract.Status.DRAFT:
        messages.error(request, "This record is locked by its workflow status.")
        return redirect("entity_edit", entity="contracts", pk=pk)
    form = EmploymentContractForm(request.POST, request.FILES, instance=instance)
    _scope_form(form, request.user, "contracts")
    if form.is_valid():
        form.save()
        messages.success(request, "Employment Contract updated successfully.")
        return redirect("entity_list", entity="contracts")
    return render(
        request,
        "core/entity_form.html",
        {
            "form": form,
            "config": config,
            "entity": "contracts",
            "mode": "Edit",
            "object": instance,
            "current_status": instance.status,
            "workflow_actions": [{"key": "activate", **EMPLOYMENT_CONTRACT_WORKFLOW["activate"]}],
            "workflow_locked": False,
            "developer_meta": developer_metadata(EmploymentContract, entity="contracts", instance=instance),
        },
    )


@transaction.atomic
def _edit_employee_change_request(request, config, scoped_queryset, pk):
    """Keep draft edits separate from concurrent submission or profile updates."""
    instance = get_object_or_404(scoped_queryset.select_for_update(), pk=pk)
    if instance.status != "draft":
        messages.error(request, "This record is locked by its workflow status.")
        return redirect("entity_edit", entity="employee-change-requests", pk=pk)

    form = EmployeeChangeRequestForm(request.POST, request.FILES, instance=instance)
    _scope_form(form, request.user, "employee-change-requests")
    if form.is_valid():
        employee = Employee.objects.select_for_update().get(pk=form.cleaned_data["employee"].pk)
        actual_value = getattr(employee, form.cleaned_data["field_name"])
        if actual_value != form.cleaned_data["current_value"]:
            form.add_error("employee", "The employee profile changed while this draft was being edited. Please review and save again.")
        else:
            form.save()
            messages.success(request, "Employee Change Request updated successfully.")
            return redirect("entity_list", entity="employee-change-requests")

    workflow_actions = [
        {"key": key, **definition}
        for key, definition in config["workflow"].items()
        if instance.status in definition["from"] and _can_workflow(request.user, "employee-change-requests", key, instance)
    ]
    return render(
        request,
        "core/entity_form.html",
        {
            "form": form,
            "config": config,
            "entity": "employee-change-requests",
            "mode": "Edit",
            "object": instance,
            "current_status": instance.status,
            "workflow_actions": workflow_actions,
            "workflow_locked": False,
            "developer_meta": developer_metadata(EmployeeChangeRequest, entity="employee-change-requests", instance=instance),
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
    scoped_queryset = _scope_queryset(request.user, entity, model.objects.all())
    instance = get_object_or_404(scoped_queryset, pk=pk)
    if entity == "attendance" and instance.from_raw_attendance:
        messages.error(request, "Raw-derived attendance must be changed through an attendance correction request.")
        return redirect("entity_list", entity=entity)
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
    queryset = _scope_queryset(request.user, entity, model.objects.all()).select_related(*report["select_related"])
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
        scoped_queryset = _scope_queryset(request.user, entity, model.objects.all())
        instance = get_object_or_404(scoped_queryset, pk=pk)
        if not _can_workflow(request.user, entity, action, instance):
            messages.error(request, "Your role cannot perform that workflow approval action.")
            return redirect("entity_edit", entity=entity, pk=pk)
        workflow_field = config.get("workflow_field", "status")
        current_status = getattr(instance, workflow_field, None)
        if current_status not in definition["from"]:
            messages.error(request, "That workflow action is not available for the current status.")
            return redirect("entity_edit", entity=entity, pk=pk)
        if entity == "product-budget-movements" and action == "approve":
            try:
                approve_budget_movement(pk, request.user)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.success(request, f"{config['singular']} moved to Approved.")
            return redirect("entity_edit", entity=entity, pk=pk)
        update_values = {workflow_field: definition["to"], "updated_at": timezone.now()}
        if action in {"approve", "reject"} and hasattr(instance, "approver_id"):
            update_values["approver_id"] = request.user.pk
        actual_status = definition["to"]
        with transaction.atomic():
            if entity == "raw-attendance":
                locked = get_object_or_404(scoped_queryset.select_related("employee"), pk=pk)
                if locked.processed != current_status:
                    updated = 0
                else:
                    try:
                        process_raw_attendance(locked, expected_company_id=locked.employee.company_id)
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                        return redirect("entity_edit", entity=entity, pk=pk)
                    instance = locked
                    updated = 1
            elif entity == "attendance-change-requests":
                locked = get_object_or_404(scoped_queryset.select_for_update(), pk=pk)
                if locked.status != current_status:
                    updated = 0
                else:
                    try:
                        transition_attendance_change_request(locked, action, request.user)
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                        return redirect("entity_edit", entity=entity, pk=pk)
                    instance = locked
                    updated = 1
            elif entity == "contracts":
                locked = get_object_or_404(scoped_queryset.select_for_update(), pk=pk)
                if locked.status != current_status:
                    updated = 0
                else:
                    try:
                        transition_employment_contract(locked, action)
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                        return redirect("entity_edit", entity=entity, pk=pk)
                    updated = 1
            elif entity == "employee-change-requests":
                locked = get_object_or_404(scoped_queryset.select_for_update(), pk=pk)
                if locked.status != current_status:
                    updated = 0
                else:
                    try:
                        transition_employee_change_request(locked, action, request.user)
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                        return redirect("entity_edit", entity=entity, pk=pk)
                    updated = 1
            elif definition.get("service_controls_state") and model._meta.app_label in {
                "gsshop_construction", "gsshop_finance", "gsshop_pos", "gsshop_hr", "gsshop_purchase"
            }:
                locked = get_object_or_404(scoped_queryset.select_for_update(), pk=pk)
                if getattr(locked, workflow_field) != current_status:
                    updated = 0
                else:
                    try:
                        if model._meta.app_label == "gsshop_finance":
                            handled = handle_accounting_workflow(entity, locked, action, request.user)
                        elif model._meta.app_label == "gsshop_construction":
                            handled = handle_construction_document_action(entity, locked, action, request.user)
                        else:
                            handled = handle_external_accounting_workflow(entity, locked, action, request.user)
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                        return redirect("entity_edit", entity=entity, pk=pk)
                    actual_status = getattr(handled, workflow_field, definition["to"])
                    updated = 1
            else:
                updated = scoped_queryset.filter(pk=pk, **{workflow_field: current_status}).update(**update_values)
            if updated == 1 and model._meta.app_label == "gsshop_hr":
                company_id = _hr_audit_company_id(instance)
                if company_id:
                    HRWorkflowAudit.objects.create(
                        company_id=company_id,
                        entity=entity,
                        record_id=instance.pk,
                        action=action,
                        previous_status="unprocessed" if entity == "raw-attendance" else current_status or "",
                        new_status="processed" if entity == "raw-attendance" else definition["to"],
                        actor=request.user,
                        details=getattr(instance, "_workflow_audit_details", {}),
                    )
            if updated == 1 and entity == "purchase-peppol-submissions":
                PurchasePeppolEvent.objects.create(
                    submission=instance,
                    status=definition["to"],
                    message=f"Workflow action: {action}",
                )
        if updated != 1:
            messages.error(request, "The record changed while this action was running. Please review it again.")
        elif entity == "raw-attendance":
            messages.success(request, "Raw punches processed into one daily attendance record.")
        elif actual_status is None and definition["to"] is None:
            messages.success(request, f"{config['singular']} action {definition['label']} completed.")
        elif actual_status != definition["to"]:
            messages.error(request, f"{config['singular']} finished in {str(actual_status).replace('_', ' ').title()}; review the validation details.")
        else:
            messages.success(request, f"{config['singular']} moved to {str(actual_status).replace('_', ' ').title()}.")
        return redirect("entity_edit", entity=entity, pk=pk)
    model = config["model"]
    if not _can(request.user, "change", model, entity):
        messages.error(request, "You do not have permission to update this document.")
        return redirect("entity_list", entity=entity)
    instance = get_object_or_404(_scope_queryset(request.user, entity, model.objects.all()), pk=pk)
    workflow_field = config.get("workflow_field", "status")
    current_status = getattr(instance, workflow_field, None)
    definition = document.get("actions", {}).get(action)
    if definition is None or current_status not in definition["from"]:
        messages.error(request, "That workflow action is not available for the current status.")
        return redirect("entity_edit", entity=entity, pk=pk)
    if not _can_workflow(request.user, entity, action, instance):
        messages.error(request, "Your role cannot perform that workflow approval action.")
        return redirect("entity_edit", entity=entity, pk=pk)
    if definition.get("requires_balance") and not instance.balanced:
        messages.error(request, "Accounting lines must have equal non-zero debit and credit totals before approval.")
        return redirect("entity_edit", entity=entity, pk=pk)
    with transaction.atomic():
        instance = get_object_or_404(
            _scope_queryset(request.user, entity, model.objects.select_for_update()),
            pk=pk,
        )
        current_status = getattr(instance, workflow_field, None)
        if current_status not in definition["from"]:
            messages.error(request, "The document changed while this action was running. Please review it again.")
            return redirect("entity_edit", entity=entity, pk=pk)
        if definition.get("requires_balance") and not instance.balanced:
            messages.error(request, "Accounting lines changed and are no longer balanced. Please review them again.")
            return redirect("entity_edit", entity=entity, pk=pk)
        previous_status = current_status
        update_fields = [workflow_field, "updated_at"]
        target_status = previous_status if definition.get("preserve_status") else definition["to"]
        setattr(instance, workflow_field, target_status)
        if definition.get("set_paid"):
            instance.paid_amount = instance.total
            update_fields.append("paid_amount")
        if action in {"approve", "reject"} and hasattr(instance, "approver_id"):
            instance.approver_id = request.user.pk
            update_fields.append("approver")
        try:
            run_before_workflow_hooks(instance, action, request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            transaction.set_rollback(True)
            return redirect("entity_edit", entity=entity, pk=pk)
        if entity == "invoices" and action in {"post", "cancel"}:
            try:
                instance = post_invoice(instance, request.user) if action == "post" else cancel_invoice(instance, request.user)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("entity_edit", entity=entity, pk=pk)
            target_status = getattr(instance, workflow_field)
        elif entity == "journal-entries" and action in {"post", "reverse"}:
            try:
                if action == "post":
                    instance = post_journal_entry(instance, request.user)
                else:
                    reverse_journal_entry(instance, request.user)
                    instance.refresh_from_db(fields=[workflow_field, "updated_at"])
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("entity_edit", entity=entity, pk=pk)
            target_status = getattr(instance, workflow_field)
        elif model._meta.app_label == "gsshop_finance" and definition.get("service_controls_state"):
            try:
                instance = handle_accounting_workflow(entity, instance, action, request.user)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("entity_edit", entity=entity, pk=pk)
            target_status = getattr(instance, workflow_field)
        elif definition.get("service_controls_state"):
            try:
                instance = handle_external_accounting_workflow(entity, instance, action, request.user)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("entity_edit", entity=entity, pk=pk)
            target_status = getattr(instance, workflow_field)
        elif model._meta.app_label == "gsshop_construction":
            handled = handle_construction_document_action(entity, instance, action, request.user)
            instance = handled
            setattr(instance, workflow_field, target_status)
            instance.save(update_fields=[workflow_field, "updated_at"])
        else:
            instance.save(update_fields=update_fields)
        if entity == "purchase-tenders":
            TenderAuditLog.objects.create(
                tender=instance,
                action=action,
                previous_status=previous_status,
                new_status=instance.status,
                actor=request.user,
            )
        if entity == "purchases" and action == "revise":
            last_revision = instance.revisions.aggregate(value=Max("revision_number"))["value"] or 0
            PurchaseOrderRevision.objects.create(
                order=instance,
                revision_number=last_revision + 1,
                reason="Workflow revision requested",
                snapshot={
                    "previous_status": previous_status,
                    "total": str(instance.total),
                    "order_date": instance.order_date.isoformat(),
                    "line_count": instance.lines.count(),
                },
                changed_by=request.user,
            )
    display_method = getattr(instance, f"get_{workflow_field}_display", None)
    display_status = display_method() if display_method else str(getattr(instance, workflow_field, target_status)).replace("_", " ").title()
    messages.success(request, f"{config['singular']} moved to {display_status}.")
    return redirect("entity_edit", entity=entity, pk=pk)
