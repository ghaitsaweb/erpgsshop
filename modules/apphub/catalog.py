APP_CATALOG = [
    # Platform
    {"slug": "foundation", "name": "ERP Foundation", "category": "Platform", "summary": "Companies, users, roles, permissions, audit-ready records, and shared master data.", "icon": "◈", "status": "stable", "enabled": True, "is_core": True, "sequence": 10},
    {"slug": "app-manager", "name": "App Manager", "category": "Platform", "summary": "Discover, enable, and monitor modular GSSHOP applications.", "icon": "▦", "status": "stable", "enabled": True, "is_core": True, "sequence": 20, "route": "app_manager"},
    {"slug": "approval-workflow", "name": "Approval Workflow", "category": "Platform", "summary": "Configurable multi-step approvals, delegation, escalation, and audit history.", "icon": "✓", "status": "planned", "sequence": 30},
    {"slug": "documents", "name": "Documents", "category": "Platform", "summary": "Document storage, versioning, tags, access control, and record attachments.", "icon": "▤", "status": "planned", "sequence": 40},
    {"slug": "mail-notifications", "name": "Mail & Notifications", "category": "Platform", "summary": "Templates, inbox, reminders, push notifications, and communication history.", "icon": "✉", "status": "planned", "sequence": 50},
    {"slug": "knowledge", "name": "Knowledge", "category": "Platform", "summary": "Internal knowledge base, articles, revision history, and access policies.", "icon": "◇", "status": "planned", "sequence": 60},
    {"slug": "security", "name": "Advanced Security", "category": "Platform", "summary": "TOTP, IP policies, password rules, sessions, and login-as controls.", "icon": "◉", "status": "planned", "sequence": 70},
    {"slug": "scheduler", "name": "Scheduled Actions", "category": "Platform", "summary": "Celery and Redis background tasks, retries, schedules, and execution logs.", "icon": "◷", "status": "beta", "enabled": True, "sequence": 80},
    # Commercial
    {"slug": "crm", "name": "CRM", "category": "Commercial", "summary": "Leads, opportunities, pipelines, activities, and conversion tracking.", "icon": "◎", "status": "beta", "enabled": True, "sequence": 10, "route": "entity_list:leads"},
    {"slug": "sales", "name": "Sales", "category": "Commercial", "summary": "Customers, quotations, sales orders, pricing, discounts, and fulfillment.", "icon": "↗", "status": "stable", "enabled": True, "sequence": 20, "route": "entity_list:sales"},
    {"slug": "invoicing", "name": "Invoicing", "category": "Commercial", "summary": "Customer invoices, vendor bills, due dates, and payment tracking.", "icon": "▧", "status": "stable", "enabled": True, "sequence": 30, "route": "entity_list:invoices"},
    {"slug": "rental", "name": "Rental", "category": "Commercial", "summary": "Rental products, contracts, schedules, returns, and recurring charges.", "icon": "↔", "status": "planned", "sequence": 40},
    {"slug": "marketplace", "name": "Marketplace", "category": "Commercial", "summary": "Channel mapping, catalog sync, order intake, and marketplace settlement.", "icon": "⌂", "status": "planned", "sequence": 50},
    # Supply chain
    {"slug": "product", "name": "Product", "category": "Supply Chain", "summary": "Products, services, units, prices, bundles, and product lifecycle.", "icon": "□", "status": "stable", "enabled": True, "sequence": 10, "route": "entity_list:products"},
    {"slug": "purchase", "name": "Purchase", "category": "Supply Chain", "summary": "Vendors, purchase orders, budgets, receipts, and vendor invoicing.", "icon": "⇣", "status": "stable", "enabled": True, "sequence": 20, "route": "entity_list:purchases"},
    {"slug": "inventory", "name": "Inventory", "category": "Supply Chain", "summary": "Warehouses, immutable stock ledger, receipts, delivery, and replenishment.", "icon": "▣", "status": "stable", "enabled": True, "sequence": 30, "route": "entity_list:stock-moves"},
    {"slug": "quality", "name": "Quality Control", "category": "Supply Chain", "summary": "Inspection plans, checks, nonconformity, and corrective actions.", "icon": "◆", "status": "beta", "enabled": True, "sequence": 40, "route": "entity_list:quality-inspections"},
    {"slug": "manufacturing", "name": "Manufacturing", "category": "Supply Chain", "summary": "Bills of material, production orders, consumption, output, and work status.", "icon": "⚙", "status": "beta", "enabled": True, "sequence": 50, "route": "entity_list:manufacturing-orders"},
    {"slug": "forecast", "name": "Forecasting", "category": "Supply Chain", "summary": "Demand forecast, procurement proposals, and inventory projections.", "icon": "⌁", "status": "planned", "sequence": 60},
    # Finance
    {"slug": "accounting", "name": "Accounting", "category": "Finance", "summary": "Chart of accounts, journals, double-entry posting, periods, and reporting.", "icon": "∑", "status": "beta", "enabled": True, "sequence": 10, "route": "entity_list:accounts"},
    {"slug": "budget", "name": "Budget", "category": "Finance", "summary": "Cost centers, budget controls, commitments, and actual comparisons.", "icon": "◫", "status": "beta", "enabled": True, "sequence": 20, "route": "entity_list:budgets"},
    {"slug": "expense", "name": "Expense & Advance", "category": "Finance", "summary": "Expense claims, employee advances, settlement, and approval.", "icon": "₿", "status": "beta", "enabled": True, "sequence": 30, "route": "entity_list:expenses"},
    {"slug": "petty-cash", "name": "Petty Cash", "category": "Finance", "summary": "Cash funds, replenishment, disbursements, and reconciliation.", "icon": "¤", "status": "planned", "sequence": 40},
    {"slug": "loan", "name": "Loans", "category": "Finance", "summary": "Employee and business loans, schedules, installments, and balances.", "icon": "%", "status": "beta", "enabled": True, "sequence": 50, "route": "entity_list:loans"},
    # People
    {"slug": "hr", "name": "Human Resources", "category": "People", "summary": "Employees, departments, contracts, attendance, leave, and overtime.", "icon": "♙", "status": "beta", "enabled": True, "sequence": 10, "route": "entity_list:employees"},
    {"slug": "payroll", "name": "Payroll", "category": "People", "summary": "Salary structures, payroll periods, payslips, benefits, and deductions.", "icon": "≋", "status": "beta", "enabled": True, "sequence": 20, "route": "entity_list:payslips"},
    {"slug": "recruitment", "name": "Recruitment", "category": "People", "summary": "Jobs, applicants, stages, interviews, offers, and onboarding.", "icon": "+", "status": "beta", "enabled": True, "sequence": 30, "route": "entity_list:candidates"},
    {"slug": "talent", "name": "Talent Management", "category": "People", "summary": "Goals, competencies, appraisal cycles, feedback, and development plans.", "icon": "☆", "status": "beta", "enabled": True, "sequence": 40, "route": "entity_list:appraisals"},
    # Work and assets
    {"slug": "projects", "name": "Projects", "category": "Projects & Assets", "summary": "Projects, milestones, tasks, ownership, deadlines, and progress.", "icon": "◇", "status": "beta", "enabled": True, "sequence": 10, "route": "entity_list:projects"},
    {"slug": "assets", "name": "Assets", "category": "Projects & Assets", "summary": "Asset register, assignment, depreciation inputs, and lifecycle control.", "icon": "△", "status": "beta", "enabled": True, "sequence": 20, "route": "entity_list:assets"},
    {"slug": "maintenance", "name": "Asset Maintenance", "category": "Projects & Assets", "summary": "Preventive schedules, requests, work logs, downtime, and costs.", "icon": "⌘", "status": "beta", "enabled": True, "sequence": 30, "route": "entity_list:maintenance"},
    {"slug": "construction", "name": "Construction", "category": "Projects & Assets", "summary": "Sites, contracts, work packages, progress, quantities, and project procurement.", "icon": "▱", "status": "beta", "enabled": True, "sequence": 40, "route": "entity_list:construction-projects"},
    {"slug": "service-operations", "name": "Service Operations", "category": "Projects & Assets", "summary": "Service jobs, assignments, visits, completion, and customer history.", "icon": "⌖", "status": "planned", "sequence": 50},
    # Retail
    {"slug": "pos", "name": "Point of Sale", "category": "Retail & F&B", "summary": "Registers, sessions, orders, payments, receipts, and stock integration.", "icon": "▥", "status": "beta", "enabled": True, "sequence": 10, "route": "entity_list:pos-orders"},
    {"slug": "pos-fnb", "name": "POS F&B", "category": "Retail & F&B", "summary": "Restaurant floors, tables, kitchen tickets, dine-in, and takeaway.", "icon": "♨", "status": "beta", "enabled": True, "sequence": 20, "route": "entity_list:restaurant-tables"},
    {"slug": "pos-realtime", "name": "POS Realtime", "category": "Retail & F&B", "summary": "Live order events, register presence, and kitchen status synchronization.", "icon": "ϟ", "status": "planned", "sequence": 30},
    # Education
    {"slug": "school", "name": "School", "category": "Education", "summary": "Schools, students, academic years, classes, enrollment, and billing links.", "icon": "⌂", "status": "beta", "enabled": True, "sequence": 10, "route": "entity_list:students"},
    {"slug": "lms", "name": "Learning Management", "category": "Education", "summary": "Courses, lessons, enrollment, learning progress, and assessment structure.", "icon": "▰", "status": "beta", "enabled": True, "sequence": 20, "route": "entity_list:courses"},
    {"slug": "library", "name": "Library", "category": "Education", "summary": "Book catalog, copies, members, lending, returns, and overdue tracking.", "icon": "▥", "status": "beta", "enabled": True, "sequence": 30, "route": "entity_list:library-books"},
    {"slug": "school-portal", "name": "School Portal", "category": "Education", "summary": "Student and parent self-service for learning, billing, and announcements.", "icon": "◌", "status": "planned", "sequence": 40},
    # AI and data
    {"slug": "ai-coworker", "name": "AI Coworker", "category": "AI & Data", "summary": "Workspace agents, conversations, governed tools, tasks, and channel configuration.", "icon": "✦", "status": "beta", "enabled": True, "sequence": 10, "route": "entity_list:ai-agents"},
    {"slug": "business-intelligence", "name": "Business Intelligence", "category": "AI & Data", "summary": "Operational metrics, dashboards, analysis datasets, and insight delivery.", "icon": "▥", "status": "planned", "sequence": 20},
    {"slug": "olap", "name": "OLAP", "category": "AI & Data", "summary": "Analytical cubes, dimensions, measures, refreshes, and query access.", "icon": "▦", "status": "planned", "sequence": 30},
    {"slug": "event-stream", "name": "Event Stream", "category": "AI & Data", "summary": "Reliable domain events for automation, analytics, and integration consumers.", "icon": "≈", "status": "planned", "sequence": 40},
    {"slug": "api-mcp", "name": "API & MCP", "category": "AI & Data", "summary": "REST APIs and governed agent tools for ERP data and operations.", "icon": "⌘", "status": "planned", "sequence": 50},
    # Localization
    {"slug": "localization-id", "name": "Indonesia", "category": "Localization", "summary": "IDR, Indonesian tax configuration, document defaults, and payroll parameters.", "icon": "ID", "status": "beta", "enabled": True, "sequence": 10, "route": "entity_list:localization-packs"},
    {"slug": "localization-sg", "name": "Singapore", "category": "Localization", "summary": "SGD, GST, Singapore documents, accounting, and HR parameters.", "icon": "SG", "status": "beta", "enabled": True, "sequence": 20, "route": "entity_list:localization-packs"},
    {"slug": "localization-my", "name": "Malaysia", "category": "Localization", "summary": "MYR, SST, Malaysian documents, accounting, and HR parameters.", "icon": "MY", "status": "beta", "enabled": True, "sequence": 30, "route": "entity_list:localization-packs"},
    {"slug": "localization-ph", "name": "Philippines", "category": "Localization", "summary": "PHP, Philippine tax, documents, accounting, and HR parameters.", "icon": "PH", "status": "beta", "enabled": True, "sequence": 40, "route": "entity_list:localization-packs"},
    {"slug": "localization-th", "name": "Thailand", "category": "Localization", "summary": "THB, Thai tax, documents, accounting, and HR parameters.", "icon": "TH", "status": "beta", "enabled": True, "sequence": 50, "route": "entity_list:localization-packs"},
    {"slug": "localization-au", "name": "Australia", "category": "Localization", "summary": "AUD, GST, Australian documents, accounting, and HR parameters.", "icon": "AU", "status": "beta", "enabled": True, "sequence": 60, "route": "entity_list:localization-packs"},
]

