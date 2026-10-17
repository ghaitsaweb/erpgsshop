"""Generic CRUD and workflow registry for the Construction workspace."""

from . import models as construction_models


APPROVAL_WORKFLOW = {
    "submit": {"label": "Submit", "from": ["draft"], "to": "submitted", "primary": True},
    "approve": {"label": "Approve", "from": ["submitted"], "to": "approved", "primary": True},
    "reject": {"label": "Reject", "from": ["submitted"], "to": "rejected"},
    "cancel": {"label": "Cancel", "from": ["draft", "submitted"], "to": "cancelled"},
    "reset": {"label": "Reset to Draft", "from": ["rejected", "cancelled"], "to": "draft"},
}

CONTRACT_WORKFLOW = {
    "activate": {"label": "Activate", "from": ["draft", "on_hold"], "to": "active", "primary": True},
    "hold": {"label": "Put On Hold", "from": ["active"], "to": "on_hold"},
    "complete": {"label": "Complete", "from": ["active"], "to": "completed", "primary": True},
    "terminate": {"label": "Terminate", "from": ["active", "on_hold"], "to": "terminated"},
    "cancel": {"label": "Cancel", "from": ["draft"], "to": "cancelled"},
}

BOQ_WORKFLOW = {
    "review": {"label": "Submit for Review", "from": ["draft", "revised"], "to": "review", "primary": True},
    "approve": {"label": "Approve", "from": ["review"], "to": "approved", "primary": True},
    "revise": {"label": "Create Revision", "from": ["review", "approved"], "to": "revised"},
    "cancel": {"label": "Cancel", "from": ["draft", "review", "revised"], "to": "cancelled"},
}

EXECUTION_WORKFLOW = {
    "plan": {"label": "Plan", "from": ["draft"], "to": "planned", "primary": True},
    "start": {"label": "Start", "from": ["planned", "on_hold"], "to": "in_progress", "primary": True},
    "hold": {"label": "Put On Hold", "from": ["in_progress"], "to": "on_hold"},
    "complete": {"label": "Complete", "from": ["in_progress"], "to": "done", "primary": True},
    "cancel": {"label": "Cancel", "from": ["draft", "planned", "on_hold"], "to": "cancelled"},
}

CLAIM_WORKFLOW = {
    "submit": {"label": "Submit", "from": ["draft"], "to": "submitted", "primary": True},
    "certify": {"label": "Certify", "from": ["submitted"], "to": "certified", "primary": True},
    "invoice": {"label": "Mark Invoiced", "from": ["certified"], "to": "invoiced", "primary": True},
    "pay": {"label": "Mark Paid", "from": ["invoiced"], "to": "paid", "primary": True},
    "reject": {"label": "Reject", "from": ["submitted"], "to": "rejected"},
    "cancel": {"label": "Cancel", "from": ["draft"], "to": "cancelled"},
}

RISK_WORKFLOW = {
    "mitigate": {"label": "Start Mitigation", "from": ["open"], "to": "mitigating", "primary": True},
    "close": {"label": "Close", "from": ["open", "mitigating"], "to": "closed", "primary": True},
    "accept": {"label": "Accept Risk", "from": ["open", "mitigating"], "to": "accepted"},
}

ISSUE_WORKFLOW = {
    "start": {"label": "Start Resolution", "from": ["open"], "to": "in_progress", "primary": True},
    "resolve": {"label": "Resolve", "from": ["in_progress"], "to": "resolved", "primary": True},
    "close": {"label": "Close", "from": ["resolved"], "to": "closed", "primary": True},
}


def _entry(model, title, singular, columns, search, form_fields, select_related=(), workflow=None, **extra):
    result = {
        "model": model,
        "title": title,
        "singular": singular,
        "columns": columns,
        "search": search,
        "form_fields": form_fields,
        "select_related": list(select_related),
    }
    if workflow:
        result["workflow"] = workflow
    result.update(extra)
    return result


CONSTRUCTION_ENTITY_CONFIG = {
    "construction-product-groups": _entry(
        construction_models.ConstructionProductGroup,
        "Construction Product Groups",
        "Construction Product Group",
        [("code", "Code"), ("name", "Group"), ("company", "Company"), ("active", "Active")],
        ["code", "name", "description"],
        ["company", "branch", "code", "name", "description", "products", "active"],
        ["company"],
    ),
    "construction-boq-templates": _entry(
        construction_models.ConstructionBOQTemplate,
        "BOQ Templates",
        "BOQ Template",
        [("code", "Code"), ("name", "Template"), ("company", "Company"), ("active", "Active")],
        ["code", "name", "description"],
        ["company", "code", "name", "description", "active"],
        ["company"],
        admin_detail=True,
    ),
    "construction-boq-template-items": _entry(
        construction_models.ConstructionBOQTemplateItem,
        "BOQ Template Items",
        "BOQ Template Item",
        [("template", "Template"), ("sequence", "Sequence"), ("description", "Description"), ("quantity", "Qty"), ("unit_cost", "Unit Cost")],
        ["template__code", "template__name", "description", "product__name"],
        ["template", "sequence", "product_group", "product", "description", "unit", "quantity", "unit_cost"],
        ["template", "product_group", "product"],
    ),
    "construction-contracts": _entry(
        construction_models.ConstructionContract,
        "Construction Contracts",
        "Construction Contract",
        [("number", "Number"), ("title", "Contract"), ("project", "Project"), ("customer", "Customer"), ("contract_value", "Value"), ("status", "Status")],
        ["number", "title", "project__code", "project__name", "customer__name"],
        ["project", "number", "customer", "title", "contract_date", "start_date", "end_date", "contract_value", "down_payment_percent", "retention_percent", "notes"],
        ["project", "customer"],
        CONTRACT_WORKFLOW,
        admin_detail=True,
        workflow_locked_statuses={"completed", "terminated", "cancelled"},
    ),
    "construction-contract-milestones": _entry(
        construction_models.ConstructionContractMilestone,
        "Contract Milestones",
        "Contract Milestone",
        [("contract", "Contract"), ("sequence", "Sequence"), ("name", "Milestone"), ("due_date", "Due Date"), ("percentage", "%"), ("achieved", "Achieved")],
        ["contract__number", "name"],
        ["contract", "sequence", "name", "due_date", "percentage", "amount", "achieved", "achieved_date"],
        ["contract", "contract__project"],
    ),
    "construction-boqs": _entry(
        construction_models.ConstructionBOQ,
        "Bills of Quantity",
        "Bill of Quantity",
        [("number", "Number"), ("revision", "Revision"), ("title", "BOQ"), ("project", "Project"), ("contract", "Contract"), ("status", "Status")],
        ["number", "title", "project__code", "project__name", "contract__number"],
        ["project", "contract", "number", "title", "revision", "boq_date", "notes"],
        ["project", "contract"],
        BOQ_WORKFLOW,
        admin_detail=True,
        workflow_locked_statuses={"approved", "cancelled"},
    ),
    "construction-boq-items": _entry(
        construction_models.ConstructionBOQItem,
        "BOQ Items",
        "BOQ Item",
        [("boq", "BOQ"), ("sequence", "Sequence"), ("description", "Description"), ("quantity", "Qty"), ("unit_price", "Unit Price"), ("procurement_method", "Procurement")],
        ["boq__number", "description", "product__name", "work_package__code"],
        ["boq", "sequence", "work_package", "product_group", "product", "description", "unit", "quantity", "unit_price", "budget_cost", "procurement_method"],
        ["boq", "boq__project", "work_package", "product_group", "product"],
    ),
    "construction-cost-sheets": _entry(
        construction_models.ConstructionCostSheet,
        "Cost Sheets",
        "Cost Sheet",
        [("number", "Number"), ("title", "Cost Sheet"), ("project", "Project"), ("budget_amount", "Budget"), ("actual_amount", "Actual"), ("status", "Status")],
        ["number", "title", "project__code", "project__name", "boq__number"],
        ["project", "boq", "number", "title", "sheet_date", "budget_amount", "committed_amount", "actual_amount"],
        ["project", "boq"],
        APPROVAL_WORKFLOW,
        admin_detail=True,
    ),
    "construction-cost-sheet-items": _entry(
        construction_models.ConstructionCostSheetItem,
        "Cost Sheet Items",
        "Cost Sheet Item",
        [("cost_sheet", "Cost Sheet"), ("description", "Description"), ("quantity", "Planned Qty"), ("unit_cost", "Unit Cost"), ("actual_quantity", "Actual Qty"), ("actual_cost", "Actual Cost")],
        ["cost_sheet__number", "description", "product__name", "boq_item__description"],
        ["cost_sheet", "boq_item", "product", "description", "quantity", "unit_cost", "actual_quantity", "actual_cost"],
        ["cost_sheet", "cost_sheet__project", "boq_item", "product"],
    ),
    "construction-budget-changes": _entry(
        construction_models.ConstructionBudgetChangeRequest,
        "Budget Change Requests",
        "Budget Change Request",
        [("number", "Number"), ("request_date", "Date"), ("title", "Request"), ("project", "Project"), ("amount", "Amount"), ("status", "Status")],
        ["number", "title", "reason", "project__code", "project__name"],
        ["project", "number", "request_date", "title", "reason", "amount", "requested_by", "approver"],
        ["project", "requested_by", "approver"],
        APPROVAL_WORKFLOW,
    ),
    "construction-budget-change-lines": _entry(
        construction_models.ConstructionBudgetChangeLine,
        "Budget Change Lines",
        "Budget Change Line",
        [("request", "Request"), ("description", "Description"), ("previous_amount", "Previous"), ("proposed_amount", "Proposed"), ("variance", "Variance")],
        ["request__number", "description", "cost_sheet_item__description"],
        ["request", "cost_sheet_item", "description", "previous_amount", "proposed_amount"],
        ["request", "request__project", "cost_sheet_item"],
    ),
    "construction-job-orders": _entry(
        construction_models.ConstructionJobOrder,
        "Construction Job Orders",
        "Construction Job Order",
        [("number", "Number"), ("title", "Job Order"), ("project", "Project"), ("responsible", "Responsible"), ("progress", "Progress %"), ("status", "Status")],
        ["number", "title", "project__code", "project__name", "responsible__username"],
        ["project", "work_package", "number", "title", "responsible", "planned_start", "planned_end", "actual_start", "actual_end", "planned_quantity", "completed_quantity", "progress", "notes"],
        ["project", "work_package", "responsible"],
        EXECUTION_WORKFLOW,
        admin_detail=True,
        workflow_locked_statuses={"done", "cancelled"},
    ),
    "construction-progress-logs": _entry(
        construction_models.ConstructionProgressLog,
        "Progress Logs",
        "Progress Log",
        [("job_order", "Job Order"), ("log_date", "Date"), ("progress", "Progress %"), ("completed_quantity", "Completed Qty"), ("recorded_by", "Recorded By")],
        ["job_order__number", "job_order__title", "notes", "weather", "recorded_by__username"],
        ["job_order", "log_date", "progress", "completed_quantity", "weather", "notes", "recorded_by"],
        ["job_order", "job_order__project", "recorded_by"],
    ),
    "construction-task-workers": _entry(
        construction_models.ConstructionTaskWorker,
        "Task Workers",
        "Task Worker",
        [("job_order", "Job Order"), ("employee", "Employee"), ("role", "Role"), ("assigned_date", "Assigned"), ("planned_hours", "Planned Hours")],
        ["job_order__number", "employee__employee_id", "employee__name", "role"],
        ["job_order", "employee", "role", "assigned_date", "released_date", "planned_hours"],
        ["job_order", "job_order__project", "employee"],
    ),
    "construction-quantity-surveys": _entry(
        construction_models.ConstructionQuantitySurvey,
        "Quantity Surveys",
        "Quantity Survey",
        [("number", "Number"), ("survey_date", "Date"), ("project", "Project"), ("job_order", "Job Order"), ("surveyor", "Surveyor"), ("status", "Status")],
        ["number", "project__code", "project__name", "job_order__number", "surveyor__username"],
        ["project", "job_order", "number", "survey_date", "surveyor", "notes"],
        ["project", "job_order", "surveyor"],
        APPROVAL_WORKFLOW,
        admin_detail=True,
    ),
    "construction-quantity-survey-lines": _entry(
        construction_models.ConstructionQuantitySurveyLine,
        "Quantity Survey Lines",
        "Quantity Survey Line",
        [("survey", "Survey"), ("boq_item", "BOQ Item"), ("measured_quantity", "Measured"), ("accepted_quantity", "Accepted"), ("location", "Location")],
        ["survey__number", "boq_item__description", "location", "remarks"],
        ["survey", "boq_item", "measured_quantity", "accepted_quantity", "location", "remarks"],
        ["survey", "survey__project", "boq_item"],
    ),
    "construction-resource-actualizations": _entry(
        construction_models.ConstructionResourceActualization,
        "Resource Actualizations",
        "Resource Actualization",
        [("number", "Number"), ("actualization_date", "Date"), ("project", "Project"), ("job_order", "Job Order"), ("status", "Status")],
        ["number", "project__code", "project__name", "job_order__number", "notes"],
        ["project", "job_order", "number", "actualization_date", "notes"],
        ["project", "job_order"],
        APPROVAL_WORKFLOW,
        admin_detail=True,
    ),
    "construction-resource-lines": _entry(
        construction_models.ConstructionResourceActualizationLine,
        "Resource Actualization Lines",
        "Resource Actualization Line",
        [("actualization", "Actualization"), ("resource_type", "Type"), ("description", "Resource"), ("quantity", "Qty"), ("unit_cost", "Unit Cost"), ("amount", "Amount")],
        ["actualization__number", "description", "product__name", "employee__name", "asset__name"],
        ["actualization", "resource_type", "product", "employee", "asset", "description", "quantity", "unit_cost"],
        ["actualization", "actualization__project", "product", "employee", "asset"],
    ),
    "construction-timesheets": _entry(
        construction_models.ConstructionTimesheet,
        "Construction Timesheets",
        "Construction Timesheet",
        [("number", "Number"), ("project", "Project"), ("period_start", "Period Start"), ("period_end", "Period End"), ("approved_by", "Approved By"), ("status", "Status")],
        ["number", "project__code", "project__name", "approved_by__username", "notes"],
        ["project", "number", "period_start", "period_end", "approved_by", "notes"],
        ["project", "approved_by"],
        APPROVAL_WORKFLOW,
        admin_detail=True,
    ),
    "construction-timesheet-lines": _entry(
        construction_models.ConstructionTimesheetLine,
        "Construction Timesheet Lines",
        "Construction Timesheet Line",
        [("work_date", "Date"), ("timesheet", "Timesheet"), ("employee", "Employee"), ("job_order", "Job Order"), ("hours", "Hours")],
        ["timesheet__number", "employee__employee_id", "employee__name", "job_order__number", "description"],
        ["timesheet", "employee", "job_order", "work_date", "hours", "description"],
        ["timesheet", "timesheet__project", "employee", "job_order"],
    ),
    "construction-progressive-claims": _entry(
        construction_models.ConstructionProgressiveClaim,
        "Progressive Claims",
        "Progressive Claim",
        [("number", "Number"), ("project", "Project"), ("contract", "Contract"), ("period_end", "Period End"), ("gross_amount", "Gross"), ("certified_amount", "Certified"), ("status", "Status")],
        ["number", "project__code", "project__name", "contract__number", "notes"],
        ["project", "contract", "number", "period_start", "period_end", "gross_amount", "retention_amount", "advance_recovery", "certified_amount", "notes"],
        ["project", "contract"],
        CLAIM_WORKFLOW,
        admin_detail=True,
        workflow_locked_statuses={"paid", "cancelled"},
    ),
    "construction-claim-lines": _entry(
        construction_models.ConstructionProgressiveClaimLine,
        "Progressive Claim Lines",
        "Progressive Claim Line",
        [("claim", "Claim"), ("boq_item", "BOQ Item"), ("previous_quantity", "Previous Qty"), ("current_quantity", "Current Qty"), ("certified_quantity", "Certified Qty"), ("current_amount", "Amount")],
        ["claim__number", "boq_item__description", "boq_item__boq__number"],
        ["claim", "boq_item", "previous_quantity", "current_quantity", "unit_price", "certified_quantity"],
        ["claim", "claim__project", "boq_item"],
    ),
    "construction-risks": _entry(
        construction_models.ConstructionRiskRegister,
        "Construction Risk Register",
        "Construction Risk",
        [("number", "Number"), ("title", "Risk"), ("project", "Project"), ("probability", "Probability"), ("impact", "Impact"), ("owner", "Owner"), ("status", "Status")],
        ["number", "title", "category", "project__code", "project__name", "mitigation"],
        ["project", "number", "title", "category", "probability", "impact", "owner", "mitigation", "due_date"],
        ["project", "owner"],
        RISK_WORKFLOW,
        workflow_locked_statuses={"closed", "accepted"},
    ),
    "construction-issues": _entry(
        construction_models.ConstructionIssueLog,
        "Construction Issue Log",
        "Construction Issue",
        [("number", "Number"), ("title", "Issue"), ("project", "Project"), ("priority", "Priority"), ("owner", "Owner"), ("target_date", "Target"), ("status", "Status")],
        ["number", "title", "description", "project__code", "project__name", "resolution"],
        ["project", "risk", "number", "title", "description", "priority", "owner", "target_date", "resolution"],
        ["project", "risk", "owner"],
        ISSUE_WORKFLOW,
        workflow_locked_statuses={"closed"},
    ),
    "construction-opening-balances": _entry(
        construction_models.ConstructionOpeningBalance,
        "Construction Opening Balances",
        "Construction Opening Balance",
        [("reference", "Reference"), ("balance_date", "Date"), ("project", "Project"), ("budget_amount", "Budget"), ("committed_amount", "Committed"), ("actual_amount", "Actual"), ("status", "Status")],
        ["reference", "project__code", "project__name", "source_note"],
        ["project", "reference", "balance_date", "budget_amount", "committed_amount", "actual_amount", "source_note"],
        ["project"],
        APPROVAL_WORKFLOW,
    ),
    "construction-account-settings": _entry(
        construction_models.ConstructionProjectAccountSettings,
        "Project Account Settings",
        "Project Account Settings",
        [("project", "Project"), ("revenue_account", "Revenue"), ("cost_account", "Cost"), ("work_in_progress_account", "WIP"), ("retention_account", "Retention"), ("active", "Active")],
        ["project__code", "project__name", "revenue_account__code", "cost_account__code"],
        ["project", "revenue_account", "cost_account", "work_in_progress_account", "retention_account", "active"],
        ["project", "revenue_account", "cost_account", "work_in_progress_account", "retention_account"],
    ),
    "construction-material-demands": _entry(
        construction_models.ConstructionMaterialDemand,
        "Construction Material Demands",
        "Construction Material Demand",
        [("number", "Number"), ("request_date", "Request Date"), ("required_date", "Required Date"), ("project", "Project"), ("job_order", "Job Order"), ("warehouse", "Warehouse"), ("status", "Status")],
        ["number", "project__code", "project__name", "job_order__number", "warehouse__name", "notes"],
        ["project", "job_order", "warehouse", "number", "request_date", "required_date", "requested_by", "notes"],
        ["project", "job_order", "warehouse", "requested_by"],
        APPROVAL_WORKFLOW,
        admin_detail=True,
    ),
    "construction-material-demand-lines": _entry(
        construction_models.ConstructionMaterialDemandLine,
        "Material Demand Lines",
        "Material Demand Line",
        [("demand", "Demand"), ("product", "Product"), ("requested_quantity", "Requested"), ("allocated_quantity", "Allocated"), ("delivered_quantity", "Delivered")],
        ["demand__number", "product__sku", "product__name", "description"],
        ["demand", "product", "description", "requested_quantity", "allocated_quantity", "delivered_quantity"],
        ["demand", "demand__project", "product"],
    ),
    "construction-procurement-allocations": _entry(
        construction_models.ConstructionProcurementAllocation,
        "Construction Procurement Allocations",
        "Construction Procurement Allocation",
        [("project", "Project"), ("description", "Allocation"), ("purchase_request", "Purchase Request"), ("purchase_order", "Purchase Order"), ("allocated_quantity", "Qty"), ("allocated_amount", "Amount"), ("budget_acknowledged", "Budget Ack")],
        ["project__code", "project__name", "description", "purchase_request__number", "purchase_order__number"],
        ["project", "boq_item", "purchase_request", "purchase_order", "description", "allocated_quantity", "allocated_amount", "budget_acknowledged", "notes"],
        ["project", "boq_item", "purchase_request", "purchase_order"],
    ),
    "construction-subcontract-job-orders": _entry(
        construction_models.ConstructionSubcontractJobOrder,
        "Subcontract Job Orders",
        "Subcontract Job Order",
        [("number", "Number"), ("project", "Project"), ("job_order", "Job Order"), ("vendor", "Vendor"), ("contract_amount", "Amount"), ("progress", "Progress %"), ("status", "Status")],
        ["number", "project__code", "project__name", "job_order__number", "vendor__name", "scope"],
        ["project", "job_order", "number", "vendor", "scope", "start_date", "end_date", "contract_amount", "progress"],
        ["project", "job_order", "vendor"],
        EXECUTION_WORKFLOW,
        workflow_locked_statuses={"done", "cancelled"},
    ),
}


CONSTRUCTION_ENTITY_CONFIG.update(
    {
        "construction-boq-template-cost-lines": _entry(
            construction_models.ConstructionBOQTemplateCostLine, "BOQ Template Resources", "BOQ Template Resource",
            [("template_item", "Template Item"), ("cost_category", "Category"), ("description", "Resource"), ("quantity", "Qty"), ("unit_rate", "Rate"), ("amount", "Amount")],
            ["template_item__template__code", "template_item__description", "description", "product__name"],
            ["template_item", "sequence", "input_type", "cost_category", "product", "job_position", "vendor", "work_package", "description", "ownership", "quantity", "unit", "unit_rate", "notes"],
            ["template_item", "template_item__template", "product", "job_position", "vendor", "work_package"],
        ),
        "construction-contract-dp-lines": _entry(
            construction_models.ConstructionContractDPLine, "Contract Down Payment Schedule", "Down Payment Schedule Line",
            [("contract", "Contract"), ("sequence", "Sequence"), ("portion_percent", "Portion %"), ("invoice_amount", "Amount"), ("payment_status", "Status")],
            ["contract__number", "invoice__number"],
            ["contract", "sequence", "invoice", "invoice_date", "portion_percent", "invoice_amount", "payment_status"],
            ["contract", "invoice"],
        ),
        "construction-boq-cost-lines": _entry(
            construction_models.ConstructionBOQCostLine, "BOQ Resource Costs", "BOQ Resource Cost",
            [("boq_item", "BOQ Item"), ("cost_category", "Category"), ("description", "Resource"), ("quantity", "Qty"), ("unit_rate", "Rate"), ("amount", "Amount")],
            ["boq_item__boq__number", "boq_item__description", "description", "product__name"],
            ["boq_item", "sequence", "input_type", "cost_category", "product", "job_position", "vendor", "work_package", "description", "ownership", "quantity", "unit", "unit_rate", "markup_type", "markup_value", "discount_type", "discount_value", "tax_percent", "notes"],
            ["boq_item", "boq_item__boq", "product", "job_position", "vendor", "work_package"],
        ),
        "construction-cost-sheet-resources": _entry(
            construction_models.ConstructionCostSheetResource, "Cost Sheet Resources", "Cost Sheet Resource",
            [("cost_sheet_item", "Cost Sheet Item"), ("cost_category", "Category"), ("description", "Resource"), ("quantity", "Qty"), ("unit_rate", "Rate"), ("revised_cost", "Revised Cost")],
            ["cost_sheet_item__cost_sheet__number", "cost_sheet_item__description", "description", "product__name"],
            ["cost_sheet_item", "boq_resource", "sequence", "cost_category", "input_type", "product", "job_position", "vendor", "work_package", "description", "unit", "quantity", "unit_rate", "budget_cost_adjustment", "revised_cost", "ownership", "reserved_quantity", "delivered_quantity", "utilised_quantity", "utilised_budget", "onsite_stock_quantity", "notes"],
            ["cost_sheet_item", "cost_sheet_item__cost_sheet", "boq_resource", "product", "job_position", "vendor", "work_package"],
        ),
        "construction-equipment-facilities": _entry(
            construction_models.ConstructionEquipmentFacility, "Job Order Equipment & Facilities", "Equipment / Facility Allocation",
            [("job_order", "Job Order"), ("asset", "Asset"), ("category", "Category"), ("start_date", "Start"), ("end_date", "End"), ("planned_hours", "Planned Hours")],
            ["job_order__number", "asset__code", "asset__name", "category"],
            ["job_order", "asset", "category", "start_date", "end_date", "planned_hours", "actual_hours", "notes"],
            ["job_order", "asset"],
        ),
        "construction-quantity-survey-sources": _entry(
            construction_models.ConstructionQuantitySurveyJOSource, "Quantity Survey Job Order Sources", "Quantity Survey Source",
            [("survey_line", "Survey Line"), ("progress_log", "Progress Log"), ("previous_approved_quantity", "Previous"), ("available_quantity", "Available"), ("approved_quantity", "Approved")],
            ["survey_line__survey__number", "progress_log__job_order__number", "notes"],
            ["survey_line", "progress_log", "previous_approved_quantity", "approved_quantity", "notes"],
            ["survey_line", "survey_line__survey", "progress_log", "progress_log__job_order"],
        ),
        "construction-claim-cumulative-lines": _entry(
            construction_models.ConstructionProgressiveClaimCumulative, "Claim Cumulative History", "Claim Cumulative Line",
            [("claim", "Claim"), ("boq_item", "BOQ Item"), ("previous_quantity", "Previous"), ("this_period_quantity", "This Period"), ("cumulative_quantity", "Cumulative"), ("cumulative_amount", "Amount")],
            ["claim__number", "boq_item__description", "invoice_reference"],
            ["claim", "sequence", "boq_item", "previous_quantity", "this_period_quantity", "cumulative_quantity", "contract_quantity", "progress_percent", "cumulative_amount", "invoice_reference", "display_status", "is_current_claim"],
            ["claim", "boq_item"], editable=False, creatable=False, deletable=False,
        ),
        "construction-claim-attachments": _entry(
            construction_models.ConstructionProgressiveClaimAttachment, "Claim Attachments", "Claim Attachment",
            [("claim", "Claim"), ("sequence", "Sequence"), ("document_name", "Document"), ("document_type", "Type"), ("document_date", "Date"), ("uploaded_by", "Uploaded By")],
            ["claim__number", "document_name", "document_type"],
            ["claim", "sequence", "document_name", "document_type", "document_date", "uploaded_by", "file"],
            ["claim", "uploaded_by"],
        ),
        "construction-project-profiles": _entry(
            construction_models.ConstructionProjectProfile, "Construction Project Profiles", "Construction Project Profile",
            [("project", "Project"), ("project_type", "Project Type"), ("construction_type", "Construction Type"), ("owner_client", "Owner"), ("financial_progress", "Financial Progress")],
            ["project__code", "project__name", "project_type", "construction_type", "owner_client__name"],
            ["project", "project_type", "construction_type", "owner_client", "financial_progress", "warehouses", "account_snapshot_ready", "account_snapshot_locked", "account_snapshot_note"],
            ["project", "owner_client"],
        ),
        "construction-account-setting-lines": _entry(
            construction_models.ConstructionProjectAccountSettingsLine, "Construction CIP Accounts", "Construction CIP Account",
            [("settings", "Settings"), ("resource_category", "Resource Category"), ("account", "Account"), ("usage_help", "Usage")],
            ["settings__project__code", "account__code", "account__name", "resource_category"],
            ["settings", "resource_category", "account", "usage_help"], ["settings", "settings__project", "account"],
        ),
        "construction-account-snapshots": _entry(
            construction_models.ConstructionProjectAccountSnapshotLine, "Construction Account Snapshots", "Account Snapshot",
            [("project", "Project"), ("resource_category", "Category"), ("account_display", "Account"), ("account_type_label", "Type")],
            ["project__code", "resource_category", "account_display"],
            ["project", "resource_category", "account", "account_display", "account_type_label", "usage_help"], ["project", "account"],
            editable=False, creatable=False, deletable=False,
        ),
        "construction-opening-balance-imports": _entry(
            construction_models.ConstructionOpeningBalanceImport, "Construction Opening Balance Imports", "Opening Balance Import",
            [("name", "Import"), ("project", "Project"), ("source_type", "Source"), ("cutoff_date", "Cutoff"), ("state", "State"), ("created_count", "Created")],
            ["name", "project__code", "source_reference", "fingerprint"],
            ["name", "project", "source_type", "source_file", "source_reference", "cutoff_date", "canonical_payload", "imported_by", "notes"], ["project", "imported_by"],
            workflow={
                "execute": {"label": "Validate & Import", "from": ["draft", "validated", "failed"], "to": "completed", "primary": True, "service_controls_state": True},
                "cancel": {"label": "Cancel", "from": ["draft", "validated", "failed"], "to": "cancelled"},
            }, workflow_field="state",
        ),
        "construction-opening-balance-exports": _entry(
            construction_models.ConstructionOpeningBalanceExportSnapshot, "Construction Opening Balance Exports", "Opening Balance Export",
            [("export_token", "Token"), ("project", "Project"), ("boq", "BOQ"), ("cutoff_date", "Cutoff"), ("state", "State")],
            ["export_token", "project__code", "boq__number", "baseline_digest"],
            ["export_token", "schema_version", "exported_by", "project", "boq", "cutoff_date", "baseline", "baseline_digest", "state"], ["exported_by", "project", "boq"],
        ),
        "construction-boq-imports": _entry(
            construction_models.ConstructionBOQImportLog, "BOQ Import History", "BOQ Import",
            [("boq", "BOQ"), ("source_type", "Source"), ("state", "State"), ("attempted_count", "Attempted"), ("imported_count", "Imported"), ("imported_by", "Imported By"), ("created_at", "Created")],
            ["boq__number", "source_type", "state", "source_checksum", "imported_by__username"],
            ["boq", "source_type", "source_file", "source_metadata", "payload", "imported_by"], ["boq", "boq__project", "imported_by"],
            workflow={
                "validate": {"label": "Validate Import", "from": ["draft", "failed"], "to": "validated", "primary": True, "service_controls_state": True},
                "import": {"label": "Execute Import", "from": ["validated"], "to": "imported", "primary": True, "service_controls_state": True},
            }, workflow_field="state",
        ),
        "construction-procurement-lines": _entry(
            construction_models.ConstructionProcurementLine, "Construction Procurement Detail", "Construction Procurement Line",
            [("project", "Project"), ("cost_sheet_resource", "Resource"), ("reserved_quantity", "Reserved"), ("ordered_quantity", "Ordered"), ("received_quantity", "Received"), ("budget_decision", "Budget Decision")],
            ["project__code", "cost_sheet__number", "cost_sheet_resource__description", "purchase_request_line__request__number", "purchase_order_line__order__number"],
            ["project", "cost_sheet", "cost_sheet_item", "cost_sheet_resource", "purchase_request_line", "purchase_order_line", "reserved_quantity", "ordered_quantity", "received_quantity", "unit_rate", "budget_decision", "notes"],
            ["project", "cost_sheet", "cost_sheet_item", "cost_sheet_resource", "purchase_request_line", "purchase_order_line"],
        ),
        "construction-inventory-transfers": _entry(
            construction_models.ConstructionInventoryTransfer, "Construction Inventory Transfers", "Construction Inventory Transfer",
            [("number", "Number"), ("project", "Project"), ("source_warehouse", "Source"), ("destination_warehouse", "Destination"), ("transfer_date", "Date"), ("status", "Status")],
            ["number", "project__code", "source_warehouse__name", "destination_warehouse__name"],
            ["project", "number", "source_warehouse", "destination_warehouse", "transfer_date", "purchase_order", "notes"],
            ["project", "source_warehouse", "destination_warehouse", "purchase_order"],
        ),
        "construction-inventory-transfer-lines": _entry(
            construction_models.ConstructionInventoryTransferLine, "Construction Transfer Lines", "Construction Transfer Line",
            [("transfer", "Transfer"), ("product", "Product"), ("quantity", "Quantity"), ("material_demand_line", "Demand Line"), ("cost_sheet_resource", "Cost Resource")],
            ["transfer__number", "product__sku", "product__name", "notes"],
            ["transfer", "material_demand_line", "cost_sheet_resource", "product", "quantity", "notes"],
            ["transfer", "product", "material_demand_line", "cost_sheet_resource"],
        ),
        "construction-subcontract-progress-lines": _entry(
            construction_models.ConstructionSubcontractProgressLine, "Subcontract Progress", "Subcontract Progress Line",
            [("subcontract", "Subcontract"), ("progress_date", "Date"), ("quantity", "Quantity"), ("cumulative_quantity", "Cumulative"), ("progress_percent", "Progress %"), ("amount", "Amount")],
            ["subcontract__number", "notes"],
            ["subcontract", "progress_date", "quantity", "cumulative_quantity", "progress_percent", "amount", "evidence", "notes"], ["subcontract"],
        ),
        "construction-subcontract-milestones": _entry(
            construction_models.ConstructionSubcontractMilestone, "Subcontract Milestones", "Subcontract Milestone",
            [("subcontract", "Subcontract"), ("sequence", "Sequence"), ("name", "Milestone"), ("percentage", "%"), ("amount", "Amount"), ("achieved", "Achieved")],
            ["subcontract__number", "name", "invoice__number"],
            ["subcontract", "sequence", "name", "percentage", "amount", "due_date", "achieved", "achieved_date", "invoice"], ["subcontract", "invoice"],
        ),
        "construction-accounting-postings": _entry(
            construction_models.ConstructionAccountingPosting, "Construction Accounting Postings", "Construction Accounting Posting",
            [("reference", "Reference"), ("project", "Project"), ("posting_type", "Type"), ("posting_date", "Date"), ("amount", "Amount"), ("journal_entry", "Journal")],
            ["reference", "project__code", "progressive_claim__number", "actualization__number", "contract__number", "journal_entry__number"],
            ["project", "posting_type", "reference", "posting_date", "amount", "progressive_claim", "actualization", "contract", "posted_by", "notes"],
            ["project", "progressive_claim", "actualization", "contract", "journal_entry", "posted_by"],
        ),
        "construction-procurement-budget-audits": _entry(
            construction_models.ConstructionProcurementBudgetAudit, "Construction Procurement Budget Audit", "Budget Audit",
            [("document_name", "Document"), ("operation", "Operation"), ("decision", "Decision"), ("acknowledged", "Acknowledged"), ("user", "User"), ("created_at", "Created")],
            ["document_name", "operation", "decision", "user__username"],
            ["document_model", "document_id", "document_name", "operation", "state_before", "state_after", "decision", "acknowledged", "snapshot"], ["user", "company"],
            editable=False, creatable=False, deletable=False,
        ),
    }
)

# Header forms expose the same operational fields as the HMX construction forms.
CONSTRUCTION_ENTITY_CONFIG["construction-boq-templates"]["form_fields"] = ["company", "branch", "code", "name", "description", "internal_notes", "active"]
CONSTRUCTION_ENTITY_CONFIG["construction-contracts"]["form_fields"] = ["project", "number", "contract_type", "customer", "title", "contract_date", "billing_type", "currency", "start_date", "end_date", "contract_value", "linked_boq", "linked_cost_sheet", "responsible_user", "dp_type", "down_payment_percent", "down_payment_amount", "down_payment_status", "retention_percent", "retention2_percent", "retention1_release_date", "retention2_release_date", "notes"]
CONSTRUCTION_ENTITY_CONFIG["construction-boqs"]["form_fields"] = ["project", "contract", "number", "title", "revision", "boq_date", "use_quantity_surveying", "template", "global_markup_type", "global_markup_value", "global_discount_type", "global_discount_value", "global_tax_percent", "internal_notes", "notes"]
CONSTRUCTION_ENTITY_CONFIG["construction-cost-sheets"]["form_fields"] = ["project", "boq", "number", "title", "project_manager", "period", "branch", "sheet_date", "budget_amount", "committed_amount", "actual_amount", "procurement_validation_level", "procurement_warning_threshold", "procurement_block_threshold"]
CONSTRUCTION_ENTITY_CONFIG["construction-quantity-surveys"]["form_fields"] = ["project", "contract", "boq", "job_order", "number", "survey_start_date", "survey_end_date", "survey_date", "surveyor", "responsible_user", "currency", "notes"]
CONSTRUCTION_ENTITY_CONFIG["construction-resource-actualizations"]["form_fields"] = ["project", "source_type", "cost_sheet", "cost_sheet_item", "boq_item", "job_order", "number", "site_location", "actualized_by", "actualization_date", "budget_cost", "notes"]
CONSTRUCTION_ENTITY_CONFIG["construction-progressive-claims"]["form_fields"] = ["project", "contract", "boq", "quantity_survey", "number", "period_start", "period_end", "claim_date", "claim_type", "vendor", "responsible_user", "prepared_by", "currency", "gross_amount", "retention_amount", "retention2_amount", "advance_recovery", "certified_amount", "invoice", "notes"]
CONSTRUCTION_ENTITY_CONFIG["construction-account-settings"]["form_fields"] = ["project", "revenue_account", "owner_down_payment_account", "owner_claim_request_account", "retention_account", "owner_penalty_receivable_account", "cost_account", "subcontract_cost_account", "subcontract_down_payment_account", "subcontract_claim_request_account", "subcontract_retention_payable_account", "subcontract_penalty_account", "work_in_progress_account", "alternative_cip_account", "active"]
CONSTRUCTION_ENTITY_CONFIG["construction-boq-template-items"]["form_fields"] = ["template", "sequence", "parent_item", "code", "name", "description", "level", "is_leaf_node", "is_direct_amount", "direct_amount_info", "product_group", "product", "unit", "quantity", "unit_cost"]
CONSTRUCTION_ENTITY_CONFIG["construction-boq-items"]["form_fields"] = ["boq", "sequence", "parent_item", "wbs_code", "level", "is_leaf_node", "is_direct_amount", "direct_amount_info", "work_package", "product_group", "product", "description", "unit", "quantity", "unit_price", "budget_cost", "actual_quantity", "physical_progress", "procurement_method", "commercial_markup_type", "commercial_markup_value", "commercial_discount_type", "commercial_discount_value", "commercial_tax_percent"]
CONSTRUCTION_ENTITY_CONFIG["construction-cost-sheet-items"]["form_fields"] = ["cost_sheet", "boq_item", "parent_item", "sequence", "wbs_code", "level", "is_leaf_node", "is_cost_sheet_only", "is_direct_amount", "product", "description", "quantity", "unit_cost", "actual_quantity", "actual_cost", "revenue_adjustment", "budget_cost_adjustment", "quantity_adjustment", "unit_rate_adjustment", "utilised_budget", "planned_start", "planned_end"]
CONSTRUCTION_ENTITY_CONFIG["construction-budget-changes"]["form_fields"] = ["project", "cost_sheet", "number", "request_date", "title", "reason", "current_total_cost", "requested_by", "approver"]
CONSTRUCTION_ENTITY_CONFIG["construction-budget-change-lines"]["form_fields"] = ["request", "sequence", "line_type", "change_type", "cost_sheet_item", "cost_sheet_resource", "description", "previous_amount", "proposed_amount", "current_quantity", "revised_quantity", "current_unit_rate", "revised_unit_rate", "new_wbs_code", "new_wbs_name", "new_description", "new_plan_start", "new_plan_end", "cost_category", "product", "job_position", "vendor", "work_package", "zeroed"]
CONSTRUCTION_ENTITY_CONFIG["construction-job-orders"]["form_fields"] = ["project", "source_type", "cost_sheet", "cost_sheet_item", "cost_sheet_resource", "boq_item", "work_package", "number", "title", "execution_type", "subcontractor", "subcontract_unit_rate", "responsible", "planned_start", "planned_end", "actual_start", "actual_end", "planned_quantity", "allocated_quantity", "completed_quantity", "unit", "progress", "is_billable", "commercial_progress_eligible", "notes"]
CONSTRUCTION_ENTITY_CONFIG["construction-quantity-survey-lines"]["form_fields"] = ["survey", "sequence", "boq_item", "previous_quantity", "measured_quantity", "accepted_quantity", "contract_quantity", "unit_rate", "location", "remarks"]
CONSTRUCTION_ENTITY_CONFIG["construction-claim-lines"]["form_fields"] = ["claim", "sequence", "boq_item", "quantity_survey_line", "job_orders", "previous_quantity", "current_quantity", "certified_quantity", "contract_quantity", "unit_price", "source"]


ENTITY_MODELS = {slug: config["model"] for slug, config in CONSTRUCTION_ENTITY_CONFIG.items()}
