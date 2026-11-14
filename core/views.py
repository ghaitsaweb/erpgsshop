import mimetypes
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, F, ImageField, Max, Prefetch, Q, Sum
from django.db.models.deletion import ProtectedError, RestrictedError
from django.forms import inlineformset_factory, modelform_factory
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header, url_has_allowed_host_and_scheme
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
    SaleOrderTypeForm,
    StockMoveForm,
    WarehouseForm,
    StyledModelForm,
)
from .developer import developer_field_metadata, developer_metadata
from .document_registry import apply_document_extensions
from .entity_registry import discover_installed_entity_configs, merge_entity_configs
from .list_sorting import prepare_list_sorting, query_without_page
from .record_navigation import build_record_navigation
from .smart_actions import (
    configure_standard_smart_actions,
    existing_smart_action_keys,
    get_smart_action,
    prepare_smart_action,
    smart_actions_for_source,
)
from .models import (
    Attendance,
    Company,
    CompanySaleSetting,
    Contact,
    Employee,
    Invoice,
    InvoiceLine,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    SaleOrder,
    SaleOrderLine,
    SaleOrderType,
    SaleQuotationEmail,
    StockMove,
    Warehouse,
)
from addons.ai.erpshop_ai_coworker.models import AIAgent, AIConversation, AIMessage, AITask, AIWorkspace
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
from modules.finance.entity_config import ACCOUNTING_DOCUMENT_CONFIG
from modules.finance.ecosystem_config import ECOSYSTEM_DOCUMENT_CONFIG
from modules.finance.core_parity_config import CORE_PARITY_DOCUMENT_CONFIG
from modules.finance.parity_config import PARITY_DOCUMENT_CONFIG
from modules.finance.models import (
    Account,
    AccountPayment,
    AccountingIntegrationEvent,
    Budget,
    ExpenseClaim,
    ExpenseClaimLine,
    JournalEntry,
    JournalLine,
    Loan,
    PaymentAllocation,
    SaleOrderInvoiceAccounting,
)
from modules.finance.payment_forms import InvoicePaymentForm
from modules.finance.expense_forms import ExpenseClaimForm, ExpenseClaimLineForm, ExpenseClaimLineFormSet
from modules.finance.expense_lines import save_expense_claim_lines, validate_expense_claim_lines
from modules.finance.services import (
    cancel_invoice,
    handle_accounting_workflow,
    handle_external_accounting_workflow,
    post_invoice,
    post_journal_entry,
    register_invoice_payment,
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
    CashAdvanceLine,
    CompanyHoliday,
    CompensationBenefit,
    ContractAllowance,
    ContractLetter,
    ContractLetterTemplate,
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
    LeaveBlackoutPeriod,
    LeaveBucket,
    LeaveBucketConsumption,
    LeaveEncashment,
    LeaveEncashmentBucket,
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
    PayrollRunChecklistItem,
    PayrollRunPreviewLine,
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
from modules.hr.cash_advances import save_cash_advance_lines, validate_cash_advance_lines
from .sales import (
    MAX_SALES_ORDER_LINES,
    can_sale_workflow_action,
    merge_sale_quotations,
    save_sale_order_lines,
    send_sale_quotation_email,
    transition_sale_order,
)
from modules.hr.forms import CashAdvanceForm, CashAdvanceLineForm, CashAdvanceLineFormSet
from modules.hr.change_requests import (
    initialize_employee_relation_drafts,
    serialize_employee_value,
    transition_employee_change_request,
)
from modules.hr.career_transitions import transition_career_request
from modules.hr.attendance_changes import transition_attendance_change_request
from modules.hr.contracts import add_contract_allowance_version, transition_employment_contract
from modules.hr.leave_requests import transition_leave_request
from modules.hr.leave_encashments import transition_leave_encashment
from modules.hr.leave_opening_balances import finalize_leave_opening_balance
from modules.hr.payroll_runs import (
    check_payroll_run,
    post_generated_payroll_accrual,
    start_payroll_generation,
    start_payroll_preview,
)
from modules.hr.payroll_payments import prepare_payroll_disbursement, post_payroll_disbursement
from modules.hr.lifecycle_checklists import complete_lifecycle_task, transition_lifecycle_process
from modules.hr.document_lifecycle import transition_employee_document
from modules.hr.leave_buckets import current_allocation_availability
from modules.hr.forms import (
    AttendanceChangeRequestForm,
    CareerTransitionRequestForm,
    ContractAllowanceForm,
    EmployeeChangeRequestForm,
    EmployeeDocumentForm,
    EmploymentContractForm,
    LeaveAllocationForm,
    LeaveEncashmentForm,
    LeaveRequestForm,
    RawAttendanceForm,
)
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
from modules.pos.models import POSOrder, POSOrderLine
from modules.pos.entity_config import POS_DOCUMENT_CONFIG
from modules.pos.access import can_pos_entity_action, can_pos_workflow_action, scope_pos_form, scope_pos_queryset
from modules.organization.context import get_current_context
from modules.organization.context import scope_form as scope_organization_form
from modules.organization.context import scope_queryset as scope_organization_queryset
from modules.organization.forms import BranchForm
from modules.organization.models import Branch
from modules.apphub.models import ERPApp
from modules.apphub.workspaces import ENTITY_APP_MAP
from modules.approval_workflow.access import (
    can_approval_entity_action,
    is_approval_entity,
    scope_approval_form,
    scope_approval_queryset,
)
from modules.approval_workflow.models import ApprovalAction, ApprovalInstance
from modules.approval_workflow.adapters import (
    apply_approval_lifecycle,
    apply_approval_result,
    apply_approval_submit,
    uses_approval_engine,
)
from modules.approval_workflow.services import (
    can_user_act,
    can_user_view,
    cancel_pending_approval,
    ensure_legacy_pending_approval,
    execute_registered_approval_action,
    pending_approval_for,
    perform_approval_action,
    submit_for_approval,
)
from modules.hr.access import can_hr_entity_action, can_hr_workflow_action, scope_hr_form, scope_hr_queryset
from modules.hr.contract_views import contract_attachment_download as serve_contract_attachment
from modules.hr.contract_views import contract_create
from modules.hr.contract_views import contract_workspace
from modules.hr.training_views import training_conduct_form, training_conduct_list
from modules.purchase.access import (
    can_purchase_entity_action,
    can_purchase_workflow_action,
    scope_purchase_form,
    scope_purchase_queryset,
)
from modules.purchase.models import (
    BlanketOrder,
    BlanketOrderLine,
    DirectPurchase,
    DirectPurchaseLine,
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseAccountingDocument,
    PurchaseAccountingLine,
    PurchaseBillLineProvenance,
    PurchaseInventoryTransfer,
    PurchaseInventoryTransferLine,
    PurchaseInvoiceAccounting,
    PurchaseOrderControl,
    PurchaseOrderInvoiceAccounting,
    PurchasePeppolEvent,
    PurchaseQuotation,
    PurchaseQuotationLine,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseTender,
    PurchaseTenderLine,
    ServiceWorkOrder,
    ServiceWorkOrderLine,
)
from modules.purchase.services import (
    MAX_PURCHASE_DOCUMENT_LINES,
    apply_purchase_milestone_template,
    apply_purchase_order_template,
    approve_budget_movement,
    handle_purchase_document_action,
    save_purchase_order_lines,
)
from .rental_views import rental_detail_list
from .document_pdf import REPORT_CONFIG, render_document_pdf


APPROVAL_WORKFLOW = {
    "submit": {"label": "Submit", "from": ["draft"], "to": "submitted", "primary": True},
    "approve": {"label": "Approve", "from": ["submitted"], "to": "approved", "primary": True},
    "reject": {"label": "Reject", "from": ["submitted"], "to": "rejected"},
    "cancel": {"label": "Cancel", "from": ["draft", "submitted"], "to": "cancelled"},
    "reset": {"label": "Reset to Draft", "from": ["rejected", "cancelled"], "to": "draft"},
}

LEAVE_ENCASHMENT_WORKFLOW = {
    "submit": {"label": "Submit", "from": ["draft"], "to": "submitted", "primary": True},
    "approve": {"label": "Approve", "from": ["submitted"], "to": "approved", "primary": True},
    "reject": {"label": "Refuse", "from": ["submitted"], "to": "rejected"},
    "cancel": {"label": "Cancel", "from": ["draft", "submitted", "approved", "waiting_for_payroll"], "to": "cancelled"},
    "assign-payroll": {"label": "Assign to Payroll", "from": ["approved"], "to": "waiting_for_payroll"},
}

LEAVE_OPENING_BALANCE_WORKFLOW = {
    "finalize": {"label": "Finalize Opening Balance", "from": ["draft"], "to": "finalized", "primary": True},
}

EMPLOYMENT_CONTRACT_WORKFLOW = {
    "submit": {"label": "Submit for Approval", "from": ["draft"], "to": "submitted", "primary": True},
    "approve": {"label": "Approve & Activate", "from": ["submitted"], "to": "active", "primary": True},
    "reject": {"label": "Reject", "from": ["submitted"], "to": "rejected"},
    "cancel": {"label": "Cancel", "from": ["draft", "submitted"], "to": "cancelled"},
    "reset": {"label": "Reset to Draft", "from": ["rejected", "cancelled"], "to": "draft"},
    "expire": {"label": "Expire", "from": ["active"], "to": "expired"},
    "terminate": {"label": "Terminate", "from": ["active"], "to": "terminated"},
}

PAYROLL_RUN_WORKFLOW = {
    "calculate": {"label": "Run Checklist", "from": ["draft", "calculating"], "to": "calculating", "primary": True},
    "mark-ready": {"label": "Mark Ready", "from": ["calculating"], "to": "ready", "primary": True},
    "preview": {"label": "Build Preview", "from": ["ready"], "to": "previewing", "primary": True},
    "generate": {"label": "Generate Payslips", "from": ["previewed"], "to": "generating", "primary": True},
    "post": {"label": "Post Payroll Accrual", "from": ["generated"], "to": "posted", "primary": True},
    "prepare-payment": {"label": "Prepare Payment", "from": ["posted"], "to": "posted", "primary": True},
    "cancel": {"label": "Cancel", "from": ["draft", "calculating", "ready", "previewed"], "to": "cancelled"},
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
        "readonly_detail": True,
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
    "sales-settings": {
        "model": CompanySaleSetting,
        "title": "Sales Settings",
        "singular": "Sales Settings",
        "columns": [
            ("company", "Company"),
            ("quotation_expiry_days", "Quotation Expiry Days"),
            ("default_delivery_date", "Default Delivery Days"),
            ("allow_order_revision", "Allow Revision"),
        ],
        "search": ["company__code", "company__name"],
        "form_fields": [
            "company",
            "quotation_expiry_days",
            "default_delivery_date",
            "allow_order_revision",
        ],
        "select_related": ["company"],
        "deletable": False,
    },
    "sales-order-types": {
        "model": SaleOrderType,
        "form": SaleOrderTypeForm,
        "title": "Sales Order Types",
        "singular": "Sales Order Type",
        "columns": [
            ("name", "Order Type"),
            ("prefix_sequence", "Prefix"),
            ("origin", "Origin"),
            ("description", "Description"),
        ],
        "search": ["name", "prefix_sequence", "description"],
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
        "columns": [("employee_id", "Employee ID"), ("name", "Name"), ("branch", "Branch"), ("department_record", "Department"), ("job_position", "Position"), ("supervisor", "Manager"), ("status", "Status")],
        "search": ["employee_id", "name", "department", "position", "branch__name", "department_record__name", "job_position__name"],
        "select_related": ["branch", "department_record", "job_position", "supervisor"],
        "kanban_variant": "employee",
        "kanban_group_by": False,
        "deletable": False,
        "selectable": False,
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
        "columns": [("employee", "Employee"), ("leave_type", "Type"), ("start_date", "Start"), ("end_date", "End"), ("days", "Days"), ("attachment_name", "Evidence"), ("status", "Status")],
        "search": ["employee__employee_id", "employee__name", "leave_type"],
        "form": LeaveRequestForm,
        "select_related": ["employee", "leave_type_record", "approver"],
        "defer": ["attachment_content"],
        "workflow": {
            **{key: APPROVAL_WORKFLOW[key] for key in ("submit", "approve", "reject")},
            "cancel": {"label": "Cancel", "from": ["draft", "submitted", "approved"], "to": "cancelled"},
        },
        "workflow_locked_statuses": {"submitted", "approved", "rejected", "cancelled"},
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
        "columns": [("number", "Number"), ("employee", "Employee"), ("contract", "Contract"), ("payroll_run", "Payroll Run"), ("period_start", "Period Start"), ("period_end", "Period End"), ("net_salary", "Net Salary"), ("status", "Status")],
        "search": ["number", "employee__employee_id", "employee__name", "contract__number", "payroll_run__number"],
        "form_fields": ["employee", "contract", "payroll_run", "number", "period_start", "period_end", "basic_salary", "allowances", "deductions", "status"],
        "select_related": ["employee", "contract", "payroll_run"],
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
        "columns": [("employee", "Employee"), ("field_name", "Primary Field"), ("effective_date", "Effective Date"), ("attachment_name", "Evidence"), ("status", "Status"), ("approver", "Approver")],
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
        "form": EmployeeDocumentForm,
        "defer": ["attachment_content"],
        "select_related": ["employee", "document_type", "replaced_by"],
        "workflow": {
            "verify": {"label": "Verify Document", "from": ["draft"], "to": "active", "primary": True},
            "expire": {"label": "Mark Expired", "from": ["active"], "to": "expired"},
            "replace": {"label": "Replace with Draft", "from": ["active"], "to": "replaced"},
            "archive": {"label": "Archive Document", "from": ["draft", "active", "expired", "replaced"], "to": "archived"},
            "unarchive": {"label": "Restore Document", "from": ["archived"], "to": "active"},
        },
        "workflow_locked_statuses": {"active", "expired", "replaced", "archived"},
        "deletable": False,
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
        "form_fields": ["company", "name", "lifecycle", "description", "responsible_role", "sequence", "requires_evidence", "active"],
        "select_related": ["company"],
    },
    "employee-onboarding": {
        "model": EmployeeOnboarding,
        "title": "Employee Onboarding",
        "singular": "Onboarding Process",
        "columns": [("employee", "Employee"), ("start_date", "Start Date"), ("owner", "Owner"), ("progress", "Progress %"), ("status", "Status")],
        "search": ["employee__employee_id", "employee__name", "owner__username"],
        "form_fields": ["employee", "start_date", "owner", "notes"],
        "select_related": ["employee", "owner"],
        "workflow": {
            "submit": {"label": "Start Onboarding", "from": ["draft", "submitted"], "to": "submitted", "primary": True},
            "complete": {"label": "Complete Onboarding", "from": ["submitted"], "to": "approved"},
            "cancel": {"label": "Cancel", "from": ["draft", "submitted"], "to": "cancelled"},
        },
        "workflow_locked_statuses": {"submitted", "approved", "rejected", "cancelled"},
        "deletable": False,
    },
    "employee-offboarding": {
        "model": EmployeeOffboarding,
        "title": "Employee Offboarding",
        "singular": "Offboarding Process",
        "columns": [("employee", "Employee"), ("last_working_date", "Last Working Date"), ("owner", "Owner"), ("progress", "Progress %"), ("status", "Status")],
        "search": ["employee__employee_id", "employee__name", "owner__username", "reason"],
        "form_fields": ["employee", "last_working_date", "owner", "reason", "notes"],
        "select_related": ["employee", "owner"],
        "workflow": {
            "submit": {"label": "Start Offboarding", "from": ["draft", "submitted"], "to": "submitted", "primary": True},
            "complete": {"label": "Complete Offboarding", "from": ["submitted"], "to": "approved"},
            "cancel": {"label": "Cancel", "from": ["draft", "submitted"], "to": "cancelled"},
        },
        "workflow_locked_statuses": {"submitted", "approved", "rejected", "cancelled"},
        "deletable": False,
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
        "form_fields": ["company", "code", "name", "annual_entitlement", "day_count_by", "eligible_gender", "min_tenure_value", "min_tenure_unit", "max_days_per_request", "max_consecutive_days", "cooling_period_days", "notice_flat_days", "allow_past_date", "max_past_days", "allow_leave_encashment", "max_encashment_days", "min_retained_balance", "allow_partial_encashment", "encashment_calculation_method", "flat_cash_amount", "paid", "requires_attachment", "attachment_trigger", "attachment_exceed_n_days", "max_file_size_mb", "active"],
        "select_related": ["company"],
    },
    "leave-blackout-periods": {
        "model": LeaveBlackoutPeriod,
        "title": "Leave Blackout Periods",
        "singular": "Leave Blackout Period",
        "columns": [("leave_type", "Leave Type"), ("date_start", "Start"), ("date_end", "End"), ("reason", "Reason"), ("active", "Active")],
        "search": ["leave_type__name", "leave_type__code", "reason"],
        "form_fields": ["leave_type", "date_start", "date_end", "reason", "active"],
        "select_related": ["leave_type", "leave_type__company"],
    },
    "leave-allocations": {
        "model": LeaveAllocation,
        "title": "Leave Allocations",
        "singular": "Leave Allocation",
        "columns": [("employee", "Employee"), ("leave_type", "Leave Type"), ("year", "Year"), ("allocated_days", "Allocated"), ("used_days", "Used"), ("balance_days", "Unconsumed"), ("available_days", "Available Today"), ("expires_on", "Expires"), ("status", "Status")],
        "search": ["employee__employee_id", "employee__name", "leave_type__name"],
        "form": LeaveAllocationForm,
        "select_related": ["employee", "leave_type"],
        "workflow": APPROVAL_WORKFLOW,
        "workflow_locked_statuses": {"submitted", "approved", "rejected", "cancelled"},
        "deletable": False,
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
        "form": CashAdvanceForm,
        "form_fields": ["employee", "number", "request_date", "purpose", "settlement_due_date"],
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
        "form_fields": ["company", "code", "name", "mandatory", "has_expiry", "allow_multiple_versions", "active"],
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
        "columns": [("number", "Number"), ("employee", "Employee"), ("transition_type", "Type"), ("effective_date", "Effective Date"), ("target_department", "Department"), ("target_job_position", "Position"), ("status", "Status")],
        "search": ["number", "employee__employee_id", "employee__name", "transition_type__name", "new_position"],
        "form": CareerTransitionRequestForm,
        "select_related": ["employee", "transition_type", "target_branch", "target_department", "target_job_position", "target_supervisor", "approver"],
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
        "columns": [("code", "Code"), ("name", "Expense Product"), ("activity_type", "Activity"), ("category", "Category"), ("maximum_amount", "Maximum Amount"), ("receipt_required", "Receipt"), ("active", "Active")],
        "search": ["code", "name", "category"],
        "form_fields": ["company", "code", "name", "activity_type", "category", "standard_price", "maximum_amount", "receipt_required", "active"],
        "select_related": ["company"],
    },
    "payroll-runs": {
        "model": PayrollRun,
        "title": "Payroll Runs",
        "singular": "Payroll Run",
        "columns": [("number", "Number"), ("period", "Period"), ("company", "Company"), ("employee_count", "Employees"), ("checklist_blocking_count", "Check Blockers"), ("preview_count", "Previewed"), ("generated_count", "Generated"), ("net_amount", "Net Amount"), ("status", "Status")],
        "search": ["number", "period__name", "company__name"],
        "form_fields": ["company", "period", "number", "notes"],
        "select_related": ["company", "period"],
        "workflow": PAYROLL_RUN_WORKFLOW,
        "workflow_locked_statuses": {"calculating", "ready", "previewing", "previewed", "generating", "generated", "failed", "posted", "cancelled"},
        "deletable": False,
    },
    "payroll-run-checklist": {
        "model": PayrollRunChecklistItem,
        "title": "Payroll Run Checklist",
        "singular": "Payroll Checklist Item",
        "columns": [("run", "Payroll Run"), ("name", "Check"), ("severity", "Severity"), ("state", "Result"), ("affected_count", "Affected"), ("checked_at", "Checked")],
        "search": ["run__number", "code", "name"],
        "select_related": ["run"],
        "creatable": False,
        "editable": False,
        "deletable": False,
    },
    "payroll-run-preview": {
        "model": PayrollRunPreviewLine,
        "title": "Payroll Run Preview",
        "singular": "Payroll Preview Line",
        "columns": [("run", "Payroll Run"), ("employee", "Employee"), ("basic_salary", "Basic"), ("allowance_amount", "Allowance"), ("encashment_amount", "Encashment"), ("deduction_amount", "Deduction"), ("net_amount", "Net"), ("state", "Result"), ("issue", "Issue")],
        "search": ["run__number", "employee__employee_id", "employee__name"],
        "select_related": ["run", "employee"],
        "creatable": False,
        "editable": False,
        "deletable": False,
    },
    "compensation-benefits": {
        "model": CompensationBenefit,
        "title": "Compensation & Benefits",
        "singular": "Compensation & Benefit",
        "columns": [("employee", "Employee"), ("contract", "Contract"), ("effective_date", "Effective Date"), ("basic_salary", "Basic Salary"), ("allowance_amount", "Allowance"), ("deduction_amount", "Deduction"), ("total_compensation", "Total"), ("status", "Status")],
        "search": ["employee__employee_id", "employee__name"],
        "form_fields": ["employee", "effective_date", "basic_salary", "allowance_amount", "deduction_amount", "approver", "notes"],
        "select_related": ["employee", "contract", "approver"],
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
        "form": ExpenseClaimForm,
        "form_fields": ["company", "employee", "cash_advance", "reference", "claim_date", "category", "description"],
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
        "creatable": False,
    },
    "ai-agents": {
        "model": AIAgent,
        "title": "AI Coworkers",
        "singular": "AI Coworker",
        "columns": [("name", "Agent"), ("workspace", "Workspace"), ("role", "Role"), ("provider", "Provider"), ("model_name", "Model"), ("active", "Active")],
        "search": ["name", "role", "model_name", "workspace__name"],
        "form_fields": ["workspace", "name", "role", "provider", "model_name", "system_prompt", "allowed_tools", "active"],
        "select_related": ["workspace"],
        "creatable": False,
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
        "creatable": False,
    },
    "ai-tasks": {
        "model": AITask,
        "title": "AI Tasks",
        "singular": "AI Task",
        "columns": [("title", "Task"), ("agent", "Agent"), ("task_type", "Type"), ("status", "Status"), ("created_at", "Created")],
        "search": ["title", "task_type", "agent__name"],
        "form_fields": ["agent", "title", "task_type", "status", "input_data", "result_data", "error_message", "started_at", "completed_at"],
        "select_related": ["agent"],
        "creatable": False,
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
            "form_fields": ["employee", "address_type", "address", "city", "province", "postal_code", "phone", "primary"],
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
            "form": ContractAllowanceForm,
            "select_related": ["contract", "contract__employee", "component"],
            "deletable": False,
        },
        "contract-letter-templates": {
            "model": ContractLetterTemplate,
            "title": "Contract Letter Templates",
            "singular": "Contract Letter Template",
            "columns": [("code", "Code"), ("name", "Template"), ("company", "Company"), ("letter_type", "Letter Type"), ("active", "Active")],
            "search": ["code", "name", "letter_type", "content"],
            "form_fields": ["company", "code", "name", "letter_type", "content", "active"],
            "select_related": ["company"],
        },
        "contract-letters": {
            "model": ContractLetter,
            "title": "Contract Letters",
            "singular": "Contract Letter",
            "columns": [("letter_number", "Letter Number"), ("contract", "Contract"), ("letter_type", "Type"), ("issued_date", "Issued Date")],
            "search": ["letter_number", "contract__number", "contract__employee__name", "letter_type"],
            "form_fields": ["contract", "template", "letter_number", "letter_type", "issued_date", "content", "generated_by", "attachment_url", "notes"],
            "select_related": ["contract", "contract__employee", "template", "generated_by"],
            "creatable": False,
            "editable": False,
            "deletable": False,
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
            "columns": [("employee", "Employee"), ("leave_type", "Leave Type"), ("year", "Year"), ("balance_days", "Available"), ("opening_used", "Previously Used"), ("status", "Status")],
            "search": ["employee__employee_id", "employee__name", "leave_type__name"],
            "form_fields": ["employee", "leave_type", "year", "balance_days", "opening_used", "cutoff_date", "expiry_date", "notes"],
            "select_related": ["employee", "leave_type"],
            "workflow": LEAVE_OPENING_BALANCE_WORKFLOW,
            "workflow_locked_statuses": {"finalized", "locked"},
        },
        "leave-movements": {
            "model": LeaveMovement,
            "title": "Leave Movements",
            "singular": "Leave Movement",
            "columns": [("movement_date", "Date"), ("employee", "Employee"), ("leave_type", "Leave Type"), ("movement_type", "Type"), ("days", "Days")],
            "search": ["employee__employee_id", "employee__name", "leave_type__name", "reference"],
            "form_fields": ["employee", "leave_type", "movement_date", "movement_type", "days", "reference", "notes"],
            "select_related": ["employee", "leave_type"],
            "creatable": False,
            "editable": False,
            "deletable": False,
        },
        "leave-buckets": {
            "model": LeaveBucket,
            "title": "Leave Count Buckets",
            "singular": "Leave Count Bucket",
            "columns": [("employee", "Employee"), ("leave_type", "Leave Type"), ("original_days", "Granted"), ("consumed_days", "Consumed"), ("forfeited_days", "Forfeited"), ("remaining_days", "Remaining"), ("expiry_date", "Expires"), ("status", "Status")],
            "search": ["employee__employee_id", "employee__name", "leave_type__name", "cycle_key"],
            "form_fields": ["employee", "leave_type", "allocation", "year", "source", "cycle_key", "original_days", "consumed_days", "forfeited_days", "start_valid_date", "expiry_date", "status"],
            "select_related": ["employee", "leave_type", "allocation"],
            "creatable": False,
            "editable": False,
            "deletable": False,
        },
        "leave-bucket-consumptions": {
            "model": LeaveBucketConsumption,
            "title": "Leave Bucket Consumption",
            "singular": "Leave Bucket Consumption",
            "columns": [("leave_request", "Leave Request"), ("bucket", "Bucket"), ("days", "Days"), ("reversed_at", "Reversed At")],
            "search": ["leave_request__employee__employee_id", "leave_request__employee__name", "bucket__cycle_key"],
            "form_fields": ["leave_request", "bucket", "days", "reversed_at"],
            "select_related": ["leave_request__employee", "bucket__employee", "bucket__leave_type"],
            "creatable": False,
            "editable": False,
            "deletable": False,
        },
        "leave-encashments": {
            "model": LeaveEncashment,
            "title": "Leave Encashments",
            "singular": "Leave Encashment",
            "columns": [("reference", "Reference"), ("request_date", "Date"), ("employee", "Employee"), ("leave_type", "Leave Type"), ("days", "Days"), ("amount", "Amount"), ("status", "Status")],
            "search": ["reference", "employee__employee_id", "employee__name", "leave_type__name"],
            "form": LeaveEncashmentForm,
            "select_related": ["employee", "leave_type", "approver"],
            "workflow": LEAVE_ENCASHMENT_WORKFLOW,
            "workflow_locked_statuses": {"submitted", "approved", "waiting_for_payroll", "payroll_generated", "rejected", "cancelled"},
            "deletable": False,
        },
        "leave-encashment-buckets": {
            "model": LeaveEncashmentBucket,
            "title": "Encashment Bucket Reservations",
            "singular": "Encashment Bucket Reservation",
            "columns": [("encashment", "Encashment"), ("bucket", "Bucket"), ("days", "Days"), ("status", "Status"), ("posted_at", "Posted"), ("reversed_at", "Reversed")],
            "search": ["encashment__reference", "encashment__employee__employee_id", "encashment__employee__name"],
            "form_fields": ["encashment", "bucket", "days", "status", "posted_at", "reversed_at"],
            "select_related": ["encashment__employee", "bucket__employee", "bucket__leave_type"],
            "creatable": False,
            "editable": False,
            "deletable": False,
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

ENTITY_CONFIG = merge_entity_configs(ENTITY_CONFIG, discover_installed_entity_configs())
configure_standard_smart_actions(ENTITY_CONFIG)


DOCUMENT_CONFIG = {
    "expenses": {
        "line_model": ExpenseClaimLine,
        "line_relation": "lines",
        "header_fields": ["company", "employee", "cash_advance", "reference", "claim_date", "category", "description"],
        "line_fields": ["date", "description", "product", "quantity", "unit_price"],
        "line_title": "Expense Lines",
        "line_select_related": ("product",),
        "total_fields": [("Claim Total", "amount")],
        "editable_statuses": [ExpenseClaim.Status.DRAFT],
        "actions": ENTITY_CONFIG["expenses"]["workflow"],
        "has_pdf": False,
    },
    "cash-advances": {
        "line_model": CashAdvanceLine,
        "line_relation": "lines",
        "header_fields": ["employee", "number", "request_date", "purpose", "settlement_due_date"],
        "line_fields": ["sequence", "product", "quantity", "request_amount"],
        "line_title": "Advance Request Lines",
        "line_select_related": ("product",),
        "total_fields": [("Total Requested", "amount")],
        "editable_statuses": ["draft"],
        "actions": ENTITY_CONFIG["cash-advances"]["workflow"],
        "has_pdf": False,
    },
    "sales": {
        "line_model": SaleOrderLine,
        "line_relation": "lines",
        "header_fields": [
            "company", "order_type", "number", "customer", "validity_date", "order_date", "currency",
            "price_list", "payment_terms", "invoicing_policy", "salesperson_name",
            "customer_reference", "delivery_address", "invoice_address", "delivery_date",
            "global_discount_percent", "terms_conditions", "notes",
        ],
        "line_fields": [
            "product", "description", "delivery_address", "delivery_date", "quantity", "uom",
            "delivered_quantity_display", "invoiced_quantity_display", "unit_price", "discount_percent",
            "gross_amount_display", "line_discount_amount_display", "untaxed_amount_display",
            "tax_percent", "tax_amount_display", "total_amount_display", "rental_start_date",
            "rental_return_date", "rental_status",
        ],
        "line_title": "Order Lines, Taxes & Fulfilment",
        "total_fields": [
            ("Subtotal", "subtotal_amount"),
            ("Discount", "discount_amount"),
            ("Untaxed", "untaxed_amount"),
            ("Tax", "tax_amount"),
            ("Total", "total"),
        ],
        "editable_statuses": [SaleOrder.Status.DRAFT],
        "actions": {
            "send-email": {
                "label": "Send by Email",
                "from": [SaleOrder.Status.DRAFT, SaleOrder.Status.SENT],
                "to": SaleOrder.Status.SENT,
                "email_dialog": True,
                "service_controls_state": True,
            },
            "submit": {
                "label": "Submit for Approval",
                "from": [SaleOrder.Status.DRAFT, SaleOrder.Status.SENT],
                "to": SaleOrder.Status.TO_APPROVE,
                "primary": True,
                "service_controls_state": True,
            },
            "approve": {
                "label": "Approve",
                "from": [SaleOrder.Status.TO_APPROVE],
                "to": SaleOrder.Status.SALE,
                "primary": True,
                "service_controls_state": True,
            },
            "reject": {
                "label": "Reject",
                "from": [SaleOrder.Status.TO_APPROVE],
                "to": SaleOrder.Status.REJECTED,
                "reason_label": "Rejection reason",
                "service_controls_state": True,
            },
            "invoice": {
                "label": "Create Invoice",
                "from": [SaleOrder.Status.SALE],
                "to": SaleOrder.Status.SALE,
                "primary": True,
                "service_controls_state": True,
            },
            "close": {
                "label": "Close",
                "from": [SaleOrder.Status.SALE],
                "to": SaleOrder.Status.CLOSED,
                "close_reason_choices": SaleOrder.CloseReason.choices,
                "acknowledgement_label": "I understand that closing is permanent and undelivered quantities will be released.",
                "service_controls_state": True,
            },
            "cancel": {
                "label": "Cancel",
                "from": [SaleOrder.Status.DRAFT, SaleOrder.Status.SENT, SaleOrder.Status.SALE],
                "to": SaleOrder.Status.CANCELLED,
                "reason_label": "Cancellation reason",
                "service_controls_state": True,
            },
            "revise": {
                "label": "Revise",
                "from": [SaleOrder.Status.CANCELLED, SaleOrder.Status.REJECTED],
                "to": SaleOrder.Status.REVISED,
                "service_controls_state": True,
            },
            "reset-draft": {
                "label": "Reset to Draft",
                "from": [SaleOrder.Status.EXPIRED],
                "to": SaleOrder.Status.DRAFT,
                "service_controls_state": True,
            },
            "duplicate": {
                "label": "Duplicate",
                "from": list(SaleOrder.Status.values),
                "to": SaleOrder.Status.DRAFT,
                "preserve_status": True,
                "service_controls_state": True,
            },
        },
    },
    "purchases": {
        "line_model": PurchaseOrderLine,
        "line_relation": "lines",
        "header_fields": ["company", "number", "vendor", "warehouse", "invoicing_policy", "order_date", "notes"],
        "line_fields": ["product", "description", "warehouse", "quantity", "unit_price"],
        "line_select_related": ["order", "product", "warehouse"],
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
                "service_controls_state": True,
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
            "create-receipt": {
                "label": "Create Goods Receipt",
                "from": [PurchaseOrder.Status.CONFIRMED],
                "to": PurchaseOrder.Status.CONFIRMED,
                "preserve_status": True,
                "service_controls_state": True,
                "primary": True,
            },
            "create-bill": {
                "label": "Create Vendor Bill",
                "from": [
                    PurchaseOrder.Status.CONFIRMED,
                    PurchaseOrder.Status.COMPLETED,
                    PurchaseOrder.Status.CLOSED,
                ],
                "to": PurchaseOrder.Status.CONFIRMED,
                "preserve_status": True,
                "service_controls_state": True,
            },
            "create-downpayment": {
                "label": "Create Down-Payment Bill",
                "from": [
                    PurchaseOrder.Status.CONFIRMED,
                    PurchaseOrder.Status.COMPLETED,
                    PurchaseOrder.Status.CLOSED,
                ],
                "to": PurchaseOrder.Status.CONFIRMED,
                "preserve_status": True,
                "service_controls_state": True,
            },
            "create-settlement": {
                "label": "Create Settlement Bill",
                "from": [
                    PurchaseOrder.Status.CONFIRMED,
                    PurchaseOrder.Status.COMPLETED,
                    PurchaseOrder.Status.CLOSED,
                ],
                "to": PurchaseOrder.Status.CONFIRMED,
                "preserve_status": True,
                "service_controls_state": True,
            },
            "complete": {
                "label": "Complete",
                "from": [PurchaseOrder.Status.CONFIRMED],
                "to": PurchaseOrder.Status.COMPLETED,
                "primary": True,
                "service_controls_state": True,
            },
            "close": {
                "label": "Close",
                "from": [PurchaseOrder.Status.CONFIRMED, PurchaseOrder.Status.COMPLETED],
                "to": PurchaseOrder.Status.CLOSED,
                "primary": True,
                "service_controls_state": True,
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
                    PurchaseOrder.Status.COMPLETED,
                    PurchaseOrder.Status.REVISED,
                ],
                "to": PurchaseOrder.Status.CANCELLED,
                "service_controls_state": True,
                "acknowledgement_label": "I understand that received goods will be returned to the vendor and inventory will be reversed.",
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
                "order_type",
                "number",
                "requestor",
                "request_date",
                "required_date",
                "expiry_date",
                "destination_warehouse",
                "currency",
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
                "approval_justification",
                "rejection_reason",
            ],
            "line_title": "Requested Products and Services",
            "total_fields": [],
            "editable_statuses": [PurchaseRequest.Status.DRAFT, PurchaseRequest.Status.REJECTED],
            "actions": {
                "submit": {"label": "Submit", "from": [PurchaseRequest.Status.DRAFT], "to": PurchaseRequest.Status.PENDING, "primary": True},
                "approve": {"label": "Approve", "from": [PurchaseRequest.Status.PENDING, PurchaseRequest.Status.PARTIALLY_APPROVED], "to": PurchaseRequest.Status.APPROVED, "primary": True},
                "reject": {"label": "Reject", "from": [PurchaseRequest.Status.PENDING, PurchaseRequest.Status.PARTIALLY_APPROVED], "to": PurchaseRequest.Status.REJECTED},
                "re-request": {"label": "Re-Request", "from": [PurchaseRequest.Status.APPROVED, PurchaseRequest.Status.PARTIALLY_RFQ, PurchaseRequest.Status.RFQ, PurchaseRequest.Status.PARTIALLY_ORDERED, PurchaseRequest.Status.IN_PROGRESS], "to": PurchaseRequest.Status.APPROVED},
                "reset": {"label": "Reset to Draft", "from": [PurchaseRequest.Status.REJECTED], "to": PurchaseRequest.Status.DRAFT},
                "create-rfq": {"label": "Create RFQ", "from": [PurchaseRequest.Status.APPROVED, PurchaseRequest.Status.PARTIALLY_RFQ, PurchaseRequest.Status.RFQ, PurchaseRequest.Status.IN_PROGRESS], "to": PurchaseRequest.Status.IN_PROGRESS, "primary": True},
                "create-blanket": {"label": "Create Blanket Order", "from": [PurchaseRequest.Status.APPROVED, PurchaseRequest.Status.PARTIALLY_RFQ, PurchaseRequest.Status.RFQ, PurchaseRequest.Status.IN_PROGRESS], "to": PurchaseRequest.Status.IN_PROGRESS},
                "create-tender": {"label": "Create Tender", "from": [PurchaseRequest.Status.APPROVED, PurchaseRequest.Status.PARTIALLY_RFQ, PurchaseRequest.Status.RFQ, PurchaseRequest.Status.IN_PROGRESS], "to": PurchaseRequest.Status.IN_PROGRESS},
                "create-direct": {"label": "Create Direct Purchase", "from": [PurchaseRequest.Status.APPROVED, PurchaseRequest.Status.PARTIALLY_RFQ, PurchaseRequest.Status.RFQ, PurchaseRequest.Status.IN_PROGRESS], "to": PurchaseRequest.Status.IN_PROGRESS},
                "mark-ordered": {"label": "Mark Fully Ordered", "from": [PurchaseRequest.Status.RFQ, PurchaseRequest.Status.PARTIALLY_ORDERED], "to": PurchaseRequest.Status.FULLY_ORDERED, "primary": True},
                "start": {"label": "Start Fulfilment", "from": [PurchaseRequest.Status.FULLY_ORDERED], "to": PurchaseRequest.Status.IN_PROGRESS, "primary": True},
                "close": {"label": "Close", "from": [PurchaseRequest.Status.IN_PROGRESS], "to": PurchaseRequest.Status.CLOSED, "primary": True},
            },
        },
        "purchase-quotations": {
            "line_model": PurchaseQuotationLine,
            "line_relation": "lines",
            "header_fields": ["company", "order_type", "order_template", "number", "request", "vendor", "warehouse", "issue_date", "response_deadline", "expected_date", "currency", "quoted_amount", "notes"],
            "line_fields": ["request_line", "product", "description", "specification", "quantity", "unit_price", "tax_percent", "discount_percent", "promised_date"],
            "line_title": "Quotation Lines",
            "total_fields": [("Quoted Amount", "quoted_amount")],
            "editable_statuses": [PurchaseQuotation.Status.DRAFT, PurchaseQuotation.Status.RECEIVED],
            "actions": {
                "send": {"label": "Send to Vendor", "from": [PurchaseQuotation.Status.DRAFT], "to": PurchaseQuotation.Status.SENT, "primary": True, "service_controls_state": True},
                "receive": {"label": "Record Response", "from": [PurchaseQuotation.Status.SENT], "to": PurchaseQuotation.Status.RECEIVED, "primary": True, "service_controls_state": True},
                "select": {"label": "Select Quote", "from": [PurchaseQuotation.Status.RECEIVED], "to": PurchaseQuotation.Status.SELECTED, "primary": True, "service_controls_state": True},
                "cancel": {"label": "Cancel", "from": [PurchaseQuotation.Status.DRAFT, PurchaseQuotation.Status.SENT, PurchaseQuotation.Status.RECEIVED], "to": PurchaseQuotation.Status.CANCELLED, "service_controls_state": True},
            },
        },
        "direct-purchases": {
            "line_model": DirectPurchaseLine,
            "line_relation": "lines",
            "header_fields": ["company", "order_type", "order_template", "number", "request", "vendor", "warehouse", "purchase_date", "description", "amount", "justification"],
            "line_fields": ["request_line", "product", "description", "quantity", "unit_price", "tax_percent", "cost_center"],
            "line_title": "Direct Purchase Lines",
            "total_fields": [("Total", "amount")],
            "editable_statuses": ["draft", "rejected"],
            "actions": {
                key: {**definition, "service_controls_state": True}
                for key, definition in APPROVAL_WORKFLOW.items()
            },
        },
        "service-work-orders": {
            "line_model": ServiceWorkOrderLine,
            "line_relation": "lines",
            "header_fields": [
                "company",
                "source_order",
                "vendor",
                "milestone",
                "milestone_name",
                "contract_term_percentage",
                "service_description",
                "contract_amount",
                "date_start",
                "date_end",
                "notes",
            ],
            "line_fields": ["purchase_order_line", "description", "unit", "quantity", "unit_rate", "status"],
            "line_title": "Service Scope Lines",
            "total_fields": [("Contract Value", "contract_amount")],
            "editable_statuses": [],
            "actions": {
                "start": {"label": "Start", "from": ["draft"], "to": "in_progress", "primary": True, "service_controls_state": True},
                "done": {"label": "Mark Done", "from": ["in_progress"], "to": "done", "primary": True, "service_controls_state": True},
                "create-bill": {"label": "Create Vendor Bill", "from": ["in_progress", "done"], "to": "in_progress", "service_controls_state": True},
            },
        },
        "purchase-tenders": {
            "line_model": PurchaseTenderLine,
            "line_relation": "lines",
            "header_fields": ["company", "order_type", "order_template", "number", "title", "category", "tender_type", "method", "award_strategy", "warehouse", "currency", "publish_date", "bid_start_date", "close_date", "validity_days", "budget_amount", "description"],
            "line_fields": ["request_line", "product", "description", "specification", "quantity", "target_unit_price", "delivery_date"],
            "line_title": "Tender Scope Lines",
            "total_fields": [("Budget", "budget_amount")],
            "editable_statuses": [PurchaseTender.Status.DRAFT],
            "actions": {
                "submit": {"label": "Submit for Approval", "from": [PurchaseTender.Status.DRAFT], "to": PurchaseTender.Status.WAITING_APPROVAL, "primary": True, "service_controls_state": True},
                "approve": {"label": "Approve", "from": [PurchaseTender.Status.WAITING_APPROVAL], "to": PurchaseTender.Status.CONFIRMED, "primary": True, "service_controls_state": True},
                "reject": {"label": "Reject", "from": [PurchaseTender.Status.WAITING_APPROVAL], "to": PurchaseTender.Status.REJECTED, "service_controls_state": True},
                "publish": {"label": "Publish", "from": [PurchaseTender.Status.CONFIRMED], "to": PurchaseTender.Status.PUBLISHED, "primary": True, "service_controls_state": True},
                "open-submission": {"label": "Open Submission", "from": [PurchaseTender.Status.PUBLISHED], "to": PurchaseTender.Status.BID_SUBMISSION, "primary": True, "service_controls_state": True},
                "open-bids": {"label": "Open Bid Submission", "from": [PurchaseTender.Status.DOCUMENT_SUBMISSION], "to": PurchaseTender.Status.BID_SUBMISSION, "primary": True, "service_controls_state": True},
                "evaluate": {"label": "Start Bid Selection", "from": [PurchaseTender.Status.BID_SUBMISSION, PurchaseTender.Status.SUBMISSION], "to": PurchaseTender.Status.BID_SELECTION, "primary": True, "service_controls_state": True},
                "award": {"label": "Award", "from": [PurchaseTender.Status.BID_SELECTION], "to": PurchaseTender.Status.AWARDED, "primary": True, "service_controls_state": True},
                "submit-decision": {"label": "Submit Decision", "from": [PurchaseTender.Status.AWARDED], "to": PurchaseTender.Status.AWARDED, "primary": True, "service_controls_state": True},
                "approve-decision": {"label": "Approve Decision", "from": [PurchaseTender.Status.AWARDED], "to": PurchaseTender.Status.AWARDED, "primary": True, "service_controls_state": True},
                "reject-decision": {
                    "label": "Reject Decision",
                    "from": [PurchaseTender.Status.AWARDED],
                    "to": PurchaseTender.Status.AWARDED,
                    "reason_label": "Tender decision rejection reason",
                    "service_controls_state": True,
                },
                "reset-decision": {"label": "Reset Decision", "from": [PurchaseTender.Status.AWARDED], "to": PurchaseTender.Status.AWARDED, "service_controls_state": True},
                "reopen-award": {"label": "Reopen Award", "from": [PurchaseTender.Status.AWARDED], "to": PurchaseTender.Status.BID_SELECTION, "service_controls_state": True},
                "close": {"label": "Close", "from": [PurchaseTender.Status.AWARDED], "to": PurchaseTender.Status.CLOSED, "primary": True, "service_controls_state": True},
                "cancel": {"label": "Cancel", "from": [PurchaseTender.Status.DRAFT, PurchaseTender.Status.WAITING_APPROVAL, PurchaseTender.Status.CONFIRMED, PurchaseTender.Status.PUBLISHED, PurchaseTender.Status.DOCUMENT_SUBMISSION, PurchaseTender.Status.BID_SUBMISSION, PurchaseTender.Status.BID_SELECTION], "to": PurchaseTender.Status.CANCELLED, "service_controls_state": True},
            },
        },
        "blanket-orders": {
            "line_model": BlanketOrderLine,
            "line_relation": "lines",
            "header_fields": ["company", "order_type", "order_template", "number", "vendor", "warehouse", "currency", "date_start", "date_end", "agreement_value", "consumed_value", "terms"],
            "line_fields": ["request_line", "product", "description", "agreed_quantity", "released_quantity", "unit_price", "minimum_release_quantity"],
            "line_title": "Agreement Lines",
            "total_fields": [("Agreement Value", "agreement_value"), ("Consumed", "consumed_value")],
            "editable_statuses": [BlanketOrder.Status.DRAFT],
            "actions": {
                "submit": {"label": "Submit for Approval", "from": [BlanketOrder.Status.DRAFT], "to": BlanketOrder.Status.WAITING_APPROVAL, "primary": True, "service_controls_state": True},
                "approve": {"label": "Approve", "from": [BlanketOrder.Status.WAITING_APPROVAL], "to": BlanketOrder.Status.CONFIRMED, "primary": True, "service_controls_state": True},
                "reject": {"label": "Reject", "from": [BlanketOrder.Status.WAITING_APPROVAL], "to": BlanketOrder.Status.REJECTED, "service_controls_state": True},
                "create-rfq": {"label": "Create RFQ", "from": [BlanketOrder.Status.CONFIRMED, BlanketOrder.Status.RUNNING], "to": BlanketOrder.Status.RUNNING, "primary": True, "service_controls_state": True},
                "run": {"label": "Run", "from": [BlanketOrder.Status.CONFIRMED], "to": BlanketOrder.Status.RUNNING, "service_controls_state": True},
                "close": {"label": "Close", "from": [BlanketOrder.Status.CONFIRMED, BlanketOrder.Status.RUNNING], "to": BlanketOrder.Status.CLOSED, "service_controls_state": True},
                "reopen": {"label": "Reopen", "from": [BlanketOrder.Status.CLOSED], "to": BlanketOrder.Status.RUNNING, "service_controls_state": True},
                "cancel": {"label": "Cancel", "from": [BlanketOrder.Status.DRAFT, BlanketOrder.Status.WAITING_APPROVAL, BlanketOrder.Status.CONFIRMED, BlanketOrder.Status.RUNNING], "to": BlanketOrder.Status.CANCELLED, "service_controls_state": True},
            },
        },
        "goods-receipts": {
            "line_model": GoodsReceiptLine,
            "line_relation": "lines",
            "header_fields": ["company", "number", "order", "warehouse", "receipt_date", "receiving_mode", "delivery_status", "notes"],
            "line_fields": ["order_line", "received_quantity", "accepted_quantity", "rejected_quantity", "rejection_reason"],
            "line_select_related": ["order_line", "order_line__product", "stock_move", "return_stock_move"],
            "line_title": "Received Lines and Inspection",
            "total_fields": [],
            "editable_statuses": [GoodsReceipt.Status.DRAFT, GoodsReceipt.Status.INSPECTING],
            "actions": {
                "inspect": {"label": "Start Inspection", "from": [GoodsReceipt.Status.DRAFT], "to": GoodsReceipt.Status.INSPECTING, "primary": True, "service_controls_state": True},
                "receive": {"label": "Accept Receipt", "from": [GoodsReceipt.Status.INSPECTING], "to": GoodsReceipt.Status.RECEIVED, "primary": True, "service_controls_state": True},
                "reject": {"label": "Reject Receipt", "from": [GoodsReceipt.Status.INSPECTING], "to": GoodsReceipt.Status.REJECTED, "service_controls_state": True},
                "cancel": {"label": "Cancel", "from": [GoodsReceipt.Status.DRAFT], "to": GoodsReceipt.Status.CANCELLED, "service_controls_state": True},
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
            "line_select_related": ["receipt_line", "receipt_line__order_line", "product", "source_stock_move", "destination_stock_move"],
            "line_title": "Transfer Lines",
            "total_fields": [],
            "editable_statuses": [PurchaseInventoryTransfer.Status.DRAFT, PurchaseInventoryTransfer.Status.READY],
            "actions": {
                "ready": {"label": "Mark Ready", "from": [PurchaseInventoryTransfer.Status.DRAFT], "to": PurchaseInventoryTransfer.Status.READY, "primary": True, "service_controls_state": True},
                "dispatch": {"label": "Dispatch", "from": [PurchaseInventoryTransfer.Status.READY], "to": PurchaseInventoryTransfer.Status.IN_TRANSIT, "primary": True, "service_controls_state": True},
                "complete": {"label": "Complete", "from": [PurchaseInventoryTransfer.Status.IN_TRANSIT], "to": PurchaseInventoryTransfer.Status.DONE, "primary": True, "service_controls_state": True},
                "cancel": {"label": "Cancel", "from": [PurchaseInventoryTransfer.Status.DRAFT, PurchaseInventoryTransfer.Status.READY], "to": PurchaseInventoryTransfer.Status.CANCELLED, "service_controls_state": True},
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
apply_document_extensions(DOCUMENT_CONFIG)


def health(request):
    return JsonResponse({"status": "ok", "service": "erp-gsshop"})


def _config(entity):
    try:
        return ENTITY_CONFIG[entity]
    except KeyError as exc:
        raise Http404("Unknown ERP module") from exc


def _employee_kanban_cards(rows):
    cards = []
    for row in rows:
        employee = row["object"]
        initials = "".join(part[:1] for part in employee.name.split() if part)[:2].upper() or "?"
        cards.append(
            {
                "row": row,
                "initials": initials,
                "role": str(employee.job_position or employee.position or "No position assigned"),
                "department": str(employee.department_record or employee.department or "Not assigned"),
                "branch": str(employee.branch or "Not assigned"),
                "manager": str(employee.supervisor or "Not assigned"),
                "status": employee.get_status_display(),
                "inactive": employee.status == Employee.Status.INACTIVE,
            }
        )
    return cards


BULK_DELETE_MAX_SELECTION = 25


def _delete_return_to(request, entity, candidate=None):
    candidate = (candidate or request.POST.get("next") or "").strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return f"/app/{entity}/"


def _parse_bulk_delete_ids(raw_ids):
    selected_ids = []
    seen = set()
    for raw_id in raw_ids:
        try:
            record_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError("The selected record list is invalid.") from exc
        if record_id <= 0:
            raise ValidationError("The selected record list is invalid.")
        if record_id not in seen:
            selected_ids.append(record_id)
            seen.add(record_id)
    if not selected_ids:
        raise ValidationError("Select at least one record first.")
    if len(selected_ids) > BULK_DELETE_MAX_SELECTION:
        raise ValidationError(
            f"Select no more than {BULK_DELETE_MAX_SELECTION} records at a time."
        )
    return selected_ids


def _delete_queryset(user, entity, config, *, for_update=False):
    queryset = _scope_queryset(user, entity, config["model"].objects.all())
    related_fields = list(config.get("select_related", ()))
    if config.get("parent_request_filter") and "request" not in related_fields:
        related_fields.append("request")
    if related_fields:
        queryset = queryset.select_related(*related_fields)
    if for_update:
        queryset = queryset.select_for_update(of=("self",))
    return queryset


def _load_delete_targets(user, entity, config, selected_ids, *, for_update=False):
    targets = list(
        _delete_queryset(user, entity, config, for_update=for_update).filter(pk__in=selected_ids)
    )
    targets_by_id = {target.pk: target for target in targets}
    if len(targets_by_id) != len(selected_ids):
        raise ValidationError(
            "The selection changed or contains records outside your access scope. Select the records again."
        )
    return [targets_by_id[record_id] for record_id in selected_ids]


def _delete_block_reason(entity, config, instance):
    if entity == "employees":
        return "Employee records are retained for HR, payroll, and approval audit history; archive the employee instead."
    if entity == "cash-advances" and instance.status != "draft":
        return "Only draft cash advances can be deleted; use the workflow for later states."
    if entity == "expenses" and instance.status != ExpenseClaim.Status.DRAFT:
        return "Only draft expense claims can be deleted; use the workflow for later states."
    if entity == "sales" and instance.status != SaleOrder.Status.DRAFT:
        return "Only Draft Sales Orders can be deleted; use the workflow for later states."
    if entity == "leave-requests" and instance.status != LeaveRequest.Status.DRAFT:
        return "Only draft leave requests can be deleted; use the workflow for later states."
    if entity == "leave-opening-balances" and instance.status != LeaveOpeningBalance.Status.DRAFT:
        return "Finalized opening balances cannot be deleted."
    if entity == "payslips" and instance.payroll_run_id:
        return "Generated payslips cannot be deleted; retain their payroll audit history."
    if entity == "attendance" and instance.from_raw_attendance:
        return "Raw-derived attendance must be changed through an attendance correction request."
    if (
        config.get("parent_request_filter")
        and hasattr(instance, "request")
        and instance.request.status != "draft"
    ):
        return "Employee change details can only be deleted while the request is a draft."
    return ""


def _delete_error_message(error):
    if isinstance(error, ValidationError):
        return " ".join(error.messages)
    if isinstance(error, (ProtectedError, RestrictedError)):
        return "The selected records are still used by other business records and cannot be deleted."
    return "The selected records could not be deleted because related business data changed."


def _can(user, action, model, entity=None):
    return (
        user.has_perm(f"{model._meta.app_label}.{action}_{model._meta.model_name}")
        and (entity not in {"ai-workspaces", "ai-agents", "ai-conversations", "ai-tasks"} or action == "view")
        and can_approval_entity_action(user, entity, action)
        and can_hr_entity_action(user, entity, action)
        and can_purchase_entity_action(user, entity, action)
        and can_construction_entity_action(user, entity, action)
        and can_pos_entity_action(user, entity, action)
        and can_accounting_entity_action(user, entity, action)
    )


def _scope_queryset(user, entity, queryset):
    queryset = scope_approval_queryset(user, entity, queryset)
    queryset = scope_hr_queryset(user, entity, queryset)
    queryset = scope_purchase_queryset(user, entity, queryset)
    queryset = scope_construction_queryset(user, entity, queryset)
    queryset = scope_pos_queryset(user, entity, queryset)
    queryset = scope_accounting_queryset(user, entity, queryset)
    if is_approval_entity(entity):
        return queryset
    if entity == "sales-order-types":
        organization_context = get_current_context()
        if organization_context and organization_context.active_company_ids:
            company_links = SaleOrderType.companies.through.objects.filter(
                company_id__in=organization_context.active_company_ids
            ).values("saleordertype_id")
            all_links = SaleOrderType.companies.through.objects.values("saleordertype_id")
            queryset = queryset.filter(
                Q(pk__in=company_links) | ~Q(pk__in=all_links)
            )
    return scope_organization_queryset(
        queryset,
        include_branch=entity != "branches",
        use_allowed_companies=entity == "companies",
    )


def _scope_form(form, user, entity):
    scope_approval_form(form, user, entity)
    scope_hr_form(form, user, entity)
    scope_purchase_form(form, user, entity)
    scope_construction_form(form, user, entity)
    scope_pos_form(form, user, entity)
    scope_accounting_form(form, user, entity)
    return scope_organization_form(form, include_branch=entity != "branches")


def _record_smart_actions(request, source_entity, source):
    """Build fixed-query, permission-aware actions for one scoped record."""
    action_candidates = []
    for definition in smart_actions_for_source(source_entity):
        if definition.is_available is not None and not definition.is_available(source):
            continue
        target_config = _config(definition.target_entity)
        target_model = target_config["model"]
        if not _can(request.user, "view", target_model, definition.target_entity):
            continue
        target_queryset = _scope_queryset(
            request.user,
            definition.target_entity,
            target_model.objects.all(),
        )
        related_queryset = definition.filter_queryset(target_queryset, source)
        if definition.show_count:
            count = related_queryset.count()
        else:
            count = 0
        if definition.hide_when_empty and not count:
            if definition.show_count:
                continue
            existence_queryset = related_queryset
        else:
            existence_queryset = None
        query = urlencode({"smart_action": definition.key, "source_id": source.pk})
        action_candidates.append((
            {
                "key": definition.key,
                "label": definition.label,
                "icon": definition.icon,
                "count": count,
                "show_count": definition.show_count,
                "href": f"{reverse('entity_list', kwargs={'entity': definition.target_entity})}?{query}",
            },
            existence_queryset,
        ))
    existing_keys = existing_smart_action_keys(
        (action["key"], queryset)
        for action, queryset in action_candidates
        if queryset is not None
    )
    return [
        action
        for action, queryset in action_candidates
        if queryset is None or action["key"] in existing_keys
    ]


def _apply_smart_action_scope(request, target_entity, queryset, config):
    """Apply only a registered cross-model filter; reject arbitrary relations."""
    action_key = request.GET.get("smart_action", "").strip()
    source_id = request.GET.get("source_id", "").strip()
    if not action_key and not source_id:
        return queryset, config, None, ""
    if not action_key or not source_id.isdecimal() or int(source_id) <= 0:
        raise Http404("Invalid smart action scope")
    definition = get_smart_action(action_key)
    if definition is None or definition.target_entity != target_entity:
        raise Http404("Unknown smart action scope")
    source_config = _config(definition.source_entity)
    source_model = source_config["model"]
    if not _can(request.user, "view", source_model, definition.source_entity):
        raise Http404("Smart action source is unavailable")
    source = get_object_or_404(
        _scope_queryset(
            request.user,
            definition.source_entity,
            source_model.objects.all(),
        ),
        pk=int(source_id),
    )
    prepare_smart_action(action_key, source)
    scoped_queryset = definition.filter_queryset(queryset, source)
    scoped_config = {
        **config,
        "title": f"{definition.label} · {source}",
        "creatable": definition.allow_create,
        "editable": definition.allow_edit,
        "deletable": definition.allow_delete,
        "selectable": definition.allow_select,
    }
    scope_query = urlencode({"smart_action": definition.key, "source_id": source.pk})
    return scoped_queryset, scoped_config, {
        "definition": definition,
        "source": source,
        "source_entity": definition.source_entity,
        "source_singular": source_config["singular"],
        "source_title": source_config["title"],
        "can_open_source": (
            source_config.get("editable", True)
            and _can(request.user, "change", source_model, definition.source_entity)
        ) or (
            source_config.get("readonly_detail", False)
            and _can(request.user, "view", source_model, definition.source_entity)
        ),
    }, scope_query


def _record_edit_redirect(entity, pk, query_string=""):
    url = reverse("entity_edit", kwargs={"entity": entity, "pk": pk})
    return redirect(f"{url}?{query_string}" if query_string else url)


def _can_workflow(user, entity, action, instance):
    return (
        (entity != "sales" or can_sale_workflow_action(user, action, instance))
        and
        can_hr_workflow_action(user, entity, action, instance)
        and can_purchase_workflow_action(user, entity, action)
        and can_construction_workflow_action(user, entity, action)
        and can_pos_workflow_action(user, entity, action)
        and can_accounting_workflow_action(user, entity, action)
    )


def _can_document_workflow(user, entity, action, definition, instance, approval=None):
    approval_action = definition.get("approval_action")
    if approval_action in {"approve", "reject"} and uses_approval_engine(instance):
        if approval is not None:
            return can_user_act(approval, user)
        # Preserve access to pre-integration records already waiting for a
        # decision; the first valid manager action creates a legacy snapshot.
        return _can_workflow(user, entity, action, instance)
    return _can_workflow(user, entity, action, instance)


def _approval_transaction_context(instance, user, pending=None):
    if not instance or not uses_approval_engine(instance):
        return {}
    approval = pending or (
        ApprovalInstance.objects.filter(
            workflow__content_type__app_label=instance._meta.app_label,
            workflow__content_type__model=instance._meta.model_name,
            object_id=instance.pk,
        )
        .select_related("rule", "submitted_by")
        .order_by("-submitted_at", "-pk")
        .first()
    )
    return {
        "record_approval_enabled": True,
        "record_approval": approval,
        "record_approval_history": (
            list(
                ApprovalAction.objects.filter(instance_id=approval.pk)
                .select_related("performed_by", "effective_approver")
                .order_by("-performed_at", "-pk")[:50]
            )
            if approval
            else []
        ),
        "record_approval_can_open": bool(approval and can_user_view(approval, user)),
    }


def _hr_audit_company_id(instance):
    """Resolve the company for one HR workflow record without recordset loops."""
    if getattr(instance, "company_id", None):
        return instance.company_id
    relation_paths = (
        "employee.company_id",
        "onboarding.employee.company_id",
        "offboarding.employee.company_id",
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


@login_required
@require_GET
def entity_image(request, entity, pk, field_name):
    """Serve one form-visible ImageField through the entity permission boundary."""
    config = _config(entity)
    model = config["model"]
    if not _can(request.user, "view", model, entity):
        raise Http404

    form_class = _form_class(config)
    if field_name not in form_class.base_fields:
        raise Http404
    try:
        model_field = model._meta.get_field(field_name)
    except FieldDoesNotExist as error:
        raise Http404 from error
    if not isinstance(model_field, ImageField):
        raise Http404

    instance = get_object_or_404(_scope_queryset(request.user, entity, model.objects.all()), pk=pk)
    image = getattr(instance, field_name, None)
    if not image or not image.name:
        raise Http404
    try:
        image.open("rb")
    except (FileNotFoundError, OSError) as error:
        raise Http404 from error

    content_type = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
    response = FileResponse(image, content_type=content_type)
    response["Content-Disposition"] = content_disposition_header(False, image.name.rsplit("/", 1)[-1])
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _document_context(request, entity, config, instance=None, mode="Create"):
    document = DOCUMENT_CONFIG[entity]
    model = config["model"]
    is_derived_line_total = entity in {"cash-advances", "expenses"}
    header_form_class = (
        SaleOrderDocumentForm if entity == "sales" else
        document.get("header_form")
        or config.get("form")
        or modelform_factory(model, form=StyledModelForm, fields=document["header_fields"])
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
    # scale-guard: allow SG001 — line sections are fixed code configuration, never a database-sized collection.
    for index, section in enumerate(section_definitions):
        prefix = f"document_lines_{index}" if document.get("line_sections") else "document_lines"
        line_form_class = (
            section.get("line_form")
            or document.get("line_form")
            or (
                SaleOrderLineForm if entity == "sales" and index == 0 else
                CashAdvanceLineForm if entity == "cash-advances" and index == 0 else
                ExpenseClaimLineForm if entity == "expenses" and index == 0 else StyledModelForm
            )
        )
        configured_line_limit = section.get("line_limit", document.get("line_limit"))
        if is_derived_line_total:
            formset_options = {
                "formset": CashAdvanceLineFormSet if entity == "cash-advances" else ExpenseClaimLineFormSet,
                "max_num": 100,
                "validate_max": True,
                "absolute_max": 120,
            }
        elif configured_line_limit is not None:
            configured_line_limit = int(configured_line_limit)
            if configured_line_limit < 1:
                raise ImproperlyConfigured(f"Document '{entity}' has an invalid line_limit.")
            formset_options = {
                "max_num": configured_line_limit,
                "validate_max": True,
                "absolute_max": configured_line_limit + 20,
            }
        elif entity == "sales":
            formset_options = {
                "max_num": MAX_SALES_ORDER_LINES,
                "validate_max": True,
                "absolute_max": MAX_SALES_ORDER_LINES + 20,
            }
        elif entity in {"purchases", "goods-receipts", "purchase-inventory-transfers"}:
            formset_options = {
                "max_num": MAX_PURCHASE_DOCUMENT_LINES,
                "validate_max": True,
                "absolute_max": MAX_PURCHASE_DOCUMENT_LINES + 20,
            }
        else:
            formset_options = {}
        formset_class = inlineformset_factory(
            model,
            section["line_model"],
            form=line_form_class,
            fields=section["line_fields"],
            extra=section.get("extra", 1),
            can_delete=True,
            fk_name=section.get("fk_name"),
            **formset_options,
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
            instance=form.instance if is_derived_line_total else instance,
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
    if entity == "sales" and instance is None and not form.is_bound:
        company_id = form.initial.get("company")
        if company_id:
            from .sales import get_sale_default_delivery_days, get_sale_quotation_validity_days

            form.fields["validity_date"].initial = timezone.localdate() + timedelta(
                days=get_sale_quotation_validity_days(company_id)
            )
            delivery_days = get_sale_default_delivery_days(company_id)
            if delivery_days:
                form.fields["delivery_date"].initial = timezone.localdate() + timedelta(
                    days=delivery_days
                )
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

    form_valid = form.is_valid() if editable and request.method == "POST" else False
    formsets_valid = all(section["formset"].is_valid() for section in line_sections) if form_valid else False
    if editable and form_valid and formsets_valid:
        with transaction.atomic():
            saved = form.save(commit=False) if is_derived_line_total else form.save()
            if is_derived_line_total:
                saved.amount = formset.total_amount
                saved.full_clean()
                saved.save()
            for section in line_sections:
                section["formset"].instance = saved
                line_saver = section.get("line_saver", document.get("line_saver"))
                if line_saver is not None:
                    if not callable(line_saver):
                        raise ImproperlyConfigured(f"Document '{entity}' line_saver must be callable.")
                    line_saver(saved, section["formset"])
                elif entity == "cash-advances":
                    save_cash_advance_lines(saved, section["formset"])
                elif entity == "expenses":
                    save_expense_claim_lines(saved, section["formset"])
                elif entity == "sales":
                    save_sale_order_lines(saved, section["formset"])
                elif entity == "purchases":
                    save_purchase_order_lines(saved, section["formset"])
                else:
                    section["formset"].save()
            if entity == "purchases":
                order_template_id = getattr(saved, "_apply_purchase_order_template_id", None)
                milestone_template_id = getattr(saved, "_apply_purchase_milestone_template_id", None)
                if order_template_id:
                    saved = apply_purchase_order_template(saved, order_template_id, request.user)
                if milestone_template_id:
                    saved = apply_purchase_milestone_template(saved, milestone_template_id, request.user)
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
    document_approval = None
    if instance and uses_approval_engine(instance) and any(
        definition.get("approval_action")
        for definition in document.get("actions", {}).values()
    ):
        document_approval = pending_approval_for(instance)
    available_actions = [
        {"key": key, **definition}
        for key, definition in document.get("actions", {}).items()
        if instance
        and current_status in definition["from"]
        and _can_document_workflow(
            request.user,
            entity,
            key,
            definition,
            instance,
            approval=document_approval,
        )
        and (
            definition.get("is_available") is None
            or definition["is_available"](instance)
        )
    ]
    for action_item in available_actions:
        href_builder = action_item.pop("href_builder", None)
        if href_builder is not None:
            action_item["href"] = href_builder(instance)
    available_action_filter = document.get("available_action_filter")
    if instance and available_action_filter is not None:
        if not callable(available_action_filter):
            raise ImproperlyConfigured(
                f"Document '{entity}' available_action_filter must be callable."
            )
        available_actions = [
            item
            for item in available_actions
            if available_action_filter(instance, item["key"])
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
    context.update(_approval_transaction_context(instance, request.user, pending=document_approval))
    context_builder = document.get("context_builder")
    if instance and context_builder is not None:
        if not callable(context_builder):
            raise ImproperlyConfigured(f"Document '{entity}' context_builder must be callable.")
        extra_context = context_builder(instance, request.user)
        if not isinstance(extra_context, dict):
            raise ImproperlyConfigured(f"Document '{entity}' context_builder must return a dict.")
        context.update(extra_context)
    if entity == "invoices" and instance:
        context["invoice_payment_state"] = instance.payment_state
        context["invoice_payment_state_choices"] = list(Invoice.PaymentState.choices)
        can_add_payment = (
            current_status == Invoice.Status.POSTED
            and instance.balance > 0
            and _can(request.user, "change", Invoice, "invoices")
            and _can(request.user, "add", AccountPayment, "account-payments")
            and _can(request.user, "add", PaymentAllocation, "payment-allocations")
        )
        if can_add_payment:
            context["available_actions"].insert(0, {
                "key": "add_payment",
                "label": "Add Payment",
                "primary": True,
                "href": reverse("invoice_add_payment", kwargs={"pk": instance.pk}),
            })
        if instance.paid_amount > 0:
            context["available_actions"] = [
                action for action in context["available_actions"] if action["key"] != "cancel"
            ]
        payment_rows = list(
            PaymentAllocation.objects.filter(invoice_id=instance.pk)
            .select_related(
                "payment",
                "payment__journal",
                "payment__journal_entry",
                "writeoff_account",
            )
            .order_by("-payment__payment_date", "-pk")[:51]
        )
        context["invoice_payments"] = payment_rows[:50]
        context["invoice_payments_has_more"] = len(payment_rows) > 50
        context["can_open_payment"] = _can(request.user, "view", AccountPayment, "account-payments")
        context["can_open_payment_journal"] = _can(request.user, "view", JournalEntry, "journal-entries")
    if entity == "sales" and instance:
        invoice_accounting = (
            SaleOrderInvoiceAccounting.objects.filter(order_id=instance.pk)
            .select_related("downpayment_invoice", "settlement_invoice")
            .first()
        )
        accounting_event = (
            AccountingIntegrationEvent.objects.filter(
                company_id=instance.company_id,
                source_model=instance._meta.label_lower,
                source_id=instance.pk,
                event_type="sale_order_complete",
            )
            .select_related("journal_entry", "journal_entry__journal")
            .first()
        )
        sales_invoice = invoice_accounting.settlement_invoice if invoice_accounting else None
        if sales_invoice:
            context["available_actions"] = [
                item for item in context["available_actions"] if item["key"] != "invoice"
            ]
        invoice_journal = None
        if sales_invoice:
            invoice_journal = (
                JournalEntry.objects.filter(
                    company_id=instance.company_id,
                    source_type="account_invoice",
                    source_reference=sales_invoice.number,
                )
                .select_related("journal")
                .order_by("pk")
                .first()
            )
        context["sales_accounting"] = {
            "event": accounting_event,
            "invoice_accounting": invoice_accounting,
            "invoice": sales_invoice,
            "journal_entry": invoice_journal or (accounting_event.journal_entry if accounting_event else None),
            "can_open_journal": _can(request.user, "change", JournalEntry, "journal-entries"),
            "can_open_invoice": _can(request.user, "change", Invoice, "invoices"),
        }
        context["sales_email_attempts"] = list(
            instance.email_attempts.select_related("sent_by").order_by("-created_at", "-pk")[:5]
        )
    if entity == "purchases" and instance:
        purchase_lines = [
            line_form.instance
            for line_form in formset.forms
            if line_form.instance.pk
        ]
        context["purchase_receiving_lines"] = purchase_lines
        context["purchase_control"] = (
            PurchaseOrderControl.objects.filter(order_id=instance.pk).first()
        )
        pending_receipt = (
            GoodsReceiptLine.objects.filter(
                receipt__order_id=instance.pk,
                receipt__status__in=[GoodsReceipt.Status.DRAFT, GoodsReceipt.Status.INSPECTING],
            ).aggregate(value=Sum("received_quantity"))["value"]
            or 0
        )
        receipt_remaining = sum(
            (
                line.quantity - line.received_quantity
                for line in purchase_lines
                if line.product.product_type == Product.ProductType.STOCKABLE
            ),
            0,
        ) - pending_receipt
        pending_bill = (
            PurchaseBillLineProvenance.objects.filter(
                order_line__order_id=instance.pk,
                invoice_line__invoice__status=Invoice.Status.DRAFT,
            ).aggregate(value=Sum("quantity"))["value"]
            or 0
        )
        bill_remaining = sum((line.remaining_to_bill for line in purchase_lines), 0) - pending_bill
        billing_plan = (
            PurchaseOrderInvoiceAccounting.objects.filter(order_id=instance.pk)
            .select_related("downpayment_invoice", "settlement_invoice")
            .first()
        )
        context["purchase_billing_plan"] = billing_plan
        active_purchase_bills = []
        if billing_plan:
            active_purchase_bills = list(
                PurchaseInvoiceAccounting.objects.filter(order_accounting=billing_plan)
                .exclude(invoice__status=Invoice.Status.CANCELLED)
                .select_related("invoice")
                .order_by("pk")[:101]
            )
        active_downpayments = [
            row
            for row in active_purchase_bills
            if row.invoice_kind == PurchaseInvoiceAccounting.InvoiceKind.DOWNPAYMENT
        ]
        active_settlements = [
            row
            for row in active_purchase_bills
            if row.invoice_kind == PurchaseInvoiceAccounting.InvoiceKind.SETTLEMENT
        ]
        filtered_actions = []
        for item in context["available_actions"]:
            if item["key"] == "create-receipt" and receipt_remaining <= 0:
                continue
            if item["key"] == "create-bill":
                if bill_remaining <= 0 or (billing_plan and billing_plan.downpayment_amount > 0):
                    continue
            if item["key"] == "create-downpayment":
                if not billing_plan or (
                    billing_plan.downpayment_type
                    == PurchaseOrderInvoiceAccounting.DownpaymentType.FIXED_AMOUNT
                    and billing_plan.downpayment_amount <= 0
                ) or (
                    billing_plan.downpayment_type
                    == PurchaseOrderInvoiceAccounting.DownpaymentType.PERCENTAGE
                    and billing_plan.downpayment_percentage <= 0
                ):
                    continue
            if item["key"] == "create-settlement":
                if (
                    not billing_plan
                    or billing_plan.status == PurchaseOrderInvoiceAccounting.Status.COMPLETED
                    or not active_downpayments
                    or any(
                        row.invoice.status not in {Invoice.Status.POSTED, Invoice.Status.PAID}
                        for row in active_downpayments
                    )
                    or any(row.invoice.status == Invoice.Status.DRAFT for row in active_settlements)
                ):
                    continue
            if item["key"] == "cancel" and not any(line.received_quantity > 0 for line in purchase_lines):
                item = {key: value for key, value in item.items() if key != "acknowledgement_label"}
            filtered_actions.append(item)
        context["available_actions"] = filtered_actions
    if entity == "purchase-requests" and instance:
        context["purchase_request_progress_lines"] = [
            line_form.instance
            for line_form in formset.forms
            if line_form.instance.pk
        ]
    if entity == "goods-receipts" and instance:
        context["goods_receipt_inventory_lines"] = [
            line_form.instance
            for line_form in formset.forms
            if line_form.instance.pk
        ]
    if instance:
        context["smart_actions"] = _record_smart_actions(request, entity, instance)
    return context, None


@login_required
def invoice_add_payment(request, pk):
    if not (
        _can(request.user, "change", Invoice, "invoices")
        and _can(request.user, "add", AccountPayment, "account-payments")
        and _can(request.user, "add", PaymentAllocation, "payment-allocations")
    ):
        messages.error(request, "You do not have permission to register invoice payments.")
        return redirect("entity_edit", entity="invoices", pk=pk)
    invoice = get_object_or_404(
        _scope_queryset(
            request.user,
            "invoices",
            Invoice.objects.select_related("company", "partner"),
        ),
        pk=pk,
    )
    if invoice.status != Invoice.Status.POSTED or invoice.balance <= 0:
        messages.error(request, "Add Payment is only available for a posted invoice with an amount due.")
        return redirect("entity_edit", entity="invoices", pk=invoice.pk)

    form = InvoicePaymentForm(request.POST or None, invoice=invoice)
    if request.method == "POST" and form.is_valid():
        try:
            payment = register_invoice_payment(
                invoice,
                payment_amount=form.cleaned_data["payment_amount"],
                payment_date=form.cleaned_data["payment_date"],
                payment_method=form.cleaned_data["payment_method"],
                reference=form.cleaned_data["reference"],
                payment_difference_handling=form.cleaned_data["payment_difference_handling"],
                writeoff_account=form.cleaned_data["writeoff_account"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(request, f"Payment {payment.number} posted and matched to {invoice.number}.")
            return redirect("entity_edit", entity="invoices", pk=invoice.pk)

    method_journals = {
        str(method.pk): str(method.journal)
        for method in form.fields["payment_method"].queryset
    }
    return render(request, "finance/invoice_payment_form.html", {
        "form": form,
        "invoice": invoice,
        "method_journals": method_journals,
    })


@require_GET
@login_required
def expense_claim_product_search(request):
    if not _can(request.user, "view", ExpenseClaim, "expenses"):
        return JsonResponse({"detail": "You do not have access to expense claims."}, status=403)
    line_form = ExpenseClaimLineForm()
    _scope_form(line_form, request.user, "expenses")
    products = line_form.fields["product"].queryset
    product_id = request.GET.get("id", "").strip()
    query = request.GET.get("q", "").strip()[:100]
    if product_id.isdigit():
        products = products.filter(pk=int(product_id))
    elif query:
        products = products.filter(Q(code__icontains=query) | Q(name__icontains=query))
    results = [
        {"id": row["id"], "text": f"{row['code']} — {row['name']}", "standard_price": str(row["standard_price"])}
        for row in products.order_by("name", "pk").values("id", "code", "name", "standard_price")[:20]
    ]
    return JsonResponse({"results": results})


@require_GET
@login_required
def expense_product_search(request):
    if not _can(request.user, "view", CashAdvance, "cash-advances"):
        return JsonResponse({"detail": "You do not have access to cash advances."}, status=403)
    line_form = CashAdvanceLineForm()
    _scope_form(line_form, request.user, "cash-advances")
    products = line_form.fields["product"].queryset
    product_id = request.GET.get("id", "").strip()
    query = request.GET.get("q", "").strip()[:100]
    if product_id.isdigit():
        products = products.filter(pk=int(product_id))
    elif query:
        products = products.filter(Q(code__icontains=query) | Q(name__icontains=query))
    results = [
        {"id": row["id"], "text": f"{row['code']} — {row['name']}", "standard_price": str(row["standard_price"])}
        for row in products.order_by("name", "pk").values("id", "code", "name", "standard_price")[:20]
    ]
    return JsonResponse({"results": results})


@require_GET
@login_required
def product_search(request):
    can_use_sales_master = any(
        _can(request.user, action, SaleOrder, "sales") for action in ("add", "change")
    )
    if not (_can(request.user, "view", Product, "products") or can_use_sales_master):
        return JsonResponse({"detail": "Anda tidak memiliki akses ke master produk."}, status=403)

    products = _scope_queryset(request.user, "products", Product.objects.filter(active=True))
    product_id = request.GET.get("id", "").strip()
    query = request.GET.get("q", "").strip()[:100]
    if product_id.isdigit():
        products = products.filter(pk=int(product_id))
    elif query:
        products = products.filter(Q(sku__icontains=query) | Q(name__icontains=query))

    results = [
        {
            "id": product["id"],
            "text": f"{product['sku']} — {product['name']}",
            "sale_price": str(product["sale_price"]),
            "unit": product["unit"],
        }
        for product in products.order_by("name", "sku").values(
            "id", "sku", "name", "sale_price", "unit"
        )[:20]
    ]
    return JsonResponse({"results": results})


@require_GET
@login_required
def customer_search(request):
    can_use_sales_master = any(
        _can(request.user, action, SaleOrder, "sales") for action in ("add", "change")
    )
    if not (_can(request.user, "view", Contact, "contacts") or can_use_sales_master):
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
    queryset, config, smart_action_scope, scope_query = _apply_smart_action_scope(
        request, entity, queryset, config
    )
    parent_request_id = request.GET.get("request", "").strip()
    if config.get("parent_request_filter") and parent_request_id.isdecimal():
        queryset = queryset.filter(request_id=int(parent_request_id))
    if entity == "employee-lifecycle-tasks":
        for parent in ("onboarding", "offboarding"):
            parent_id = request.GET.get(parent, "").strip()
            if parent_id.isdecimal():
                queryset = queryset.filter(**{f"{parent}_id": int(parent_id)})
    if config.get("select_related"):
        queryset = queryset.select_related(*config["select_related"])
    if config.get("defer"):
        queryset = queryset.defer(*config["defer"])
    query = request.GET.get("q", "").strip()
    if query:
        filters = Q()
        for field in config["search"]:
            filters |= Q(**{f"{field}__icontains": query})
        queryset = queryset.filter(filters)
    columns = [
        {"label": label, **developer_field_metadata(model, field)}
        for field, label in config["columns"]
    ]
    columns, list_ordering, current_sort, current_sort_direction = prepare_list_sorting(
        request, model, columns
    )
    if list_ordering:
        queryset = queryset.order_by(*list_ordering)
    elif not queryset.ordered:
        queryset = queryset.order_by("pk")
    page = Paginator(queryset, 25).get_page(request.GET.get("page"))
    page_items = list(page.object_list)
    if entity == "leave-allocations":
        availability = current_allocation_availability(page_items)
        for allocation in page_items:
            allocation.available_days = availability[allocation.pk]
    rows = [
        {
            "object": obj,
            "developer_meta": developer_metadata(model, entity=entity, instance=obj),
            "cells": [
                {"value": _display_value(obj, column["name"]), **column}
                for column in columns
            ],
        }
        for obj in page_items
    ]
    view_modes = tuple(config.get("view_modes", ("list", "kanban", "form")))
    collection_views = tuple(mode for mode in view_modes if mode in {"list", "kanban"})
    layout = request.GET.get("layout", config.get("default_collection_view", "list"))
    layout = layout if layout in collection_views else config.get("default_collection_view", "list")
    field_names = {field.name for field in model._meta.get_fields()}
    kanban_field = (
        config["kanban_group_by"]
        if "kanban_group_by" in config
        else ("status" if "status" in field_names else "")
    )
    kanban_groups = []
    if kanban_field:
        grouped = {}
        for row in rows:
            label = _display_value(row["object"], kanban_field) or "Uncategorized"
            grouped.setdefault(str(label), []).append(row)
        kanban_groups = [{"label": label, "rows": group_rows} for label, group_rows in grouped.items()]
    elif rows:
        kanban_groups = [{"label": config["title"], "rows": rows}]
    context = {
        "entity": entity,
        "config": config,
        "columns": columns,
        "rows": rows,
        "page": page,
        "query": query,
        "current_sort": current_sort,
        "current_sort_direction": current_sort_direction,
        "list_query": query_without_page(request),
        "layout": layout,
        "view_modes": view_modes,
        "kanban_groups": kanban_groups,
        "employee_kanban_cards": _employee_kanban_cards(rows) if config.get("kanban_variant") == "employee" else [],
        "developer_meta": developer_metadata(model, entity=entity),
        "can_add": config.get("creatable", True) and _can(request.user, "add", model, entity),
        "can_change": (
            config.get("editable", True) and _can(request.user, "change", model, entity)
        ) or (
            config.get("readonly_detail", False) and _can(request.user, "view", model, entity)
        ),
        "can_select": config.get("selectable", True),
        "can_delete": config.get("deletable", True) and _can(request.user, "delete", model, entity),
        "can_merge_sales": entity == "sales"
        and _can(request.user, "add", model, entity)
        and _can(request.user, "change", model, entity),
        "collection_actions": [
            action
            for action in config.get("collection_actions", [])
            if not action.get("requires_change") or _can(request.user, "change", model, entity)
        ],
        "smart_action_scope": smart_action_scope,
        "scope_query": scope_query,
    }
    if smart_action_scope:
        source = smart_action_scope["source"]
        source_entity = smart_action_scope["source_entity"]
        source_url = (
            reverse("entity_edit", kwargs={"entity": source_entity, "pk": source.pk})
            if smart_action_scope["can_open_source"]
            else ""
        )
        context["breadcrumbs"] = [
            {"label": "Home", "url": reverse("dashboard")},
            {
                "label": smart_action_scope["source_title"],
                "url": reverse("entity_list", kwargs={"entity": source_entity}),
            },
            {"label": str(source), "url": source_url},
            {"label": smart_action_scope["definition"].label},
        ]
    return render(request, "core/entity_list.html", context)


@login_required
@require_GET
def entity_jump(request, entity):
    """Resolve one typed record number inside the same scoped edit boundary."""
    config = _config(entity)
    unavailable = _unavailable_application(request, entity)
    if unavailable:
        return unavailable
    model = config["model"]
    can_open = (
        _can(request.user, "view", model, entity)
        if entity == "training-conducts"
        else config.get("editable", True) and _can(request.user, "change", model, entity)
    )
    if not can_open:
        messages.error(request, "You do not have permission to open this record.")
        return redirect("entity_list", entity=entity)

    current = request.GET.get("current", "").strip()
    target = request.GET.get("record", "").strip()
    return_query = QueryDict(request.GET.get("return_query", "")[:2000]).urlencode()

    def return_to_current():
        if current.isdecimal() and int(current) > 0:
            return _record_edit_redirect(entity, int(current), return_query)
        return redirect("entity_list", entity=entity)

    if not target.isdecimal() or int(target) < 1:
        messages.error(request, "Enter a valid record number.")
        return return_to_current()

    scoped_queryset = _scope_queryset(request.user, entity, model.objects.all())
    target_pk = (
        scoped_queryset.filter(pk=int(target))
        .order_by()
        .values_list("pk", flat=True)
        .first()
    )
    if target_pk is None:
        messages.error(request, "That record does not exist or is outside your access scope.")
        return return_to_current()
    return _record_edit_redirect(entity, target_pk, return_query)


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
    if entity == "contracts":
        return contract_create(
            request,
            config,
            scope_form=lambda form: _scope_form(form, request.user, "contracts"),
        )
    if entity in DOCUMENT_CONFIG:
        context, response = _document_context(request, entity, config)
        return response or render(request, "core/document_form.html", context)
    initial = None
    if config.get("parent_request_filter") and request.method == "GET" and config.get("creatable", True):
        request_id = request.GET.get("request")
        if request_id and request_id.isdecimal():
            parent_queryset = _scope_queryset(
                request.user,
                "employee-change-requests",
                EmployeeChangeRequest.objects.filter(status="draft"),
            )
            parent = parent_queryset.filter(pk=int(request_id)).first()
            if parent:
                initial = {"request": parent}
    form = _form_class(config)(request.POST or None, request.FILES or None, initial=initial)
    _scope_form(form, request.user, entity)
    if form.is_valid():
        if entity == "contract-allowances" and form.cleaned_data["contract"].status == EmploymentContract.Status.ACTIVE:
            try:
                add_contract_allowance_version(
                    contract=form.cleaned_data["contract"],
                    component=form.cleaned_data["component"],
                    amount=form.cleaned_data["amount"],
                    effective_date=form.cleaned_data["effective_date"],
                    end_date=form.cleaned_data.get("end_date"),
                    actor=request.user,
                    reason=form.cleaned_data["change_reason"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Contract allowance version created and compensation refreshed.")
                return redirect("entity_list", entity=entity)
        if form.errors:
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
        instance = form.save(commit=False)
        if isinstance(instance, StockMove):
            instance.created_by = request.user
        instance.save()
        if entity == "employee-change-requests":
            initialize_employee_relation_drafts(instance)
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
            navigation_queryset=scoped_queryset,
        )
    if config.get("readonly_detail", False) and not config.get("editable", True):
        if not _can(request.user, "view", model, entity):
            messages.error(request, "You do not have permission to view this record.")
            return redirect("entity_list", entity=entity)
        if request.method == "POST":
            messages.error(request, "This record is immutable.")
            return redirect("entity_edit", entity=entity, pk=pk)
        scoped_queryset = _scope_queryset(request.user, entity, model.objects.all())
        instance = get_object_or_404(scoped_queryset, pk=pk)
        form = _form_class(config)(instance=instance)
        _scope_form(form, request.user, entity)
        for field in form.fields.values():
            field.disabled = True
        return render(
            request,
            "core/entity_form.html",
            {
                "form": form,
                "config": config,
                "entity": entity,
                "mode": "View",
                "object": instance,
                "workflow_locked": True,
                "smart_actions": _record_smart_actions(request, entity, instance),
                "record_navigation": build_record_navigation(
                    scoped_queryset, instance, entity, request.GET.urlencode()
                ),
                "developer_meta": developer_metadata(model, entity=entity, instance=instance),
            },
        )
    if not config.get("editable", True) or not _can(request.user, "change", model, entity):
        messages.error(request, "This record is immutable or you do not have permission to edit it.")
        return redirect("entity_list", entity=entity)
    scoped_queryset = _scope_queryset(request.user, entity, model.objects.all())
    if entity == "contracts":
        return contract_workspace(
            request,
            config,
            scoped_queryset,
            pk,
            can_change=True,
            can_workflow=lambda action, instance: _can_workflow(request.user, "contracts", action, instance),
            scope_form=lambda form: _scope_form(form, request.user, "contracts"),
        )
    if entity == "raw-attendance" and request.method == "POST":
        return _edit_raw_attendance(request, config, scoped_queryset, pk)
    if entity == "attendance-change-requests" and request.method == "POST":
        return _edit_attendance_change_request(request, config, scoped_queryset, pk)
    if entity == "employee-change-requests" and request.method == "POST":
        return _edit_employee_change_request(request, config, scoped_queryset, pk)
    if entity == "employee-documents" and request.method == "POST":
        return _edit_employee_document(request, config, scoped_queryset, pk)
    if entity == "employee-change-requests":
        scoped_queryset = scoped_queryset.prefetch_related(
            "change_lines",
            "change_history",
            "relation_history",
            "address_changes",
            "emergency_contact_changes",
            "bank_account_changes",
            "dependent_changes",
            "education_changes",
        )
    if entity in {"cash-advances", "expenses"} and request.method == "POST":
        with transaction.atomic():
            instance = get_object_or_404(scoped_queryset.select_for_update(), pk=pk)
            context, response = _document_context(request, entity, config, instance=instance, mode="Edit")
            if response:
                return response
            context["record_navigation"] = build_record_navigation(
                scoped_queryset, instance, entity, request.GET.urlencode()
            )
            return render(request, "core/document_form.html", context)
    instance = get_object_or_404(scoped_queryset, pk=pk)
    if entity in DOCUMENT_CONFIG:
        context, response = _document_context(request, entity, config, instance=instance, mode="Edit")
        if response:
            return response
        context["record_navigation"] = build_record_navigation(
            scoped_queryset, instance, entity, request.GET.urlencode()
        )
        return render(request, "core/document_form.html", context)
    current_status = getattr(instance, config.get("workflow_field", "status"), None)
    locked_statuses = set(config.get("workflow_locked_statuses", {"approved", "cancelled"}))
    workflow_locked = (entity == "attendance" and instance.from_raw_attendance) or (
        entity == "payslips" and instance.payroll_run_id is not None
    ) or (
        entity == "contract-allowances"
        and instance.contract.status == EmploymentContract.Status.ACTIVE
    ) or (
        config.get("parent_request_filter")
        and instance.request.status != "draft"
    ) or (
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
    record_pending_approval = pending_approval_for(instance) if uses_approval_engine(instance) else None
    workflow_actions = [
        {"key": key, **definition}
        for key, definition in config.get("workflow", {}).items()
        if current_status in definition["from"]
        and (
            can_user_act(record_pending_approval, request.user)
            if key in {"approve", "reject"} and record_pending_approval is not None
            else _can_workflow(request.user, entity, key, instance)
        )
    ]
    lifecycle_tasks_preview = None
    lifecycle_tasks_has_more = False
    if entity in {"employee-onboarding", "employee-offboarding"}:
        preview_rows = list(instance.checklist_tasks.order_by("sequence", "pk").only(
            "id", "name", "sequence", "requires_evidence", "evidence_document_number", "status"
        )[:51])
        lifecycle_tasks_preview = preview_rows[:50]
        lifecycle_tasks_has_more = len(preview_rows) > 50
    approval_context = _approval_transaction_context(
        instance,
        request.user,
        pending=record_pending_approval,
    )
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
            "smart_actions": _record_smart_actions(request, entity, instance),
            "lifecycle_tasks_preview": lifecycle_tasks_preview,
            "lifecycle_tasks_has_more": lifecycle_tasks_has_more,
            "record_navigation": build_record_navigation(
                scoped_queryset, instance, entity, request.GET.urlencode()
            ),
            "developer_meta": developer_metadata(model, entity=entity, instance=instance),
            **approval_context,
        },
    )


@transaction.atomic
def _edit_employee_document(request, config, scoped_queryset, pk):
    original = get_object_or_404(scoped_queryset.only("id", "employee_id"), pk=pk)
    Employee.objects.select_for_update().get(pk=original.employee_id)
    document = get_object_or_404(scoped_queryset.select_for_update(of=("self",)), pk=pk)
    if document.status != EmployeeDocument.Status.DRAFT:
        messages.error(request, "Only a draft employee document can be edited.")
        return redirect("entity_edit", entity="employee-documents", pk=pk)
    form = EmployeeDocumentForm(request.POST, request.FILES, instance=document)
    _scope_form(form, request.user, "employee-documents")
    if form.is_valid():
        form.save()
        messages.success(request, "Employee Document updated successfully.")
        return redirect("entity_list", entity="employee-documents")
    return render(request, "core/entity_form.html", {
        "form": form,
        "config": config,
        "entity": "employee-documents",
        "mode": "Edit",
        "object": document,
        "current_status": document.status,
        "workflow_actions": [
            {"key": key, **definition} for key, definition in config["workflow"].items()
            if document.status in definition["from"] and _can_workflow(request.user, "employee-documents", key, document)
        ],
        "workflow_locked": False,
        "developer_meta": developer_metadata(EmployeeDocument, entity="employee-documents", instance=document),
    })


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
            "workflow_actions": [{"key": "submit", **EMPLOYMENT_CONTRACT_WORKFLOW["submit"]}],
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
        field_name = form.cleaned_data["field_name"]
        actual_value = serialize_employee_value(getattr(employee, field_name)) if field_name else ""
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
    instance = get_object_or_404(_delete_queryset(request.user, entity, config), pk=pk)
    block_reason = _delete_block_reason(entity, config, instance)
    if block_reason:
        messages.error(request, block_reason)
        return redirect("entity_list", entity=entity)
    if request.method == "POST":
        try:
            with transaction.atomic():
                locked_instance = get_object_or_404(
                    _delete_queryset(request.user, entity, config, for_update=True),
                    pk=pk,
                )
                block_reason = _delete_block_reason(entity, config, locked_instance)
                if block_reason:
                    raise ValidationError(block_reason)
                locked_instance.delete()
        except (ValidationError, ProtectedError, RestrictedError, IntegrityError) as error:
            messages.error(request, _delete_error_message(error))
            return redirect("entity_list", entity=entity)
        messages.success(request, f"{config['singular']} deleted.")
        return redirect("entity_list", entity=entity)
    return render(request, "core/entity_confirm_delete.html", {"object": instance, "config": config, "entity": entity})


@require_POST
@login_required
def entity_bulk_delete(request, entity):
    config = _config(entity)
    unavailable = _unavailable_application(request, entity)
    if unavailable:
        return unavailable
    model = config["model"]
    return_to = _delete_return_to(request, entity)
    if not config.get("deletable", True) or not _can(request.user, "delete", model, entity):
        messages.error(request, "These records cannot be deleted or you do not have permission.")
        return redirect(return_to)

    if request.POST.get("confirmation") != "delete-selected-records":
        messages.error(request, "Confirm deletion from the selection wizard.")
        return redirect(return_to)

    try:
        selected_ids = _parse_bulk_delete_ids(request.POST.getlist("selected_ids"))
        with transaction.atomic():
            targets = _load_delete_targets(
                request.user,
                entity,
                config,
                selected_ids,
                for_update=True,
            )
            blocked = [
                reason
                for target in targets
                if (reason := _delete_block_reason(entity, config, target))
            ]
            if blocked:
                raise ValidationError(blocked[0])
            # scale-guard: allow SG003 — per-instance delete guards must run; selection is capped at 25 records.
            for target in targets:
                target.delete()
    except (ValidationError, ProtectedError, RestrictedError, IntegrityError) as error:
        messages.error(request, _delete_error_message(error))
        return redirect(return_to)

    messages.success(request, f"{len(selected_ids)} {config['title'].lower()} deleted.")
    return redirect(return_to)


@require_POST
@login_required
def sales_merge(request):
    return_to = _delete_return_to(request, "sales")
    if not (
        _can(request.user, "add", SaleOrder, "sales")
        and _can(request.user, "change", SaleOrder, "sales")
    ):
        messages.error(request, "You do not have permission to merge Sales quotations.")
        return redirect(return_to)
    try:
        selected_ids = _parse_bulk_delete_ids(request.POST.getlist("selected_ids"))
        scoped_ids = set(
            _scope_queryset(request.user, "sales", SaleOrder.objects.all())
            .filter(pk__in=selected_ids)
            .values_list("pk", flat=True)
        )
        if scoped_ids != set(selected_ids):
            raise ValidationError(
                "The selection changed or contains quotations outside your access scope."
            )
        merged = merge_sale_quotations(selected_ids, request.user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect(return_to)
    messages.success(
        request,
        f"Merged {len(selected_ids)} quotations into {merged.number}.",
    )
    return redirect("entity_edit", entity="sales", pk=merged.pk)


@require_GET
@login_required
def employee_change_request_attachment(request, pk):
    if not _can(request.user, "view", EmployeeChangeRequest, "employee-change-requests"):
        raise Http404("Attachment not found.")
    queryset = _scope_queryset(
        request.user,
        "employee-change-requests",
        EmployeeChangeRequest.objects.only(
            "id",
            "employee_id",
            "attachment_name",
            "attachment_content_type",
            "attachment_content",
        ),
    )
    change_request = get_object_or_404(queryset, pk=pk)
    if not change_request.attachment_content or not change_request.attachment_name:
        raise Http404("Attachment not found.")
    response = HttpResponse(
        bytes(change_request.attachment_content),
        content_type=change_request.attachment_content_type or "application/octet-stream",
    )
    response["Content-Disposition"] = content_disposition_header(True, change_request.attachment_name)
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
@login_required
def contract_attachment_download(request, pk, attachment_pk):
    if not _can(request.user, "view", EmploymentContract, "contracts"):
        raise Http404("Attachment not found.")
    queryset = _scope_queryset(request.user, "contracts", EmploymentContract.objects.all())
    return serve_contract_attachment(request, queryset, pk, attachment_pk)


@require_GET
@login_required
def leave_request_attachment(request, pk):
    if not _can(request.user, "view", LeaveRequest, "leave-requests"):
        raise Http404("Attachment not found.")
    queryset = _scope_queryset(
        request.user,
        "leave-requests",
        LeaveRequest.objects.only(
            "id", "employee_id", "attachment_name", "attachment_content_type", "attachment_content"
        ),
    )
    leave_request = get_object_or_404(queryset, pk=pk)
    if not leave_request.attachment_name or not leave_request.attachment_content:
        raise Http404("Attachment not found.")
    response = HttpResponse(
        bytes(leave_request.attachment_content),
        content_type=leave_request.attachment_content_type or "application/octet-stream",
    )
    response["Content-Disposition"] = content_disposition_header(True, leave_request.attachment_name)
    response["X-Content-Type-Options"] = "nosniff"
    return response


@require_GET
@login_required
def employee_document_attachment(request, pk):
    if not _can(request.user, "view", EmployeeDocument, "employee-documents"):
        raise Http404("Attachment not found.")
    queryset = _scope_queryset(
        request.user,
        "employee-documents",
        EmployeeDocument.objects.only(
            "id", "employee_id", "attachment_name", "attachment_content_type", "attachment_content"
        ),
    )
    document = get_object_or_404(queryset, pk=pk)
    if not document.attachment_name or not document.attachment_content:
        raise Http404("Attachment not found.")
    response = HttpResponse(bytes(document.attachment_content), content_type=document.attachment_content_type)
    response["Content-Disposition"] = content_disposition_header(True, document.attachment_name)
    response["X-Content-Type-Options"] = "nosniff"
    return response


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
    if entity == "sales":
        queryset = queryset.select_related("invoice_accounting__settlement_invoice")
    line_queryset = document["line_model"].objects.all()
    if report["line_select_related"]:
        line_queryset = line_queryset.select_related(*report["line_select_related"])
    queryset = queryset.prefetch_related(Prefetch(document["line_relation"], queryset=line_queryset))
    instance = get_object_or_404(queryset, pk=pk)
    if entity == "sales":
        invoice_accounting = getattr(instance, "invoice_accounting", None)
        settlement_invoice = invoice_accounting.settlement_invoice if invoice_accounting else None
        instance._show_paid_watermark = bool(
            settlement_invoice and settlement_invoice.status == Invoice.Status.PAID
        )
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
        # These records need domain transitions that also update balances,
        # checklists, or contract state; the generic approval adapter must not
        # bypass their workflow authorization.
        local_workflow_entities = {
            "attendance-change-requests",
            "leave-requests",
            "employee-onboarding",
            "employee-offboarding",
            "contracts",
            "career-transition-requests",
        }
        approval_engine_decision = (
            entity not in local_workflow_entities
            and uses_approval_engine(instance)
            and action in {"approve", "reject"}
        )
        if not approval_engine_decision and not _can_workflow(request.user, entity, action, instance):
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
        approval_advanced = False
        workflow_audit_details = {}
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
            elif entity == "leave-requests":
                try:
                    instance = transition_leave_request(
                        instance, action, request.user, reason=request.POST.get("reason", "")
                    )
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                    return redirect("entity_edit", entity=entity, pk=pk)
                actual_status = instance.status
                updated = 1
            elif entity == "leave-encashments":
                try:
                    instance = transition_leave_encashment(instance, action, request.user)
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                    return redirect("entity_edit", entity=entity, pk=pk)
                actual_status = instance.status
                updated = 1
            elif entity == "leave-opening-balances":
                try:
                    instance = finalize_leave_opening_balance(instance, request.user)
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                    return redirect("entity_edit", entity=entity, pk=pk)
                actual_status = instance.status
                updated = 1
            elif entity == "payroll-runs" and action in {"calculate", "mark-ready", "preview", "generate", "post", "prepare-payment"}:
                try:
                    if action in {"calculate", "mark-ready"}:
                        instance = check_payroll_run(instance, request.user, mark_ready=action == "mark-ready")
                    elif action == "preview":
                        instance = start_payroll_preview(instance, request.user)
                    elif action == "generate":
                        instance = start_payroll_generation(instance, request.user)
                    elif action == "prepare-payment":
                        prepare_payroll_disbursement(instance, request.user)
                    else:
                        instance = post_generated_payroll_accrual(instance, request.user)
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                    return redirect("entity_edit", entity=entity, pk=pk)
                actual_status = instance.status
                updated = 1
            elif entity == "payroll-disbursements" and action == "confirm-payment":
                try:
                    instance = post_payroll_disbursement(
                        instance, request.user, payment_reference=request.POST.get("payment_reference", "")
                    )
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                    return redirect("entity_edit", entity=entity, pk=pk)
                actual_status = instance.status
                updated = 1
            elif entity in {"employee-onboarding", "employee-offboarding"}:
                try:
                    instance = transition_lifecycle_process(instance, action, request.user)
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                    return redirect("entity_edit", entity=entity, pk=pk)
                actual_status = instance.status
                updated = 1
            elif entity == "employee-lifecycle-tasks" and action == "complete":
                try:
                    instance = complete_lifecycle_task(
                        instance, request.user, evidence_document_id=request.POST.get("evidence_document_id")
                    )
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                    return redirect("entity_edit", entity=entity, pk=pk)
                actual_status = instance.status
                updated = 1
            elif entity == "employee-documents":
                try:
                    instance = transition_employee_document(
                        instance, action, request.user,
                        replacement_document_id=request.POST.get("replacement_document_id"),
                        note=request.POST.get("reason", ""),
                    )
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                    return redirect("entity_edit", entity=entity, pk=pk)
                actual_status = instance.status
                updated = 1
            elif entity == "contracts":
                locked = get_object_or_404(scoped_queryset.select_for_update(), pk=pk)
                if locked.status != current_status:
                    updated = 0
                else:
                    try:
                        transition_employment_contract(
                            locked, "activate" if action == "approve" else action, request.user
                        )
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                        return redirect("entity_edit", entity=entity, pk=pk)
                    instance = locked
                    updated = 1
            elif entity == "career-transition-requests":
                locked = get_object_or_404(
                    scoped_queryset.select_for_update(of=("self",)).select_related(
                        "employee",
                        "transition_type",
                        "target_branch",
                        "target_department",
                        "target_job_position__department",
                        "target_supervisor",
                    ),
                    pk=pk,
                )
                if locked.status != current_status:
                    updated = 0
                else:
                    try:
                        transition_career_request(locked, action, request.user)
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                        return redirect("entity_edit", entity=entity, pk=pk)
                    instance = locked
                    actual_status = locked.status
                    updated = 1
            elif uses_approval_engine(instance):
                locked = get_object_or_404(scoped_queryset.select_for_update(), pk=pk)
                if locked.status != current_status:
                    updated = 0
                else:
                    try:
                        if action == "submit":
                            submit_for_approval(
                                locked,
                                request.user,
                                entity=entity,
                                apply_submit=lambda target: apply_approval_submit(target, request.user),
                            )
                        elif action in {"approve", "reject"}:
                            approval_instance = (
                                ApprovalInstance.objects.filter(
                                    workflow__content_type__app_label=locked._meta.app_label,
                                    workflow__content_type__model=locked._meta.model_name,
                                    object_id=locked.pk,
                                    status=ApprovalInstance.Status.PENDING,
                                )
                                .select_related("rule", "workflow", "content_type")
                                .first()
                            )
                            if approval_instance is None:
                                approval_instance = ensure_legacy_pending_approval(
                                    locked,
                                    request.user,
                                    entity=entity,
                                )
                            outcome = perform_approval_action(
                                approval_instance,
                                request.user,
                                action,
                                reason=request.POST.get("reason", ""),
                                apply_result=lambda target, result_action: apply_approval_result(
                                    target,
                                    result_action,
                                    request.user,
                                    request.POST.get("reason", ""),
                                ),
                            )
                            workflow_audit_details = outcome.audit_details
                            if not outcome.completed:
                                approval_advanced = True
                                workflow_audit_details = {
                                    "approval_instance_id": approval_instance.pk,
                                    "approval_level_advanced": True,
                                }
                        elif action == "cancel" and pending_approval_for(locked) is not None:
                            cancel_pending_approval(
                                locked,
                                request.user,
                                apply_cancel=lambda target: apply_approval_lifecycle(
                                    target,
                                    "cancel",
                                    request.user,
                                    request.POST.get("reason", ""),
                                ),
                            )
                        else:
                            apply_approval_lifecycle(
                                locked,
                                action,
                                request.user,
                                request.POST.get("reason", ""),
                            )
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                        return redirect("entity_edit", entity=entity, pk=pk)
                    locked.refresh_from_db()
                    if workflow_audit_details:
                        locked._workflow_audit_details = workflow_audit_details
                    instance = locked
                    actual_status = locked.status
                    updated = 1
            elif entity == "contracts":
                locked = get_object_or_404(scoped_queryset.select_for_update(), pk=pk)
                if locked.status != current_status:
                    updated = 0
                else:
                    try:
                        transition_employment_contract(locked, action, request.user)
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                        return redirect("entity_edit", entity=entity, pk=pk)
                    instance = locked
                    updated = 1
            elif entity == "career-transition-requests":
                locked = get_object_or_404(
                    scoped_queryset.select_for_update(of=("self",)).select_related(
                        "employee",
                        "transition_type",
                        "target_branch",
                        "target_department",
                        "target_job_position__department",
                        "target_supervisor",
                    ),
                    pk=pk,
                )
                if locked.status != current_status:
                    updated = 0
                else:
                    try:
                        transition_career_request(locked, action, request.user)
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                        return redirect("entity_edit", entity=entity, pk=pk)
                    instance = locked
                    actual_status = locked.status
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
                        elif model._meta.app_label == "gsshop_purchase" and entity in {
                            "vendor-pricelists",
                            "vendor-pricelist-amendments",
                            "vendor-pricelist-imports",
                            "vendor-performance-evaluations",
                            "vendor-profiles",
                            "vendor-legal-documents",
                            "vendor-prequalifications",
                            "vendor-document-reviews",
                            "service-work-orders",
                            "service-work-order-lines",
                        }:
                            handled = handle_purchase_document_action(
                                entity,
                                locked,
                                action,
                                request.user,
                                reason=request.POST.get("reason", ""),
                            )
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
                        new_status="processed" if entity == "raw-attendance" else actual_status,
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
        elif approval_advanced:
            messages.success(request, "Approval recorded and advanced to the next level.")
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
    approval_engine_decision = (
        definition.get("approval_action") in {"approve", "reject"}
        and uses_approval_engine(instance)
    )
    if not approval_engine_decision and not _can_workflow(request.user, entity, action, instance):
        messages.error(request, "Your role cannot perform that workflow approval action.")
        return redirect("entity_edit", entity=entity, pk=pk)
    if entity == "purchase-requests" and action in {
        "create-rfq",
        "create-blanket",
        "create-tender",
        "create-direct",
    }:
        route = {
            "create-rfq": "purchase_request_convert_to_rfq",
            "create-blanket": "purchase_request_convert_to_blanket",
            "create-tender": "purchase_request_convert_to_tender",
            "create-direct": "purchase_request_convert_to_direct",
        }[action]
        return redirect(route, pk=pk)
    if entity == "purchase-requests" and action == "approve":
        return redirect("purchase_request_approve", pk=pk)
    if entity == "purchase-requests" and action == "re-request":
        return redirect("purchase_request_re_request", pk=pk)
    if entity == "blanket-orders" and action == "create-rfq":
        return redirect("blanket_order_convert_to_rfq", pk=pk)
    if entity == "sales" and action == "send-email":
        try:
            attempt = send_sale_quotation_email(
                instance,
                request.user,
                email_to=request.POST.get("email_to", ""),
                email_cc=request.POST.get("email_cc", ""),
                subject=request.POST.get("subject", ""),
                body_html=request.POST.get("body_html", ""),
            )
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            if attempt.status == SaleQuotationEmail.Status.SENT:
                messages.success(request, "Quotation email sent and the quotation moved to Sent.")
            else:
                messages.error(request, f"Quotation email failed: {attempt.failure_reason}")
        return redirect("entity_edit", entity=entity, pk=pk)
    if definition.get("requires_balance") and not instance.balanced:
        messages.error(request, "Accounting lines must have equal non-zero debit and credit totals before approval.")
        return redirect("entity_edit", entity=entity, pk=pk)
    with transaction.atomic():
        instance = get_object_or_404(
            _scope_queryset(
                request.user,
                entity,
                model.objects.select_for_update(of=("self",)),
            ),
            pk=pk,
        )
        current_status = getattr(instance, workflow_field, None)
        if current_status not in definition["from"]:
            messages.error(request, "The document changed while this action was running. Please review it again.")
            return redirect("entity_edit", entity=entity, pk=pk)
        if definition.get("requires_balance") and not instance.balanced:
            messages.error(request, "Accounting lines changed and are no longer balanced. Please review them again.")
            return redirect("entity_edit", entity=entity, pk=pk)
        approval_action = definition.get("approval_action")
        if action == "cancel" and uses_approval_engine(instance):
            pending = pending_approval_for(instance)
            if pending is not None:
                try:
                    cancel_pending_approval(
                        instance,
                        request.user,
                        apply_cancel=lambda target: apply_approval_lifecycle(
                            target,
                            "cancel",
                            request.user,
                            request.POST.get("reason", ""),
                        ),
                    )
                except ValidationError as exc:
                    messages.error(request, "; ".join(exc.messages))
                    return redirect("entity_edit", entity=entity, pk=pk)
                instance.refresh_from_db(fields=[workflow_field, "updated_at"])
                messages.success(request, f"{config['singular']} approval cancelled.")
                return redirect("entity_edit", entity=entity, pk=pk)
        if approval_action and uses_approval_engine(instance):
            try:
                outcome = execute_registered_approval_action(
                    instance,
                    request.user,
                    entity=entity,
                    action=approval_action,
                    reason=request.POST.get("reason", ""),
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("entity_edit", entity=entity, pk=pk)
            instance.refresh_from_db(fields=[workflow_field, "updated_at"])
            if outcome.advanced:
                messages.success(request, "Approval recorded and advanced to the next level.")
            else:
                display_method = getattr(instance, f"get_{workflow_field}_display", None)
                display_status = (
                    display_method()
                    if display_method
                    else str(getattr(instance, workflow_field)).replace("_", " ").title()
                )
                messages.success(request, f"{config['singular']} moved to {display_status}.")
            return redirect("entity_edit", entity=entity, pk=pk)
        if entity == "cash-advances" and action in {"submit", "approve"}:
            try:
                validate_cash_advance_lines(instance)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("entity_edit", entity=entity, pk=pk)
        if entity == "expenses" and action in {"submit", "approve"}:
            try:
                validate_expense_claim_lines(instance)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
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
        if entity != "sales":
            try:
                run_before_workflow_hooks(instance, action, request.user)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                transaction.set_rollback(True)
                return redirect("entity_edit", entity=entity, pk=pk)
        if entity == "sales" and action != "invoice":
            try:
                instance = transition_sale_order(
                    instance,
                    action,
                    request.user,
                    reason=request.POST.get("reason", ""),
                    note=request.POST.get("note", ""),
                    acknowledge_incomplete=request.POST.get("acknowledge_incomplete") == "on",
                    validity_date=request.POST.get("validity_date"),
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("entity_edit", entity=entity, pk=pk)
            target_status = instance.status
            if action in {"revise", "duplicate"} and instance.pk != pk:
                label = "Revision" if action == "revise" else "Duplicate"
                messages.success(request, f"{label} {instance.number} created as Draft.")
                return redirect("entity_edit", entity=entity, pk=instance.pk)
            if getattr(instance, "_approval_advanced", False):
                messages.success(request, "Approval recorded and advanced to the next level.")
                return redirect("entity_edit", entity=entity, pk=pk)
        elif entity == "invoices" and action in {"post", "cancel"}:
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
        elif definition.get("handler") is not None:
            try:
                instance = definition["handler"](instance, request.user)
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
                if entity in {
                    "purchases",
                    "purchase-quotations",
                    "purchase-tenders",
                    "tender-award-responses",
                    "blanket-orders",
                    "direct-purchases",
                    "goods-receipts",
                    "purchase-inventory-transfers",
                    "vendor-pricelists",
                    "vendor-pricelist-amendments",
                    "vendor-pricelist-imports",
                    "vendor-performance-evaluations",
                    "purchase-kpi-targets",
                    "vendor-profiles",
                    "vendor-legal-documents",
                    "vendor-prequalifications",
                    "vendor-document-reviews",
                    "service-work-orders",
                    "service-work-order-lines",
                }:
                    instance = handle_purchase_document_action(
                        entity,
                        instance,
                        action,
                        request.user,
                        acknowledge_received_return=request.POST.get("acknowledge_incomplete") == "on",
                        reason=request.POST.get("reason", ""),
                    )
                else:
                    instance = handle_external_accounting_workflow(entity, instance, action, request.user)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
                return redirect("entity_edit", entity=entity, pk=pk)
            target_status = getattr(instance, workflow_field)
            if entity == "purchases" and action == "create-receipt":
                receipt_id = getattr(instance, "_created_goods_receipt_id", None)
                if receipt_id:
                    count = getattr(instance, "_created_goods_receipt_count", 1)
                    messages.success(request, f"{count} draft goods receipt(s) prepared from {instance.number}.")
                    return redirect("entity_edit", entity="goods-receipts", pk=receipt_id)
            if entity == "purchases" and action in {
                "create-bill",
                "create-downpayment",
                "create-settlement",
            }:
                invoice_id = getattr(instance, "_created_vendor_bill_id", None)
                if invoice_id:
                    messages.success(request, f"Draft vendor bill created from {instance.number}.")
                    return redirect("entity_edit", entity="invoices", pk=invoice_id)
            if entity == "service-work-orders" and action == "create-bill":
                invoice_id = getattr(instance, "_created_vendor_bill_id", None)
                if invoice_id:
                    messages.success(request, f"Draft vendor bill created from {instance.number}.")
                    return redirect("entity_edit", entity="invoices", pk=invoice_id)
            if definition.get("redirect_to_result") and instance.pk != pk:
                messages.success(request, f"{config['singular']} revision {instance.number} created as Draft.")
                return redirect("entity_edit", entity=entity, pk=instance.pk)
            if entity == "purchase-quotations" and action == "select":
                order_id = getattr(instance, "_created_purchase_order_id", None)
                if order_id:
                    messages.success(request, f"Draft purchase order created from {instance.number}.")
                    return redirect("entity_edit", entity="purchases", pk=order_id)
            if entity == "blanket-orders" and action == "create-rfq":
                quotation_id = getattr(instance, "_created_rfq_id", None)
                if quotation_id:
                    messages.success(request, f"Draft RFQ created from {instance.number}.")
                    return redirect("entity_edit", entity="purchase-quotations", pk=quotation_id)
            if entity == "direct-purchases" and action == "approve" and instance.purchase_order_id:
                messages.success(request, f"Purchase Order created from {instance.number}.")
                return redirect("entity_edit", entity="purchases", pk=instance.purchase_order_id)
            if entity == "sales" and action == "invoice":
                invoice_id = SaleOrderInvoiceAccounting.objects.filter(
                    order_id=instance.pk
                ).values_list("settlement_invoice_id", flat=True).first()
                if invoice_id:
                    messages.success(request, "Draft Sales invoice created. Review and Post it to create the journal.")
                    return redirect("entity_edit", entity="invoices", pk=invoice_id)
        elif model._meta.app_label == "gsshop_construction":
            handled = handle_construction_document_action(entity, instance, action, request.user)
            instance = handled
            setattr(instance, workflow_field, target_status)
            instance.save(update_fields=[workflow_field, "updated_at"])
        else:
            instance.save(update_fields=update_fields)
        action_audit_logger = document.get("action_audit_logger")
        if action_audit_logger is not None:
            if not callable(action_audit_logger):
                raise ImproperlyConfigured(
                    f"Document '{entity}' action_audit_logger must be callable."
                )
            action_audit_logger(
                instance,
                action,
                request.user,
                previous_status,
            )
    display_method = getattr(instance, f"get_{workflow_field}_display", None)
    display_status = display_method() if display_method else str(getattr(instance, workflow_field, target_status)).replace("_", " ").title()
    messages.success(request, f"{config['singular']} moved to {display_status}.")
    return redirect("entity_edit", entity=entity, pk=pk)
