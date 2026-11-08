"""Bounded, company-scoped Accounting dashboards and financial summaries."""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from modules.organization.context import scope_queryset as scope_organization_queryset

from .access import scope_accounting_queryset
from .models import (
    Account,
    AccountCheque,
    AccountDeposit,
    AccountPayment,
    BankStatement,
    CompanyBankAccount,
    CurrencyRevaluation,
    JournalEntry,
    JournalLine,
    PettyCashFund,
    PettyCashVoucher,
    Prepayment,
    WithholdingTax,
)


ACCOUNTING_ANALYTIC_FEATURES = {
    "dashboard", "general-ledger", "trial-balance", "partner-ledger", "balance-sheet",
    "profit-loss", "cash-flow", "changes-in-equity", "financial-ratios", "income-overview",
    "aging-summary", "bank-balance", "deposit-summary", "cheque-summary", "prepayment-report",
    "foreign-exchange", "journal-audit", "bank-cash-book", "accounting-vision", "withholding-dashboard",
    "general-ledger-report", "partner-aging", "financial-statements", "business-statements",
    "statement-of-account", "customer-prepayment-details", "vendor-prepayment-details", "day-book",
    "journal-entry-report", "tax-report", "cost-center-ledger", "debt-collection",
    "accounting-reports",
    "petty-cash-report",
}

MONEY = DecimalField(max_digits=18, decimal_places=2)

PROFIT_LOSS_PERIOD_OPTIONS = (
    ("today", "Today"),
    ("this_month", "This Month"),
    ("this_quarter", "This Quarter"),
    ("this_year", "This Year"),
    ("last_month", "Last Month"),
    ("last_quarter", "Last Quarter"),
    ("last_year", "Last Year"),
    ("custom", "Custom"),
)

PROFIT_LOSS_COMPARISON_OPTIONS = (
    ("none", "No Comparison"),
    ("previous_period", "Previous Period"),
    ("same_period_last_year", "Same Period Last Year"),
    ("custom", "Custom Comparison"),
)


def _scoped(user, entity, model):
    return scope_organization_queryset(scope_accounting_queryset(user, entity, model.objects.all()))


def _metric(label, value, detail, tone="blue"):
    return {"label": label, "value": value or 0, "detail": detail, "tone": tone}


def _rows(queryset, field="status"):
    grouped = queryset.values(field).annotate(total=Count("id")).order_by(field)
    return [{"label": row[field] or "Unassigned", "total": row["total"]} for row in grouped]


def _posted_lines(user):
    return _scoped(user, "journal-entries", JournalLine).filter(entry__status=JournalEntry.Status.POSTED)


def _balance_rows(lines, *, account_types=None, limit=20):
    if account_types:
        lines = lines.filter(account__account_type__in=account_types)
    rows = (
        lines.values("account__code", "account__name")
        .annotate(
            debit=Coalesce(Sum("debit"), Value(Decimal("0")), output_field=MONEY),
            credit=Coalesce(Sum("credit"), Value(Decimal("0")), output_field=MONEY),
        )
        .order_by("account__code")[:limit]
    )
    return [
        {"label": f"{row['account__code']} · {row['account__name']}", "total": row["debit"] - row["credit"]}
        for row in rows
    ]


def _journal_summary(user, feature_slug):
    entries = _scoped(user, "journal-entries", JournalEntry)
    posted = entries.filter(status=JournalEntry.Status.POSTED)
    totals = posted.aggregate(debit=Sum("total_debit"), credit=Sum("total_credit"), count=Count("id"))
    lines = _posted_lines(user)
    if feature_slug == "trial-balance":
        title = "Trial balance by account"
        breakdown = _balance_rows(lines, limit=50)
    elif feature_slug == "general-ledger":
        title = "General ledger balances"
        breakdown = _balance_rows(lines, limit=50)
    else:
        title = "Journal entries by status"
        breakdown = _rows(entries)
    return {
        "metrics": [
            _metric("Posted Entries", totals["count"], "Finalized journal entries"),
            _metric("Total Debit", totals["debit"], "Posted debit movement", "violet"),
            _metric("Total Credit", totals["credit"], "Posted credit movement", "violet"),
            _metric("Draft Entries", entries.filter(status=JournalEntry.Status.DRAFT).count(), "Awaiting posting", "amber"),
        ],
        "breakdown_title": title,
        "breakdown": breakdown,
        "recent_title": "Recent journal entries",
        "recent": list(entries.select_related("company", "journal").order_by("-entry_date", "-id")[:8]),
    }


def _financial_statement(user, feature_slug):
    lines = _posted_lines(user)
    account_types = {
        "balance-sheet": [Account.AccountType.ASSET, Account.AccountType.LIABILITY, Account.AccountType.EQUITY],
        "profit-loss": [Account.AccountType.INCOME, Account.AccountType.EXPENSE],
        "income-overview": [Account.AccountType.INCOME, Account.AccountType.EXPENSE],
        "changes-in-equity": [Account.AccountType.EQUITY],
    }.get(feature_slug, [])
    scoped = lines.filter(account__account_type__in=account_types) if account_types else lines
    totals = scoped.aggregate(debit=Sum("debit"), credit=Sum("credit"), lines=Count("id"))
    title = feature_slug.replace("-", " ").title()
    return {
        "metrics": [
            _metric("Journal Items", totals["lines"], "Posted items in this statement"),
            _metric("Debit", totals["debit"], "Debit movement", "violet"),
            _metric("Credit", totals["credit"], "Credit movement", "violet"),
            _metric("Net Movement", (totals["credit"] or 0) - (totals["debit"] or 0), "Credit less debit", "amber"),
        ],
        "breakdown_title": f"{title} by account",
        "breakdown": _balance_rows(scoped, limit=50),
        "recent_title": "Recent posted entries",
        "recent": list(
            _scoped(user, "journal-entries", JournalEntry)
            .filter(status=JournalEntry.Status.POSTED)
            .select_related("company", "journal")
            .order_by("-entry_date", "-id")[:8]
        ),
    }


def _month_range(value):
    start = value.replace(day=1)
    return start, start.replace(day=calendar.monthrange(start.year, start.month)[1])


def _quarter_range(value):
    start_month = ((value.month - 1) // 3) * 3 + 1
    start = value.replace(month=start_month, day=1)
    end_month = start_month + 2
    end = start.replace(month=end_month, day=calendar.monthrange(start.year, end_month)[1])
    return start, end


def _previous_year(value):
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _parse_report_date(raw_value):
    try:
        return date.fromisoformat(str(raw_value or ""))
    except ValueError:
        return None


def _profit_loss_period(params):
    today = timezone.localdate()
    preset = (params.get("period") if params else None) or "this_month"
    error = ""
    if preset == "today":
        start = end = today
    elif preset == "this_quarter":
        start, end = _quarter_range(today)
    elif preset == "this_year":
        start, end = today.replace(month=1, day=1), today.replace(month=12, day=31)
    elif preset == "last_month":
        start, end = _month_range(today.replace(day=1) - timedelta(days=1))
    elif preset == "last_quarter":
        current_start, _current_end = _quarter_range(today)
        start, end = _quarter_range(current_start - timedelta(days=1))
    elif preset == "last_year":
        start = today.replace(year=today.year - 1, month=1, day=1)
        end = today.replace(year=today.year - 1, month=12, day=31)
    elif preset == "custom":
        start = _parse_report_date(params.get("date_from"))
        end = _parse_report_date(params.get("date_to"))
        if start is None or end is None or start > end:
            preset = "this_month"
            start, end = _month_range(today)
            error = "Enter a valid custom period whose From date is not after its To date."
    else:
        preset = "this_month"
        start, end = _month_range(today)
    return preset, start, end, error


def _profit_loss_comparison(params, start, end):
    mode = (params.get("comparison") if params else None) or "none"
    error = ""
    if mode == "previous_period":
        comparison_end = start - timedelta(days=1)
        comparison_start = comparison_end - (end - start)
    elif mode == "same_period_last_year":
        comparison_start = _previous_year(start)
        comparison_end = _previous_year(end)
    elif mode == "custom":
        comparison_start = _parse_report_date(params.get("comparison_from"))
        comparison_end = _parse_report_date(params.get("comparison_to"))
        if (
            comparison_start is None
            or comparison_end is None
            or comparison_start > comparison_end
        ):
            mode = "none"
            comparison_start = comparison_end = None
            error = "Enter a valid custom comparison period."
    else:
        mode = "none"
        comparison_start = comparison_end = None
    return mode, comparison_start, comparison_end, error


def _profit_loss_bucket(account_type, account_code, account_name):
    digits = "".join(character for character in str(account_code) if character.isdigit())
    numeric_code = int(digits[:4]) if len(digits) >= 4 else None
    normalized_name = str(account_name).casefold()
    if account_type == Account.AccountType.INCOME:
        if numeric_code in {4010, 4011}:
            return "sales_deduction", "debit"
        if numeric_code is not None and 4000 <= numeric_code < 5000:
            return "gross_revenue", "credit"
        if numeric_code == 7001:
            return "interest_income", "credit"
        if numeric_code == 7005:
            return "dividend_income", "credit"
        return "other_operating_income", "credit"
    if "income tax" in normalized_name or "pajak penghasilan" in normalized_name:
        return "income_tax_expense", "debit"
    if numeric_code is not None and 5000 <= numeric_code < 6000:
        return "cost_of_revenue", "debit"
    if numeric_code is not None and 6000 <= numeric_code <= 6005:
        return "selling_marketing_expense", "debit"
    if numeric_code is not None and 6000 <= numeric_code < 7000:
        return "general_admin_expense", "debit"
    if numeric_code == 8001:
        return "interest_expense", "debit"
    return "other_operating_expense", "debit"


def _profit_loss_statement(user, params=None):
    params = params or {}
    preset, start, end, period_error = _profit_loss_period(params)
    comparison_mode, comparison_start, comparison_end, comparison_error = _profit_loss_comparison(
        params, start, end
    )
    current_filter = Q(entry__entry_date__range=(start, end))
    scope_filter = current_filter
    annotations = {
        "current_debit": Coalesce(
            Sum("debit", filter=current_filter), Value(Decimal("0")), output_field=MONEY
        ),
        "current_credit": Coalesce(
            Sum("credit", filter=current_filter), Value(Decimal("0")), output_field=MONEY
        ),
    }
    if comparison_start and comparison_end:
        comparison_filter = Q(entry__entry_date__range=(comparison_start, comparison_end))
        scope_filter |= comparison_filter
        annotations.update({
            "comparison_debit": Coalesce(
                Sum("debit", filter=comparison_filter), Value(Decimal("0")), output_field=MONEY
            ),
            "comparison_credit": Coalesce(
                Sum("credit", filter=comparison_filter), Value(Decimal("0")), output_field=MONEY
            ),
        })

    account_rows = (
        _posted_lines(user)
        .filter(
            scope_filter,
            account__account_type__in=[Account.AccountType.INCOME, Account.AccountType.EXPENSE],
        )
        .values("account_id", "account__code", "account__name", "account__account_type")
        .annotate(**annotations)
        .order_by("account__code", "account_id")
        .iterator(chunk_size=500)
    )
    bucket_names = (
        "gross_revenue", "sales_deduction", "cost_of_revenue",
        "selling_marketing_expense", "general_admin_expense", "other_operating_income",
        "other_operating_expense", "interest_income", "dividend_income", "interest_expense",
        "income_tax_expense",
    )
    current = {name: Decimal("0") for name in bucket_names}
    comparison = {name: Decimal("0") for name in bucket_names}
    for row in account_rows:
        bucket, normal_side = _profit_loss_bucket(
            row["account__account_type"], row["account__code"], row["account__name"]
        )
        if normal_side == "credit":
            current[bucket] += row["current_credit"] - row["current_debit"]
            if comparison_start:
                comparison[bucket] += row["comparison_credit"] - row["comparison_debit"]
        else:
            current[bucket] += row["current_debit"] - row["current_credit"]
            if comparison_start:
                comparison[bucket] += row["comparison_debit"] - row["comparison_credit"]

    def formulas(values):
        values = dict(values)
        values["net_revenue"] = values["gross_revenue"] - values["sales_deduction"]
        values["gross_profit"] = values["net_revenue"] - values["cost_of_revenue"]
        values["operating_profit"] = (
            values["gross_profit"]
            - values["selling_marketing_expense"]
            - values["general_admin_expense"]
            + values["other_operating_income"]
            - values["other_operating_expense"]
        )
        values["profit_before_financing_tax"] = (
            values["operating_profit"] + values["interest_income"] + values["dividend_income"]
        )
        values["profit_before_tax"] = values["profit_before_financing_tax"] - values["interest_expense"]
        values["profit_loss_period"] = values["profit_before_tax"] - values["income_tax_expense"]
        values["total_income"] = (
            values["net_revenue"] + values["other_operating_income"]
            + values["interest_income"] + values["dividend_income"]
        )
        values["total_expense"] = (
            values["cost_of_revenue"] + values["selling_marketing_expense"]
            + values["general_admin_expense"] + values["other_operating_expense"]
            + values["interest_expense"] + values["income_tax_expense"]
        )
        return values

    current = formulas(current)
    comparison = formulas(comparison)
    has_comparison = bool(comparison_start and comparison_end)

    def statement_row(label, key=None, kind="line", indent=0):
        if kind == "section":
            return {"label": label, "kind": kind, "indent": indent}
        current_value = current[key]
        comparison_value = comparison[key] if has_comparison else None
        variance = current_value - comparison_value if has_comparison else None
        variance_percent = None
        if has_comparison and comparison_value:
            variance_percent = (variance / abs(comparison_value) * Decimal("100")).quantize(Decimal("0.01"))
        return {
            "label": label,
            "kind": kind,
            "indent": indent,
            "current": current_value,
            "comparison": comparison_value,
            "variance": variance,
            "variance_percent": variance_percent,
        }

    statement_rows = [
        statement_row("Operating Category", kind="section"),
        statement_row("Revenue from Contracts with Customers", "gross_revenue", indent=1),
        statement_row("Sales Returns and Discounts", "sales_deduction", indent=1),
        statement_row("Net Revenue", "net_revenue", kind="subtotal"),
        statement_row("Cost of Revenue", "cost_of_revenue", indent=1),
        statement_row("Gross Profit", "gross_profit", kind="subtotal"),
        statement_row("Selling and Marketing Expenses", "selling_marketing_expense", indent=1),
        statement_row("General and Administrative Expenses", "general_admin_expense", indent=1),
        statement_row("Other Operating Income", "other_operating_income", indent=1),
        statement_row("Other Operating Expenses", "other_operating_expense", indent=1),
        statement_row("Operating Profit / Loss", "operating_profit", kind="subtotal"),
        statement_row("Investing Category", kind="section"),
        statement_row("Interest Income from Cash / Deposits", "interest_income", indent=1),
        statement_row("Dividend Income", "dividend_income", indent=1),
        statement_row("Profit Before Financing and Income Tax", "profit_before_financing_tax", kind="subtotal"),
        statement_row("Financing Category", kind="section"),
        statement_row("Interest Expense on Borrowings", "interest_expense", indent=1),
        statement_row("Profit Before Income Tax", "profit_before_tax", kind="subtotal"),
        statement_row("Income Tax", kind="section"),
        statement_row("Income Tax Expense", "income_tax_expense", indent=1),
        statement_row("Profit / Loss for the Period", "profit_loss_period", kind="total"),
    ]
    margin = (
        current["profit_loss_period"] / current["total_income"] * Decimal("100")
        if current["total_income"]
        else Decimal("0")
    )
    filtered_entries = (
        _scoped(user, "journal-entries", JournalEntry)
        .filter(status=JournalEntry.Status.POSTED, entry_date__range=(start, end))
    )
    return {
        "view_type": "profit_loss",
        "metrics": [
            _metric("Total Income", current["total_income"], f"{start:%d %b %Y} – {end:%d %b %Y}"),
            _metric("Total Expenses", current["total_expense"], "Expenses recognized in the period", "violet"),
            _metric("Profit / Loss", current["profit_loss_period"], "Income less expenses", "amber"),
            _metric("Net Margin", f"{margin.quantize(Decimal('0.01'))}%", "Profit or loss as a share of income"),
        ],
        "statement_rows": statement_rows,
        "has_comparison": has_comparison,
        "period": {"preset": preset, "start": start, "end": end},
        "comparison": {
            "mode": comparison_mode,
            "start": comparison_start,
            "end": comparison_end,
        },
        "period_options": PROFIT_LOSS_PERIOD_OPTIONS,
        "comparison_options": PROFIT_LOSS_COMPARISON_OPTIONS,
        "filter_error": " ".join(part for part in (period_error, comparison_error) if part),
        "breakdown_title": f"Profit and Loss · {start:%d %b %Y} – {end:%d %b %Y}",
        "breakdown": [
            {"label": row["label"], "total": row.get("current", "")}
            for row in statement_rows if row["kind"] != "section"
        ],
        "recent_title": "Recent posted entries in period",
        "recent": list(filtered_entries.select_related("company", "journal").order_by("-entry_date", "-id")[:8]),
    }


def _partner_summary(user, feature_slug):
    lines = _posted_lines(user).exclude(partner_id=None)
    totals = lines.aggregate(debit=Sum("debit"), credit=Sum("credit"), partners=Count("partner_id", distinct=True))
    breakdown = (
        lines.values("partner__name")
        .annotate(debit=Sum("debit"), credit=Sum("credit"))
        .order_by("partner__name")[:40]
    )
    overdue = lines.filter(due_date__lt=timezone.localdate(), reconciled=False).count()
    return {
        "metrics": [
            _metric("Partners", totals["partners"], "Partners with posted movements"),
            _metric("Debit", totals["debit"], "Partner debit movement", "violet"),
            _metric("Credit", totals["credit"], "Partner credit movement", "violet"),
            _metric("Overdue Items", overdue, "Unreconciled items past due", "amber"),
        ],
        "breakdown_title": "Partner open balance" if feature_slug == "aging-summary" else "Partner ledger balance",
        "breakdown": [
            {"label": row["partner__name"], "total": row["debit"] - row["credit"]} for row in breakdown
        ],
        "recent_title": "Recent partner journals",
        "recent": list(
            _scoped(user, "journal-entries", JournalEntry)
            .filter(lines__partner_id__isnull=False)
            .distinct()
            .order_by("-entry_date", "-id")[:8]
        ),
    }


def _treasury_summary(user, feature_slug):
    bank_accounts = _scoped(user, "company-bank-accounts", CompanyBankAccount)
    statements = _scoped(user, "bank-statements", BankStatement)
    payments = _scoped(user, "account-payments", AccountPayment)
    deposits = _scoped(user, "account-deposits", AccountDeposit)
    cheques = _scoped(user, "account-cheques", AccountCheque)
    if feature_slug == "deposit-summary":
        total = deposits.aggregate(value=Sum("amount"), unapplied=Sum("unapplied_amount"), count=Count("id"))
        recent = deposits.select_related("partner", "journal").order_by("-deposit_date", "-id")[:8]
        breakdown = _rows(deposits)
        metrics = [
            _metric("Deposits", total["count"], "Customer and vendor deposits"),
            _metric("Deposit Value", total["value"], "Gross deposit value", "violet"),
            _metric("Unapplied", total["unapplied"], "Available for allocation", "amber"),
            _metric("Applied", deposits.filter(status="applied").count(), "Fully applied deposits"),
        ]
    elif feature_slug == "cheque-summary":
        total = cheques.aggregate(value=Sum("amount"), count=Count("id"))
        recent = cheques.select_related("partner", "bank_account").order_by("-cheque_date", "-id")[:8]
        breakdown = _rows(cheques)
        metrics = [
            _metric("Cheques", total["count"], "Receipt and payment cheques"),
            _metric("Cheque Value", total["value"], "Gross cheque value", "violet"),
            _metric("Cleared", cheques.filter(status="cleared").count(), "Cleared by the bank"),
            _metric("Bounced", cheques.filter(status="bounced").count(), "Needs attention", "amber"),
        ]
    else:
        balance = statements.filter(status__in=["reconciled", "closed"]).aggregate(value=Sum("closing_balance"))["value"]
        recent = statements.select_related("bank_account").order_by("-date_to", "-id")[:8]
        breakdown = _rows(statements)
        metrics = [
            _metric("Bank Accounts", bank_accounts.filter(active=True).count(), "Active bank and cash accounts"),
            _metric("Latest Closing Total", balance, "Reconciled statement balances", "violet"),
            _metric("Unreconciled Statements", statements.exclude(status__in=["reconciled", "closed"]).count(), "Needs reconciliation", "amber"),
            _metric("Posted Payments", payments.filter(status="posted").count(), "Receipts and payments posted"),
        ]
    return {
        "metrics": metrics,
        "breakdown_title": feature_slug.replace("-", " ").title() + " by status",
        "breakdown": breakdown,
        "recent_title": "Recent treasury activity",
        "recent": list(recent),
    }


def build_accounting_analytics(feature_slug, user, params=None):
    if feature_slug not in ACCOUNTING_ANALYTIC_FEATURES:
        return None
    if feature_slug == "profit-loss":
        return _profit_loss_statement(user, params)
    if feature_slug in {
        "general-ledger", "general-ledger-report", "trial-balance", "journal-audit", "day-book",
        "journal-entry-report", "dashboard", "accounting-vision", "cost-center-ledger",
    }:
        return _journal_summary(user, feature_slug)
    if feature_slug in {
        "balance-sheet", "cash-flow", "changes-in-equity", "financial-ratios",
        "income-overview", "financial-statements", "business-statements", "accounting-reports",
    }:
        return _financial_statement(user, feature_slug)
    if feature_slug in {"partner-ledger", "aging-summary", "partner-aging", "statement-of-account", "debt-collection"}:
        return _partner_summary(user, feature_slug)
    if feature_slug in {"bank-balance", "bank-cash-book", "deposit-summary", "cheque-summary"}:
        return _treasury_summary(user, feature_slug)
    if feature_slug == "petty-cash-report":
        funds = _scoped(user, "petty-cash-funds", PettyCashFund)
        vouchers = _scoped(user, "petty-cash-vouchers", PettyCashVoucher)
        totals = funds.aggregate(balance=Sum("current_balance"), limit=Sum("imprest_limit"), count=Count("id"))
        return {
            "metrics": [
                _metric("Petty Cash Funds", totals["count"], "Active and inactive funds"),
                _metric("Current Balance", totals["balance"], "Total counted fund balance", "violet"),
                _metric("Imprest Limit", totals["limit"], "Maximum authorized float"),
                _metric("Draft Vouchers", vouchers.filter(status="draft").count(), "Awaiting review", "amber"),
            ],
            "breakdown_title": "Petty cash vouchers by status", "breakdown": _rows(vouchers),
            "recent_title": "Recent petty cash vouchers",
            "recent": list(vouchers.select_related("fund").order_by("-voucher_date", "-id")[:8]),
        }
    if feature_slug in {"prepayment-report", "customer-prepayment-details", "vendor-prepayment-details"}:
        records = _scoped(user, "prepayments", Prepayment)
        totals = records.aggregate(total=Sum("total_amount"), applied=Sum("applied_amount"), count=Count("id"))
        return {
            "metrics": [
                _metric("Prepayments", totals["count"], "Customer and vendor prepayments"),
                _metric("Total", totals["total"], "Original value", "violet"),
                _metric("Applied", totals["applied"], "Allocated value"),
                _metric("Remaining", (totals["total"] or 0) - (totals["applied"] or 0), "Still available", "amber"),
            ],
            "breakdown_title": "Prepayments by status", "breakdown": _rows(records),
            "recent_title": "Recent prepayments", "recent": list(records.select_related("partner").order_by("-start_date", "-id")[:8]),
        }
    if feature_slug == "foreign-exchange":
        records = _scoped(user, "currency-revaluations", CurrencyRevaluation)
        totals = records.aggregate(gain=Sum("unrealized_gain"), loss=Sum("unrealized_loss"), count=Count("id"))
        return {
            "metrics": [
                _metric("Revaluations", totals["count"], "Currency revaluation runs"),
                _metric("Unrealized Gain", totals["gain"], "Calculated FX gain", "violet"),
                _metric("Unrealized Loss", totals["loss"], "Calculated FX loss", "amber"),
                _metric("Net FX", (totals["gain"] or 0) - (totals["loss"] or 0), "Gain less loss"),
            ],
            "breakdown_title": "Revaluations by currency", "breakdown": _rows(records, "currency"),
            "recent_title": "Recent revaluations", "recent": list(records.order_by("-revaluation_date", "-id")[:8]),
        }
    records = _scoped(user, "withholding-taxes", WithholdingTax)
    return {
        "metrics": [
            _metric("Withholding Taxes", records.count(), "Configured withholding taxes"),
            _metric("Customer", records.filter(party_type="customer", active=True).count(), "Active customer withholding"),
            _metric("Vendor", records.filter(party_type="vendor", active=True).count(), "Active vendor withholding", "violet"),
            _metric("Inactive", records.filter(active=False).count(), "Archived configurations", "amber"),
        ],
        "breakdown_title": "Withholding tax by party", "breakdown": _rows(records, "party_type"),
        "recent_title": "Recent withholding tax configuration", "recent": list(records.select_related("account").order_by("-updated_at")[:8]),
    }
