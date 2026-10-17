"""Bounded, company-scoped Accounting dashboards and financial summaries."""

from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

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


def _scoped(user, entity, model):
    return scope_accounting_queryset(user, entity, model.objects.all())


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


def build_accounting_analytics(feature_slug, user):
    if feature_slug not in ACCOUNTING_ANALYTIC_FEATURES:
        return None
    if feature_slug in {
        "general-ledger", "general-ledger-report", "trial-balance", "journal-audit", "day-book",
        "journal-entry-report", "dashboard", "accounting-vision", "cost-center-ledger",
    }:
        return _journal_summary(user, feature_slug)
    if feature_slug in {
        "balance-sheet", "profit-loss", "cash-flow", "changes-in-equity", "financial-ratios",
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
