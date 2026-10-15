from django import template


register = template.Library()


APP_ICONS = {
    "foundation": "building",
    "app-manager": "grid",
    "approval-workflow": "approval",
    "documents": "document",
    "mail-notifications": "bell",
    "knowledge": "book",
    "security": "shield",
    "scheduler": "clock",
    "crm": "target",
    "sales": "trend",
    "invoicing": "receipt",
    "rental": "repeat",
    "marketplace": "store",
    "product": "box",
    "purchase": "cart",
    "inventory": "warehouse",
    "quality": "quality",
    "manufacturing": "gear",
    "forecast": "chart",
    "accounting": "calculator",
    "budget": "wallet",
    "expense": "card",
    "petty-cash": "coins",
    "loan": "percent",
    "hr": "users",
    "payroll": "banknote",
    "recruitment": "user-plus",
    "talent": "star",
    "projects": "clipboard",
    "assets": "asset",
    "maintenance": "wrench",
    "construction": "construction",
    "service-operations": "headset",
    "pos": "monitor",
    "pos-fnb": "restaurant",
    "pos-realtime": "lightning",
    "school": "school",
    "lms": "graduation",
    "library": "library",
    "school-portal": "portal",
    "ai-coworker": "sparkle",
    "business-intelligence": "chart",
    "olap": "database",
    "event-stream": "stream",
    "api-mcp": "code",
}


CATEGORY_KEYS = {
    "Platform": "platform",
    "Commercial": "commercial",
    "Supply Chain": "supply",
    "Finance": "finance",
    "People": "people",
    "Projects & Assets": "projects",
    "Retail & F&B": "retail",
    "Education": "education",
    "AI & Data": "ai",
    "Localization": "localization",
}


@register.filter
def app_icon_key(app):
    if str(app.slug).startswith("localization-"):
        return "globe"
    return APP_ICONS.get(app.slug, "module")


@register.filter
def app_category_key(app):
    return CATEGORY_KEYS.get(app.category, "custom")
