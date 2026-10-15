"""Independent GSSHOP application workspace definitions.

These definitions connect application cards to the clean-room ERP screens. They
contain no HMX source code or assets.
"""


APP_WORKSPACES = {
    "foundation": {
        "tagline": "Shared business foundation",
        "entities": ["companies", "contacts"],
        "workflow": ["Configure companies", "Create customers and vendors", "Assign users and permissions"],
    },
    "app-manager": {
        "tagline": "Control your ERP application suite",
        "entities": [],
        "workflow": ["Review application maturity", "Install required capabilities", "Open each application workspace"],
    },
    "product": {
        "tagline": "One catalog for every sales and supply workflow",
        "entities": ["products"],
        "workflow": ["Create products and services", "Set buying and selling prices", "Monitor stock levels"],
    },
    "inventory": {
        "tagline": "Traceable inventory across every warehouse",
        "entities": ["warehouses", "stock-moves", "products"],
        "workflow": ["Configure warehouses", "Record stock movements", "Review replenishment levels"],
    },
    "sales": {
        "tagline": "Turn customer demand into fulfilled orders",
        "entities": ["sales", "rental-details", "contacts", "products"],
        "workflow": ["Select a customer", "Create an order with rental dates", "Monitor Detail Sewa", "Confirm and fulfill"],
    },
    "purchase": {
        "tagline": "Control supplier purchasing from request to receipt",
        "entities": ["purchases", "contacts", "products"],
        "workflow": ["Select a supplier", "Create a purchase order", "Receive and reconcile"],
    },
    "invoicing": {
        "tagline": "Manage customer invoices and vendor bills",
        "entities": ["invoices", "contacts"],
        "workflow": ["Create an invoice or bill", "Post the document", "Track payment status"],
    },
    "crm": {
        "tagline": "Build a visible, accountable sales pipeline",
        "entities": ["leads", "opportunities"],
        "workflow": ["Capture a lead", "Qualify the opportunity", "Advance and close the deal"],
    },
    "hr": {
        "tagline": "Manage the employee lifecycle in one workspace",
        "entities": ["employees", "departments", "contracts", "attendance", "leave-requests", "overtime"],
        "workflow": ["Set up departments", "Onboard employees", "Run attendance and leave operations"],
    },
    "payroll": {
        "tagline": "Prepare and track employee compensation",
        "entities": ["payslips", "contracts", "employees"],
        "workflow": ["Verify active contracts", "Prepare payslips", "Confirm and mark payments"],
    },
    "recruitment": {
        "tagline": "Move candidates from application to hire",
        "entities": ["candidates", "employees"],
        "workflow": ["Register candidates", "Progress recruitment stages", "Convert successful hires"],
    },
    "talent": {
        "tagline": "Develop performance with structured reviews",
        "entities": ["appraisals", "employees"],
        "workflow": ["Schedule an appraisal", "Record performance feedback", "Track development plans"],
    },
    "accounting": {
        "tagline": "A controlled foundation for double-entry accounting",
        "entities": ["accounts", "journal-entries"],
        "workflow": ["Configure the chart of accounts", "Prepare journal entries", "Post balanced transactions"],
    },
    "budget": {
        "tagline": "Compare plans, commitments, and actual spending",
        "entities": ["budgets"],
        "workflow": ["Create a budget period", "Record commitments", "Review actual performance"],
    },
    "expense": {
        "tagline": "Track employee expenses through approval",
        "entities": ["expenses", "employees"],
        "workflow": ["Submit a claim", "Review supporting details", "Approve and reimburse"],
    },
    "loan": {
        "tagline": "Track principal, terms, and outstanding balances",
        "entities": ["loans", "employees"],
        "workflow": ["Create the loan", "Set terms and opening balance", "Monitor repayment status"],
    },
    "quality": {
        "tagline": "Keep product inspections visible and actionable",
        "entities": ["quality-inspections", "products"],
        "workflow": ["Create an inspection", "Record the result", "Follow up failed checks"],
    },
    "manufacturing": {
        "tagline": "Plan production from bill of material to output",
        "entities": ["boms", "manufacturing-orders", "products", "warehouses"],
        "workflow": ["Define a bill of material", "Schedule production", "Track production status"],
    },
    "projects": {
        "tagline": "Coordinate work, ownership, deadlines, and progress",
        "entities": ["projects", "project-tasks"],
        "workflow": ["Create a project", "Break work into tasks", "Update progress and delivery"],
    },
    "assets": {
        "tagline": "Know where business assets are and who owns them",
        "entities": ["assets", "employees"],
        "workflow": ["Register an asset", "Assign ownership and location", "Track lifecycle status"],
    },
    "maintenance": {
        "tagline": "Plan and control asset maintenance work",
        "entities": ["maintenance", "assets"],
        "workflow": ["Raise a maintenance request", "Schedule responsible work", "Record completion and cost"],
    },
    "construction": {
        "tagline": "Control construction contracts and work packages",
        "entities": ["construction-projects", "work-packages"],
        "workflow": ["Register the construction contract", "Create work packages", "Track progress and actual cost"],
    },
    "pos": {
        "tagline": "Operate retail registers and payment sessions",
        "entities": ["pos-registers", "pos-sessions", "pos-orders"],
        "workflow": ["Open a register session", "Capture customer orders", "Take payment and close the session"],
    },
    "pos-fnb": {
        "tagline": "Run tables, dine-in orders, and kitchen tickets",
        "entities": ["restaurant-tables", "pos-orders", "kitchen-tickets"],
        "workflow": ["Assign a table", "Send the order to the kitchen", "Serve and settle the bill"],
    },
    "school": {
        "tagline": "Manage schools, students, and enrollment",
        "entities": ["schools", "students", "enrollments"],
        "workflow": ["Configure a school", "Register students", "Manage course enrollment"],
    },
    "lms": {
        "tagline": "Deliver courses and track learning progress",
        "entities": ["courses", "enrollments", "students"],
        "workflow": ["Create a course", "Enroll learners", "Monitor completion progress"],
    },
    "library": {
        "tagline": "Control the book catalog and lending cycle",
        "entities": ["library-books", "book-loans", "students"],
        "workflow": ["Catalog books", "Issue a loan", "Track returns and overdue items"],
    },
    "ai-coworker": {
        "tagline": "Configure governed assistants for ERP work",
        "entities": ["ai-workspaces", "ai-agents", "ai-conversations", "ai-tasks"],
        "workflow": ["Create a workspace", "Configure an agent and allowed tools", "Queue and review AI tasks"],
    },
}


LOCALIZATION_ENTITIES = ["localization-packs", "tax-rules", "payroll-parameters", "document-sequences"]
for country_slug in ("localization-id", "localization-sg", "localization-my", "localization-ph", "localization-th", "localization-au"):
    APP_WORKSPACES[country_slug] = {
        "tagline": "Country accounting, tax, payroll, and document defaults",
        "entities": LOCALIZATION_ENTITIES,
        "workflow": ["Review the localization pack", "Configure tax and payroll parameters", "Set document sequences"],
    }


ENTITY_APP_MAP = {}
for app_slug, workspace in APP_WORKSPACES.items():
    for entity in workspace["entities"]:
        ENTITY_APP_MAP.setdefault(entity, app_slug)

for entity in (
    "business-units",
    "work-locations",
    "job-positions",
    "employee-change-requests",
    "employee-documents",
    "employee-disciplinary",
    "employee-checklists",
    "employee-onboarding",
    "employee-offboarding",
    "raw-attendance",
    "shifts",
    "work-schedules",
    "schedule-assignments",
    "leave-types",
    "leave-allocations",
    "overtime-rules",
    "payroll-periods",
    "salary-components",
    "travel-requests",
    "training-programs",
    "cash-advances",
    "hr-years",
    "company-holidays",
    "employee-grades",
    "skills",
    "document-types",
    "hr-settings",
    "loan-types",
    "loan-policies",
    "career-transition-types",
    "career-transition-letters",
    "career-transition-requests",
    "travel-packages",
    "training-courses",
    "training-trainers",
    "training-conducts",
    "expense-products",
    "payroll-runs",
    "compensation-benefits",
):
    ENTITY_APP_MAP[entity] = "hr"


# Labels and groupings used by the contextual application sidebar.  Keeping
# this metadata beside the workspace definitions makes the same navigation
# available on both an application's landing page and its operational screens.
ENTITY_MENU_LABELS = {
    "companies": "Companies",
    "contacts": "Partners",
    "products": "Products",
    "warehouses": "Warehouses",
    "stock-moves": "Stock Ledger",
    "sales": "Sales Orders",
    "rental-details": "Detail Sewa",
    "purchases": "Purchase Orders",
    "invoices": "Invoices & Bills",
    "employees": "Employees",
    "departments": "Departments",
    "contracts": "Employment Contracts",
    "attendance": "Attendance",
    "leave-requests": "Leave Requests",
    "overtime": "Overtime Requests",
    "payslips": "Payslips",
    "candidates": "Candidates",
    "appraisals": "Employee Appraisals",
    "business-units": "Business Units",
    "work-locations": "Work Locations",
    "job-positions": "Job Positions",
    "employee-change-requests": "Employee Change Requests",
    "employee-documents": "Employee Documents",
    "employee-disciplinary": "Employee Disciplinary",
    "employee-checklists": "Employee Checklists",
    "employee-onboarding": "Employee Onboarding",
    "employee-offboarding": "Employee Offboarding",
    "raw-attendance": "Raw Attendance",
    "shifts": "Shift Master",
    "work-schedules": "Work Schedules",
    "schedule-assignments": "Schedule Assignments",
    "leave-types": "Leave Types",
    "leave-allocations": "Leave Allocations",
    "overtime-rules": "Overtime Rules",
    "payroll-periods": "Payslip Periods",
    "salary-components": "Salary Components",
    "travel-requests": "Travel Requests",
    "training-programs": "Training Programs",
    "cash-advances": "Cash Advances",
    "hr-years": "HR Years",
    "company-holidays": "Company Public Holidays",
    "employee-grades": "Employee Grades",
    "skills": "Skills",
    "document-types": "Document Types",
    "hr-settings": "HR Settings",
    "loan-types": "Loan Types",
    "loan-policies": "Loan Policies",
    "career-transition-types": "Transition Types",
    "career-transition-letters": "Transition Letters",
    "career-transition-requests": "Transition Requests",
    "travel-packages": "Travel Packages",
    "training-courses": "Training Courses",
    "training-trainers": "Trainers",
    "training-conducts": "Training Conduct",
    "expense-products": "Expense Products",
    "payroll-runs": "Payroll Runs",
    "compensation-benefits": "Compensation & Benefits",
    "accounts": "Chart of Accounts",
    "journal-entries": "Journal Entries",
    "budgets": "Budgets",
    "expenses": "Expense Claims",
    "loans": "Loans",
    "leads": "Leads",
    "opportunities": "Opportunities",
    "boms": "Bills of Material",
    "manufacturing-orders": "Manufacturing Orders",
    "quality-inspections": "Quality Inspections",
    "projects": "Projects",
    "project-tasks": "Project Tasks",
    "assets": "Assets",
    "maintenance": "Maintenance Requests",
    "construction-projects": "Construction Projects",
    "work-packages": "Work Packages",
    "schools": "Schools",
    "students": "Students",
    "courses": "Courses",
    "enrollments": "Enrollments",
    "library-books": "Book Catalog",
    "book-loans": "Book Loans",
    "pos-registers": "POS Registers",
    "pos-sessions": "POS Sessions",
    "pos-orders": "POS Orders",
    "restaurant-tables": "Restaurant Tables",
    "kitchen-tickets": "Kitchen Tickets",
    "ai-workspaces": "AI Workspaces",
    "ai-agents": "AI Agents",
    "ai-conversations": "Conversations",
    "ai-tasks": "AI Tasks",
    "localization-packs": "Localization Packs",
    "tax-rules": "Tax Rules",
    "payroll-parameters": "Payroll Parameters",
    "document-sequences": "Document Sequences",
}


APP_MENU_GROUPS = {
    "foundation": [
        {"label": "Organization", "icon": "O", "entities": ["companies"]},
        {"label": "Directory", "icon": "D", "entities": ["contacts"]},
    ],
    "inventory": [
        {"label": "Inventory Operations", "icon": "I", "entities": ["warehouses", "stock-moves"]},
        {"label": "Catalog", "icon": "C", "entities": ["products"]},
    ],
    "sales": [
        {"label": "Sales", "icon": "S", "entities": ["sales", "rental-details"]},
        {"label": "Master Data", "icon": "M", "entities": ["contacts", "products"]},
    ],
    "purchase": [
        {"label": "Procurement", "icon": "P", "entities": ["purchases"]},
        {"label": "Master Data", "icon": "M", "entities": ["contacts", "products"]},
    ],
    "hr": [
        {
            "label": "Dashboard",
            "icon": "D",
            "items": [
                {"label": "Human Resources Overview", "app_slug": "hr"},
                {"label": "My Dashboard", "feature": "my-dashboard"},
                {"label": "Director Dashboard", "feature": "director-dashboard"},
                {"label": "HR Operational", "feature": "hr-operational"},
            ],
        },
        {
            "label": "Organizations",
            "icon": "O",
            "items": [
                {"label": "Organization Chart", "feature": "organization-chart"},
                {"label": "Departments", "entity": "departments"},
                {"label": "Business Units", "entity": "business-units"},
                {"label": "Work Locations", "entity": "work-locations"},
                {"label": "Job Positions", "entity": "job-positions"},
            ],
        },
        {
            "label": "Employees",
            "icon": "E",
            "items": [
                {"label": "Employees", "entity": "employees"},
                {"label": "Employee Change Requests", "entity": "employee-change-requests"},
                {"label": "Employment Contracts", "entity": "contracts"},
                {"label": "Employee Documents", "entity": "employee-documents"},
                {"label": "Employee Disciplinary", "entity": "employee-disciplinary"},
            ],
        },
        {
            "label": "Onboarding & Offboarding",
            "icon": "B",
            "items": [
                {"label": "Onboarding Dashboard", "feature": "onboarding-dashboard"},
                {"label": "Employee Onboarding", "entity": "employee-onboarding"},
                {"label": "Offboarding Dashboard", "feature": "offboarding-dashboard"},
                {"label": "Employee Offboarding", "entity": "employee-offboarding"},
                {"label": "Employee Checklists", "entity": "employee-checklists"},
            ],
        },
        {
            "label": "Attendance",
            "icon": "A",
            "items": [
                {"label": "Employee Attendance", "entity": "attendance"},
                {"label": "Raw Attendance", "entity": "raw-attendance"},
                {"label": "My Attendance", "entity": "attendance"},
                {"label": "Work Schedules", "entity": "work-schedules"},
                {"label": "Shift Master", "entity": "shifts"},
                {"label": "Schedule Assignment", "entity": "schedule-assignments"},
            ],
        },
        {
            "label": "Leave",
            "icon": "L",
            "items": [
                {"label": "Leave Requests", "entity": "leave-requests"},
                {"label": "Leave Types", "entity": "leave-types"},
                {"label": "Leave Allocations", "entity": "leave-allocations"},
                {"label": "Leave Dashboard", "feature": "leave-dashboard"},
                {"label": "Leave Reporting", "feature": "leave-reporting"},
            ],
        },
        {
            "label": "Overtimes",
            "icon": "V",
            "items": [
                {"label": "Overtime Requests", "entity": "overtime"},
                {"label": "Overtime Rules", "entity": "overtime-rules"},
                {"label": "Overtime Analysis", "feature": "overtime-analysis"},
            ],
        },
        {
            "label": "Payroll",
            "icon": "P",
            "items": [
                {"label": "Payslips", "entity": "payslips"},
                {"label": "Payroll Run", "entity": "payroll-runs"},
                {"label": "Compensation & Benefits", "entity": "compensation-benefits"},
                {"label": "Payslip Periods", "entity": "payroll-periods"},
                {"label": "Salary Components", "entity": "salary-components"},
            ],
        },
        {
            "label": "Loan Management",
            "icon": "N",
            "items": [
                {"label": "Loans", "entity": "loans"},
                {"label": "My Loan Center", "entity": "loans"},
                {"label": "Loan Types", "entity": "loan-types"},
                {"label": "Loan Policies", "entity": "loan-policies"},
                {"label": "Loan Analysis", "feature": "loan-analysis"},
            ],
        },
        {
            "label": "Career Transition",
            "icon": "C",
            "items": [
                {"label": "Transition Requests", "entity": "career-transition-requests"},
                {"label": "Transition Types", "entity": "career-transition-types"},
                {"label": "Transition Letters", "entity": "career-transition-letters"},
                {"label": "Career Transition Analysis", "feature": "career-transition-analysis"},
            ],
        },
        {
            "label": "Travel",
            "icon": "T",
            "items": [
                {"label": "Travel Requests", "entity": "travel-requests"},
                {"label": "Travel Requests to Approve", "entity": "travel-requests"},
                {"label": "Travel Packages", "entity": "travel-packages"},
                {"label": "Travel Reporting", "feature": "travel-reporting"},
            ],
        },
        {
            "label": "Training",
            "icon": "R",
            "items": [
                {"label": "Training Programs", "entity": "training-programs"},
                {"label": "Training Courses", "entity": "training-courses"},
                {"label": "Training Conduct", "entity": "training-conducts"},
                {"label": "Trainers", "entity": "training-trainers"},
            ],
        },
        {
            "label": "Employee Expenses",
            "icon": "X",
            "items": [
                {"label": "Expense Claims", "entity": "expenses"},
                {"label": "My Advance", "entity": "cash-advances"},
                {"label": "Manage Advances", "entity": "cash-advances"},
                {"label": "Expense Analysis", "feature": "expense-analysis"},
                {"label": "Expense Products", "entity": "expense-products"},
            ],
        },
        {
            "label": "Configuration",
            "icon": "S",
            "items": [
                {"label": "HR Settings", "entity": "hr-settings"},
                {"label": "HR Year", "entity": "hr-years"},
                {"label": "Company Public Holidays", "entity": "company-holidays"},
                {"label": "Employee Grades", "entity": "employee-grades"},
                {"label": "Skills", "entity": "skills"},
                {"label": "Document Types", "entity": "document-types"},
            ],
        },
        {
            "label": "Reporting",
            "icon": "Q",
            "items": [
                {"label": "Employee Analysis", "feature": "employee-analysis"},
                {"label": "Attendance Analysis", "feature": "attendance-analysis"},
                {"label": "Leave Analysis", "feature": "leave-analysis"},
                {"label": "Payroll Analysis", "feature": "payroll-analysis"},
            ],
        },
    ],
    "payroll": [
        {"label": "Payroll", "icon": "P", "entities": ["payslips", "contracts"]},
        {"label": "People", "icon": "E", "entities": ["employees"]},
    ],
    "manufacturing": [
        {"label": "Production", "icon": "P", "entities": ["manufacturing-orders", "boms"]},
        {"label": "Resources", "icon": "R", "entities": ["products", "warehouses"]},
    ],
    "projects": [
        {"label": "Project Planning", "icon": "P", "entities": ["projects", "project-tasks"]},
    ],
    "construction": [
        {"label": "Projects", "icon": "P", "entities": ["construction-projects"]},
        {"label": "Execution", "icon": "E", "entities": ["work-packages"]},
    ],
    "pos": [
        {"label": "Registers", "icon": "R", "entities": ["pos-registers", "pos-sessions"]},
        {"label": "Transactions", "icon": "T", "entities": ["pos-orders"]},
    ],
    "pos-fnb": [
        {"label": "Restaurant Floor", "icon": "F", "entities": ["restaurant-tables", "pos-orders"]},
        {"label": "Kitchen", "icon": "K", "entities": ["kitchen-tickets"]},
    ],
    "school": [
        {"label": "School Setup", "icon": "S", "entities": ["schools"]},
        {"label": "Students", "icon": "L", "entities": ["students", "enrollments"]},
    ],
    "lms": [
        {"label": "Learning", "icon": "L", "entities": ["courses", "enrollments", "students"]},
    ],
    "library": [
        {"label": "Collection", "icon": "C", "entities": ["library-books"]},
        {"label": "Circulation", "icon": "L", "entities": ["book-loans", "students"]},
    ],
    "ai-coworker": [
        {"label": "AI Workspace", "icon": "A", "entities": ["ai-workspaces", "ai-agents"]},
        {"label": "Work Queue", "icon": "W", "entities": ["ai-conversations", "ai-tasks"]},
    ],
}


def app_menu_groups(app_slug):
    """Return complete, presentation-ready menu groups for an application."""
    workspace = APP_WORKSPACES.get(app_slug, {})
    entities = workspace.get("entities", [])
    definitions = APP_MENU_GROUPS.get(app_slug)
    if definitions is None and entities:
        definitions = [{"label": "Operations", "icon": "O", "entities": entities}]

    groups = []
    included = set()
    for definition in definitions or []:
        items = []
        if "items" in definition:
            for raw_item in definition["items"]:
                item = dict(raw_item)
                item.setdefault("icon", item["label"][:1].upper())
                if item.get("entity") in entities:
                    included.add(item["entity"])
                items.append(item)
        else:
            for entity in definition["entities"]:
                if entity not in entities:
                    continue
                included.add(entity)
                items.append(
                    {
                        "label": ENTITY_MENU_LABELS.get(entity, entity.replace("-", " ").title()),
                        "entity": entity,
                        "icon": ENTITY_MENU_LABELS.get(entity, entity)[:1].upper(),
                    }
                )
        if items:
            groups.append({"label": definition["label"], "icon": definition.get("icon", "M"), "items": items})

    remaining = [entity for entity in entities if entity not in included]
    if remaining:
        groups.append(
            {
                "label": "More",
                "icon": "+",
                "items": [
                    {
                        "label": ENTITY_MENU_LABELS.get(entity, entity.replace("-", " ").title()),
                        "entity": entity,
                        "icon": ENTITY_MENU_LABELS.get(entity, entity)[:1].upper(),
                    }
                    for entity in remaining
                ],
            }
        )
    return groups


def app_navigation_contains(app_slug, *, entity=None, feature=None):
    """Return whether an application owns a contextual navigation target."""
    return any(
        (entity and item.get("entity") == entity) or (feature and item.get("feature") == feature)
        for group in app_menu_groups(app_slug)
        for item in group["items"]
    )


def hr_feature_definition(feature_slug):
    """Resolve an HR foundation page from the audited clean-room menu map."""
    descriptions = {
        "organization-chart": "Explore reporting lines, departments, positions, and organization structure.",
        "employee-onboarding": "Coordinate employee joining activities, owners, deadlines, and completion.",
        "employee-offboarding": "Coordinate access, asset, handover, and exit clearance activities.",
        "raw-attendance": "Review imported clock events before attendance processing and correction.",
        "work-schedules": "Plan reusable work schedules and assign them to employees.",
        "payroll-run": "Prepare a controlled payroll cycle from inputs through final payslips.",
        "travel-requests": "Submit and approve employee business travel with cost controls.",
        "training-programs": "Plan employee learning programs, courses, trainers, and attendance.",
        "my-advance": "Request and track employee cash advances and their settlement.",
    }
    for group in app_menu_groups("hr"):
        for item in group["items"]:
            if item.get("feature") == feature_slug:
                return {
                    "slug": feature_slug,
                    "title": item["label"],
                    "group": group["label"],
                    "description": descriptions.get(
                        feature_slug,
                        f"Manage {item['label'].lower()} within the Human Resources workspace.",
                    ),
                    "group_items": group["items"],
                }
    return None


APP_DEPENDENCIES = {
    "product": ["foundation"],
    "inventory": ["product"],
    "sales": ["foundation", "product"],
    "purchase": ["foundation", "product"],
    "invoicing": ["foundation"],
    "crm": ["foundation"],
    "hr": ["foundation"],
    "payroll": ["hr"],
    "recruitment": ["hr"],
    "talent": ["hr"],
    "accounting": ["foundation"],
    "budget": ["accounting"],
    "expense": ["hr", "accounting"],
    "loan": ["hr", "accounting"],
    "quality": ["product"],
    "manufacturing": ["inventory", "product"],
    "projects": ["foundation"],
    "assets": ["foundation"],
    "maintenance": ["assets"],
    "construction": ["projects", "purchase"],
    "pos": ["sales", "inventory"],
    "pos-fnb": ["pos"],
    "school": ["foundation"],
    "lms": ["school"],
    "library": ["school"],
    "ai-coworker": ["foundation", "scheduler"],
}

for localization_slug in ("localization-id", "localization-sg", "localization-my", "localization-ph", "localization-th", "localization-au"):
    APP_DEPENDENCIES[localization_slug] = ["accounting", "hr"]
