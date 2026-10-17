"""Transactional accounting services shared by UI and dependent modules."""

import calendar
import csv
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
import re

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from .localization_packs import country_pack, provision_country_profile
from .invoice_posting import (
    notify_invoice_cancelled,
    notify_invoice_posted,
    notify_invoice_posting_failure,
    resolve_invoice_posting_override,
)
from .external_connectors import external_connector_backend
from .provisioning import accounting_blueprint, run_company_accounting_provisioners
from .tax_connectors import connector_backend
from .models import (
    Account,
    AccountJournal,
    AccountCheque,
    AccountDeposit,
    AccountDepositConversion,
    AccountDepositHistory,
    AccountPayment,
    AccountPaymentMethod,
    AccountingAuditEvent,
    AccountingSettings,
    AccountingIntegrationEvent,
    AccountingSourceAdapter,
    AccountingVisionReport,
    AccountingIntelligenceSettings,
    AccountTax,
    AssetAccountingSettings,
    AssetCapitalization,
    AssetCapitalizationAsset,
    AssetDepreciationLine,
    AssetDisposal,
    AssetRevaluation,
    BankStatement,
    BankCashValuation,
    BankStatementParseJob,
    BankStatementLine,
    Budget,
    BudgetCarryOver,
    BudgetChangeRequest,
    BudgetMovementAudit,
    BudgetTransfer,
    BudgetTransaction,
    ClosingEntry,
    CompanyAccountingProvisioningRun,
    CommercialAccountingSettings,
    ComplianceReport,
    ComplianceAuditEvent,
    CurrencyRevaluation,
    DepositApplication,
    ExternalTaxSubmission,
    ExternalAccountingConnector,
    ExternalInvoiceSync,
    FiscalPeriod,
    InternalBankTransfer,
    JournalEntry,
    JournalEntryTemplate,
    JournalLine,
    JournalLineCostCenterAllocation,
    MultiPaymentBatch,
    MarketplaceAccountingOrder,
    OtherIncomeExpense,
    PaymentAllocation,
    PaymentVoucher,
    PartnerAccountingDefault,
    PettyCashReconciliation,
    PettyCashReplenishment,
    PettyCashVoucher,
    POSAccountingSettings,
    POSPaymentAccountMapping,
    PayrollAccountingSettings,
    PayrollComponentAccountMapping,
    InventoryAccountingSettings,
    InventoryAccountingDocument,
    ExpenseAdvanceAccountingSettings,
    ExpenseAdvanceSettlement,
    ExpenseClaim,
    Prepayment,
    PrepaymentApplication,
    ProductAccountMapping,
    ProductAccountingRule,
    ReconciliationHistory,
    ReconciliationRule,
    ReconciliationRuleTestRun,
    RecurringJournal,
    RecurringJournalExecution,
    ARReconciliationSession,
    ARReconciliationAllocation,
    ARReconciliationLine,
    RentalDepositHold,
    RentalOrderAccounting,
    SourceDocumentCancellation,
    SourceAccountingPosting,
    StatutoryReportLine,
    StatutoryReportTemplate,
    StudentFeeSchedule,
    TaxAuthorityDocument,
    TaxIdentifierLookup,
    TaxMiddlewareConnector,
    WithholdingTaxHistory,
    WithholdingTaxTransaction,
)


def _assert_posting_date_is_open(company_id, posting_date):
    settings = AccountingSettings.objects.filter(company_id=company_id).only(
        "lock_date", "allow_posting_to_closed_period"
    ).first()
    if settings and settings.lock_date and posting_date <= settings.lock_date:
        raise ValidationError("The posting date is on or before the accounting lock date.")
    if settings and settings.allow_posting_to_closed_period:
        return
    if FiscalPeriod.objects.filter(
        fiscal_year__company_id=company_id,
        start_date__lte=posting_date,
        end_date__gte=posting_date,
        status__in=[FiscalPeriod.Status.CLOSED, FiscalPeriod.Status.LOCKED],
    ).exists():
        raise ValidationError("The posting date belongs to a closed or locked fiscal period.")


def _audit(instance, event_type, actor=None, details=None):
    AccountingAuditEvent.objects.create(
        company_id=instance.company_id,
        event_type=event_type,
        object_type=instance._meta.label_lower,
        object_id=instance.pk,
        reference=str(instance),
        actor=actor,
        details=details or {},
    )


def _resolve_product_account_ids(company_id, products, account_field):
    """Resolve product accounts in one pair of bounded queries.

    Precedence mirrors HMX: explicit per-variant mapping, scoped variant rule,
    product rule, then product-category rule. GS Shop's current product master
    uses SKU as its variant reference and product_type as its category key.
    """
    products = list(products)
    if not products:
        return {}
    if account_field not in {"revenue_account", "expense_account", "prepayment_account", "inventory_account"}:
        raise ValueError("Unsupported product account field.")
    product_ids = {product.pk for product in products}
    mappings = {
        mapping.product_id: getattr(mapping, f"{account_field}_id")
        for mapping in ProductAccountMapping.objects.filter(
            company_id=company_id, product_id__in=product_ids, active=True
        ).only("product_id", f"{account_field}_id")
    }
    if account_field == "inventory_account":
        return {product_id: account_id for product_id, account_id in mappings.items() if account_id}
    references = {
        value
        for product in products
        for value in (str(product.pk), product.sku, product.name, product.product_type)
        if value
    }
    rules = list(
        ProductAccountingRule.objects.filter(
            company_id=company_id, active=True, reference__in=references
        ).only("scope", "reference", f"{account_field}_id")
    )
    indexed = {
        (rule.scope, rule.reference): getattr(rule, f"{account_field}_id")
        for rule in rules
        if getattr(rule, f"{account_field}_id")
    }
    resolved = {}
    for product in products:
        account_id = mappings.get(product.pk)
        if not account_id:
            account_id = (
                indexed.get((ProductAccountingRule.Scope.VARIANT, str(product.pk)))
                or indexed.get((ProductAccountingRule.Scope.VARIANT, product.sku))
                or indexed.get((ProductAccountingRule.Scope.PRODUCT, product.sku))
                or indexed.get((ProductAccountingRule.Scope.PRODUCT, product.name))
                or indexed.get((ProductAccountingRule.Scope.CATEGORY, product.product_type))
            )
        if account_id:
            resolved[product.pk] = account_id
    return resolved


@transaction.atomic
def post_journal_entry(entry, actor=None):
    locked = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if locked.status != JournalEntry.Status.DRAFT:
        raise ValidationError("Only draft journal entries can be posted.")
    _assert_posting_date_is_open(locked.company_id, locked.entry_date)
    totals = locked.lines.aggregate(debit=Sum("debit"), credit=Sum("credit"))
    debit = totals["debit"] or Decimal("0")
    credit = totals["credit"] or Decimal("0")
    if debit <= 0 or debit != credit:
        raise ValidationError("Journal entry must have equal non-zero debit and credit totals.")
    invalid_company = locked.lines.exclude(account__company_id=locked.company_id).exists()
    if invalid_company:
        raise ValidationError("Every journal line account must belong to the journal company.")
    allocation_totals = list(
        JournalLineCostCenterAllocation.objects.filter(journal_line__entry=locked)
        .values("journal_line_id")
        .annotate(total=Sum("percentage"))
    )
    invalid_allocations = [row["journal_line_id"] for row in allocation_totals if row["total"] != Decimal("100")]
    if invalid_allocations:
        raise ValidationError("Each journal line with cost-center allocations must total exactly 100%.")
    locked.status = JournalEntry.Status.POSTED
    locked.total_debit = debit
    locked.total_credit = credit
    locked.posted_at = timezone.now()
    locked.posted_by = actor
    locked.save(update_fields=["status", "total_debit", "total_credit", "posted_at", "posted_by", "updated_at"])
    _audit(locked, "journal_posted", actor, {"debit": str(debit), "credit": str(credit)})
    return locked


@transaction.atomic
def reverse_journal_entry(entry, actor=None, reversal_date=None):
    locked = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if locked.status != JournalEntry.Status.POSTED:
        raise ValidationError("Only posted journal entries can be reversed.")
    if hasattr(locked, "reversal_entry"):
        raise ValidationError("This journal entry already has a reversal.")
    reversal_date = reversal_date or timezone.localdate()
    _assert_posting_date_is_open(locked.company_id, reversal_date)
    source_lines = list(locked.lines.all())
    if not source_lines:
        raise ValidationError("A journal entry without lines cannot be reversed.")
    source_allocations = list(
        JournalLineCostCenterAllocation.objects.filter(journal_line__entry=locked).values(
            "journal_line_id", "cost_center_id", "percentage"
        )
    )
    reversal = JournalEntry.objects.create(
        company_id=locked.company_id,
        journal_id=locked.journal_id,
        number=f"REV-{locked.number}",
        entry_date=reversal_date,
        reference=locked.reference,
        description=f"Reversal of {locked.number}: {locked.description}"[:240],
        currency=locked.currency,
        exchange_rate=locked.exchange_rate,
        source_type="journal_reversal",
        source_reference=locked.number,
        reversal_of=locked,
    )
    reversal_lines = [
        JournalLine(
            entry=reversal,
            account_id=line.account_id,
            partner_id=line.partner_id,
            cost_center_id=line.cost_center_id,
            description=line.description,
            debit=line.credit,
            credit=line.debit,
            currency=line.currency,
            amount_currency=-line.amount_currency,
            due_date=line.due_date,
        )
        for line in source_lines
    ]
    JournalLine.objects.bulk_create(reversal_lines)
    reversal_by_source = {source.pk: target for source, target in zip(source_lines, reversal_lines)}
    JournalLineCostCenterAllocation.objects.bulk_create([
        JournalLineCostCenterAllocation(
            journal_line=reversal_by_source[row["journal_line_id"]],
            cost_center_id=row["cost_center_id"],
            percentage=row["percentage"],
        )
        for row in source_allocations
    ])
    reversal = post_journal_entry(reversal, actor)
    locked.status = JournalEntry.Status.REVERSED
    locked.save(update_fields=["status", "updated_at"])
    _audit(locked, "journal_reversed", actor, {"reversal_entry_id": reversal.pk})
    return reversal


@transaction.atomic
def post_payment(payment, actor=None):
    locked = AccountPayment.objects.select_for_update().select_related("journal").get(pk=payment.pk)
    if locked.status != AccountPayment.Status.DRAFT:
        raise ValidationError("Only draft payments can be posted.")
    _assert_posting_date_is_open(locked.company_id, locked.payment_date)
    try:
        settings = AccountingSettings.objects.get(company_id=locked.company_id)
    except AccountingSettings.DoesNotExist as exc:
        raise ValidationError("Configure Accounting Settings before posting payments.") from exc
    partner_account = (
        settings.default_receivable_account
        if locked.direction == AccountPayment.Direction.RECEIVE
        else settings.default_payable_account
    )
    bank_account = (
        locked.journal.default_debit_account
        if locked.direction == AccountPayment.Direction.RECEIVE
        else locked.journal.default_credit_account
    )
    if not partner_account or not bank_account:
        raise ValidationError("Configure partner and bank clearing accounts before posting this payment.")
    entry = JournalEntry.objects.create(
        company_id=locked.company_id,
        journal=locked.journal,
        number=f"PAY-{locked.number}",
        entry_date=locked.payment_date,
        reference=locked.reference or locked.number,
        description=f"{locked.get_direction_display()} {locked.number}",
        currency=locked.currency,
        source_type="account_payment",
        source_reference=locked.number,
    )
    debit_account, credit_account = (
        (bank_account, partner_account)
        if locked.direction == AccountPayment.Direction.RECEIVE
        else (partner_account, bank_account)
    )
    JournalLine.objects.bulk_create(
        [
            JournalLine(entry=entry, account=debit_account, partner=locked.partner, debit=locked.amount),
            JournalLine(entry=entry, account=credit_account, partner=locked.partner, credit=locked.amount),
        ]
    )
    entry = post_journal_entry(entry, actor)
    locked.status = AccountPayment.Status.POSTED
    locked.journal_entry = entry
    locked.save(update_fields=["status", "journal_entry", "updated_at"])
    _audit(locked, "payment_posted", actor, {"journal_entry_id": entry.pk})
    return locked


@transaction.atomic
def post_bank_transfer(transfer, actor=None):
    locked = InternalBankTransfer.objects.select_for_update().select_related(
        "source_account__journal", "destination_account__journal"
    ).get(pk=transfer.pk)
    if locked.status != InternalBankTransfer.Status.DRAFT:
        raise ValidationError("Only draft bank transfers can be posted.")
    _assert_posting_date_is_open(locked.company_id, locked.transfer_date)
    debit_account = locked.destination_account.journal.default_debit_account
    credit_account = locked.source_account.journal.default_credit_account
    if not debit_account or not credit_account:
        raise ValidationError("Configure debit and credit accounts on both bank journals.")
    entry = JournalEntry.objects.create(
        company_id=locked.company_id,
        journal=locked.destination_account.journal,
        number=f"TRF-{locked.number}",
        entry_date=locked.transfer_date,
        reference=locked.reference or locked.number,
        description=f"Bank transfer {locked.number}",
        source_type="internal_bank_transfer",
        source_reference=locked.number,
    )
    JournalLine.objects.bulk_create(
        [
            JournalLine(entry=entry, account=debit_account, debit=locked.amount),
            JournalLine(entry=entry, account=credit_account, credit=locked.amount),
        ]
    )
    entry = post_journal_entry(entry, actor)
    locked.status = InternalBankTransfer.Status.POSTED
    locked.journal_entry = entry
    locked.save(update_fields=["status", "journal_entry", "updated_at"])
    _audit(locked, "bank_transfer_posted", actor, {"journal_entry_id": entry.pk})
    return locked


@transaction.atomic
def process_account_cheque(cheque, action, actor=None):
    locked = AccountCheque.objects.select_for_update().select_related(
        "bank_account__journal", "bank_account__receipt_cheque_account", "bank_account__payment_cheque_account", "partner"
    ).get(pk=cheque.pk)
    source_model = locked._meta.label_lower
    if action == "issue":
        if locked.status != AccountCheque.Status.DRAFT:
            raise ValidationError("Only a draft cheque can be issued or received.")
        rows = list(locked.lines.select_related("account"))
        if not rows:
            raise ValidationError("Add at least one cheque allocation line.")
        total = sum((row.amount for row in rows), Decimal("0"))
        if total != locked.amount:
            raise ValidationError("Cheque allocation total must equal the cheque amount.")
        receipt = locked.cheque_type == AccountCheque.ChequeType.RECEIPT
        clearing_account = (
            locked.bank_account.receipt_cheque_account
            if receipt
            else locked.bank_account.payment_cheque_account
        )
        if clearing_account is None:
            raise ValidationError("Configure the receipt/payment cheque clearing account on the bank account.")
        lines = [{
            "account_id": clearing_account.pk,
            "debit": str(total) if receipt else "0",
            "credit": "0" if receipt else str(total),
            "description": locked.cheque_number,
        }]
        lines.extend([
            {
                "account_id": row.account_id,
                "debit": "0" if receipt else str(row.amount),
                "credit": str(row.amount) if receipt else "0",
                "description": row.description or locked.cheque_number,
            }
            for row in rows
        ])
        _post_simple_bridge(
            company_id=locked.company_id, source=locked, event_type="cheque_issue",
            reference=locked.cheque_number, event_date=locked.cheque_date, amount=total,
            journal_id=locked.bank_account.journal_id, lines=lines, actor=actor,
        )
        locked.status = AccountCheque.Status.ISSUED
    elif action == "clear":
        if locked.status != AccountCheque.Status.ISSUED:
            raise ValidationError("Only an issued cheque can be cleared.")
        receipt = locked.cheque_type == AccountCheque.ChequeType.RECEIPT
        clearing_account = (
            locked.bank_account.receipt_cheque_account
            if receipt
            else locked.bank_account.payment_cheque_account
        )
        bank_ledger_account = (
            locked.bank_account.journal.default_debit_account
            if receipt
            else locked.bank_account.journal.default_credit_account
        )
        if clearing_account is None or bank_ledger_account is None:
            raise ValidationError("Configure bank ledger and cheque clearing accounts before clearance.")
        _post_simple_bridge(
            company_id=locked.company_id, source=locked, event_type="cheque_clearance",
            reference=locked.cheque_number, event_date=timezone.localdate(), amount=locked.amount,
            journal_id=locked.bank_account.journal_id, actor=actor,
            lines=[
                {
                    "account_id": bank_ledger_account.pk,
                    "debit": str(locked.amount) if receipt else "0",
                    "credit": "0" if receipt else str(locked.amount),
                },
                {
                    "account_id": clearing_account.pk,
                    "debit": "0" if receipt else str(locked.amount),
                    "credit": str(locked.amount) if receipt else "0",
                },
            ],
        )
        locked.status = AccountCheque.Status.CLEARED
    elif action == "bounce":
        if locked.status != AccountCheque.Status.ISSUED:
            raise ValidationError("Only an issued cheque can be bounced.")
        issue_event = AccountingIntegrationEvent.objects.filter(
            company_id=locked.company_id,
            source_model=source_model,
            source_id=locked.pk,
            event_type="cheque_issue",
        ).first()
        if issue_event is None:
            raise ValidationError("The cheque issue accounting event was not found.")
        reverse_integration_event(issue_event, actor)
        locked.status = AccountCheque.Status.BOUNCED
    else:
        raise ValidationError("Unsupported cheque workflow action.")
    locked.save(update_fields=["status", "updated_at"])
    _audit(locked, f"cheque_{action}", actor)
    return locked


@transaction.atomic
def _post_invoice_atomic(invoice, actor=None):
    from core.models import Invoice
    from .models import AccountJournal

    locked = Invoice.objects.select_for_update().select_related("company", "partner").get(pk=invoice.pk)
    if locked.status != Invoice.Status.DRAFT:
        raise ValidationError("Only draft invoices or bills can be posted.")
    _assert_posting_date_is_open(locked.company_id, locked.invoice_date)
    rows = list(locked.lines.select_related("product"))
    if not rows:
        raise ValidationError("Add at least one invoice line before posting.")
    journal_type = (
        AccountJournal.JournalType.SALES
        if locked.invoice_type == Invoice.InvoiceType.CUSTOMER
        else AccountJournal.JournalType.PURCHASE
    )
    commercial = CommercialAccountingSettings.objects.select_related("sales_journal", "purchase_journal").filter(
        company_id=locked.company_id
    ).first()
    journal = (
        commercial.sales_journal
        if commercial and locked.invoice_type == Invoice.InvoiceType.CUSTOMER
        else commercial.purchase_journal
        if commercial
        else None
    )
    journal = journal or AccountJournal.objects.filter(
        company_id=locked.company_id, journal_type=journal_type, active=True
    ).order_by("id").first() or _general_journal(locked.company_id)
    settings = AccountingSettings.objects.select_related(
        "default_receivable_account", "default_payable_account"
    ).get(company_id=locked.company_id)
    partner_default = PartnerAccountingDefault.objects.select_related("receivable_account", "payable_account").filter(
        company_id=locked.company_id,
        partner_id=locked.partner_id,
        active=True,
    ).first()
    if locked.invoice_type == Invoice.InvoiceType.CUSTOMER:
        partner_account = (
            partner_default.receivable_account
            if partner_default and partner_default.receivable_account_id
            else settings.default_receivable_account
        )
    else:
        partner_account = (
            partner_default.payable_account
            if partner_default and partner_default.payable_account_id
            else settings.default_payable_account
        )
    if partner_account is None:
        raise ValidationError("Configure the default receivable/payable account before posting invoices.")
    total = sum((row.quantity * row.unit_price for row in rows), Decimal("0"))
    if total <= 0:
        raise ValidationError("The invoice total must be greater than zero.")
    override = resolve_invoice_posting_override(
        invoice=locked,
        rows=rows,
        partner_account=partner_account,
        total=total,
        journal=journal,
        actor=actor,
    )
    if override and override.journal is not None:
        journal = override.journal
    entry = JournalEntry.objects.create(
        company_id=locked.company_id, journal=journal,
        number=("INV-" if locked.invoice_type == Invoice.InvoiceType.CUSTOMER else "BILL-") + locked.number,
        entry_date=locked.invoice_date, reference=locked.number,
        description=(override.description if override else locked.notes)[:240],
        source_type="account_invoice", source_reference=locked.number,
    )
    if override:
        journal_lines = [
            JournalLine(
                entry=entry,
                account=line.account,
                partner=line.partner,
                description=line.description,
                debit=line.debit,
                credit=line.credit,
                due_date=line.due_date,
            )
            for line in override.lines
        ]
    else:
        is_customer = locked.invoice_type == Invoice.InvoiceType.CUSTOMER
        products = [row.product for row in rows if row.product_id]
        account_field = "revenue_account" if is_customer else "expense_account"
        account_ids = _resolve_product_account_ids(locked.company_id, products, account_field)
        missing_products = [row.product for row in rows if row.product_id and row.product_id not in account_ids]
        if missing_products:
            raise ValidationError(
                f"Configure a {account_field.replace('_', ' ')} for: "
                f"{', '.join(str(item) for item in missing_products[:5])}."
            )
        detail_lines = []
        for row in rows:
            account_id = account_ids.get(row.product_id) if row.product_id else (
                journal.default_credit_account_id if is_customer else journal.default_debit_account_id
            )
            if account_id is None:
                raise ValidationError("Configure an account for every invoice line.")
            amount = row.quantity * row.unit_price
            detail_lines.append(JournalLine(
                entry=entry, account_id=account_id, partner=locked.partner, description=row.description,
                debit=amount if not is_customer else Decimal("0"),
                credit=amount if is_customer else Decimal("0"),
            ))
        partner_line = JournalLine(
            entry=entry, account=partner_account, partner=locked.partner, description=locked.number,
            debit=total if is_customer else Decimal("0"), credit=total if not is_customer else Decimal("0"),
            due_date=locked.due_date,
        )
        journal_lines = [partner_line, *detail_lines]
    JournalLine.objects.bulk_create(journal_lines)
    post_journal_entry(entry, actor)
    locked.total = total
    locked.status = Invoice.Status.POSTED
    locked.save(update_fields=["total", "status", "updated_at"])
    if override and override.after_post:
        override.after_post(entry)
    notify_invoice_posted(invoice=locked, entry=entry, actor=actor)
    _audit(locked, "invoice_posted", actor, {"journal_entry_id": entry.pk})
    return locked


def post_invoice(invoice, actor=None):
    try:
        return _post_invoice_atomic(invoice, actor)
    except Exception as exc:
        notify_invoice_posting_failure(invoice=invoice, error=exc)
        raise


@transaction.atomic
def cancel_invoice(invoice, actor=None):
    from core.models import Invoice

    locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if locked.status not in {Invoice.Status.DRAFT, Invoice.Status.POSTED}:
        raise ValidationError("Only draft or posted invoices can be cancelled.")
    entry = JournalEntry.objects.filter(
        company_id=locked.company_id, source_type="account_invoice", source_reference=locked.number
    ).order_by("id").first()
    if entry and entry.status == JournalEntry.Status.POSTED:
        reverse_journal_entry(entry, actor)
    locked.status = Invoice.Status.CANCELLED
    locked.save(update_fields=["status", "updated_at"])
    notify_invoice_cancelled(invoice=locked, actor=actor)
    _audit(locked, "invoice_cancelled", actor, {"journal_entry_id": entry.pk if entry else None})
    return locked


def _general_journal(company_id):
    journal = AccountJournal.objects.filter(
        company_id=company_id, journal_type=AccountJournal.JournalType.GENERAL, active=True
    ).order_by("id").first()
    if journal is None:
        raise ValidationError("Configure an active General journal before posting this transaction.")
    return journal


@transaction.atomic
def instantiate_journal_template(template, number, entry_date=None, actor=None):
    template = JournalEntryTemplate.objects.select_for_update().select_related("journal").get(pk=template.pk)
    lines = list(template.lines.select_related("account", "cost_center").order_by("sequence", "id"))
    if not lines:
        raise ValidationError("The journal template requires at least one line.")
    entry = JournalEntry.objects.create(
        company_id=template.company_id,
        journal=template.journal,
        number=number,
        entry_date=entry_date or timezone.localdate(),
        description=template.description or template.name,
        source_type="journal_template",
        source_reference=template.code,
    )
    JournalLine.objects.bulk_create([
        JournalLine(
            entry=entry,
            account=line.account,
            cost_center=line.cost_center,
            description=line.description,
            debit=line.amount if line.side == line.Side.DEBIT else Decimal("0"),
            credit=line.amount if line.side == line.Side.CREDIT else Decimal("0"),
        )
        for line in lines
    ])
    _audit(entry, "journal_created_from_template", actor, {"template_id": template.pk})
    return entry


@transaction.atomic
def post_other_income_expense(document, actor=None):
    locked = OtherIncomeExpense.objects.select_for_update().select_related("journal", "partner").get(pk=document.pk)
    if locked.status not in {OtherIncomeExpense.Status.DRAFT, OtherIncomeExpense.Status.APPROVED}:
        raise ValidationError("Only draft or approved other income/expense documents can be posted.")
    _assert_posting_date_is_open(locked.company_id, locked.transaction_date)
    rows = list(locked.lines.select_related("account", "cost_center", "budget", "budget_account"))
    if not rows:
        raise ValidationError("Add at least one income or expense line before posting.")
    total = sum((row.amount for row in rows), Decimal("0"))
    budget_rows = [row for row in rows if row.budget_id]
    if budget_rows and locked.transaction_type != OtherIncomeExpense.TransactionType.EXPENSE:
        raise ValidationError("Budget consumption can only be attached to other-expense documents.")
    budget_ids = {row.budget_id for row in budget_rows}
    budgets = {
        budget.pk: budget
        for budget in Budget.objects.select_for_update().filter(pk__in=budget_ids, company_id=locked.company_id)
    }
    if len(budgets) != len(budget_ids):
        raise ValidationError("One or more selected budgets are invalid for this company.")
    budget_increments = {}
    for row in budget_rows:
        budget_increments[row.budget_id] = budget_increments.get(row.budget_id, Decimal("0")) + row.amount
    for budget_id, increment in budget_increments.items():
        budget = budgets[budget_id]
        if budget.status != Budget.Status.APPROVED:
            raise ValidationError(f"Budget {budget} must be approved before consumption.")
        if not (budget.start_date <= locked.transaction_date <= budget.end_date):
            raise ValidationError(f"Transaction date is outside budget {budget} period.")
        if increment > budget.remaining:
            raise ValidationError(f"Budget {budget} has insufficient remaining amount.")
    clearing = (
        locked.journal.default_debit_account
        if locked.transaction_type == OtherIncomeExpense.TransactionType.INCOME
        else locked.journal.default_credit_account
    )
    if clearing is None:
        raise ValidationError("Configure the clearing account on the selected journal.")
    entry = JournalEntry.objects.create(
        company_id=locked.company_id,
        journal=locked.journal,
        number=f"OIE-{locked.number}",
        entry_date=locked.transaction_date,
        reference=locked.reference or locked.number,
        description=locked.description[:240],
        source_type="other_income_expense",
        source_reference=locked.number,
    )
    detail_lines = [
        JournalLine(
            entry=entry, account=row.account, partner=locked.partner, cost_center=row.cost_center,
            description=row.description,
            debit=row.amount if locked.transaction_type == OtherIncomeExpense.TransactionType.EXPENSE else Decimal("0"),
            credit=row.amount if locked.transaction_type == OtherIncomeExpense.TransactionType.INCOME else Decimal("0"),
        )
        for row in rows
    ]
    clearing_line = JournalLine(
        entry=entry, account=clearing, partner=locked.partner, description=locked.number,
        debit=total if locked.transaction_type == OtherIncomeExpense.TransactionType.INCOME else Decimal("0"),
        credit=total if locked.transaction_type == OtherIncomeExpense.TransactionType.EXPENSE else Decimal("0"),
    )
    JournalLine.objects.bulk_create([*detail_lines, clearing_line])
    entry = post_journal_entry(entry, actor)
    budget_timestamp = timezone.now()
    for budget_id, increment in budget_increments.items():
        budgets[budget_id].actual += increment
        budgets[budget_id].updated_at = budget_timestamp
    if budgets:
        Budget.objects.bulk_update(list(budgets.values()), ["actual", "updated_at"])
    locked.total_amount = total
    locked.status = OtherIncomeExpense.Status.POSTED
    locked.journal_entry = entry
    locked.save(update_fields=["total_amount", "status", "journal_entry", "updated_at"])
    _audit(locked, "other_income_expense_posted", actor, {"journal_entry_id": entry.pk})
    return locked


@transaction.atomic
def reverse_other_income_expense(document, actor=None):
    locked = OtherIncomeExpense.objects.select_for_update().select_related("journal_entry").get(pk=document.pk)
    if locked.status != OtherIncomeExpense.Status.POSTED or locked.journal_entry_id is None:
        raise ValidationError("Only a posted other income/expense document can be reversed.")
    budget_totals = {
        row["budget_id"]: row["amount"]
        for row in locked.lines.filter(budget_id__isnull=False)
        .values("budget_id")
        .annotate(amount=Sum("amount"))
    }
    budgets = {
        budget.pk: budget
        for budget in Budget.objects.select_for_update().filter(pk__in=budget_totals, company_id=locked.company_id)
    }
    reverse_journal_entry(locked.journal_entry, actor)
    budget_timestamp = timezone.now()
    for budget_id, amount in budget_totals.items():
        budget = budgets[budget_id]
        budget.actual = max(Decimal("0"), budget.actual - amount)
        budget.updated_at = budget_timestamp
    if budgets:
        Budget.objects.bulk_update(list(budgets.values()), ["actual", "updated_at"])
    locked.status = OtherIncomeExpense.Status.REVERSED
    locked.save(update_fields=["status", "updated_at"])
    _audit(locked, "other_income_expense_reversed", actor, {"journal_entry_id": locked.journal_entry_id})
    return locked


@transaction.atomic
def post_payment_voucher(voucher, actor=None):
    locked = PaymentVoucher.objects.select_for_update().select_related("journal").get(pk=voucher.pk)
    if locked.status not in {PaymentVoucher.Status.DRAFT, PaymentVoucher.Status.APPROVED}:
        raise ValidationError("Only draft or approved vouchers can be posted.")
    _assert_posting_date_is_open(locked.company_id, locked.voucher_date)
    rows = list(locked.lines.select_related("account", "partner", "cost_center"))
    debit = sum((row.debit for row in rows), Decimal("0"))
    credit = sum((row.credit for row in rows), Decimal("0"))
    if debit <= 0 or debit != credit:
        raise ValidationError("Voucher lines must have equal non-zero debit and credit totals.")
    entry = JournalEntry.objects.create(
        company_id=locked.company_id,
        journal=locked.journal,
        number=f"VCH-{locked.number}",
        entry_date=locked.voucher_date,
        reference=locked.reference or locked.number,
        description=locked.description[:240],
        source_type="payment_voucher",
        source_reference=locked.number,
    )
    JournalLine.objects.bulk_create([
        JournalLine(
            entry=entry, account=row.account, partner=row.partner, cost_center=row.cost_center,
            description=row.description, debit=row.debit, credit=row.credit,
        )
        for row in rows
    ])
    entry = post_journal_entry(entry, actor)
    locked.total_amount = debit
    locked.status = PaymentVoucher.Status.POSTED
    locked.journal_entry = entry
    locked.save(update_fields=["total_amount", "status", "journal_entry", "updated_at"])
    _audit(locked, "voucher_posted", actor, {"journal_entry_id": entry.pk})
    return locked


@transaction.atomic
def post_multi_payment_batch(batch, actor=None):
    locked = MultiPaymentBatch.objects.select_for_update().select_related("partner", "journal").get(pk=batch.pk)
    if locked.status not in {MultiPaymentBatch.Status.DRAFT, MultiPaymentBatch.Status.CONFIRMED}:
        raise ValidationError("Only draft or confirmed multi-payment batches can be posted.")
    rows = list(locked.lines.select_related("invoice"))
    if not rows:
        raise ValidationError("Add at least one invoice or bill to the batch.")
    if any(row.invoice.company_id != locked.company_id or row.invoice.partner_id != locked.partner_id for row in rows):
        raise ValidationError("Every invoice must belong to the batch company and partner.")
    total = sum((row.amount for row in rows), Decimal("0"))
    if total <= 0:
        raise ValidationError("The batch total must be greater than zero.")
    payment = AccountPayment.objects.create(
        company_id=locked.company_id,
        number=locked.number,
        payment_date=locked.payment_date,
        direction=(AccountPayment.Direction.RECEIVE if locked.batch_type == MultiPaymentBatch.BatchType.CUSTOMER_RECEIPT else AccountPayment.Direction.PAY),
        partner=locked.partner,
        journal=locked.journal,
        payment_method=locked.payment_method.name if locked.payment_method else "",
        amount=total,
        reference=locked.reference,
    )
    payment = post_payment(payment, actor)
    PaymentAllocation.objects.bulk_create([
        PaymentAllocation(payment=payment, invoice=row.invoice, amount=row.amount) for row in rows
    ])
    invoice_updates = []
    for row in rows:
        row.invoice.paid_amount = min(row.invoice.total, row.invoice.paid_amount + row.amount)
        invoice_updates.append(row.invoice)
    type(rows[0].invoice).objects.bulk_update(invoice_updates, ["paid_amount", "updated_at"])
    locked.total_amount = total
    locked.status = MultiPaymentBatch.Status.POSTED
    locked.save(update_fields=["total_amount", "status", "updated_at"])
    _audit(locked, "multi_payment_posted", actor, {"payment_id": payment.pk})
    return locked


@transaction.atomic
def validate_withholding_tax(transaction_record, actor=None):
    locked = WithholdingTaxTransaction.objects.select_for_update().select_related(
        "withholding_tax__account", "partner"
    ).get(pk=transaction_record.pk)
    if locked.status != WithholdingTaxTransaction.Status.PENDING:
        raise ValidationError("Only pending withholding tax transactions can be validated.")
    _assert_posting_date_is_open(locked.company_id, locked.transaction_date)
    settings = AccountingSettings.objects.select_related(
        "default_receivable_account", "default_payable_account"
    ).get(company_id=locked.company_id)
    tax_account = locked.withholding_tax.account
    partner_account = (
        settings.default_receivable_account
        if locked.withholding_tax.party_type == "customer"
        else settings.default_payable_account
    )
    if partner_account is None:
        raise ValidationError("Configure the receivable/payable account before validating withholding tax.")
    journal = _general_journal(locked.company_id)
    entry = JournalEntry.objects.create(
        company_id=locked.company_id, journal=journal, number=f"WHT-{locked.number}",
        entry_date=locked.transaction_date, reference=locked.reference or locked.number,
        description=f"Withholding tax {locked.number}", source_type="withholding_tax", source_reference=locked.number,
    )
    customer = locked.withholding_tax.party_type == "customer"
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=tax_account, partner=locked.partner,
                    debit=locked.tax_amount if customer else Decimal("0"),
                    credit=locked.tax_amount if not customer else Decimal("0")),
        JournalLine(entry=entry, account=partner_account, partner=locked.partner,
                    debit=locked.tax_amount if not customer else Decimal("0"),
                    credit=locked.tax_amount if customer else Decimal("0")),
    ])
    entry = post_journal_entry(entry, actor)
    locked.status = WithholdingTaxTransaction.Status.VALIDATED
    locked.journal_entry = entry
    locked.save(update_fields=["status", "journal_entry", "updated_at"])
    WithholdingTaxHistory.objects.create(
        transaction=locked, action="validate", previous_status="pending", new_status="validated", actor=actor
    )
    _audit(locked, "withholding_tax_validated", actor, {"journal_entry_id": entry.pk})
    return locked


@transaction.atomic
def reverse_withholding_tax(transaction_record, actor=None):
    locked = WithholdingTaxTransaction.objects.select_for_update().get(pk=transaction_record.pk)
    if locked.status != WithholdingTaxTransaction.Status.VALIDATED or locked.journal_entry_id is None:
        raise ValidationError("Only validated withholding tax transactions can be reversed.")
    source_entry = JournalEntry.objects.get(pk=locked.journal_entry_id)
    reverse_withholding_entry = reverse_journal_entry(source_entry, actor)
    locked.status = WithholdingTaxTransaction.Status.REVERSED
    locked.save(update_fields=["status", "updated_at"])
    WithholdingTaxHistory.objects.create(
        transaction=locked, action="reverse", previous_status="validated", new_status="reversed", actor=actor,
        reason=f"Reversal entry {reverse_withholding_entry.number}",
    )
    _audit(locked, "withholding_tax_reversed", actor, {"reversal_entry_id": reverse_withholding_entry.pk})
    return locked


@transaction.atomic
def reconcile_statement_line(statement_line, journal_line, actor, reference=None):
    statement_line = BankStatementLine.objects.select_for_update().select_related("statement").get(pk=statement_line.pk)
    journal_line = JournalLine.objects.select_for_update().select_related("entry").get(pk=journal_line.pk)
    if statement_line.statement.company_id != journal_line.entry.company_id:
        raise ValidationError("Bank and journal lines must belong to the same company.")
    if statement_line.reconciled or journal_line.reconciled:
        raise ValidationError("One of the selected lines is already reconciled.")
    journal_amount = journal_line.debit - journal_line.credit
    if abs(statement_line.amount) != abs(journal_amount):
        raise ValidationError("Bank and journal line amounts must match.")
    statement_line.reconciled = True
    statement_line.matched_journal_line = journal_line
    statement_line.save(update_fields=["reconciled", "matched_journal_line", "updated_at"])
    journal_line.reconciled = True
    journal_line.save(update_fields=["reconciled", "updated_at"])
    history = ReconciliationHistory.objects.create(
        company_id=statement_line.statement.company_id,
        reference=reference or f"REC-{statement_line.pk}-{journal_line.pk}",
        journal_line=journal_line,
        bank_statement_line=statement_line,
        amount=abs(statement_line.amount),
        actor=actor,
    )
    _audit(statement_line.statement, "bank_line_reconciled", actor, {"history_id": history.pk})
    return history


@transaction.atomic
def unreconcile(history, actor=None):
    history = ReconciliationHistory.objects.select_for_update().get(pk=history.pk)
    if history.reversed:
        raise ValidationError("This reconciliation has already been reversed.")
    JournalLine.objects.filter(pk=history.journal_line_id).update(reconciled=False)
    if history.bank_statement_line_id:
        BankStatementLine.objects.filter(pk=history.bank_statement_line_id).update(
            reconciled=False, matched_journal_line=None
        )
    history.reversed = True
    history.save(update_fields=["reversed", "updated_at"])
    if history.bank_statement_line_id:
        statement = BankStatementLine.objects.select_related("statement").get(pk=history.bank_statement_line_id).statement
        _audit(statement, "bank_line_unreconciled", actor, {"history_id": history.pk})
    return history


@transaction.atomic
def generate_closing_entry(closing_entry, actor=None):
    locked = ClosingEntry.objects.select_for_update().select_related("fiscal_period__fiscal_year").get(pk=closing_entry.pk)
    if locked.status != ClosingEntry.Status.DRAFT:
        raise ValidationError("Only draft closing entries can be generated.")
    if locked.closing_type == ClosingEntry.ClosingType.OPENING:
        if not locked.lines.exists():
            raise ValidationError("Add opening balance lines before generating.")
    else:
        if locked.lines.exists():
            raise ValidationError("This draft already contains closing lines; review them before generating again.")
        balances = list(
            JournalLine.objects.filter(
                entry__company_id=locked.company_id,
                entry__status=JournalEntry.Status.POSTED,
                entry__entry_date__lte=locked.closing_date,
                account__account_type__in=[Account.AccountType.INCOME, Account.AccountType.EXPENSE],
            )
            .values("account_id")
            .annotate(debit=Sum("debit"), credit=Sum("credit"))
        )
        from .models import OpeningBalanceLine

        OpeningBalanceLine.objects.bulk_create([
            OpeningBalanceLine(
                closing_entry=locked,
                account_id=row["account_id"],
                debit=max((row["credit"] or 0) - (row["debit"] or 0), Decimal("0")),
                credit=max((row["debit"] or 0) - (row["credit"] or 0), Decimal("0")),
                description="Period closing",
            )
            for row in balances
            if (row["debit"] or 0) != (row["credit"] or 0)
        ])
    locked.status = ClosingEntry.Status.GENERATED
    locked.save(update_fields=["status", "updated_at"])
    _audit(locked, "closing_entry_generated", actor)
    return locked


def _reconciliation_condition_matches(condition, values):
    current = values.get(condition.field_name, "")
    expected = condition.value
    if condition.field_name == "amount" or condition.operator in {"greater_than", "less_than"}:
        try:
            current_decimal = Decimal(str(current))
            expected_decimal = Decimal(str(expected))
        except (ArithmeticError, ValueError):
            return False
        if condition.operator == "greater_than":
            return current_decimal > expected_decimal
        if condition.operator == "less_than":
            return current_decimal < expected_decimal
        return current_decimal == expected_decimal
    current_text = str(current).casefold()
    expected_text = str(expected).casefold()
    if condition.operator == "contains":
        return expected_text in current_text
    if condition.operator == "starts_with":
        return current_text.startswith(expected_text)
    return current_text == expected_text


@transaction.atomic
def run_reconciliation_rule_test(test_run, actor=None):
    locked = ReconciliationRuleTestRun.objects.select_for_update().select_related("statement_line").get(pk=test_run.pk)
    if locked.status != ReconciliationRuleTestRun.Status.DRAFT:
        raise ValidationError("Only a draft reconciliation rule test can be run.")
    if locked.statement_line_id:
        locked.reference = locked.statement_line.reference
        locked.partner_name = locked.statement_line.partner_name
        locked.description = locked.statement_line.description
        locked.amount = locked.statement_line.amount
    values = {
        "reference": locked.reference,
        "partner_name": locked.partner_name,
        "description": locked.description,
        "amount": locked.amount,
    }
    rules = list(
        ReconciliationRule.objects.filter(company_id=locked.company_id, active=True)
        .prefetch_related("conditions", "counterpart_lines__account")
        .order_by("sequence", "id")
    )
    matched = None
    for rule in rules:
        if rule.match_type == ReconciliationRule.MatchType.LABEL:
            base_match = rule.match_value.casefold() in locked.description.casefold()
        elif rule.match_type == ReconciliationRule.MatchType.AMOUNT:
            try:
                base_match = abs(locked.amount) == Decimal(rule.match_value)
            except (ArithmeticError, ValueError):
                base_match = False
        else:
            base_match = rule.match_value.casefold() in locked.partner_name.casefold()
        if base_match and all(_reconciliation_condition_matches(condition, values) for condition in rule.conditions.all()):
            matched = rule
            break
    if matched is None:
        locked.status = ReconciliationRuleTestRun.Status.UNMATCHED
        locked.result = {"matched": False, "input": {key: str(value) for key, value in values.items()}}
    else:
        remaining = abs(locked.amount)
        proposed_lines = []
        for counterpart in matched.counterpart_lines.all():
            if counterpart.amount_type == "fixed":
                amount = counterpart.amount
            elif counterpart.amount_type == "percent":
                amount = (abs(locked.amount) * counterpart.amount / Decimal("100")).quantize(Decimal("0.01"))
            else:
                amount = remaining
            amount = min(amount, remaining)
            remaining -= amount
            proposed_lines.append({
                "account_id": counterpart.account_id,
                "account": str(counterpart.account),
                "amount": str(amount),
                "label": counterpart.label,
            })
        locked.matched_rule = matched
        locked.status = ReconciliationRuleTestRun.Status.MATCHED
        locked.result = {
            "matched": True,
            "rule_id": matched.pk,
            "rule": matched.name,
            "counterpart_lines": proposed_lines,
            "unallocated_amount": str(remaining),
        }
    locked.save(update_fields=[
        "reference", "partner_name", "description", "amount", "matched_rule", "status", "result", "updated_at"
    ])
    _audit_for_company(locked.company_id, locked, "reconciliation_rule_tested", actor, locked.result)
    return locked


@transaction.atomic
def post_closing_entry(closing_entry, actor=None):
    locked = ClosingEntry.objects.select_for_update().select_related("journal").get(pk=closing_entry.pk)
    if locked.status != ClosingEntry.Status.GENERATED:
        raise ValidationError("Generate the closing or opening entry before posting.")
    rows = list(locked.lines.select_related("account", "partner"))
    debit = sum((row.debit for row in rows), Decimal("0"))
    credit = sum((row.credit for row in rows), Decimal("0"))
    if debit != credit or debit <= 0:
        raise ValidationError("Closing/opening lines must have equal non-zero debit and credit totals.")
    entry = JournalEntry.objects.create(
        company_id=locked.company_id, journal=locked.journal, number=f"CLS-{locked.number}",
        entry_date=locked.closing_date, description=locked.note[:240],
        source_type="closing_entry", source_reference=locked.number,
    )
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=row.account, partner=row.partner, description=row.description,
                    debit=row.debit, credit=row.credit)
        for row in rows
    ])
    entry = post_journal_entry(entry, actor)
    locked.status = ClosingEntry.Status.POSTED
    locked.journal_entry = entry
    locked.save(update_fields=["status", "journal_entry", "updated_at"])
    _audit(locked, "closing_entry_posted", actor, {"journal_entry_id": entry.pk})
    return locked


@transaction.atomic
def post_currency_revaluation(revaluation, actor=None):
    locked = CurrencyRevaluation.objects.select_for_update().get(pk=revaluation.pk)
    if locked.status != CurrencyRevaluation.Status.DRAFT:
        raise ValidationError("Only draft currency revaluations can be posted.")
    _assert_posting_date_is_open(locked.company_id, locked.revaluation_date)
    rows = list(locked.lines.select_related("account", "partner"))
    if not rows:
        raise ValidationError("Add at least one currency revaluation line before posting.")
    settings = AccountingSettings.objects.select_related("exchange_gain_account", "exchange_loss_account").get(
        company_id=locked.company_id
    )
    if settings.exchange_gain_account_id is None or settings.exchange_loss_account_id is None:
        raise ValidationError("Configure exchange gain and exchange loss accounts before revaluation.")
    journal = _general_journal(locked.company_id)
    entry = JournalEntry.objects.create(
        company_id=locked.company_id, journal=journal, number=f"FX-{locked.number}",
        entry_date=locked.revaluation_date, description=f"Currency revaluation {locked.currency}",
        currency=locked.currency, exchange_rate=locked.rate,
        source_type="currency_revaluation", source_reference=locked.number,
    )
    journal_lines = []
    gain = Decimal("0")
    loss = Decimal("0")
    for row in rows:
        amount = row.gain_loss_amount
        if amount > 0:
            gain += amount
            journal_lines.extend([
                JournalLine(entry=entry, account=row.account, partner=row.partner, debit=amount),
                JournalLine(entry=entry, account=settings.exchange_gain_account, partner=row.partner, credit=amount),
            ])
        elif amount < 0:
            absolute = abs(amount)
            loss += absolute
            journal_lines.extend([
                JournalLine(entry=entry, account=settings.exchange_loss_account, partner=row.partner, debit=absolute),
                JournalLine(entry=entry, account=row.account, partner=row.partner, credit=absolute),
            ])
    if not journal_lines:
        raise ValidationError("Currency revaluation lines do not contain a gain or loss.")
    JournalLine.objects.bulk_create(journal_lines)
    entry = post_journal_entry(entry, actor)
    locked.unrealized_gain = gain
    locked.unrealized_loss = loss
    locked.journal_entry = entry
    locked.status = CurrencyRevaluation.Status.POSTED
    locked.save(update_fields=["unrealized_gain", "unrealized_loss", "journal_entry", "status", "updated_at"])
    _audit(locked, "currency_revaluation_posted", actor, {"journal_entry_id": entry.pk})
    return locked


def _next_recurring_date(value, frequency):
    months = {RecurringJournal.Frequency.MONTHLY: 1, RecurringJournal.Frequency.QUARTERLY: 3}.get(frequency, 12)
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _create_recurring_entry(schedule, actor=None):
    scheduled_date = schedule.next_run_date
    template_lines = list(schedule.template_entry.lines.all())
    if not template_lines:
        raise ValidationError("The recurring journal template does not contain journal items.")
    entry = JournalEntry.objects.create(
        company_id=schedule.company_id,
        journal_id=schedule.template_entry.journal_id,
        number=f"REC-{schedule.pk}-{schedule.next_run_date:%Y%m%d}",
        entry_date=schedule.next_run_date,
        reference=schedule.template_entry.reference,
        description=schedule.template_entry.description,
        currency=schedule.template_entry.currency,
        exchange_rate=schedule.template_entry.exchange_rate,
        source_type="recurring_journal",
        source_reference=str(schedule.pk),
    )
    JournalLine.objects.bulk_create([
        JournalLine(
            entry=entry, account_id=line.account_id, partner_id=line.partner_id, cost_center_id=line.cost_center_id,
            description=line.description, debit=line.debit, credit=line.credit,
            currency=line.currency, amount_currency=line.amount_currency, due_date=line.due_date,
        )
        for line in template_lines
    ])
    if schedule.auto_post:
        entry = post_journal_entry(entry, actor)
    RecurringJournalExecution.objects.create(
        schedule=schedule,
        scheduled_date=scheduled_date,
        journal_entry=entry,
        status="posted" if entry.status == JournalEntry.Status.POSTED else "generated",
    )
    schedule.next_run_date = _next_recurring_date(schedule.next_run_date, schedule.frequency)
    schedule.save(update_fields=["next_run_date", "updated_at"])
    _audit(schedule, "recurring_journal_generated", actor, {"journal_entry_id": entry.pk})
    return entry


@transaction.atomic
def run_recurring_journal(schedule, actor=None):
    schedule = (
        RecurringJournal.objects.select_for_update()
        .prefetch_related("template_entry__lines")
        .get(pk=schedule.pk)
    )
    if not schedule.active:
        raise ValidationError("Only active recurring journals can be run.")
    if schedule.end_date and schedule.next_run_date > schedule.end_date:
        raise ValidationError("The recurring journal schedule has ended.")
    _create_recurring_entry(schedule, actor)
    return schedule


@transaction.atomic
def run_due_recurring_journals(run_date=None, actor=None):
    run_date = run_date or timezone.localdate()
    schedules = list(
        RecurringJournal.objects.select_for_update()
        .prefetch_related("template_entry__lines")
        .filter(active=True, next_run_date__lte=run_date)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=run_date))
    )
    entries = []
    # scale-guard: allow SG001 — each due schedule creates and optionally posts a distinct audited journal lifecycle
    for schedule in schedules:
        entries.append(_create_recurring_entry(schedule, actor))
    return entries


@transaction.atomic
def apply_deposit(application, actor=None):
    locked = DepositApplication.objects.select_for_update().select_related(
        "deposit__journal", "deposit__partner", "invoice"
    ).get(pk=application.pk)
    if locked.reversed or locked.journal_entry_id:
        raise ValidationError("This deposit application has already been processed.")
    deposit = locked.deposit
    invoice = locked.invoice
    expected_invoice_type = (
        invoice.InvoiceType.CUSTOMER
        if deposit.deposit_type == AccountDeposit.DepositType.CUSTOMER
        else invoice.InvoiceType.VENDOR
    )
    if invoice.invoice_type != expected_invoice_type:
        raise ValidationError("Customer deposits can only settle customer invoices and vendor deposits can only settle vendor bills.")
    if locked.amount > deposit.unapplied_amount or locked.amount > invoice.balance:
        raise ValidationError("Application cannot exceed the remaining deposit or invoice balance.")
    settings = AccountingSettings.objects.select_related("default_receivable_account", "default_payable_account").get(
        company_id=deposit.company_id
    )
    partner_account = (
        settings.default_receivable_account
        if invoice.invoice_type == invoice.InvoiceType.CUSTOMER
        else settings.default_payable_account
    )
    deposit_account = deposit.journal.default_credit_account
    if not partner_account or not deposit_account:
        raise ValidationError("Configure deposit clearing and partner accounts before application.")
    entry = JournalEntry.objects.create(
        company_id=deposit.company_id, journal=deposit.journal,
        number=f"DAPP-{deposit.number}-{locked.pk}", entry_date=locked.application_date,
        reference=invoice.number, description=f"Apply deposit {deposit.number}",
        source_type="deposit_application", source_reference=str(locked.pk),
    )
    customer = invoice.invoice_type == invoice.InvoiceType.CUSTOMER
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=deposit_account, partner=deposit.partner,
                    debit=locked.amount if customer else Decimal("0"), credit=locked.amount if not customer else Decimal("0")),
        JournalLine(entry=entry, account=partner_account, partner=deposit.partner,
                    debit=locked.amount if not customer else Decimal("0"), credit=locked.amount if customer else Decimal("0")),
    ])
    entry = post_journal_entry(entry, actor)
    deposit.unapplied_amount -= locked.amount
    deposit.status = AccountDeposit.Status.APPLIED if deposit.unapplied_amount == 0 else AccountDeposit.Status.RECEIVED
    deposit.save(update_fields=["unapplied_amount", "status", "updated_at"])
    invoice.paid_amount += locked.amount
    if invoice.paid_amount >= invoice.total:
        invoice.status = invoice.Status.PAID
    invoice.save(update_fields=["paid_amount", "status", "updated_at"])
    locked.journal_entry = entry
    locked.status = DepositApplication.Status.APPLIED
    locked.save(update_fields=["journal_entry", "status", "updated_at"])
    _audit(deposit, "deposit_applied", actor, {"application_id": locked.pk, "invoice_id": invoice.pk})
    return locked


@transaction.atomic
def apply_prepayment(application, actor=None):
    locked = PrepaymentApplication.objects.select_for_update().select_related(
        "prepayment__account", "prepayment__partner", "invoice"
    ).get(pk=application.pk)
    if locked.reversed or locked.journal_entry_id:
        raise ValidationError("This prepayment application has already been processed.")
    prepayment = locked.prepayment
    invoice = locked.invoice
    if locked.amount > prepayment.remaining_amount or locked.amount > invoice.balance:
        raise ValidationError("Application cannot exceed the remaining prepayment or invoice balance.")
    settings = AccountingSettings.objects.select_related("default_receivable_account", "default_payable_account").get(
        company_id=prepayment.company_id
    )
    partner_account = (
        settings.default_receivable_account
        if invoice.invoice_type == invoice.InvoiceType.CUSTOMER
        else settings.default_payable_account
    )
    if partner_account is None:
        raise ValidationError("Configure the receivable/payable account before applying a prepayment.")
    journal = _general_journal(prepayment.company_id)
    entry = JournalEntry.objects.create(
        company_id=prepayment.company_id, journal=journal,
        number=f"PAPP-{prepayment.number}-{locked.pk}", entry_date=locked.application_date,
        reference=invoice.number, description=f"Apply prepayment {prepayment.number}",
        source_type="prepayment_application", source_reference=str(locked.pk),
    )
    customer = invoice.invoice_type == invoice.InvoiceType.CUSTOMER
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=prepayment.account, partner=prepayment.partner,
                    debit=locked.amount if customer else Decimal("0"), credit=locked.amount if not customer else Decimal("0")),
        JournalLine(entry=entry, account=partner_account, partner=prepayment.partner,
                    debit=locked.amount if not customer else Decimal("0"), credit=locked.amount if customer else Decimal("0")),
    ])
    entry = post_journal_entry(entry, actor)
    prepayment.applied_amount += locked.amount
    prepayment.status = Prepayment.Status.FULLY_APPLIED if prepayment.remaining_amount == 0 else Prepayment.Status.ACTIVE
    prepayment.save(update_fields=["applied_amount", "status", "updated_at"])
    invoice.paid_amount += locked.amount
    if invoice.paid_amount >= invoice.total:
        invoice.status = invoice.Status.PAID
    invoice.save(update_fields=["paid_amount", "status", "updated_at"])
    locked.journal_entry = entry
    locked.status = PrepaymentApplication.Status.APPLIED
    locked.save(update_fields=["journal_entry", "status", "updated_at"])
    _audit(prepayment, "prepayment_applied", actor, {"application_id": locked.pk, "invoice_id": invoice.pk})
    return locked


def _restore_invoice_after_application(invoice, amount):
    invoice.paid_amount = max(Decimal("0"), invoice.paid_amount - amount)
    if invoice.status == invoice.Status.PAID and invoice.paid_amount < invoice.total:
        invoice.status = invoice.Status.POSTED
    invoice.save(update_fields=["paid_amount", "status", "updated_at"])


@transaction.atomic
def reverse_deposit_application(application, actor=None):
    locked = DepositApplication.objects.select_for_update().select_related(
        "deposit", "invoice", "journal_entry"
    ).get(pk=application.pk)
    if locked.reversed or locked.journal_entry_id is None:
        raise ValidationError("Only a processed, unreversed deposit application can be reversed.")
    reverse_journal_entry(locked.journal_entry, actor)
    deposit = AccountDeposit.objects.select_for_update().get(pk=locked.deposit_id)
    invoice = locked.invoice
    deposit.unapplied_amount += locked.amount
    deposit.status = AccountDeposit.Status.RECEIVED
    deposit.save(update_fields=["unapplied_amount", "status", "updated_at"])
    _restore_invoice_after_application(invoice, locked.amount)
    locked.reversed = True
    locked.status = DepositApplication.Status.REVERSED
    locked.save(update_fields=["reversed", "status", "updated_at"])
    _audit(deposit, "deposit_application_reversed", actor, {"application_id": locked.pk, "invoice_id": invoice.pk})
    return locked


@transaction.atomic
def reverse_prepayment_application(application, actor=None):
    locked = PrepaymentApplication.objects.select_for_update().select_related(
        "prepayment", "invoice", "journal_entry"
    ).get(pk=application.pk)
    if locked.reversed or locked.journal_entry_id is None:
        raise ValidationError("Only a processed, unreversed prepayment application can be reversed.")
    reverse_journal_entry(locked.journal_entry, actor)
    prepayment = Prepayment.objects.select_for_update().get(pk=locked.prepayment_id)
    invoice = locked.invoice
    prepayment.applied_amount = max(Decimal("0"), prepayment.applied_amount - locked.amount)
    prepayment.status = Prepayment.Status.ACTIVE
    prepayment.save(update_fields=["applied_amount", "status", "updated_at"])
    _restore_invoice_after_application(invoice, locked.amount)
    locked.reversed = True
    locked.status = PrepaymentApplication.Status.REVERSED
    locked.save(update_fields=["reversed", "status", "updated_at"])
    _audit(prepayment, "prepayment_application_reversed", actor, {"application_id": locked.pk, "invoice_id": invoice.pk})
    return locked


@transaction.atomic
def post_asset_depreciation(depreciation, actor=None):
    locked = AssetDepreciationLine.objects.select_for_update().select_related("asset").get(pk=depreciation.pk)
    if locked.status != AssetDepreciationLine.Status.DRAFT:
        raise ValidationError("Only draft depreciation lines can be posted.")
    config = AssetAccountingSettings.objects.select_related(
        "journal", "depreciation_expense_account", "accumulated_depreciation_account"
    ).get(company_id=locked.asset.company_id)
    entry = JournalEntry.objects.create(
        company_id=locked.asset.company_id, journal=config.journal,
        number=f"DEP-{locked.asset.code}-{locked.period_date:%Y%m%d}", entry_date=locked.period_date,
        description=f"Depreciation {locked.asset}", source_type="asset_depreciation", source_reference=str(locked.pk),
    )
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=config.depreciation_expense_account, debit=locked.depreciation_amount),
        JournalLine(entry=entry, account=config.accumulated_depreciation_account, credit=locked.depreciation_amount),
    ])
    entry = post_journal_entry(entry, actor)
    locked.status = AssetDepreciationLine.Status.POSTED
    locked.journal_entry = entry
    locked.save(update_fields=["status", "journal_entry", "updated_at"])
    _audit_for_company(locked.asset.company_id, locked, "asset_depreciation_posted", actor, {"journal_entry_id": entry.pk})
    return locked


@transaction.atomic
def approve_asset_capitalization(capitalization, actor=None):
    locked = AssetCapitalization.objects.select_for_update().get(pk=capitalization.pk)
    if locked.status != AssetCapitalization.Status.WAITING_APPROVAL:
        raise ValidationError("Only a capitalization waiting for approval can be approved.")
    locked.status = AssetCapitalization.Status.CONFIRMED
    locked.approved_by = actor
    locked.save(update_fields=["status", "approved_by", "updated_at"])
    _audit_for_company(locked.company_id, locked, "asset_capitalization_approved", actor)
    return locked


@transaction.atomic
def capitalize_assets(capitalization, actor=None):
    locked = AssetCapitalization.objects.select_for_update().select_related(
        "company", "product", "cost_center", "cip_clearing_account", "fixed_asset_account"
    ).get(pk=capitalization.pk)
    if locked.status != AssetCapitalization.Status.CONFIRMED:
        raise ValidationError("Assets can only be created from a confirmed capitalization.")
    source_lines = list(locked.lines.all())
    if not source_lines:
        raise ValidationError("Add at least one CIP clearing line before capitalization.")
    total = sum((line.amount for line in source_lines), Decimal("0"))
    if total <= 0:
        raise ValidationError("Total CIP clearing must be greater than zero.")
    if locked.capitalization_threshold <= 0 or total > locked.capitalization_threshold:
        raise ValidationError("Total CIP clearing cannot exceed the configured capitalization threshold.")
    if locked.estimated_budget <= 0 or total > locked.estimated_budget:
        raise ValidationError("Total CIP clearing cannot exceed the estimated budget.")
    if locked.estimated_budget > locked.capitalization_threshold:
        raise ValidationError("Estimated budget cannot exceed the capitalization threshold.")
    if locked.allocation_mode == "manual":
        allocation_rows = list(locked.allocations.all())
        if len(allocation_rows) != locked.asset_count:
            raise ValidationError("Manual allocation requires one allocation row per asset.")
        if sum((row.acquisition_value for row in allocation_rows), Decimal("0")) != total:
            raise ValidationError("Manual asset allocation must equal total CIP clearing.")
        asset_values = [
            {
                "code": row.asset_code,
                "name": row.asset_name,
                "acquisition_value": row.acquisition_value,
                "salvage_value": row.salvage_value,
            }
            for row in allocation_rows
        ]
    else:
        per_asset = (total / locked.asset_count).quantize(Decimal("0.01"))
        asset_values = []
        allocated = Decimal("0")
        for sequence in range(1, locked.asset_count + 1):
            acquisition_value = total - allocated if sequence == locked.asset_count else per_asset
            allocated += acquisition_value
            asset_values.append({
                "code": f"{locked.number}-{sequence:03d}"[:40],
                "name": locked.name if locked.asset_count == 1 else f"{locked.name} {sequence}",
                "acquisition_value": acquisition_value,
                "salvage_value": locked.salvage_value,
            })
    asset_codes = {values["code"] for values in asset_values}
    if len(asset_codes) != len(asset_values):
        raise ValidationError("Generated asset codes are not unique; shorten the capitalization number.")
    Asset = apps.get_model("gsshop_operations", "Asset")
    if Asset.objects.filter(company_id=locked.company_id, code__in=asset_codes).exists():
        raise ValidationError("One or more capitalization asset codes already exist.")
    settings = AssetAccountingSettings.objects.select_related("journal").get(company_id=locked.company_id)
    entry = JournalEntry.objects.create(
        company_id=locked.company_id,
        journal=settings.journal,
        number=f"CIP-{locked.number}",
        entry_date=locked.end_date or timezone.localdate(),
        description=locked.description[:240] or locked.name,
        source_type="asset_capitalization",
        source_reference=str(locked.pk),
    )
    JournalLine.objects.bulk_create([
        JournalLine(
            entry=entry, account=locked.fixed_asset_account, cost_center=locked.cost_center,
            description=locked.name, debit=total,
        ),
        JournalLine(
            entry=entry, account=locked.cip_clearing_account, cost_center=locked.cost_center,
            description=locked.name, credit=total,
        ),
    ])
    entry = post_journal_entry(entry, actor)
    Asset.objects.bulk_create([
        Asset(
            company_id=locked.company_id,
            code=values["code"],
            name=values["name"],
            product_id=locked.product_id,
            purchase_date=locked.end_date or timezone.localdate(),
            acquisition_value=values["acquisition_value"],
            assigned_employee_id=locked.responsible_employee_id,
            status=Asset.Status.AVAILABLE,
        )
        for values in asset_values
    ])
    created_assets = {asset.code: asset for asset in Asset.objects.filter(company_id=locked.company_id, code__in=asset_codes)}
    AssetCapitalizationAsset.objects.bulk_create([
        AssetCapitalizationAsset(
            capitalization=locked,
            asset=created_assets[values["code"]],
            allocated_amount=values["acquisition_value"],
            salvage_value=values["salvage_value"],
            useful_life_years=locked.useful_life_years,
        )
        for values in asset_values
    ])
    locked.total_amount = total
    locked.journal_entry = entry
    locked.status = AssetCapitalization.Status.CAPITALIZED
    locked.save(update_fields=["total_amount", "journal_entry", "status", "updated_at"])
    _audit_for_company(
        locked.company_id,
        locked,
        "assets_capitalized",
        actor,
        {"journal_entry_id": entry.pk, "asset_ids": [asset.pk for asset in created_assets.values()]},
    )
    return locked


def _audit_for_company(company_id, instance, event_type, actor=None, details=None):
    AccountingAuditEvent.objects.create(
        company_id=company_id, event_type=event_type, object_type=instance._meta.label_lower,
        object_id=instance.pk, reference=str(instance), actor=actor, details=details or {},
    )


@transaction.atomic
def post_asset_revaluation(revaluation, actor=None):
    locked = AssetRevaluation.objects.select_for_update().select_related("asset").get(pk=revaluation.pk)
    if locked.status not in {AssetRevaluation.Status.DRAFT, AssetRevaluation.Status.APPROVED}:
        raise ValidationError("Only draft or approved asset revaluations can be posted.")
    if locked.gain_loss_amount == 0:
        raise ValidationError("Asset revaluation gain/loss cannot be zero.")
    config = AssetAccountingSettings.objects.select_related(
        "journal", "fixed_asset_account", "disposal_gain_account", "disposal_loss_account"
    ).get(company_id=locked.asset.company_id)
    amount = abs(locked.gain_loss_amount)
    gain = locked.gain_loss_amount > 0
    entry = JournalEntry.objects.create(
        company_id=locked.asset.company_id, journal=config.journal, number=f"ARV-{locked.number}",
        entry_date=locked.revaluation_date, description=locked.note[:240],
        source_type="asset_revaluation", source_reference=locked.number,
    )
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=config.fixed_asset_account,
                    debit=amount if gain else Decimal("0"), credit=amount if not gain else Decimal("0")),
        JournalLine(entry=entry, account=config.disposal_gain_account if gain else config.disposal_loss_account,
                    debit=amount if not gain else Decimal("0"), credit=amount if gain else Decimal("0")),
    ])
    entry = post_journal_entry(entry, actor)
    locked.status = AssetRevaluation.Status.POSTED
    locked.journal_entry = entry
    locked.save(update_fields=["status", "journal_entry", "updated_at"])
    _audit_for_company(locked.asset.company_id, locked, "asset_revaluation_posted", actor, {"journal_entry_id": entry.pk})
    return locked


@transaction.atomic
def _create_asset_disposal_invoice_atomic(disposal, actor=None):
    from core.models import Invoice, InvoiceLine

    locked = AssetDisposal.objects.select_for_update().select_related("asset", "partner").get(pk=disposal.pk)
    if locked.disposal_type != AssetDisposal.DisposalType.SOLD:
        raise ValidationError("Only a sold asset disposal creates a customer invoice.")
    if locked.status != AssetDisposal.Status.APPROVED:
        raise ValidationError("Approve the asset disposal before creating its invoice.")
    if locked.customer_invoice_id:
        raise ValidationError("A customer invoice has already been created for this asset disposal.")
    if locked.partner_id is None:
        raise ValidationError("Select the customer before creating the asset disposal invoice.")
    if locked.proceeds <= 0:
        raise ValidationError("Asset sale proceeds must be greater than zero.")
    if locked.disposal_date > timezone.localdate():
        raise ValidationError("Asset sale accounting cannot be finalized before its disposal date.")
    invoice = Invoice.objects.create(
        company_id=locked.asset.company_id,
        number=f"AS-{locked.number}",
        invoice_type=Invoice.InvoiceType.CUSTOMER,
        partner=locked.partner,
        invoice_date=locked.disposal_date,
        due_date=locked.disposal_date,
        notes=locked.note or f"Asset sale {locked.asset.code}",
    )
    InvoiceLine.objects.create(
        invoice=invoice,
        description=f"{locked.asset.code} - {locked.asset.name}",
        quantity=Decimal("1"),
        unit_price=locked.proceeds,
    )
    locked.customer_invoice = invoice
    locked.accounting_status = AssetDisposal.AccountingStatus.INVOICE_DRAFT
    locked.accounting_error_message = ""
    locked.save(update_fields=["customer_invoice", "accounting_status", "accounting_error_message", "updated_at"])
    _audit_for_company(
        locked.asset.company_id,
        locked,
        "asset_disposal_invoice_created",
        actor,
        {"invoice_id": invoice.pk},
    )
    return locked


def create_asset_disposal_invoice(disposal, actor=None):
    try:
        return _create_asset_disposal_invoice_atomic(disposal, actor)
    except Exception as exc:
        AssetDisposal.objects.filter(pk=disposal.pk).update(
            accounting_status=AssetDisposal.AccountingStatus.ACCOUNTING_ERROR,
            accounting_error_message=str(exc)[:2000],
        )
        raise


@transaction.atomic
def post_asset_disposal(disposal, actor=None):
    locked = AssetDisposal.objects.select_for_update(of=("self",)).select_related(
        "asset", "category_accounting_rule__disposal_journal",
        "category_accounting_rule__fixed_asset_account",
        "category_accounting_rule__accumulated_depreciation_account",
        "category_accounting_rule__disposal_gain_account",
        "category_accounting_rule__disposal_loss_account",
    ).get(pk=disposal.pk)
    legacy_direct_sale = (
        locked.disposal_type == AssetDisposal.DisposalType.SOLD
        and locked.partner_id is None
        and locked.customer_invoice_id is None
    )
    if locked.disposal_type != AssetDisposal.DisposalType.SCRAPPED and not legacy_direct_sale:
        raise ValidationError("Sold assets are posted through their customer invoice.")
    if locked.status != AssetDisposal.Status.APPROVED and not (
        legacy_direct_sale and locked.status == AssetDisposal.Status.DRAFT
    ):
        raise ValidationError("Only approved asset disposals can be posted.")
    if locked.disposal_type == AssetDisposal.DisposalType.SCRAPPED and locked.proceeds:
        raise ValidationError("A scrapped asset cannot have sale proceeds.")
    if locked.disposal_date > timezone.localdate():
        raise ValidationError("Asset disposal accounting cannot be finalized before its disposal date.")
    config = AssetAccountingSettings.objects.select_related(
        "journal", "fixed_asset_account", "accumulated_depreciation_account",
        "disposal_gain_account", "disposal_loss_account", "disposal_clearing_account"
    ).get(company_id=locked.asset.company_id)
    category = locked.category_accounting_rule
    disposal_journal = category.disposal_journal if category and category.disposal_journal_id else config.journal
    fixed_asset_account = (
        category.fixed_asset_account if category and category.fixed_asset_account_id else config.fixed_asset_account
    )
    accumulated_account = (
        category.accumulated_depreciation_account
        if category and category.accumulated_depreciation_account_id
        else config.accumulated_depreciation_account
    )
    loss_account = (
        category.disposal_loss_account if category and category.disposal_loss_account_id else config.disposal_loss_account
    )
    gain_account = (
        category.disposal_gain_account if category and category.disposal_gain_account_id else config.disposal_gain_account
    )
    asset_cost = locked.asset.acquisition_value
    if asset_cost <= 0:
        raise ValidationError("The disposed asset must have a positive acquisition value.")
    if locked.book_value < 0 or locked.book_value > asset_cost:
        raise ValidationError("Asset book value must be between zero and acquisition value.")
    accumulated = asset_cost - locked.book_value
    gain_loss = locked.proceeds - locked.book_value
    if locked.gain_loss_amount != gain_loss:
        raise ValidationError("Gain/loss must equal proceeds less book value.")
    entry = JournalEntry.objects.create(
        company_id=locked.asset.company_id, journal=disposal_journal, number=f"ADP-{locked.number}",
        entry_date=locked.disposal_date, description=locked.note[:240],
        source_type="asset_disposal", source_reference=locked.number,
    )
    label = f"Asset {'Sale' if legacy_direct_sale else 'Scrap'}: {locked.asset.code} - {locked.asset.name}"
    journal_lines = [JournalLine(entry=entry, account=fixed_asset_account, credit=asset_cost, description=label)]
    if accumulated:
        journal_lines.append(JournalLine(
            entry=entry,
            account=accumulated_account,
            debit=accumulated,
            description=label,
        ))
    if legacy_direct_sale and locked.proceeds:
        clearing_account = config.disposal_clearing_account or disposal_journal.default_debit_account
        if clearing_account is None:
            raise ValidationError("Configure an asset disposal clearing account or journal debit account.")
        journal_lines.append(JournalLine(
            entry=entry, account=clearing_account, debit=locked.proceeds, description=label,
        ))
    if gain_loss > 0:
        journal_lines.append(JournalLine(entry=entry, account=gain_account, credit=gain_loss, description=label))
    elif gain_loss < 0:
        journal_lines.append(JournalLine(
            entry=entry,
            account=loss_account,
            debit=abs(gain_loss),
            description=label,
        ))
    JournalLine.objects.bulk_create(journal_lines)
    entry = post_journal_entry(entry, actor)
    locked.status = AssetDisposal.Status.POSTED
    locked.accounting_status = AssetDisposal.AccountingStatus.JOURNAL_POSTED
    locked.accounting_error_message = ""
    locked.journal_entry = entry
    locked.save(update_fields=[
        "status", "accounting_status", "accounting_error_message", "journal_entry", "updated_at"
    ])
    locked.asset.status = locked.asset.Status.RETIRED
    locked.asset.save(update_fields=["status", "updated_at"])
    _audit_for_company(locked.asset.company_id, locked, "asset_disposal_posted", actor, {"journal_entry_id": entry.pk})
    return locked


@transaction.atomic
def retry_asset_disposal_accounting(disposal, actor=None):
    locked = AssetDisposal.objects.select_for_update().get(pk=disposal.pk)
    if locked.accounting_status != AssetDisposal.AccountingStatus.ACCOUNTING_ERROR:
        raise ValidationError("Only an asset disposal with an accounting error can be retried.")
    locked.accounting_status = (
        AssetDisposal.AccountingStatus.INVOICE_DRAFT
        if locked.customer_invoice_id
        else AssetDisposal.AccountingStatus.NOT_STARTED
    )
    locked.accounting_error_message = ""
    locked.save(update_fields=["accounting_status", "accounting_error_message", "updated_at"])
    _audit_for_company(locked.asset.company_id, locked, "asset_disposal_accounting_retry", actor)
    return locked


def run_due_asset_disposals(run_date=None, actor=None, batch_size=100):
    """Finalize approved sold/scrapped disposals that have reached their date."""
    run_date = run_date or timezone.localdate()
    batch_size = max(1, min(int(batch_size), 500))
    due = list(
        AssetDisposal.objects.filter(
            status=AssetDisposal.Status.APPROVED,
            disposal_date__lte=run_date,
        ).order_by("disposal_date", "id").values("id", "disposal_type", "customer_invoice_id")[:batch_size]
    )
    result = {"selected": len(due), "posted": 0, "failed": 0, "errors": []}
    # scale-guard: allow SG001 — each due disposal needs an isolated atomic posting and failure record
    for candidate in due:
        try:
            disposal = AssetDisposal.objects.get(pk=candidate["id"])
            if candidate["disposal_type"] == AssetDisposal.DisposalType.SOLD:
                if not candidate["customer_invoice_id"]:
                    disposal = create_asset_disposal_invoice(disposal, actor)
                post_invoice(disposal.customer_invoice, actor)
            else:
                post_asset_disposal(disposal, actor)
            result["posted"] += 1
        except Exception as exc:
            AssetDisposal.objects.filter(pk=candidate["id"]).update(
                accounting_status=AssetDisposal.AccountingStatus.ACCOUNTING_ERROR,
                accounting_error_message=str(exc)[:2000],
                updated_at=timezone.now(),
            )
            result["failed"] += 1
            result["errors"].append({"id": candidate["id"], "error": str(exc)[:500]})
    return result


@transaction.atomic
def reverse_asset_accounting(record, actor=None):
    model = record.__class__
    related = ["asset"]
    if model is AssetDisposal:
        related.append("customer_invoice")
    locked = model.objects.select_for_update(of=("self",)).select_related(*related).get(pk=record.pk)
    if locked.status != "posted" or locked.journal_entry_id is None:
        raise ValidationError("Only posted asset accounting records can be reversed.")
    if isinstance(locked, AssetDisposal) and locked.customer_invoice_id:
        cancel_invoice(locked.customer_invoice, actor)
        locked.refresh_from_db()
        _audit_for_company(locked.asset.company_id, locked, "asset_accounting_reversed", actor)
        return locked
    reverse_journal_entry(locked.journal_entry, actor)
    locked.status = "reversed"
    locked.save(update_fields=["status", "updated_at"])
    if isinstance(locked, AssetDisposal):
        locked.asset.status = locked.asset.Status.AVAILABLE
        locked.asset.save(update_fields=["status", "updated_at"])
    _audit_for_company(locked.asset.company_id, locked, "asset_accounting_reversed", actor)
    return locked


@transaction.atomic
def approve_budget_transaction(budget_transaction, actor=None):
    locked = BudgetTransaction.objects.select_for_update().get(pk=budget_transaction.pk)
    if locked.status not in {BudgetTransaction.Status.DRAFT, BudgetTransaction.Status.SUBMITTED}:
        raise ValidationError("Only draft or submitted budget transactions can be approved.")
    source = Budget.objects.select_for_update().filter(pk=locked.source_budget_id).first() if locked.source_budget_id else None
    destination = Budget.objects.select_for_update().filter(pk=locked.destination_budget_id).first() if locked.destination_budget_id else None
    if locked.transaction_type == BudgetTransaction.TransactionType.TRANSFER:
        if source.remaining < locked.amount:
            raise ValidationError("The source budget does not have enough remaining value.")
        source.amount -= locked.amount
        destination.amount += locked.amount
        Budget.objects.bulk_update([source, destination], ["amount", "updated_at"])
    elif locked.transaction_type == BudgetTransaction.TransactionType.CARRY_OVER:
        if source and source.remaining < locked.amount:
            raise ValidationError("The source budget does not have enough remaining value.")
        if destination is None:
            raise ValidationError("Carry-over requires a destination budget.")
        destination.amount += locked.amount
        destination.save(update_fields=["amount", "updated_at"])
    else:
        target = destination or source
        if target is None:
            raise ValidationError("Select a budget for this allocation or change request.")
        target.amount += locked.amount
        target.save(update_fields=["amount", "updated_at"])
    previous = locked.status
    locked.status = BudgetTransaction.Status.APPROVED
    locked.approved_by = actor
    locked.save(update_fields=["status", "approved_by", "updated_at"])
    BudgetMovementAudit.objects.create(
        transaction_record=locked, action="approve", previous_status=previous,
        new_status=locked.status, actor=actor, details={"amount": str(locked.amount)},
    )
    _audit_for_company(locked.company_id, locked, "budget_transaction_approved", actor, {"amount": str(locked.amount)})
    return locked


def _apply_budget_pairs(lines, *, subtract_source=True):
    budget_ids = {
        budget_id
        for line in lines
        for budget_id in (getattr(line, "source_budget_id", None), getattr(line, "destination_budget_id", None))
        if budget_id
    }
    budgets = {
        budget.pk: budget
        for budget in Budget.objects.select_for_update().filter(pk__in=budget_ids).order_by("pk")
    }
    for line in lines:
        source = budgets.get(getattr(line, "source_budget_id", None))
        destination = budgets.get(getattr(line, "destination_budget_id", None))
        if source is None or destination is None:
            raise ValidationError("Every budget movement line requires source and destination budgets.")
        if source.company_id != destination.company_id:
            raise ValidationError("Source and destination budgets must belong to the same company.")
        if subtract_source:
            if source.amount - source.committed - source.actual < line.amount:
                raise ValidationError(f"Budget {source} does not have enough remaining value.")
            source.amount -= line.amount
        destination.amount += line.amount
    Budget.objects.bulk_update(list(budgets.values()), ["amount", "updated_at"])


@transaction.atomic
def approve_budget_change_request(change_request, actor=None):
    locked = BudgetChangeRequest.objects.select_for_update().get(pk=change_request.pk)
    if locked.status not in {BudgetChangeRequest.Status.DRAFT, BudgetChangeRequest.Status.SUBMITTED}:
        raise ValidationError("Only draft or submitted budget change requests can be approved.")
    lines = list(locked.lines.select_related("budget", "budget_account"))
    if not lines:
        raise ValidationError("Add at least one budget change line before approval.")
    budgets = {
        budget.pk: budget
        for budget in Budget.objects.select_for_update().filter(pk__in={line.budget_id for line in lines}).order_by("pk")
    }
    for line in lines:
        budget = budgets[line.budget_id]
        new_amount = budget.amount + line.amount
        if new_amount < budget.committed + budget.actual or new_amount < 0:
            raise ValidationError(f"Budget {budget} cannot be reduced below committed and actual value.")
        budget.amount = new_amount
    Budget.objects.bulk_update(list(budgets.values()), ["amount", "updated_at"])
    locked.status = BudgetChangeRequest.Status.APPROVED
    locked.approved_by = actor
    locked.save(update_fields=["status", "approved_by", "updated_at"])
    _audit_for_company(locked.company_id, locked, "budget_change_request_approved", actor, {"line_count": len(lines)})
    return locked


@transaction.atomic
def approve_budget_transfer(transfer, actor=None):
    locked = BudgetTransfer.objects.select_for_update().get(pk=transfer.pk)
    if locked.status not in {BudgetTransfer.Status.DRAFT, BudgetTransfer.Status.SUBMITTED}:
        raise ValidationError("Only draft or submitted budget transfers can be approved.")
    lines = list(locked.lines.select_related("source_budget", "destination_budget", "budget_account"))
    if not lines:
        raise ValidationError("Add at least one budget transfer line before approval.")
    _apply_budget_pairs(lines)
    locked.total_amount = sum((line.amount for line in lines), Decimal("0"))
    locked.status = BudgetTransfer.Status.APPROVED
    locked.approved_by = actor
    locked.save(update_fields=["total_amount", "status", "approved_by", "updated_at"])
    _audit_for_company(locked.company_id, locked, "budget_transfer_approved", actor, {"amount": str(locked.total_amount)})
    return locked


@transaction.atomic
def approve_budget_carry_over(carry_over, actor=None):
    locked = BudgetCarryOver.objects.select_for_update().get(pk=carry_over.pk)
    if locked.status not in {BudgetCarryOver.Status.DRAFT, BudgetCarryOver.Status.SUBMITTED}:
        raise ValidationError("Only draft or submitted budget carry-overs can be approved.")
    lines = list(locked.lines.select_related("source_budget", "destination_budget", "budget_account"))
    if not lines:
        raise ValidationError("Add at least one budget carry-over line before approval.")
    _apply_budget_pairs(lines)
    locked.total_amount = sum((line.amount for line in lines), Decimal("0"))
    locked.status = BudgetCarryOver.Status.APPROVED
    locked.approved_by = actor
    locked.save(update_fields=["total_amount", "status", "approved_by", "updated_at"])
    _audit_for_company(locked.company_id, locked, "budget_carry_over_approved", actor, {"amount": str(locked.total_amount)})
    return locked


@transaction.atomic
def post_petty_cash_voucher(voucher, actor=None):
    locked = PettyCashVoucher.objects.select_for_update().select_related("fund__cash_account", "fund__journal").get(pk=voucher.pk)
    if locked.status not in {PettyCashVoucher.Status.DRAFT, PettyCashVoucher.Status.APPROVED}:
        raise ValidationError("Only draft or approved petty cash vouchers can be posted.")
    rows = list(locked.lines.select_related("category__expense_account", "cost_center"))
    total = sum((row.amount for row in rows), Decimal("0"))
    if total <= 0 or total > locked.fund.current_balance:
        raise ValidationError("Voucher total must be positive and cannot exceed the current fund balance.")
    entry = JournalEntry.objects.create(
        company_id=locked.fund.company_id, journal=locked.fund.journal, number=f"PCV-{locked.number}",
        entry_date=locked.voucher_date, reference=locked.reference, description=locked.note[:240],
        source_type="petty_cash_voucher", source_reference=locked.number,
    )
    JournalLine.objects.bulk_create([
        *[
            JournalLine(entry=entry, account=row.category.expense_account, cost_center=row.cost_center,
                        description=row.description, debit=row.amount)
            for row in rows
        ],
        JournalLine(entry=entry, account=locked.fund.cash_account, credit=total),
    ])
    entry = post_journal_entry(entry, actor)
    locked.total_amount = total
    locked.status = PettyCashVoucher.Status.POSTED
    locked.journal_entry = entry
    locked.save(update_fields=["total_amount", "status", "journal_entry", "updated_at"])
    locked.fund.current_balance -= total
    locked.fund.save(update_fields=["current_balance", "updated_at"])
    _audit_for_company(locked.fund.company_id, locked, "petty_cash_voucher_posted", actor, {"journal_entry_id": entry.pk})
    return locked


@transaction.atomic
def post_petty_cash_replenishment(replenishment, actor=None):
    locked = PettyCashReplenishment.objects.select_for_update().select_related(
        "fund__cash_account", "fund__journal", "bank_account__journal"
    ).get(pk=replenishment.pk)
    if locked.status not in {PettyCashReplenishment.Status.DRAFT, PettyCashReplenishment.Status.APPROVED}:
        raise ValidationError("Only draft or approved replenishments can be posted.")
    bank_credit = locked.bank_account.journal.default_credit_account
    if bank_credit is None:
        raise ValidationError("Configure the bank journal credit account.")
    if locked.fund.current_balance + locked.amount > locked.fund.imprest_limit:
        raise ValidationError("Replenishment would exceed the fund imprest limit.")
    entry = JournalEntry.objects.create(
        company_id=locked.fund.company_id, journal=locked.fund.journal, number=f"PCR-{locked.number}",
        entry_date=locked.replenishment_date, description=locked.note[:240],
        source_type="petty_cash_replenishment", source_reference=locked.number,
    )
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=locked.fund.cash_account, debit=locked.amount),
        JournalLine(entry=entry, account=bank_credit, credit=locked.amount),
    ])
    entry = post_journal_entry(entry, actor)
    locked.status = PettyCashReplenishment.Status.POSTED
    locked.journal_entry = entry
    locked.save(update_fields=["status", "journal_entry", "updated_at"])
    locked.fund.current_balance += locked.amount
    locked.fund.save(update_fields=["current_balance", "updated_at"])
    _audit_for_company(locked.fund.company_id, locked, "petty_cash_replenishment_posted", actor, {"journal_entry_id": entry.pk})
    return locked


@transaction.atomic
def post_petty_cash_reconciliation(reconciliation, actor=None):
    locked = PettyCashReconciliation.objects.select_for_update().select_related(
        "fund__cash_account", "fund__journal"
    ).get(pk=reconciliation.pk)
    if locked.status not in {"draft", "reconciled"}:
        raise ValidationError("Only draft or reconciled petty cash counts can be posted.")
    if locked.variance == 0:
        locked.status = "posted"
        locked.save(update_fields=["status", "updated_at"])
        _audit_for_company(locked.fund.company_id, locked, "petty_cash_reconciled", actor)
        return locked
    settings = AccountingSettings.objects.select_related("exchange_gain_account", "exchange_loss_account").get(
        company_id=locked.fund.company_id
    )
    variance_account = settings.exchange_gain_account if locked.variance > 0 else settings.exchange_loss_account
    if variance_account is None:
        raise ValidationError("Configure gain/loss accounts before posting a petty cash variance.")
    amount = abs(locked.variance)
    entry = JournalEntry.objects.create(
        company_id=locked.fund.company_id, journal=locked.fund.journal,
        number=f"PCC-{locked.reference}", entry_date=locked.reconciliation_date,
        description=locked.note[:240], source_type="petty_cash_reconciliation", source_reference=locked.reference,
    )
    positive = locked.variance > 0
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=locked.fund.cash_account,
                    debit=amount if positive else Decimal("0"), credit=amount if not positive else Decimal("0")),
        JournalLine(entry=entry, account=variance_account,
                    debit=amount if not positive else Decimal("0"), credit=amount if positive else Decimal("0")),
    ])
    entry = post_journal_entry(entry, actor)
    locked.status = "posted"
    locked.journal_entry = entry
    locked.save(update_fields=["status", "journal_entry", "updated_at"])
    locked.fund.current_balance = locked.counted_balance
    locked.fund.save(update_fields=["current_balance", "updated_at"])
    _audit_for_company(locked.fund.company_id, locked, "petty_cash_variance_posted", actor, {"journal_entry_id": entry.pk})
    return locked


@transaction.atomic
def reverse_petty_cash_document(record, actor=None):
    model = record.__class__
    locked = model.objects.select_for_update().select_related("fund").get(pk=record.pk)
    if locked.status != "posted":
        raise ValidationError("Only posted petty cash documents can be reversed.")
    fund = locked.fund
    if locked.journal_entry_id:
        reverse_journal_entry(locked.journal_entry, actor)
    if isinstance(locked, PettyCashVoucher):
        fund.current_balance += locked.total_amount
    elif isinstance(locked, PettyCashReplenishment):
        if fund.current_balance < locked.amount:
            raise ValidationError("The fund balance is insufficient to reverse this replenishment.")
        fund.current_balance -= locked.amount
    elif isinstance(locked, PettyCashReconciliation):
        previous_balance = locked.system_balance
        fund.current_balance = previous_balance
    fund.save(update_fields=["current_balance", "updated_at"])
    locked.status = "reversed"
    locked.save(update_fields=["status", "updated_at"])
    _audit_for_company(fund.company_id, locked, "petty_cash_document_reversed", actor)
    return locked


@transaction.atomic
def reverse_integration_event(event, actor=None):
    locked = AccountingIntegrationEvent.objects.select_for_update().select_related("journal_entry").get(pk=event.pk)
    if locked.status != AccountingIntegrationEvent.Status.POSTED or locked.journal_entry_id is None:
        raise ValidationError("Only posted integration events can be reversed.")
    reverse_journal_entry(locked.journal_entry, actor)
    _apply_integration_budget_impacts(locked, reverse=True)
    locked.status = AccountingIntegrationEvent.Status.REVERSED
    locked.save(update_fields=["status", "updated_at"])
    _audit(locked, "integration_event_reversed", actor)
    return locked


def _apply_integration_budget_impacts(event, *, reverse=False):
    """Apply optional budget impacts supplied by any source-module adapter."""
    impacts = event.payload.get("budget_consumptions", []) if isinstance(event.payload, dict) else []
    if not impacts:
        return {}
    if not isinstance(impacts, list) or len(impacts) > 200:
        raise ValidationError("Integration budget_consumptions must be a list of at most 200 rows.")
    normalized = []
    for row in impacts:
        if not isinstance(row, dict) or not row.get("budget_id") or not row.get("budget_account_id"):
            raise ValidationError("Every budget consumption requires budget_id and budget_account_id.")
        amount = Decimal(str(row.get("amount", 0)))
        if amount <= 0:
            raise ValidationError("Every budget consumption amount must be positive.")
        normalized.append((int(row["budget_id"]), int(row["budget_account_id"]), amount))
    budget_ids = {budget_id for budget_id, _account_id, _amount in normalized}
    account_ids = {account_id for _budget_id, account_id, _amount in normalized}
    budgets = {
        budget.pk: budget
        for budget in Budget.objects.select_for_update().filter(
            pk__in=budget_ids, company_id=event.company_id
        )
    }
    from .models import BudgetAccount

    valid_account_ids = set(BudgetAccount.objects.filter(
        pk__in=account_ids, company_id=event.company_id, active=True
    ).values_list("pk", flat=True))
    if len(budgets) != len(budget_ids) or valid_account_ids != account_ids:
        raise ValidationError("One or more integration budget references are invalid for this company.")
    increments = {}
    for budget_id, _account_id, amount in normalized:
        increments[budget_id] = increments.get(budget_id, Decimal("0")) + amount
    now = timezone.now()
    for budget_id, amount in increments.items():
        budget = budgets[budget_id]
        if reverse:
            if amount > budget.actual:
                raise ValidationError(f"Budget {budget} actual balance is too small for this reversal.")
            budget.actual -= amount
        else:
            if budget.status != Budget.Status.APPROVED:
                raise ValidationError(f"Budget {budget} must be approved before consumption.")
            if not budget.start_date <= event.event_date <= budget.end_date:
                raise ValidationError(f"Integration event date is outside budget {budget} period.")
            if amount > budget.remaining:
                raise ValidationError(f"Budget {budget} has insufficient remaining amount.")
            budget.actual += amount
        budget.updated_at = now
    Budget.objects.bulk_update(list(budgets.values()), ["actual", "updated_at"])
    return {str(key): str(value) for key, value in increments.items()}


@transaction.atomic
def _post_integration_event_atomic(event, actor=None):
    locked = AccountingIntegrationEvent.objects.select_for_update().get(pk=event.pk)
    if locked.status not in {AccountingIntegrationEvent.Status.PENDING, AccountingIntegrationEvent.Status.FAILED}:
        raise ValidationError("Only pending or failed integration events can be posted.")
    payload_lines = locked.payload.get("lines", [])
    if not isinstance(payload_lines, list) or not payload_lines:
        raise ValidationError("Integration payload requires accounting lines.")
    account_ids = {line.get("account_id") for line in payload_lines if line.get("account_id")}
    accounts = {record.pk: record for record in Account.objects.filter(company_id=locked.company_id, pk__in=account_ids)}
    if len(accounts) != len(account_ids):
        raise ValidationError("One or more integration accounts are invalid for this company.")
    journal_id = locked.payload.get("journal_id")
    journal = AccountJournal.objects.filter(company_id=locked.company_id, pk=journal_id).first() if journal_id else _general_journal(locked.company_id)
    entry = JournalEntry.objects.create(
        company_id=locked.company_id, journal=journal, number=f"INT-{locked.pk}", entry_date=locked.event_date,
        reference=locked.reference, description=f"{locked.source_module}: {locked.event_type}",
        source_type=locked.source_model, source_reference=str(locked.source_id),
    )
    JournalLine.objects.bulk_create([
        JournalLine(
            entry=entry, account=accounts[line["account_id"]], description=str(line.get("description", ""))[:200],
            debit=Decimal(str(line.get("debit", 0))), credit=Decimal(str(line.get("credit", 0))),
            partner_id=line.get("partner_id"), cost_center_id=line.get("cost_center_id"),
        )
        for line in payload_lines
    ])
    entry = post_journal_entry(entry, actor)
    budget_impacts = _apply_integration_budget_impacts(locked)
    locked.status = AccountingIntegrationEvent.Status.POSTED
    locked.journal_entry = entry
    locked.error_message = ""
    locked.save(update_fields=["status", "journal_entry", "error_message", "updated_at"])
    _audit(
        locked, "integration_event_posted", actor,
        {"journal_entry_id": entry.pk, "budget_impacts": budget_impacts},
    )
    return locked


def post_integration_event(event, actor=None):
    try:
        return _post_integration_event_atomic(event, actor)
    except ValidationError as exc:
        AccountingIntegrationEvent.objects.filter(
            pk=event.pk,
            status__in=[AccountingIntegrationEvent.Status.PENDING, AccountingIntegrationEvent.Status.FAILED],
        ).update(
            status=AccountingIntegrationEvent.Status.FAILED,
            retry_count=F("retry_count") + 1,
            error_message="; ".join(exc.messages),
            updated_at=timezone.now(),
        )
        raise


def _source_posting_lines(posting):
    payload = posting.payload if isinstance(posting.payload, dict) else {}
    requested_lines = payload.get("lines")
    if requested_lines is None:
        return [
            {
                "account_id": posting.adapter.default_debit_account_id,
                "debit": str(posting.amount),
                "credit": "0",
                "partner_id": posting.partner_id,
                "cost_center_id": posting.cost_center_id,
                "description": posting.description,
            },
            {
                "account_id": posting.adapter.default_credit_account_id,
                "debit": "0",
                "credit": str(posting.amount),
                "partner_id": posting.partner_id,
                "cost_center_id": posting.cost_center_id,
                "description": posting.description,
            },
        ]
    if not isinstance(requested_lines, list) or not requested_lines:
        raise ValidationError("Source posting payload lines must be a non-empty list.")
    normalized = []
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for line in requested_lines:
        if not isinstance(line, dict) or not line.get("account_id"):
            raise ValidationError("Every source posting line requires an account_id.")
        debit = Decimal(str(line.get("debit", 0)))
        credit = Decimal(str(line.get("credit", 0)))
        if debit < 0 or credit < 0 or (debit and credit) or (not debit and not credit):
            raise ValidationError("Each source posting line requires one positive debit or credit amount.")
        total_debit += debit
        total_credit += credit
        normalized.append({
            "account_id": line["account_id"],
            "debit": str(debit),
            "credit": str(credit),
            "partner_id": line.get("partner_id", posting.partner_id),
            "cost_center_id": line.get("cost_center_id", posting.cost_center_id),
            "description": str(line.get("description", posting.description))[:200],
        })
    if total_debit != total_credit:
        raise ValidationError("Source posting debit and credit totals must be equal.")
    return normalized


def enqueue_source_accounting_posting(
    *, company_id, adapter_code, source_id, reference, event_date, amount,
    actor, partner_id=None, cost_center_id=None, description="", payload=None,
):
    """Stable integration contract for any installed source module.

    Source modules provide business data and an adapter code; Finance owns
    validation, idempotency, posting, retry state, reversal, and audit.
    """
    try:
        adapter = AccountingSourceAdapter.objects.select_related(
            "journal", "default_debit_account", "default_credit_account"
        ).get(company_id=company_id, code=adapter_code, active=True)
    except AccountingSourceAdapter.DoesNotExist as exc:
        raise ValidationError(f"Active accounting source adapter {adapter_code!r} is not configured.") from exc
    posting, created = SourceAccountingPosting.objects.get_or_create(
        adapter=adapter,
        source_id=str(source_id),
        reference=reference,
        defaults={
            "event_date": event_date,
            "amount": amount,
            "partner_id": partner_id,
            "cost_center_id": cost_center_id,
            "description": description,
            "payload": payload or {},
            "status": SourceAccountingPosting.Status.PENDING,
            "requested_by": actor,
        },
    )
    if not created:
        immutable = (
            posting.event_date != event_date
            or posting.amount != Decimal(str(amount))
            or posting.partner_id != partner_id
            or posting.cost_center_id != cost_center_id
        )
        if immutable:
            raise ValidationError("The source posting key already exists with different accounting values.")
    if adapter.auto_post and posting.status in {
        SourceAccountingPosting.Status.DRAFT,
        SourceAccountingPosting.Status.PENDING,
        SourceAccountingPosting.Status.FAILED,
    }:
        return post_source_accounting_posting(posting, actor)
    return posting


def validate_external_accounting_connector(connector, actor=None):
    locked = ExternalAccountingConnector.objects.get(pk=connector.pk)
    try:
        backend = external_connector_backend(locked)
        backend.validate_configuration()
        connect = getattr(backend, "connect", None)
        if connect:
            connect()
    except Exception as exc:
        ExternalAccountingConnector.objects.filter(pk=locked.pk).update(
            status=ExternalAccountingConnector.Status.ERROR,
            last_error=str(exc)[:2000],
        )
        raise
    locked.status = ExternalAccountingConnector.Status.CONNECTED
    locked.last_error = ""
    locked.save(update_fields=["status", "last_error", "updated_at"])
    return locked


def dispatch_external_invoice_sync(sync, actor=None):
    locked = ExternalInvoiceSync.objects.select_related(
        "connector", "invoice__company", "invoice__partner"
    ).get(pk=sync.pk)
    if locked.status not in {ExternalInvoiceSync.Status.PENDING, ExternalInvoiceSync.Status.FAILED}:
        raise ValidationError("Only pending or failed external invoice syncs can be dispatched.")
    if locked.invoice.status not in {locked.invoice.Status.POSTED, locked.invoice.Status.PAID}:
        raise ValidationError("Post the invoice before dispatching it to external accounting.")
    try:
        result = external_connector_backend(locked.connector).send_invoice(locked)
    except Exception as exc:
        ExternalInvoiceSync.objects.filter(pk=locked.pk).update(
            status=ExternalInvoiceSync.Status.FAILED,
            retry_count=F("retry_count") + 1,
            error_message=str(exc)[:2000],
        )
        raise
    with transaction.atomic():
        current = ExternalInvoiceSync.objects.select_for_update().get(pk=locked.pk)
        if current.status not in {ExternalInvoiceSync.Status.PENDING, ExternalInvoiceSync.Status.FAILED}:
            raise ValidationError("External invoice sync status changed while dispatch was running.")
        current.status = (
            ExternalInvoiceSync.Status.CONFIRMED
            if result.status == "confirmed"
            else ExternalInvoiceSync.Status.SENT
        )
        current.external_reference = result.external_reference
        current.request_payload = result.request_payload
        current.response_payload = result.response_payload
        current.error_message = ""
        current.save(update_fields=[
            "status", "external_reference", "request_payload", "response_payload", "error_message", "updated_at"
        ])
    return current


@transaction.atomic
def apply_external_invoice_status(*, connector_code, external_reference, status, payload=None):
    normalized = str(status).lower()
    status_map = {
        "sent": ExternalInvoiceSync.Status.SENT,
        "posted": ExternalInvoiceSync.Status.CONFIRMED,
        "confirmed": ExternalInvoiceSync.Status.CONFIRMED,
        "paid": ExternalInvoiceSync.Status.CONFIRMED,
        "failed": ExternalInvoiceSync.Status.FAILED,
        "cancelled": ExternalInvoiceSync.Status.CANCELLED,
    }
    if normalized not in status_map:
        raise ValidationError("Unsupported external invoice status.")
    sync = ExternalInvoiceSync.objects.select_for_update().select_related("invoice").get(
        connector__code=connector_code,
        external_reference=external_reference,
    )
    sync.status = status_map[normalized]
    sync.response_payload = payload or {}
    sync.error_message = "" if sync.status != ExternalInvoiceSync.Status.FAILED else str((payload or {}).get("error", ""))
    sync.save(update_fields=["status", "response_payload", "error_message", "updated_at"])
    return sync


@transaction.atomic
def _post_marketplace_accounting_atomic(record, action, actor=None):
    locked = MarketplaceAccountingOrder.objects.select_for_update().select_related(
        "accounting_settings__journal",
        "accounting_settings__marketplace_receivable_account",
        "accounting_settings__settlement_account",
        "accounting_settings__revenue_account",
        "accounting_settings__fee_expense_account",
    ).get(pk=record.pk)
    config = locked.accounting_settings
    if action == "post_order":
        if locked.status not in {MarketplaceAccountingOrder.Status.DRAFT, MarketplaceAccountingOrder.Status.FAILED}:
            raise ValidationError("Only a draft or failed marketplace order can be posted.")
        if locked.gross_amount <= 0:
            raise ValidationError("Marketplace gross order amount must be positive.")
        event = _post_simple_bridge(
            company_id=config.company_id,
            source=locked,
            event_type="marketplace_order",
            reference=locked.reference,
            event_date=locked.transaction_date,
            amount=locked.gross_amount,
            journal_id=config.journal_id,
            actor=actor,
            lines=[
                {
                    "account_id": config.marketplace_receivable_account_id,
                    "debit": str(locked.gross_amount),
                    "credit": "0",
                    "partner_id": locked.partner_id,
                },
                {
                    "account_id": config.revenue_account_id,
                    "debit": "0",
                    "credit": str(locked.gross_amount),
                    "partner_id": locked.partner_id,
                },
            ],
        )
        locked.order_event = event
        locked.status = MarketplaceAccountingOrder.Status.ORDER_POSTED
        update_fields = ["order_event", "status", "error_message", "updated_at"]
    elif action == "settle":
        if locked.status != MarketplaceAccountingOrder.Status.ORDER_POSTED:
            raise ValidationError("Post the marketplace order before settlement.")
        if locked.settlement_amount + locked.fee_amount + locked.refund_amount != locked.gross_amount:
            raise ValidationError("Settlement, fee, and refund amounts must add up to gross amount.")
        lines = [{
            "account_id": config.marketplace_receivable_account_id,
            "debit": "0",
            "credit": str(locked.gross_amount),
            "partner_id": locked.partner_id,
        }]
        if locked.settlement_amount:
            lines.append({
                "account_id": config.settlement_account_id,
                "debit": str(locked.settlement_amount),
                "credit": "0",
            })
        if locked.fee_amount:
            lines.append({
                "account_id": config.fee_expense_account_id,
                "debit": str(locked.fee_amount),
                "credit": "0",
            })
        if locked.refund_amount:
            lines.append({
                "account_id": config.revenue_account_id,
                "debit": str(locked.refund_amount),
                "credit": "0",
                "partner_id": locked.partner_id,
            })
        event = _post_simple_bridge(
            company_id=config.company_id,
            source=locked,
            event_type="marketplace_settlement",
            reference=locked.reference,
            event_date=locked.transaction_date,
            amount=locked.gross_amount,
            journal_id=config.journal_id,
            actor=actor,
            lines=lines,
        )
        locked.settlement_event = event
        locked.status = MarketplaceAccountingOrder.Status.SETTLED
        update_fields = ["settlement_event", "status", "error_message", "updated_at"]
    else:
        raise ValidationError("Unsupported marketplace accounting action.")
    locked.error_message = ""
    locked.save(update_fields=update_fields)
    return locked


def post_marketplace_accounting(record, action, actor=None):
    try:
        return _post_marketplace_accounting_atomic(record, action, actor)
    except Exception as exc:
        MarketplaceAccountingOrder.objects.filter(pk=record.pk).update(
            status=MarketplaceAccountingOrder.Status.FAILED,
            error_message=str(exc)[:2000],
        )
        raise


@transaction.atomic
def reverse_marketplace_accounting(record, actor=None):
    locked = MarketplaceAccountingOrder.objects.select_for_update().select_related(
        "order_event", "settlement_event"
    ).get(pk=record.pk)
    if locked.status not in {
        MarketplaceAccountingOrder.Status.ORDER_POSTED,
        MarketplaceAccountingOrder.Status.SETTLED,
    }:
        raise ValidationError("Only posted or settled marketplace accounting can be reversed.")
    for event in (locked.settlement_event, locked.order_event):
        if event and event.status == AccountingIntegrationEvent.Status.POSTED:
            reverse_integration_event(event, actor)
    locked.status = MarketplaceAccountingOrder.Status.REVERSED
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def hold_rental_deposit(order, actor):
    terms = RentalOrderAccounting.objects.select_for_update(of=("self",)).select_related(
        "order", "selected_customer_deposit"
    ).filter(order_id=order.pk).first()
    if terms is None or not terms.deposit_required or terms.required_deposit_amount <= 0:
        return None
    existing = RentalDepositHold.objects.select_for_update().filter(rental_terms=terms).first()
    if existing:
        if existing.status == RentalDepositHold.Status.HELD:
            return existing
        raise ValidationError("A released rental deposit hold cannot be reused; create new rental terms.")
    deposit = terms.selected_customer_deposit
    if deposit is None:
        deposit = AccountDeposit.objects.filter(
            company_id=order.company_id,
            partner_id=order.customer_id,
            deposit_type=AccountDeposit.DepositType.CUSTOMER,
            status=AccountDeposit.Status.RECEIVED,
            unapplied_amount__gt=0,
        ).order_by("deposit_date", "id").first()
        if deposit is None:
            raise ValidationError("A received customer deposit is required before confirming this rental order.")
    deposit = AccountDeposit.objects.select_for_update().get(pk=deposit.pk)
    if deposit.company_id != order.company_id or deposit.partner_id != order.customer_id:
        raise ValidationError("Customer deposit must match the rental order company and customer.")
    if deposit.deposit_type != AccountDeposit.DepositType.CUSTOMER or deposit.status != AccountDeposit.Status.RECEIVED:
        raise ValidationError("Rental orders require a received customer deposit.")
    if deposit.unapplied_amount < terms.required_deposit_amount:
        raise ValidationError("Customer deposit balance is insufficient for this rental order.")
    previous_status = deposit.status
    deposit.unapplied_amount -= terms.required_deposit_amount
    if deposit.unapplied_amount == 0:
        deposit.status = AccountDeposit.Status.APPLIED
    deposit.save(update_fields=["unapplied_amount", "status", "updated_at"])
    if terms.selected_customer_deposit_id != deposit.pk:
        terms.selected_customer_deposit = deposit
    terms.deposit_status = RentalOrderAccounting.DepositStatus.HELD
    terms.save(update_fields=["selected_customer_deposit", "deposit_status", "updated_at"])
    hold = RentalDepositHold.objects.create(
        rental_terms=terms,
        account_deposit=deposit,
        amount=terms.required_deposit_amount,
        held_by=actor,
    )
    AccountDepositHistory.objects.create(
        deposit=deposit,
        action="rental_hold",
        amount=hold.amount,
        previous_status=previous_status,
        new_status=deposit.status,
        actor=actor,
        details={"sale_order_id": order.pk, "rental_hold_id": hold.pk},
    )
    return hold


@transaction.atomic
def release_rental_deposit(order, actor):
    terms = RentalOrderAccounting.objects.select_for_update().filter(order_id=order.pk).first()
    if terms is None:
        return None
    hold = RentalDepositHold.objects.select_for_update().filter(rental_terms=terms).first()
    if hold is None or hold.status == RentalDepositHold.Status.RELEASED:
        return hold
    deposit = AccountDeposit.objects.select_for_update().get(pk=hold.account_deposit_id)
    previous_status = deposit.status
    deposit.unapplied_amount += hold.amount
    deposit.status = AccountDeposit.Status.RECEIVED
    deposit.save(update_fields=["unapplied_amount", "status", "updated_at"])
    hold.status = RentalDepositHold.Status.RELEASED
    hold.released_at = timezone.now()
    hold.released_by = actor
    hold.save(update_fields=["status", "released_at", "released_by", "updated_at"])
    terms.deposit_status = RentalOrderAccounting.DepositStatus.RELEASED
    terms.save(update_fields=["deposit_status", "updated_at"])
    AccountDepositHistory.objects.create(
        deposit=deposit,
        action="release_rental_hold",
        amount=hold.amount,
        previous_status=previous_status,
        new_status=deposit.status,
        actor=actor,
        details={"sale_order_id": order.pk, "rental_hold_id": hold.pk},
    )
    return hold


def post_source_accounting_posting(posting, actor=None):
    try:
        with transaction.atomic():
            locked = SourceAccountingPosting.objects.select_for_update().select_related(
                "adapter", "adapter__journal", "adapter__default_debit_account",
                "adapter__default_credit_account", "integration_event",
            ).get(pk=posting.pk)
            if locked.status not in {
                SourceAccountingPosting.Status.DRAFT,
                SourceAccountingPosting.Status.PENDING,
                SourceAccountingPosting.Status.FAILED,
            }:
                raise ValidationError("Only draft, pending, or failed source postings can be posted.")
            if not locked.adapter.active:
                raise ValidationError("The selected accounting source adapter is inactive.")
            lines = _source_posting_lines(locked)
            payload = {
                "journal_id": locked.adapter.journal_id,
                "lines": lines,
                "origin": {
                    "source_module": locked.adapter.source_module,
                    "source_model": locked.adapter.source_model,
                    "source_id": locked.source_id,
                    "reference": locked.reference,
                },
            }
            event, _created = AccountingIntegrationEvent.objects.get_or_create(
                company_id=locked.adapter.company_id,
                source_model="gsshop_finance.sourceaccountingposting",
                source_id=locked.pk,
                event_type=locked.adapter.event_type,
                defaults={
                    "source_module": locked.adapter.source_module,
                    "reference": locked.reference,
                    "event_date": locked.event_date,
                    "amount": locked.amount,
                    "payload": payload,
                },
            )
            if event.status == AccountingIntegrationEvent.Status.REVERSED:
                raise ValidationError("A reversed source posting cannot be posted again; create a new request.")
            if event.status != AccountingIntegrationEvent.Status.POSTED:
                event.source_module = locked.adapter.source_module
                event.reference = locked.reference
                event.event_date = locked.event_date
                event.amount = locked.amount
                event.payload = payload
                event.error_message = ""
                event.save(update_fields=[
                    "source_module", "reference", "event_date", "amount", "payload", "error_message", "updated_at"
                ])
                event = post_integration_event(event, actor)
            locked.integration_event = event
            locked.status = SourceAccountingPosting.Status.POSTED
            locked.error_message = ""
            locked.save(update_fields=["integration_event", "status", "error_message", "updated_at"])
            _audit_for_company(
                locked.adapter.company_id,
                locked,
                "source_accounting_posted",
                actor,
                {"adapter_id": locked.adapter_id, "integration_event_id": event.pk},
            )
            return locked
    except ValidationError as exc:
        SourceAccountingPosting.objects.filter(pk=posting.pk).update(
            status=SourceAccountingPosting.Status.FAILED,
            retry_count=F("retry_count") + 1,
            error_message="; ".join(exc.messages),
            updated_at=timezone.now(),
        )
        raise


@transaction.atomic
def reverse_source_accounting_posting(posting, actor=None):
    locked = SourceAccountingPosting.objects.select_for_update().select_related(
        "adapter", "integration_event"
    ).get(pk=posting.pk)
    if locked.status != SourceAccountingPosting.Status.POSTED or locked.integration_event_id is None:
        raise ValidationError("Only a posted source accounting request can be reversed.")
    reverse_integration_event(locked.integration_event, actor)
    locked.status = SourceAccountingPosting.Status.REVERSED
    locked.save(update_fields=["status", "updated_at"])
    _audit_for_company(
        locked.adapter.company_id,
        locked,
        "source_accounting_reversed",
        actor,
        {"integration_event_id": locked.integration_event_id},
    )
    return locked


@transaction.atomic
def post_pos_session_accounting(session, actor=None):
    from .models import POSOrderAccounting

    session = session.__class__.objects.select_for_update().select_related("register__profile").get(pk=session.pk)
    profile = session.register.profile
    if profile is None:
        raise ValidationError("Assign a POS profile before closing the session.")
    try:
        config = POSAccountingSettings.objects.select_related(
            "journal", "sales_account", "cash_account", "inventory_account", "cogs_account",
            "consumption_expense_account", "variance_gain_account", "variance_loss_account",
        ).get(profile_id=profile.pk)
    except POSAccountingSettings.DoesNotExist as exc:
        raise ValidationError("Configure POS Accounting Settings before closing this session.") from exc
    payment_model = apps.get_model("gsshop_pos", "POSPayment")
    payment_rows = list(
        payment_model.objects.filter(order__session_id=session.pk)
        .exclude(order__order_accounting__status=POSOrderAccounting.Status.POSTED)
        .values("payment_method_id", "payment_method__method_type")
        .annotate(amount=Sum("amount"), fee=Sum("mdr_charge_amount"))
    )
    mapping_ids = {row["payment_method_id"] for row in payment_rows}
    payment_mappings = {
        mapping.payment_method_id: mapping
        for mapping in POSPaymentAccountMapping.objects.select_related("settlement_account", "fee_account").filter(
            payment_method_id__in=mapping_ids, active=True
        )
    }
    sales = sum((row["amount"] or Decimal("0") for row in payment_rows), Decimal("0"))
    if not payment_rows:
        order_model = apps.get_model("gsshop_pos", "POSOrder")
        sales = order_model.objects.filter(session_id=session.pk).exclude(
            order_accounting__status=POSOrderAccounting.Status.POSTED
        ).aggregate(total=Sum("total"))["total"] or Decimal("0")
    cogs = Decimal(str((session.closing_summary or {}).get("cogs_total", 0)))
    consumption = Decimal(str((session.closing_summary or {}).get("non_sales_consumption_total", 0)))
    variance = session.variance_amount
    lines = []
    if payment_rows:
        for row in payment_rows:
            amount = row["amount"] or Decimal("0")
            fee = row["fee"] or Decimal("0")
            mapping = payment_mappings.get(row["payment_method_id"])
            if mapping is None and row["payment_method__method_type"] != "cash":
                raise ValidationError("Configure an accounting mapping for every non-cash POS payment method.")
            settlement_account_id = mapping.settlement_account_id if mapping else config.cash_account_id
            if fee and (mapping is None or mapping.fee_account_id is None):
                raise ValidationError("Configure a fee account for POS payment methods with MDR charges.")
            net = amount - fee
            lines.append({"account_id": settlement_account_id, "debit": str(net), "credit": "0", "description": "POS payment settlement"})
            if fee:
                lines.append({"account_id": mapping.fee_account_id, "debit": str(fee), "credit": "0", "description": "POS payment fee"})
            lines.append({"account_id": config.sales_account_id, "debit": "0", "credit": str(amount), "description": "POS sales revenue"})
    elif sales:
        lines.extend([
            {"account_id": config.cash_account_id, "debit": str(sales), "credit": "0", "description": "POS cash sales"},
            {"account_id": config.sales_account_id, "debit": "0", "credit": str(sales), "description": "POS sales revenue"},
        ])
    if cogs:
        if not config.cogs_account_id or not config.inventory_account_id:
            raise ValidationError("Configure POS inventory and COGS accounts before closing inventory-valued sales.")
        lines.extend([
            {"account_id": config.cogs_account_id, "debit": str(cogs), "credit": "0", "description": "POS cost of goods sold"},
            {"account_id": config.inventory_account_id, "debit": "0", "credit": str(cogs), "description": "POS inventory relief"},
        ])
    if consumption:
        if not config.consumption_expense_account_id or not config.inventory_account_id:
            raise ValidationError("Configure POS consumption expense and inventory accounts.")
        lines.extend([
            {"account_id": config.consumption_expense_account_id, "debit": str(consumption), "credit": "0", "description": "POS non-sales consumption"},
            {"account_id": config.inventory_account_id, "debit": "0", "credit": str(consumption), "description": "POS non-sales inventory relief"},
        ])
    if variance:
        variance_account_id = config.variance_gain_account_id if variance > 0 else config.variance_loss_account_id
        if not variance_account_id:
            raise ValidationError("Configure the POS cash variance gain/loss account.")
        amount = abs(variance)
        if variance > 0:
            lines.extend([
                {"account_id": config.cash_account_id, "debit": str(amount), "credit": "0", "description": "POS cash overage"},
                {"account_id": variance_account_id, "debit": "0", "credit": str(amount), "description": "POS cash overage"},
            ])
        else:
            lines.extend([
                {"account_id": variance_account_id, "debit": str(amount), "credit": "0", "description": "POS cash shortage"},
                {"account_id": config.cash_account_id, "debit": "0", "credit": str(amount), "description": "POS cash shortage"},
            ])
    if not lines:
        session.status = session.Status.CLOSED
        session.closed_by = actor
        session.closed_at = timezone.now()
        session.save(update_fields=["status", "closed_by", "closed_at", "updated_at"])
        return session
    event, created = AccountingIntegrationEvent.objects.get_or_create(
        company_id=profile.company_id, source_model=session._meta.label_lower, source_id=session.pk,
        event_type="session_close",
        defaults={
            "source_module": "pos", "reference": f"POS-{session.pk}", "event_date": timezone.localdate(),
            "amount": sales, "payload": {"journal_id": config.journal_id, "lines": lines},
        },
    )
    if not created and event.status == AccountingIntegrationEvent.Status.POSTED:
        return session
    if config.auto_post_on_close:
        post_integration_event(event, actor)
    session.status = session.Status.CLOSED
    session.closed_by = actor
    session.closed_at = timezone.now()
    session.save(update_fields=["status", "closed_by", "closed_at", "updated_at"])
    return session


@transaction.atomic
def process_pos_order_accounting(order_accounting, actor=None):
    """Post invoice/deposit/MDR accounting once for one paid POS order."""
    from core.models import Invoice, InvoiceLine

    from .models import POSOrderAccounting

    locked = POSOrderAccounting.objects.select_for_update().select_related(
        "order__session__register__profile", "order__customer", "invoice", "customer_deposit"
    ).get(pk=order_accounting.pk)
    if locked.status == POSOrderAccounting.Status.POSTED:
        return locked
    if locked.status not in {POSOrderAccounting.Status.PENDING, POSOrderAccounting.Status.FAILED}:
        raise ValidationError("Only pending or failed POS order accounting can be processed.")
    order = locked.order
    if order.status != order.Status.PAID:
        raise ValidationError("Only paid POS orders can be accounted per order.")
    if locked.invoice_requested and order.customer_id is None:
        raise ValidationError("A customer is required when a POS invoice is requested.")
    profile = order.session.register.profile
    if profile is None:
        raise ValidationError("Assign a POS profile before processing order accounting.")
    try:
        config = POSAccountingSettings.objects.select_related(
            "journal", "sales_account", "cash_account"
        ).get(profile_id=profile.pk)
    except POSAccountingSettings.DoesNotExist as exc:
        raise ValidationError("Configure POS Accounting Settings before processing an order.") from exc
    order_line_model = apps.get_model("gsshop_pos", "POSOrderLine")
    payment_model = apps.get_model("gsshop_pos", "POSPayment")
    order_lines = list(order_line_model.objects.filter(order_id=order.pk).select_related("product"))
    payments = list(payment_model.objects.filter(order_id=order.pk).select_related("payment_method"))
    if not order_lines:
        raise ValidationError("The POS order has no lines.")
    if not payments:
        raise ValidationError("The paid POS order has no payment lines.")
    payment_method_ids = {payment.payment_method_id for payment in payments}
    mappings = {
        mapping.payment_method_id: mapping
        for mapping in POSPaymentAccountMapping.objects.select_related("settlement_account", "fee_account").filter(
            payment_method_id__in=payment_method_ids, active=True
        )
    }
    deposit_payments = [payment for payment in payments if payment.payment_method.method_type == "deposit"]
    deposit_amount = sum((payment.amount for payment in deposit_payments), Decimal("0"))
    if deposit_amount:
        if actor is None:
            raise ValidationError("An authenticated actor is required to apply a customer deposit.")
        if locked.customer_deposit_id is None:
            raise ValidationError("Select the customer deposit used by this POS order.")
        if order.customer_id != locked.customer_deposit.partner_id:
            raise ValidationError("POS customer and customer deposit partner must match.")
        if locked.customer_deposit.unapplied_amount < deposit_amount:
            raise ValidationError("The selected customer deposit does not have enough unapplied balance.")
    accounting_lines = []
    payment_snapshot = []
    total_payment = Decimal("0")
    total_mdr = Decimal("0")
    commercial = CommercialAccountingSettings.objects.select_related("sales_downpayment_account").filter(
        company_id=profile.company_id
    ).first()
    for payment in payments:
        amount = payment.amount
        fee = payment.mdr_charge_amount or Decimal("0")
        total_payment += amount
        total_mdr += fee
        mapping = mappings.get(payment.payment_method_id)
        if payment.payment_method.method_type == "deposit":
            if commercial is None:
                raise ValidationError("Configure Sales & Purchase Accounting Settings for POS deposit use.")
            settlement_account_id = commercial.sales_downpayment_account_id
        elif mapping:
            settlement_account_id = mapping.settlement_account_id
        elif payment.payment_method.method_type == "cash":
            settlement_account_id = config.cash_account_id
        else:
            raise ValidationError(f"Configure accounting mapping for POS payment method {payment.payment_method}.")
        if fee and (mapping is None or mapping.fee_account_id is None):
            raise ValidationError(f"Configure an MDR fee account for {payment.payment_method}.")
        accounting_lines.append({
            "account_id": settlement_account_id, "debit": amount - fee, "credit": Decimal("0"),
            "description": f"POS settlement · {payment.payment_method.name}",
        })
        if fee:
            accounting_lines.append({
                "account_id": mapping.fee_account_id, "debit": fee, "credit": Decimal("0"),
                "description": f"POS MDR · {payment.payment_method.name}",
            })
        payment_snapshot.append({
            "payment_id": payment.pk, "method": payment.payment_method.code,
            "amount": str(amount), "mdr": str(fee), "settlement_account_id": settlement_account_id,
        })
    if total_payment != order.total:
        raise ValidationError("POS payment total must equal the order total before accounting.")
    accounting_lines.append({
        "account_id": config.sales_account_id, "debit": Decimal("0"), "credit": total_payment,
        "description": f"POS revenue · {order.number}",
    })
    entry = JournalEntry.objects.create(
        company_id=profile.company_id, journal=config.journal, number=f"POS-{order.number}"[:40],
        entry_date=timezone.localdate(), reference=order.number, description=f"POS order accounting {order.number}",
        source_type=order._meta.label_lower, source_reference=str(order.pk),
    )
    JournalLine.objects.bulk_create([
        JournalLine(
            entry=entry, account_id=line["account_id"], description=line["description"],
            debit=line["debit"], credit=line["credit"], partner_id=order.customer_id,
        )
        for line in accounting_lines
    ])
    post_journal_entry(entry, actor)
    invoice = locked.invoice
    if locked.invoice_requested and invoice is None:
        invoice = Invoice.objects.create(
            company_id=profile.company_id, number=f"POS-{order.number}"[:40],
            invoice_type=Invoice.InvoiceType.CUSTOMER, partner=order.customer,
            invoice_date=timezone.localdate(), due_date=timezone.localdate(), status=Invoice.Status.PAID,
            total=order.total, paid_amount=order.total, notes=f"Generated from POS order {order.number}",
        )
        InvoiceLine.objects.bulk_create([
            InvoiceLine(
                invoice=invoice, product=line.product, description=line.notes or line.product.name,
                quantity=line.quantity, unit_price=line.unit_price,
            )
            for line in order_lines
        ])
    if deposit_amount:
        locked.customer_deposit.unapplied_amount -= deposit_amount
        if locked.customer_deposit.unapplied_amount == 0:
            locked.customer_deposit.status = AccountDeposit.Status.APPLIED
        locked.customer_deposit.save(update_fields=["unapplied_amount", "status", "updated_at"])
        AccountDepositHistory.objects.create(
            deposit=locked.customer_deposit, action="pos_payment", amount=deposit_amount,
            previous_status=AccountDeposit.Status.RECEIVED,
            new_status=locked.customer_deposit.status, actor=actor,
            details={"pos_order_id": order.pk, "journal_entry_id": entry.pk},
        )
    locked.invoice = invoice
    locked.journal_entry = entry
    locked.deposit_applied = deposit_amount
    locked.mdr_amount = total_mdr
    locked.accounting_snapshot = {
        "order_total": str(order.total), "payments": payment_snapshot,
        "journal_entry_id": entry.pk, "invoice_id": invoice.pk if invoice else None,
    }
    locked.status = POSOrderAccounting.Status.POSTED
    locked.error_message = ""
    locked.save(update_fields=[
        "invoice", "journal_entry", "deposit_applied", "mdr_amount", "accounting_snapshot",
        "status", "error_message", "updated_at",
    ])
    _audit_for_company(
        profile.company_id, locked, "pos_order_accounting_posted", actor,
        {"journal_entry_id": entry.pk, "invoice_id": invoice.pk if invoice else None},
    )
    return locked


@transaction.atomic
def reverse_pos_order_accounting(order_accounting, actor=None):
    from core.models import Invoice
    from .models import POSOrderAccounting

    locked = POSOrderAccounting.objects.select_for_update().select_related(
        "journal_entry", "invoice", "customer_deposit", "order__session__register"
    ).get(pk=order_accounting.pk)
    if locked.status not in {POSOrderAccounting.Status.POSTED, POSOrderAccounting.Status.INVOICED}:
        raise ValidationError("Only posted POS order accounting can be reversed.")
    if locked.journal_entry_id:
        reverse_journal_entry(locked.journal_entry, actor)
    if locked.invoice_id and locked.invoice.status != Invoice.Status.CANCELLED:
        locked.invoice.status = Invoice.Status.CANCELLED
        locked.invoice.save(update_fields=["status", "updated_at"])
    if locked.deposit_applied and locked.customer_deposit_id:
        previous_status = locked.customer_deposit.status
        locked.customer_deposit.unapplied_amount += locked.deposit_applied
        locked.customer_deposit.status = AccountDeposit.Status.RECEIVED
        locked.customer_deposit.save(update_fields=["unapplied_amount", "status", "updated_at"])
        AccountDepositHistory.objects.create(
            deposit=locked.customer_deposit, action="pos_payment_reversal", amount=locked.deposit_applied,
            previous_status=previous_status, new_status=AccountDeposit.Status.RECEIVED, actor=actor,
            details={"pos_order_id": locked.order_id, "journal_entry_id": locked.journal_entry_id},
        )
    locked.status = POSOrderAccounting.Status.REVERSED
    locked.save(update_fields=["status", "updated_at"])
    _audit_for_company(
        locked.order.session.register.company_id, locked, "pos_order_accounting_reversed", actor,
        {"journal_entry_id": locked.journal_entry_id},
    )
    return locked


@transaction.atomic
def reverse_pos_session_accounting(session, actor=None):
    session = session.__class__.objects.select_for_update().get(pk=session.pk)
    bridge = AccountingIntegrationEvent.objects.filter(
        company_id=session.register.company_id, source_model=session._meta.label_lower,
        source_id=session.pk, event_type="session_close",
    ).first()
    if bridge and bridge.status == AccountingIntegrationEvent.Status.POSTED:
        reverse_integration_event(bridge, actor)
    session.status = session.Status.OPEN
    session.closed_by = None
    session.closed_at = None
    session.save(update_fields=["status", "closed_by", "closed_at", "updated_at"])
    return session


@transaction.atomic
def post_payroll_accounting_event(payroll_event, actor=None):
    event_record = payroll_event.__class__.objects.select_for_update().select_related("payroll_run").get(pk=payroll_event.pk)
    run = event_record.payroll_run
    try:
        config = PayrollAccountingSettings.objects.select_related(
            "journal", "salary_expense_account", "payroll_payable_account", "bank_account"
        ).get(company_id=run.company_id)
    except PayrollAccountingSettings.DoesNotExist as exc:
        raise ValidationError("Configure Payroll Accounting Settings before posting payroll.") from exc
    if event_record.event_type == "accrual":
        component_rows = event_record.payload.get("components", []) if isinstance(event_record.payload, dict) else []
        if component_rows:
            try:
                component_ids = {int(row["component_id"]) for row in component_rows if row.get("component_id")}
            except (TypeError, ValueError) as exc:
                raise ValidationError("Payroll component IDs must be integers.") from exc
            SalaryComponent = apps.get_model("gsshop_hr", "SalaryComponent")
            components = {
                row.pk: row
                for row in SalaryComponent.objects.filter(pk__in=component_ids, company_id=run.company_id)
            }
            if len(components) != len(component_ids):
                raise ValidationError("One or more payroll components do not belong to the payroll company.")
            mappings = {
                row.salary_component_id: row
                for row in PayrollComponentAccountMapping.objects.select_related("salary_component").filter(
                    salary_component_id__in=component_ids,
                    salary_component__company_id=run.company_id,
                    active=True,
                )
            }
            account_totals = {}
            gross_earnings = Decimal("0")
            total_deductions = Decimal("0")
            for component_row in component_rows:
                component_id = int(component_row["component_id"])
                amount = Decimal(str(component_row.get("amount", "0")))
                if amount <= 0:
                    continue
                mapping = mappings.get(component_id)
                component_type = component_row.get("component_type") or components[component_id].component_type
                if component_type == "deduction":
                    total_deductions += amount
                    account_id = mapping.payroll_account_override_id if mapping else config.payroll_payable_account_id
                    totals = account_totals.setdefault(account_id, {"debit": Decimal("0"), "credit": Decimal("0")})
                    totals["credit"] += amount
                else:
                    gross_earnings += amount
                    account_id = mapping.payroll_account_override_id if mapping else config.salary_expense_account_id
                    totals = account_totals.setdefault(account_id, {"debit": Decimal("0"), "credit": Decimal("0")})
                    totals["debit"] += amount
            net_payable = gross_earnings - total_deductions
            if net_payable < 0:
                raise ValidationError("Payroll component deductions cannot exceed earnings.")
            payable_totals = account_totals.setdefault(
                config.payroll_payable_account_id,
                {"debit": Decimal("0"), "credit": Decimal("0")},
            )
            payable_totals["credit"] += net_payable
            lines = [
                {
                    "account_id": account_id,
                    "debit": str(totals["debit"]),
                    "credit": str(totals["credit"]),
                    "description": "Payroll component accrual",
                }
                for account_id, totals in account_totals.items()
                if totals["debit"] or totals["credit"]
            ]
        else:
            lines = [
                {"account_id": config.salary_expense_account_id, "debit": str(event_record.amount), "credit": "0"},
                {"account_id": config.payroll_payable_account_id, "debit": "0", "credit": str(event_record.amount)},
            ]
    elif event_record.event_type == "payment":
        lines = [
            {"account_id": config.payroll_payable_account_id, "debit": str(event_record.amount), "credit": "0"},
            {"account_id": config.bank_account_id, "debit": "0", "credit": str(event_record.amount)},
        ]
    else:
        raise ValidationError("Use the accounting reversal workflow to reverse a posted payroll event.")
    bridge, _created = AccountingIntegrationEvent.objects.get_or_create(
        company_id=run.company_id, source_model=event_record._meta.label_lower, source_id=event_record.pk,
        event_type=event_record.event_type,
        defaults={
            "source_module": "payroll", "reference": event_record.reference,
            "event_date": event_record.event_date, "amount": event_record.amount,
            "payload": {"journal_id": config.journal_id, "lines": lines},
        },
    )
    if bridge.status != AccountingIntegrationEvent.Status.POSTED and config.auto_post:
        bridge = post_integration_event(bridge, actor)
    event_record.status = bridge.status
    event_record.journal_reference = bridge.journal_entry.number if bridge.journal_entry_id else ""
    event_record.save(update_fields=["status", "journal_reference", "updated_at"])
    return event_record


@transaction.atomic
def post_purchase_accounting_document(document, actor=None):
    locked = document.__class__.objects.select_for_update().select_related("order").get(pk=document.pk)
    rows = list(locked.lines.all())
    if not rows or not locked.balanced:
        raise ValidationError("Purchase accounting document must contain balanced lines matching the document total.")
    company_id = locked.order.company_id
    account_codes = {row.account_code for row in rows}
    accounts = {account.code: account for account in Account.objects.filter(company_id=company_id, code__in=account_codes)}
    if len(accounts) != len(account_codes):
        missing = sorted(account_codes - set(accounts))
        raise ValidationError(f"Configure purchase accounts for codes: {', '.join(missing)}")
    journal = _general_journal(company_id)
    bridge, _created = AccountingIntegrationEvent.objects.get_or_create(
        company_id=company_id, source_model=locked._meta.label_lower, source_id=locked.pk,
        event_type=locked.document_type,
        defaults={
            "source_module": "purchase", "reference": locked.number, "event_date": locked.document_date,
            "amount": locked.total_amount,
            "payload": {
                "journal_id": journal.pk,
                "lines": [
                    {
                        "account_id": accounts[row.account_code].pk, "description": row.description,
                        "debit": str(row.debit), "credit": str(row.credit),
                    }
                    for row in rows
                ],
            },
        },
    )
    if bridge.status != AccountingIntegrationEvent.Status.POSTED:
        bridge = post_integration_event(bridge, actor)
    locked.status = "approved"
    locked.journal_reference = bridge.journal_entry.number
    locked.save(update_fields=["status", "journal_reference", "updated_at"])
    return locked


@transaction.atomic
def confirm_sale_order_localization(localization, actor=None):
    from .models import LocalizedTaxTransactionLine, SaleOrderLocalization

    locked = SaleOrderLocalization.objects.select_for_update().select_related(
        "order__company", "order__customer", "profile"
    ).get(pk=localization.pk)
    if locked.status == SaleOrderLocalization.Status.CONFIRMED:
        return locked
    if locked.status not in {SaleOrderLocalization.Status.DRAFT, SaleOrderLocalization.Status.CALCULATED}:
        raise ValidationError("Only draft or calculated sales tax localization can be confirmed.")
    selections = list(locked.tax_lines.select_related("order_line", "tax_code", "tax_code__posting_account"))
    if not selections:
        raise ValidationError("Select at least one localized tax code for the sales order.")
    now = timezone.now()
    total_tax = Decimal("0")
    base_by_order_line = {}
    for selection in selections:
        base = selection.order_line.subtotal
        tax_amount = (base * selection.tax_code.rate / Decimal("100")).quantize(Decimal("0.01"))
        if tax_amount and selection.tax_code.posting_account_id is None:
            raise ValidationError(f"Configure a posting account for tax code {selection.tax_code.code}.")
        selection.tax_base = base
        selection.tax_amount = tax_amount
        selection.gross_amount = base + tax_amount
        selection.updated_at = now
        total_tax += tax_amount
        base_by_order_line[selection.order_line_id] = base
    locked.tax_lines.model.objects.bulk_update(
        selections, ["tax_base", "tax_amount", "gross_amount", "updated_at"]
    )
    existing = {
        (row.sale_order_line_id, row.tax_code_id): row
        for row in LocalizedTaxTransactionLine.objects.filter(
            profile_id=locked.profile_id, sale_order_id=locked.order_id,
            sale_order_line_id__in=base_by_order_line,
        )
    }
    creates, updates = [], []
    for selection in selections:
        values = {
            "partner_id": locked.order.customer_id, "transaction_date": locked.order.order_date,
            "document_number": locked.order.number, "document_type": "sale",
            "supply_category": str(selection.tax_code.metadata.get("category", "")) if isinstance(selection.tax_code.metadata, dict) else "",
            "quantity": selection.order_line.quantity, "tax_base": selection.tax_base,
            "tax_amount": selection.tax_amount, "gross_amount": selection.gross_amount,
            "currency": locked.order.company.currency, "authority_fields": selection.authority_fields,
        }
        source = existing.get((selection.order_line_id, selection.tax_code_id))
        if source is None:
            creates.append(LocalizedTaxTransactionLine(
                profile_id=locked.profile_id, tax_code_id=selection.tax_code_id,
                sale_order_id=locked.order_id, sale_order_line_id=selection.order_line_id, **values,
            ))
        else:
            for field_name, value in values.items():
                setattr(source, field_name, value)
            source.updated_at = now
            updates.append(source)
    LocalizedTaxTransactionLine.objects.bulk_create(creates)
    if updates:
        LocalizedTaxTransactionLine.objects.bulk_update(
            updates,
            ["partner", "transaction_date", "document_number", "document_type", "supply_category", "quantity",
             "tax_base", "tax_amount", "gross_amount", "currency", "authority_fields", "updated_at"],
        )
    locked.total_tax_base = sum(base_by_order_line.values(), Decimal("0"))
    locked.total_tax = total_tax
    locked.total_gross = locked.total_tax_base + total_tax
    locked.status = SaleOrderLocalization.Status.CONFIRMED
    locked.save(update_fields=["total_tax_base", "total_tax", "total_gross", "status", "updated_at"])
    _audit_for_company(
        locked.order.company_id, locked, "sale_order_localization_confirmed", actor,
        {"country_code": locked.profile.country_code, "total_tax": str(total_tax)},
    )
    return locked


@transaction.atomic
def post_sale_order_accounting(order, actor=None):
    locked = order.__class__.objects.select_for_update().select_related("company", "customer").get(pk=order.pk)
    if locked.status != locked.Status.CONFIRMED:
        raise ValidationError("Only a confirmed sales order can be completed and posted.")
    rows = list(locked.lines.select_related("product").filter(display_type=""))
    if not rows:
        raise ValidationError("Add at least one product line before completing the sales order.")
    commercial = CommercialAccountingSettings.objects.select_related("sales_journal").get(company_id=locked.company_id)
    accounting = AccountingSettings.objects.select_related("default_receivable_account").get(company_id=locked.company_id)
    partner_default = PartnerAccountingDefault.objects.select_related("receivable_account").filter(
        company_id=locked.company_id,
        partner_id=locked.customer_id,
        active=True,
    ).first()
    receivable_account = (
        partner_default.receivable_account
        if partner_default and partner_default.receivable_account_id
        else accounting.default_receivable_account
    )
    if receivable_account is None:
        raise ValidationError("Configure a receivable account for the customer or company.")
    product_ids = {row.product_id for row in rows}
    mappings = {
        mapping.product_id: mapping
        for mapping in ProductAccountMapping.objects.select_related(
            "revenue_account", "expense_account", "inventory_account"
        ).filter(company_id=locked.company_id, product_id__in=product_ids, active=True)
    }
    revenue_accounts = _resolve_product_account_ids(
        locked.company_id, [row.product for row in rows], "revenue_account"
    )
    missing = [row.product.sku for row in rows if row.product_id not in revenue_accounts]
    if missing:
        raise ValidationError(f"Configure revenue accounts for products: {', '.join(sorted(set(missing)))}")
    sales_subtotal = sum((row.subtotal for row in rows), Decimal("0"))
    from .models import SaleOrderLocalization

    localization = SaleOrderLocalization.objects.filter(order_id=locked.pk).first()
    if localization is not None:
        localization = confirm_sale_order_localization(localization, actor)
    sales_total = sales_subtotal + (localization.total_tax if localization else Decimal("0"))
    payload_lines = [{
        "account_id": receivable_account.pk,
        "debit": str(sales_total),
        "credit": "0",
        "description": locked.number,
    }]
    for row in rows:
        mapping = mappings.get(row.product_id)
        revenue = row.subtotal
        payload_lines.append({
            "account_id": revenue_accounts[row.product_id],
            "debit": "0",
            "credit": str(revenue),
            "description": row.description or str(row.product),
        })
        cogs = row.quantity * row.product.purchase_price
        if cogs > 0:
            if mapping is None or not mapping.inventory_account_id or not mapping.expense_account_id:
                raise ValidationError(f"Configure inventory and expense accounts for stock product {row.product.sku}.")
            payload_lines.extend([
                {
                    "account_id": mapping.expense_account_id,
                    "debit": str(cogs),
                    "credit": "0",
                    "description": f"COGS {row.product.sku}",
                },
                {
                    "account_id": mapping.inventory_account_id,
                    "debit": "0",
                    "credit": str(cogs),
                    "description": f"Inventory {row.product.sku}",
                },
            ])
    if localization:
        tax_totals = list(localization.tax_lines.values("tax_code__posting_account_id", "tax_code__code").annotate(
            amount=Sum("tax_amount")
        ))
        for tax_total in tax_totals:
            if tax_total["amount"]:
                payload_lines.append({
                    "account_id": tax_total["tax_code__posting_account_id"], "debit": "0",
                    "credit": str(tax_total["amount"]),
                    "description": f"Tax {tax_total['tax_code__code']} · {locked.number}",
                })
    bridge, _created = AccountingIntegrationEvent.objects.get_or_create(
        company_id=locked.company_id,
        source_model=locked._meta.label_lower,
        source_id=locked.pk,
        event_type="sale_order_complete",
        defaults={
            "source_module": "sales",
            "reference": locked.number,
            "event_date": locked.order_date,
            "amount": sales_total,
            "payload": {"journal_id": commercial.sales_journal_id, "lines": payload_lines},
        },
    )
    if bridge.status != AccountingIntegrationEvent.Status.POSTED and commercial.auto_post_sales:
        post_integration_event(bridge, actor)
    locked.total = sales_total
    locked.status = locked.Status.COMPLETED
    locked.save(update_fields=["total", "status", "updated_at"])
    return locked


@transaction.atomic
def post_inventory_stock_move(stock_move):
    config = InventoryAccountingSettings.objects.select_related(
        "journal", "inventory_account", "cogs_account", "variance_account"
    ).filter(company_id=stock_move.company_id).first()
    if config is None:
        return None
    amount = stock_move.quantity * stock_move.product.purchase_price
    if amount <= 0:
        return None
    incoming = stock_move.direction == stock_move.Direction.IN
    debit_account = config.inventory_account if incoming else config.cogs_account
    credit_account = config.variance_account if incoming else config.inventory_account
    bridge, _created = AccountingIntegrationEvent.objects.get_or_create(
        company_id=stock_move.company_id, source_model=stock_move._meta.label_lower, source_id=stock_move.pk,
        event_type="stock_in" if incoming else "stock_out",
        defaults={
            "source_module": "inventory", "reference": stock_move.reference or f"MOVE-{stock_move.pk}",
            "event_date": stock_move.movement_date, "amount": amount,
            "payload": {
                "journal_id": config.journal_id,
                "lines": [
                    {"account_id": debit_account.pk, "debit": str(amount), "credit": "0", "description": str(stock_move.product)},
                    {"account_id": credit_account.pk, "debit": "0", "credit": str(amount), "description": str(stock_move.product)},
                ],
            },
        },
    )
    if config.auto_post and bridge.status != AccountingIntegrationEvent.Status.POSTED:
        bridge = post_integration_event(bridge, stock_move.created_by)
    return bridge


@transaction.atomic
def approve_inventory_accounting_document(document, actor=None):
    locked = InventoryAccountingDocument.objects.select_for_update().get(pk=document.pk)
    if locked.status != InventoryAccountingDocument.Status.WAITING_APPROVAL:
        raise ValidationError("Only an inventory document waiting for approval can be approved.")
    locked.status = InventoryAccountingDocument.Status.APPROVED
    locked.approved_by = actor
    locked.save(update_fields=["status", "approved_by", "updated_at"])
    _audit_for_company(locked.company_id, locked, "inventory_accounting_approved", actor)
    return locked


@transaction.atomic
def post_inventory_accounting_document(document, actor=None):
    locked = InventoryAccountingDocument.objects.select_for_update().select_related("usage_type").get(pk=document.pk)
    if locked.status != InventoryAccountingDocument.Status.APPROVED:
        raise ValidationError("Only approved inventory accounting documents can be posted.")
    rows = list(locked.lines.select_related("product", "debit_account", "credit_account", "cost_center"))
    if not rows:
        raise ValidationError("Add at least one inventory accounting line.")
    config = InventoryAccountingSettings.objects.select_related(
        "journal", "inventory_account", "cogs_account", "variance_account",
        "usage_expense_account", "landed_cost_clearing_account",
    ).get(company_id=locked.company_id)
    product_ids = {row.product_id for row in rows}
    product_mappings = {
        mapping.product_id: mapping
        for mapping in ProductAccountMapping.objects.select_related("inventory_account", "expense_account").filter(
            company_id=locked.company_id,
            product_id__in=product_ids,
            active=True,
        )
    }
    allocated_amounts = {}
    if locked.document_type == InventoryAccountingDocument.DocumentType.LANDED_COST:
        if locked.source_amount <= 0:
            raise ValidationError("Landed cost requires a positive source amount.")
        weights = []
        for row in rows:
            if locked.allocation_method == "equal":
                weight = Decimal("1")
            elif locked.allocation_method == "quantity":
                weight = abs(row.quantity)
            else:
                weight = abs(row.quantity * row.unit_cost)
            weights.append(weight)
        weight_total = sum(weights, Decimal("0"))
        if weight_total <= 0:
            raise ValidationError("Landed cost allocation requires positive quantities or values.")
        allocated_total = Decimal("0")
        for index, row in enumerate(rows):
            amount = (
                locked.source_amount - allocated_total
                if index == len(rows) - 1
                else (locked.source_amount * weights[index] / weight_total).quantize(Decimal("0.01"))
            )
            allocated_total += amount
            allocated_amounts[row.pk] = amount
    journal_lines = []
    posted_total = Decimal("0")
    for row in rows:
        inventory_account = (
            product_mappings[row.product_id].inventory_account
            if row.product_id in product_mappings and product_mappings[row.product_id].inventory_account_id
            else config.inventory_account
        )
        if locked.document_type == InventoryAccountingDocument.DocumentType.LANDED_COST:
            if config.landed_cost_clearing_account_id is None:
                raise ValidationError("Configure the landed-cost clearing account.")
            amount = allocated_amounts[row.pk]
            debit_account = row.debit_account or inventory_account
            credit_account = row.credit_account or config.landed_cost_clearing_account
        elif locked.document_type == InventoryAccountingDocument.DocumentType.PRODUCT_USAGE:
            amount = row.quantity * row.unit_cost
            usage_expense_account = locked.usage_type.expense_account if locked.usage_type_id else config.usage_expense_account
            debit_account = row.debit_account or usage_expense_account
            credit_account = row.credit_account or inventory_account
        elif locked.document_type == InventoryAccountingDocument.DocumentType.STOCK_COUNT:
            difference = row.counted_quantity - row.book_quantity
            amount = abs(difference * row.unit_cost)
            if difference >= 0:
                debit_account = row.debit_account or inventory_account
                credit_account = row.credit_account or config.variance_account
            else:
                debit_account = row.debit_account or config.variance_account
                credit_account = row.credit_account or inventory_account
        elif locked.document_type == InventoryAccountingDocument.DocumentType.STOCK_BLOCK:
            amount = row.quantity * row.unit_cost
            debit_account = row.debit_account or config.variance_account
            credit_account = row.credit_account or inventory_account
        else:
            amount = row.quantity * row.unit_cost
            if not row.debit_account_id or not row.credit_account_id:
                raise ValidationError("Transfer lines require destination debit and source credit accounts.")
            debit_account = row.debit_account
            credit_account = row.credit_account
        if amount <= 0:
            continue
        if debit_account is None or credit_account is None:
            raise ValidationError(f"Configure debit and credit accounts for {row.product}.")
        posted_total += amount
        description = row.description or str(row.product)
        journal_lines.extend([
            JournalLine(entry=None, account=debit_account, cost_center=row.cost_center, description=description, debit=amount),
            JournalLine(entry=None, account=credit_account, cost_center=row.cost_center, description=description, credit=amount),
        ])
    if not journal_lines:
        raise ValidationError("Inventory accounting document has no non-zero values to post.")
    entry = JournalEntry.objects.create(
        company_id=locked.company_id,
        journal=config.journal,
        number=f"INV-{locked.number}",
        entry_date=locked.document_date,
        description=locked.note[:240] or locked.get_document_type_display(),
        source_type="inventory_accounting_document",
        source_reference=str(locked.pk),
    )
    for line in journal_lines:
        line.entry = entry
    JournalLine.objects.bulk_create(journal_lines)
    entry = post_journal_entry(entry, actor)
    if allocated_amounts:
        allocation_timestamp = timezone.now()
        for row in rows:
            row.allocated_amount = allocated_amounts[row.pk]
            row.updated_at = allocation_timestamp
        locked.lines.model.objects.bulk_update(rows, ["allocated_amount", "updated_at"])
    locked.posted_amount = posted_total
    locked.journal_entry = entry
    locked.status = InventoryAccountingDocument.Status.POSTED
    locked.save(update_fields=["posted_amount", "journal_entry", "status", "updated_at"])
    _audit_for_company(locked.company_id, locked, "inventory_accounting_posted", actor, {"journal_entry_id": entry.pk})
    return locked


@transaction.atomic
def reverse_inventory_accounting_document(document, actor=None):
    locked = InventoryAccountingDocument.objects.select_for_update().select_related("journal_entry").get(pk=document.pk)
    if locked.status != InventoryAccountingDocument.Status.POSTED or locked.journal_entry_id is None:
        raise ValidationError("Only a posted inventory accounting document can be reversed.")
    reverse_journal_entry(locked.journal_entry, actor)
    locked.status = InventoryAccountingDocument.Status.REVERSED
    locked.save(update_fields=["status", "updated_at"])
    _audit_for_company(locked.company_id, locked, "inventory_accounting_reversed", actor)
    return locked


def _post_simple_bridge(*, company_id, source, event_type, reference, event_date, amount, journal_id, lines, actor):
    bridge, _created = AccountingIntegrationEvent.objects.get_or_create(
        company_id=company_id, source_model=source._meta.label_lower, source_id=source.pk,
        event_type=event_type,
        defaults={
            "source_module": source._meta.app_label, "reference": reference, "event_date": event_date,
            "amount": amount, "payload": {"journal_id": journal_id, "lines": lines},
        },
    )
    if bridge.status != AccountingIntegrationEvent.Status.POSTED:
        bridge = post_integration_event(bridge, actor)
    return bridge


@transaction.atomic
def post_cash_advance_accounting(cash_advance, actor=None):
    locked = cash_advance.__class__.objects.select_for_update().select_related("employee").get(pk=cash_advance.pk)
    company_id = locked.employee.company_id
    try:
        config = ExpenseAdvanceAccountingSettings.objects.get(company_id=company_id)
    except ExpenseAdvanceAccountingSettings.DoesNotExist as exc:
        raise ValidationError("Configure Advance & Expense Accounting Settings before approval.") from exc
    _post_simple_bridge(
        company_id=company_id, source=locked, event_type="advance_disbursement", reference=locked.number,
        event_date=locked.request_date, amount=locked.amount, journal_id=config.journal_id, actor=actor,
        lines=[
            {"account_id": config.advance_account_id, "debit": str(locked.amount), "credit": "0"},
            {"account_id": config.bank_account_id, "debit": "0", "credit": str(locked.amount)},
        ],
    )
    locked.status = "approved"
    locked.approver = actor
    locked.save(update_fields=["status", "approver", "updated_at"])
    return locked


@transaction.atomic
def post_expense_claim_accounting(expense, action, actor=None):
    locked = ExpenseClaim.objects.select_for_update().select_related("cash_advance__employee", "product").get(
        pk=expense.pk
    )
    try:
        config = ExpenseAdvanceAccountingSettings.objects.get(company_id=locked.company_id)
    except ExpenseAdvanceAccountingSettings.DoesNotExist as exc:
        raise ValidationError("Configure Advance & Expense Accounting Settings before posting expenses.") from exc
    if action == "approve":
        event_type = "expense_recognition"
        advance_amount = min(locked.cash_advance.amount, locked.amount) if locked.cash_advance_id else Decimal("0")
        reimbursement_amount = max(locked.amount - advance_amount, Decimal("0"))
        return_amount = max((locked.cash_advance.amount if locked.cash_advance_id else Decimal("0")) - locked.amount, Decimal("0"))
        if locked.cash_advance_id and locked.cash_advance.status != "approved":
            raise ValidationError("Only an approved cash advance can be recognized against an expense.")
        expense_account_id = config.expense_account_id
        if locked.product_id:
            expense_account_id = _resolve_product_account_ids(
                locked.company_id, [locked.product], "expense_account"
            ).get(locked.product_id)
            if expense_account_id is None:
                raise ValidationError(f"Configure an expense account for product {locked.product}.")
        lines = [{"account_id": expense_account_id, "debit": str(locked.amount), "credit": "0"}]
        if advance_amount:
            lines.append({"account_id": config.advance_account_id, "debit": "0", "credit": str(advance_amount)})
        if reimbursement_amount:
            lines.append({"account_id": config.payable_account_id, "debit": "0", "credit": str(reimbursement_amount)})
        event = _post_simple_bridge(
            company_id=locked.company_id, source=locked, event_type=event_type, reference=locked.reference,
            event_date=locked.claim_date, amount=locked.amount, journal_id=config.journal_id, actor=actor,
            lines=lines,
        )
        settlement, created = ExpenseAdvanceSettlement.objects.get_or_create(
            expense=locked,
            defaults={
                "advance_amount": locked.cash_advance.amount if locked.cash_advance_id else Decimal("0"),
                "expense_amount": locked.amount,
                "reimbursement_amount": reimbursement_amount,
                "return_amount": return_amount,
                "recognition_event": event,
            },
        )
        if not created and settlement.status != ExpenseAdvanceSettlement.Status.REVERSED:
            raise ValidationError("This expense has already been recognized in accounting.")
        if not created:
            settlement.advance_amount = locked.cash_advance.amount if locked.cash_advance_id else Decimal("0")
            settlement.expense_amount = locked.amount
            settlement.reimbursement_amount = reimbursement_amount
            settlement.return_amount = return_amount
            settlement.recognition_event = event
        settlement.status = (
            ExpenseAdvanceSettlement.Status.CLOSED
            if not reimbursement_amount and not return_amount
            else ExpenseAdvanceSettlement.Status.RECOGNIZED
        )
        settlement.closed_at = timezone.now() if settlement.status == ExpenseAdvanceSettlement.Status.CLOSED else None
        settlement.save()
        next_status = (
            ExpenseClaim.Status.APPROVED if reimbursement_amount
            else ExpenseClaim.Status.RETURN_DUE if return_amount
            else ExpenseClaim.Status.CLOSED
        )
    elif action == "pay":
        if locked.status != ExpenseClaim.Status.APPROVED:
            raise ValidationError("Only approved expenses can be paid.")
        try:
            settlement = locked.advance_settlement
        except ExpenseAdvanceSettlement.DoesNotExist as exc:
            raise ValidationError("Recognize the expense before reimbursement.") from exc
        amount = settlement.reimbursement_amount
        if amount <= 0:
            raise ValidationError("This expense has no reimbursement amount.")
        event = _post_simple_bridge(
            company_id=locked.company_id, source=locked, event_type="expense_reimbursement", reference=locked.reference,
            event_date=locked.claim_date, amount=amount, journal_id=config.journal_id, actor=actor,
            lines=[
                {"account_id": config.payable_account_id, "debit": str(amount), "credit": "0"},
                {"account_id": config.bank_account_id, "debit": "0", "credit": str(amount)},
            ],
        )
        settlement.reimbursement_event = event
        settlement.status = ExpenseAdvanceSettlement.Status.CLOSED
        settlement.closed_at = timezone.now()
        settlement.save(update_fields=["reimbursement_event", "status", "closed_at", "updated_at"])
        next_status = ExpenseClaim.Status.CLOSED
    elif action == "return":
        if locked.status != ExpenseClaim.Status.RETURN_DUE:
            raise ValidationError("Only expenses with an outstanding advance return can record a return.")
        try:
            settlement = locked.advance_settlement
        except ExpenseAdvanceSettlement.DoesNotExist as exc:
            raise ValidationError("Recognize the expense before recording an advance return.") from exc
        amount = settlement.return_amount
        if amount <= 0:
            raise ValidationError("This expense has no advance amount to return.")
        event = _post_simple_bridge(
            company_id=locked.company_id, source=locked, event_type="advance_return", reference=locked.reference,
            event_date=locked.claim_date, amount=amount, journal_id=config.journal_id, actor=actor,
            lines=[
                {"account_id": config.bank_account_id, "debit": str(amount), "credit": "0"},
                {"account_id": config.advance_account_id, "debit": "0", "credit": str(amount)},
            ],
        )
        settlement.return_event = event
        settlement.status = ExpenseAdvanceSettlement.Status.CLOSED
        settlement.closed_at = timezone.now()
        settlement.save(update_fields=["return_event", "status", "closed_at", "updated_at"])
        next_status = ExpenseClaim.Status.CLOSED
    elif action == "reverse":
        try:
            settlement = locked.advance_settlement
        except ExpenseAdvanceSettlement.DoesNotExist as exc:
            raise ValidationError("This expense has no accounting settlement to reverse.") from exc
        for event in (settlement.return_event, settlement.reimbursement_event, settlement.recognition_event):
            if event and event.status == AccountingIntegrationEvent.Status.POSTED:
                reverse_integration_event(event, actor)
        settlement.status = ExpenseAdvanceSettlement.Status.REVERSED
        settlement.closed_at = None
        settlement.save(update_fields=["status", "closed_at", "updated_at"])
        next_status = ExpenseClaim.Status.REVERSED
    else:
        raise ValidationError("Unsupported expense accounting action.")
    locked.status = next_status
    locked.save(update_fields=["status", "updated_at"])
    return locked


def handle_external_accounting_workflow(entity, instance, action, actor=None):
    if entity == "sales" and action == "complete":
        return post_sale_order_accounting(instance, actor)
    if entity == "pos-sessions" and action == "close":
        return post_pos_session_accounting(instance, actor)
    if entity == "pos-sessions" and action == "reopen":
        return reverse_pos_session_accounting(instance, actor)
    if entity == "payroll-accounting-events" and action == "post":
        return post_payroll_accounting_event(instance, actor)
    if entity == "purchase-accounting-documents" and action == "approve":
        return post_purchase_accounting_document(instance, actor)
    if entity == "cash-advances" and action == "approve":
        return post_cash_advance_accounting(instance, actor)
    raise ValidationError("This cross-module accounting workflow has no transactional service.")


@transaction.atomic
def generate_compliance_report(report, actor=None):
    locked = ComplianceReport.objects.select_for_update().select_related("profile").get(pk=report.pk)
    if locked.status != ComplianceReport.Status.DRAFT:
        raise ValidationError("Only draft compliance reports can be generated.")
    template = StatutoryReportTemplate.objects.filter(
        profile=locked.profile, form_code=locked.report_type, active=True
    ).order_by("-version").first()
    from .localization_runtime import COUNTRY_BUILDERS, build_country_report

    country_code = locked.profile.country_code.strip().upper()
    if country_code in COUNTRY_BUILDERS and template is not None:
        result = build_country_report(locked, template.output_format)
        existing_by_code = {row.field_code: row for row in locked.lines.all()}
        creates = []
        updates = []
        for sequence, values in enumerate(result["lines"], start=1):
            current = existing_by_code.get(values["field_code"])
            attributes = {
                "sequence": sequence * 10, "label": values["label"], "value": values["value"],
                "tax_base": values["tax_base"], "tax_amount": values["tax_amount"],
                "metadata": values["metadata"],
            }
            if current is None:
                creates.append(StatutoryReportLine(report=locked, field_code=values["field_code"], **attributes))
            else:
                for field_name, value in attributes.items():
                    setattr(current, field_name, value)
                current.updated_at = timezone.now()
                updates.append(current)
        StatutoryReportLine.objects.bulk_create(creates)
        if updates:
            StatutoryReportLine.objects.bulk_update(
                updates, ["sequence", "label", "value", "tax_base", "tax_amount", "metadata", "updated_at"]
            )
        locked.totals = result["totals"]
        locked.payload = result["payload"]
        locked.payload["template"] = {"form_code": template.form_code, "version": template.version}
        locked.rendered_filename = result["rendered_filename"]
        locked.rendered_content = result["rendered_content"]
        locked.content_sha256 = result["content_sha256"]
        locked.status = ComplianceReport.Status.GENERATED
        locked.save(update_fields=[
            "totals", "payload", "rendered_filename", "rendered_content", "content_sha256", "status", "updated_at"
        ])
        _audit_for_company(
            locked.profile.company_id, locked, "compliance_report_generated", actor,
            {**locked.totals, "content_sha256": locked.content_sha256},
        )
        return locked
    lines = JournalLine.objects.filter(
        entry__company_id=locked.profile.company_id,
        entry__status=JournalEntry.Status.POSTED,
        entry__entry_date__range=(locked.period_start, locked.period_end),
    )
    totals = lines.aggregate(debit=Sum("debit"), credit=Sum("credit"))
    wht = WithholdingTaxTransaction.objects.filter(
        company_id=locked.profile.company_id,
        transaction_date__range=(locked.period_start, locked.period_end),
        status=WithholdingTaxTransaction.Status.VALIDATED,
    ).aggregate(gross=Sum("gross_amount"), tax=Sum("tax_amount"))
    locked.totals = {
        "debit": str(totals["debit"] or 0), "credit": str(totals["credit"] or 0),
        "withholding_gross": str(wht["gross"] or 0), "withholding_tax": str(wht["tax"] or 0),
    }
    locked.payload = {
        "country_code": locked.profile.country_code, "report_type": locked.report_type,
        "period_start": locked.period_start.isoformat(), "period_end": locked.period_end.isoformat(),
        "registration_number": locked.profile.registration_number, "totals": locked.totals,
    }
    if template:
        mappings = template.field_mapping if isinstance(template.field_mapping, dict) else {}
        existing_codes = set(locked.lines.values_list("field_code", flat=True))
        line_records = []
        for sequence, (field_code, source_key) in enumerate(mappings.items(), start=1):
            if field_code in existing_codes:
                continue
            value = locked.totals.get(str(source_key), "0")
            line_records.append(StatutoryReportLine(
                report=locked, sequence=sequence * 10, field_code=field_code,
                label=field_code.replace("_", " ").title(), value=str(value),
            ))
        StatutoryReportLine.objects.bulk_create(line_records)
        locked.payload["template"] = {"form_code": template.form_code, "version": template.version}
    locked.status = ComplianceReport.Status.GENERATED
    locked.save(update_fields=["totals", "payload", "status", "updated_at"])
    _audit_for_company(locked.profile.company_id, locked, "compliance_report_generated", actor, locked.totals)
    return locked


@transaction.atomic
def validate_compliance_report(report, actor=None):
    locked = ComplianceReport.objects.select_for_update().select_related("profile").get(pk=report.pk)
    if locked.status != ComplianceReport.Status.GENERATED:
        raise ValidationError("Only a generated compliance report can be validated.")
    template = StatutoryReportTemplate.objects.filter(
        profile=locked.profile,
        form_code=locked.report_type,
        active=True,
    ).order_by("-version").first()
    if template is None:
        raise ValidationError("Configure an active statutory report template before validation.")
    from .localization_runtime import COUNTRY_BUILDERS, validate_country_report

    if locked.profile.country_code.strip().upper() in COUNTRY_BUILDERS:
        validate_country_report(locked)
    rules = template.validation_rules
    if isinstance(rules, list):
        rules = {"required_fields": rules}
    if not isinstance(rules, dict):
        raise ValidationError("Template validation rules must be an object or a required-field list.")
    values = {line.field_code: line.value for line in locked.lines.all()}
    missing = [code for code in rules.get("required_fields", []) if not str(values.get(code, "")).strip()]
    if missing:
        raise ValidationError(f"Required statutory fields are missing: {', '.join(missing)}")
    invalid_non_negative = []
    for code in rules.get("non_negative_fields", []):
        try:
            if Decimal(str(values.get(code, "0"))) < 0:
                invalid_non_negative.append(code)
        except (ArithmeticError, ValueError):
            invalid_non_negative.append(code)
    if invalid_non_negative:
        raise ValidationError(f"Statutory fields must be non-negative numbers: {', '.join(invalid_non_negative)}")
    for equality in rules.get("equal_fields", []):
        if len(equality) != 2:
            raise ValidationError("Each equal-fields validation rule must contain exactly two field codes.")
        if Decimal(str(values.get(equality[0], "0"))) != Decimal(str(values.get(equality[1], "0"))):
            raise ValidationError(f"Statutory fields {equality[0]} and {equality[1]} must be equal.")
    if rules.get("registration_required", True) and not locked.profile.registration_number:
        raise ValidationError("The country tax profile requires a registration number.")
    locked.status = ComplianceReport.Status.VALIDATED
    locked.save(update_fields=["status", "updated_at"])
    _compliance_audit(locked.profile_id, locked, "compliance_report_validated", actor, {"template": template.form_code})
    return locked


def _compliance_audit(profile_id, instance, event_type, actor=None, details=None):
    ComplianceAuditEvent.objects.create(
        profile_id=profile_id, event_type=event_type, object_type=instance._meta.label_lower,
        object_id=instance.pk, reference=str(instance), actor=actor, details=details or {},
    )


@transaction.atomic
def validate_tax_connector(connector, actor=None):
    locked = TaxMiddlewareConnector.objects.select_for_update().get(pk=connector.pk)
    if not locked.endpoint_url or not locked.credential_alias:
        locked.status = TaxMiddlewareConnector.Status.ERROR
        locked.last_error = "Endpoint URL and credential alias are required."
        locked.last_health_check_at = timezone.now()
        locked.save(update_fields=["status", "last_error", "last_health_check_at", "updated_at"])
        raise ValidationError(locked.last_error)
    try:
        connector_backend(locked).validate_configuration()
    except ValidationError as exc:
        locked.status = TaxMiddlewareConnector.Status.ERROR
        locked.last_error = "; ".join(exc.messages)
        locked.last_health_check_at = timezone.now()
        locked.save(update_fields=["status", "last_error", "last_health_check_at", "updated_at"])
        raise
    locked.status = TaxMiddlewareConnector.Status.READY
    locked.last_error = ""
    locked.last_health_check_at = timezone.now()
    locked.save(update_fields=["status", "last_error", "last_health_check_at", "updated_at"])
    _compliance_audit(locked.profile_id, locked, "connector_configuration_validated", actor)
    return locked


@transaction.atomic
def queue_tax_authority_document(document, actor=None):
    locked = TaxAuthorityDocument.objects.select_for_update().select_related("profile").get(pk=document.pk)
    if locked.status not in {TaxAuthorityDocument.Status.DRAFT, TaxAuthorityDocument.Status.REJECTED}:
        raise ValidationError("Only draft or rejected tax documents can be queued.")
    connector = TaxMiddlewareConnector.objects.filter(profile=locked.profile, status=TaxMiddlewareConnector.Status.READY).first()
    if connector is None:
        raise ValidationError("Validate the country tax middleware connector before queueing documents.")
    locked.status = TaxAuthorityDocument.Status.QUEUED
    locked.error_message = ""
    locked.save(update_fields=["status", "error_message", "updated_at"])
    _compliance_audit(locked.profile_id, locked, "tax_document_queued", actor, {"connector_id": connector.pk})
    return locked


def dispatch_tax_authority_document(document, actor=None):
    with transaction.atomic():
        locked = TaxAuthorityDocument.objects.select_for_update().select_related("profile").get(pk=document.pk)
        if locked.status != TaxAuthorityDocument.Status.QUEUED:
            raise ValidationError("Only queued tax documents can be sent.")
        if locked.direction != "outgoing":
            raise ValidationError("Only outgoing tax documents can be sent to an authority.")
        try:
            connector = locked.profile.middleware_connector
        except TaxMiddlewareConnector.DoesNotExist as exc:
            raise ValidationError("Configure a tax middleware connector for this country profile.") from exc
        if connector.status != TaxMiddlewareConnector.Status.READY:
            raise ValidationError("Validate the tax middleware connector before sending documents.")
    try:
        result = connector_backend(connector).send_document(locked)
    except ValidationError as exc:
        TaxAuthorityDocument.objects.filter(pk=document.pk, status=TaxAuthorityDocument.Status.QUEUED).update(
            status=TaxAuthorityDocument.Status.REJECTED,
            error_message="; ".join(exc.messages),
            updated_at=timezone.now(),
        )
        raise
    accepted_statuses = {"accepted", "valid", "approved", "success"}
    rejected_statuses = {"rejected", "invalid", "failed", "error"}
    status = (
        TaxAuthorityDocument.Status.ACCEPTED if result.status in accepted_statuses
        else TaxAuthorityDocument.Status.REJECTED if result.status in rejected_statuses
        else TaxAuthorityDocument.Status.SENT
    )
    with transaction.atomic():
        locked = TaxAuthorityDocument.objects.select_for_update().get(pk=document.pk)
        if locked.status != TaxAuthorityDocument.Status.QUEUED:
            raise ValidationError("Tax document state changed while the authority request was running.")
        locked.status = status
        locked.external_reference = result.external_reference
        locked.response_payload = result.payload
        locked.error_message = "" if status != TaxAuthorityDocument.Status.REJECTED else str(result.payload)[:1000]
        locked.save(update_fields=[
            "status", "external_reference", "response_payload", "error_message", "updated_at"
        ])
        _compliance_audit(
            locked.profile_id, locked, "tax_document_dispatched", actor,
            {"status": status, "external_reference": result.external_reference},
        )
    return locked


def queue_tax_identifier_lookup(lookup, actor=None):
    with transaction.atomic():
        locked = TaxIdentifierLookup.objects.select_for_update().select_related("profile").get(pk=lookup.pk)
        if locked.status not in {TaxIdentifierLookup.Status.PENDING, TaxIdentifierLookup.Status.FAILED}:
            raise ValidationError("Only pending or failed identifier lookups can be run.")
        connector = TaxMiddlewareConnector.objects.filter(
            profile=locked.profile, status=TaxMiddlewareConnector.Status.READY
        ).first()
        if connector is None:
            raise ValidationError("Validate the country tax middleware connector before lookup.")
        locked.status = TaxIdentifierLookup.Status.PENDING
        locked.error_message = ""
        locked.save(update_fields=["status", "error_message", "updated_at"])
    try:
        result = connector_backend(connector).lookup_identifier(locked)
    except ValidationError as exc:
        TaxIdentifierLookup.objects.filter(pk=lookup.pk).update(
            status=TaxIdentifierLookup.Status.FAILED,
            error_message="; ".join(exc.messages),
            updated_at=timezone.now(),
        )
        raise
    found = result.status not in {"not_found", "missing", "rejected", "invalid"}
    with transaction.atomic():
        locked = TaxIdentifierLookup.objects.select_for_update().get(pk=lookup.pk)
        locked.status = TaxIdentifierLookup.Status.FOUND if found else TaxIdentifierLookup.Status.NOT_FOUND
        locked.result_name = str(result.payload.get("name") or result.payload.get("taxpayer_name") or "")[:200]
        locked.result = result.payload
        locked.error_message = ""
        locked.save(update_fields=["status", "result_name", "result", "error_message", "updated_at"])
        _compliance_audit(
            locked.profile_id, locked, "tax_identifier_lookup_completed", actor,
            {"status": locked.status, "external_reference": result.external_reference},
        )
    return locked


@transaction.atomic
def queue_tax_submission(report, actor=None):
    locked = ComplianceReport.objects.select_for_update().select_related("profile").get(pk=report.pk)
    if locked.status not in {ComplianceReport.Status.GENERATED, ComplianceReport.Status.VALIDATED}:
        raise ValidationError("Generate and validate the compliance report before submission.")
    connector = TaxMiddlewareConnector.objects.filter(
        profile=locked.profile, status=TaxMiddlewareConnector.Status.READY
    ).first()
    if connector is None:
        raise ValidationError("Validate the country tax middleware connector before submission.")
    submission = ExternalTaxSubmission.objects.create(report=locked, request_payload=locked.payload)
    locked.status = ComplianceReport.Status.SUBMITTED
    locked.save(update_fields=["status", "updated_at"])
    _audit_for_company(
        locked.profile.company_id,
        locked,
        "tax_submission_queued",
        actor,
        {"submission_id": submission.pk, "connector_id": connector.pk},
    )
    return locked


def dispatch_tax_submission(submission, actor=None):
    with transaction.atomic():
        locked = ExternalTaxSubmission.objects.select_for_update().select_related("report__profile").get(
            pk=submission.pk
        )
        if locked.status not in {"queued", "failed"}:
            raise ValidationError("Only queued or failed tax submissions can be dispatched.")
        try:
            connector = locked.report.profile.middleware_connector
        except TaxMiddlewareConnector.DoesNotExist as exc:
            raise ValidationError("Configure a tax middleware connector for this country profile.") from exc
        if connector.status != TaxMiddlewareConnector.Status.READY:
            raise ValidationError("Validate the tax middleware connector before dispatching submissions.")
    try:
        result = connector_backend(connector).submit_report(locked)
    except ValidationError as exc:
        ExternalTaxSubmission.objects.filter(pk=submission.pk).update(
            status="failed",
            retry_count=F("retry_count") + 1,
            error_message="; ".join(exc.messages),
            updated_at=timezone.now(),
        )
        raise
    status = "accepted" if result.status in {"accepted", "valid", "approved", "success"} else (
        "rejected" if result.status in {"rejected", "invalid", "failed", "error"} else "submitted"
    )
    with transaction.atomic():
        locked = ExternalTaxSubmission.objects.select_for_update().get(pk=submission.pk)
        if locked.status not in {"queued", "failed"}:
            raise ValidationError("Submission state changed while the authority request was running.")
        locked.status = status
        locked.submitted_at = timezone.now()
        locked.external_reference = result.external_reference
        locked.response_payload = result.payload
        locked.error_message = "" if status != "rejected" else str(result.payload)[:1000]
        locked.save(update_fields=[
            "status", "submitted_at", "external_reference", "response_payload", "error_message", "updated_at"
        ])
        profile_id = locked.report.profile_id
        _compliance_audit(
            profile_id, locked, "tax_submission_dispatched", actor,
            {"status": status, "external_reference": result.external_reference},
        )
    return locked


def apply_tax_authority_callback(callback, actor=None):
    """Verify and apply one idempotent authority callback."""
    import hashlib
    import hmac
    import json

    from django.conf import settings as django_settings

    from .models import TaxAuthorityCallbackEvent

    current = TaxAuthorityCallbackEvent.objects.select_related("profile").get(pk=callback.pk)
    profile_config = current.profile.configuration if isinstance(current.profile.configuration, dict) else {}
    alias = str(profile_config.get("callback_secret_alias") or "").strip()
    if not alias:
        raise ValidationError("Configure callback_secret_alias on the country tax profile.")
    configured = getattr(django_settings, "ACCOUNTING_CONNECTOR_CREDENTIALS", {}).get(alias)
    secret = configured.get("callback_secret") if isinstance(configured, dict) else configured
    if not secret:
        raise ValidationError("The callback signing secret is not configured in server settings.")
    serialized = json.dumps(current.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    expected = hmac.new(str(secret).encode("utf-8"), serialized, hashlib.sha256).hexdigest()
    supplied = current.signature.removeprefix("sha256=").strip().lower()
    if not supplied or not hmac.compare_digest(expected, supplied):
        TaxAuthorityCallbackEvent.objects.filter(pk=current.pk).update(
            status=TaxAuthorityCallbackEvent.Status.FAILED,
            error_message="Callback signature verification failed.",
            processed_at=timezone.now(),
            updated_at=timezone.now(),
        )
        raise ValidationError("Callback signature verification failed.")
    with transaction.atomic():
        locked = TaxAuthorityCallbackEvent.objects.select_for_update().select_related("profile").get(pk=callback.pk)
        if locked.payload != current.payload or locked.signature != current.signature:
            raise ValidationError("Callback changed during signature verification; retry the event.")
        if locked.status == TaxAuthorityCallbackEvent.Status.APPLIED:
            return locked
        normalized_status = str(locked.payload.get("status") or "").lower()
        document_status = {
            "accepted": TaxAuthorityDocument.Status.ACCEPTED,
            "approved": TaxAuthorityDocument.Status.ACCEPTED,
            "valid": TaxAuthorityDocument.Status.ACCEPTED,
            "rejected": TaxAuthorityDocument.Status.REJECTED,
            "invalid": TaxAuthorityDocument.Status.REJECTED,
            "cancelled": TaxAuthorityDocument.Status.CANCELLED,
        }.get(normalized_status, TaxAuthorityDocument.Status.SENT)
        updated = TaxAuthorityDocument.objects.filter(
            profile_id=locked.profile_id, external_reference=locked.external_reference
        ).update(
            status=document_status, response_payload=locked.payload,
            error_message=str(locked.payload.get("message") or "")[:2000] if document_status == TaxAuthorityDocument.Status.REJECTED else "",
            updated_at=timezone.now(),
        )
        submission_status = {
            TaxAuthorityDocument.Status.ACCEPTED: "accepted",
            TaxAuthorityDocument.Status.REJECTED: "rejected",
        }.get(document_status, "submitted")
        updated += ExternalTaxSubmission.objects.filter(
            report__profile_id=locked.profile_id, external_reference=locked.external_reference
        ).update(
            status=submission_status, response_payload=locked.payload,
            error_message=str(locked.payload.get("message") or "")[:2000] if submission_status == "rejected" else "",
            updated_at=timezone.now(),
        )
        locked.status = TaxAuthorityCallbackEvent.Status.APPLIED if updated else TaxAuthorityCallbackEvent.Status.IGNORED
        locked.error_message = "" if updated else "No matching submitted authority document was found."
        locked.processed_at = timezone.now()
        locked.save(update_fields=["status", "error_message", "processed_at", "updated_at"])
        _compliance_audit(
            locked.profile_id, locked, "tax_authority_callback_applied", actor,
            {"matched_records": updated, "external_reference": locked.external_reference},
        )
        return locked


def sync_received_tax_documents(cursor, actor=None):
    """Fetch received IRBM/Peppol documents without holding a DB lock over I/O."""
    from .models import TaxAuthoritySyncCursor

    current = TaxAuthoritySyncCursor.objects.select_related("profile").get(pk=cursor.pk)
    try:
        connector = current.profile.middleware_connector
    except TaxMiddlewareConnector.DoesNotExist as exc:
        raise ValidationError("Configure a tax middleware connector before synchronizing received documents.") from exc
    if connector.status != TaxMiddlewareConnector.Status.READY:
        raise ValidationError("Validate the tax middleware connector before synchronization.")
    result = connector_backend(connector).fetch_received_documents(current)
    documents = result.payload.get("documents") or result.payload.get("items") or []
    if not isinstance(documents, list):
        raise ValidationError("Authority received-document response requires a documents array.")
    references = {
        str(item.get("external_reference") or item.get("uuid") or item.get("id") or "").strip()
        for item in documents if isinstance(item, dict)
    } - {""}
    existing = set(TaxAuthorityDocument.objects.filter(
        profile_id=current.profile_id, direction="incoming", external_reference__in=references
    ).values_list("external_reference", flat=True))
    creates = []
    for item in documents:
        if not isinstance(item, dict):
            continue
        reference = str(item.get("external_reference") or item.get("uuid") or item.get("id") or "").strip()
        if not reference or reference in existing:
            continue
        issue_date = item.get("issue_date") or timezone.localdate()
        if isinstance(issue_date, str):
            try:
                issue_date = date.fromisoformat(issue_date[:10])
            except ValueError as exc:
                raise ValidationError(f"Authority document {reference} has an invalid issue_date.") from exc
        creates.append(TaxAuthorityDocument(
            profile_id=current.profile_id, direction="incoming",
            document_type=str(item.get("document_type") or item.get("type") or current.resource_type)[:50],
            internal_reference=f"RECV-{reference}"[:100], external_reference=reference[:120],
            issue_date=issue_date,
            status=TaxAuthorityDocument.Status.RECEIVED, payload=item, response_payload=result.payload,
        ))
    with transaction.atomic():
        locked = TaxAuthoritySyncCursor.objects.select_for_update().get(pk=cursor.pk)
        TaxAuthorityDocument.objects.bulk_create(creates)
        locked.cursor = str(result.payload.get("next_cursor") or result.payload.get("cursor") or locked.cursor)[:255]
        locked.last_sync_at = timezone.now()
        locked.last_status = "completed"
        locked.last_error = ""
        locked.metadata = {"received": len(documents), "created": len(creates)}
        locked.save(update_fields=["cursor", "last_sync_at", "last_status", "last_error", "metadata", "updated_at"])
        _compliance_audit(locked.profile_id, locked, "received_tax_documents_synchronized", actor, locked.metadata)
        return locked


def sync_regulatory_rates(run, actor=None):
    """Synchronize BI/authority rates in one provider call and batched writes."""
    from .models import RegulatoryExchangeRate, RegulatoryRateSyncRun
    from .rate_providers import rate_provider

    current = RegulatoryRateSyncRun.objects.select_related("profile").get(pk=run.pk)
    if current.status not in {"pending", "failed"}:
        raise ValidationError("Only pending or failed regulatory rate runs can be synchronized.")
    try:
        result = rate_provider(current).fetch()
    except (ArithmeticError, KeyError, TypeError, ValueError, ValidationError) as exc:
        message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        RegulatoryRateSyncRun.objects.filter(pk=run.pk).update(
            status="failed", error_message=message, updated_at=timezone.now()
        )
        raise ValidationError(message) from exc
    keys = {(row["rate_date"], row["currency"]) for row in result.rates}
    existing = {
        (record.rate_date, record.currency): record
        for record in RegulatoryExchangeRate.objects.filter(
            profile_id=current.profile_id,
            rate_date__range=(current.date_from, current.date_to),
            currency__in={currency for _, currency in keys},
            source=current.source,
        )
    }
    now = timezone.now()
    creates, updates = [], []
    for values in result.rates:
        key = (values["rate_date"], values["currency"])
        record = existing.get(key)
        if record is None:
            creates.append(RegulatoryExchangeRate(profile_id=current.profile_id, source=current.source, **values))
        else:
            record.rate = values["rate"]
            record.reference = values["reference"]
            record.payload = values["payload"]
            record.updated_at = now
            updates.append(record)
    with transaction.atomic():
        locked = RegulatoryRateSyncRun.objects.select_for_update().get(pk=run.pk)
        RegulatoryExchangeRate.objects.bulk_create(creates)
        if updates:
            RegulatoryExchangeRate.objects.bulk_update(updates, ["rate", "reference", "payload", "updated_at"])
        locked.status = "completed"
        locked.imported_count = len(creates) + len(updates)
        locked.response_payload = result.payload
        locked.error_message = ""
        locked.save(update_fields=["status", "imported_count", "response_payload", "error_message", "updated_at"])
        _compliance_audit(
            locked.profile_id, locked, "regulatory_rates_synchronized", actor,
            {"source": locked.source, "imported_count": locked.imported_count},
        )
        return locked


def parse_bank_statement_job(job):
    current = BankStatementParseJob.objects.get(pk=job.pk)
    if current.status not in {BankStatementParseJob.Status.QUEUED, BankStatementParseJob.Status.FAILED}:
        raise ValidationError("Only queued or failed parse jobs can be processed.")
    try:
        from .bank_parsers import bank_parser

        result = bank_parser(current).parse()
        if not result.rows:
            raise ValidationError("No bank transactions were detected in the supplied content.")
        maximum_rows = int((current.provider_options or {}).get("max_rows", 5000))
        if len(result.rows) > maximum_rows:
            raise ValidationError(f"Parsed bank result exceeds the configured {maximum_rows}-row limit.")
        rows = []
        date_formats = (current.provider_options or {}).get(
            "date_formats", ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"]
        )
        for row_number, source in enumerate(result.rows, start=1):
            if not isinstance(source, dict):
                raise ValidationError(f"Parsed bank row {row_number} is not an object.")
            raw_date = str(source.get("date") or source.get("transaction_date") or "").strip()
            parsed_date = None
            for date_format in date_formats:
                try:
                    parsed_date = datetime.strptime(raw_date, date_format).date()
                    break
                except ValueError:
                    continue
            if parsed_date is None:
                raise ValidationError(f"Parsed bank row {row_number} has an invalid date: {raw_date!r}.")
            raw_amount = str(source.get("amount", "0")).strip()
            negative = raw_amount.startswith("(") and raw_amount.endswith(")")
            amount = Decimal(raw_amount.strip("()").replace(",", ""))
            if negative:
                amount = -amount
            rows.append({
                "date": parsed_date.isoformat(),
                "reference": str(source.get("reference") or source.get("transaction_id") or "")[:80],
                "description": str(source.get("description") or source.get("memo") or "")[:240],
                "amount": str(amount),
                "confidence": str(source.get("confidence", result.confidence)),
            })
    except (ArithmeticError, AttributeError, TypeError, ValueError, ValidationError) as exc:
        BankStatementParseJob.objects.filter(
            pk=current.pk, status__in=[BankStatementParseJob.Status.QUEUED, BankStatementParseJob.Status.FAILED]
        ).update(status=BankStatementParseJob.Status.FAILED, error_message=str(exc), updated_at=timezone.now())
        raise ValidationError(str(exc)) from exc
    with transaction.atomic():
        locked = BankStatementParseJob.objects.select_for_update().get(pk=current.pk)
        if locked.status not in {BankStatementParseJob.Status.QUEUED, BankStatementParseJob.Status.FAILED}:
            raise ValidationError("Parse job state changed while the provider request was running.")
        locked.parsed_rows = rows
        locked.row_count = len(rows)
        locked.confidence = result.confidence
        locked.raw_provider_result = result.raw_result
        auto_import_threshold = Decimal(str((locked.provider_options or {}).get("auto_import_confidence", "99")))
        locked.status = (
            BankStatementParseJob.Status.PARSED
            if result.confidence >= auto_import_threshold
            else BankStatementParseJob.Status.REVIEW
        )
        locked.error_message = ""
        locked.save(update_fields=[
            "parsed_rows", "row_count", "confidence", "raw_provider_result", "status", "error_message", "updated_at"
        ])
        return locked


@transaction.atomic
def import_bank_statement_job(job):
    locked = BankStatementParseJob.objects.select_for_update().select_related("bank_account").get(pk=job.pk)
    if locked.status not in {BankStatementParseJob.Status.PARSED, BankStatementParseJob.Status.REVIEW}:
        raise ValidationError("Only parsed or reviewed bank data can be imported.")
    if locked.bank_statement_id:
        raise ValidationError("This parse job has already been imported.")
    normalized_rows = [
        {
            **row,
            "date": datetime.strptime(row["date"], "%Y-%m-%d").date(),
            "amount": Decimal(str(row["amount"])),
        }
        for row in locked.parsed_rows
    ]
    if not normalized_rows:
        raise ValidationError("No parsed bank rows are available for import.")
    dates = [row["date"] for row in normalized_rows]
    total = sum((row["amount"] for row in normalized_rows), Decimal("0"))
    statement = BankStatement.objects.create(
        company_id=locked.company_id,
        bank_account=locked.bank_account,
        reference=f"IMP-{locked.pk}",
        date_from=min(dates),
        date_to=max(dates),
        opening_balance=locked.bank_account.opening_balance,
        closing_balance=locked.bank_account.opening_balance + total,
        status=BankStatement.Status.IMPORTED,
        import_source=locked.filename,
    )
    BankStatementLine.objects.bulk_create([
        BankStatementLine(
            statement=statement,
            transaction_date=row["date"],
            reference=str(row.get("reference", ""))[:80],
            description=str(row.get("description", ""))[:240] or "Imported bank transaction",
            amount=row["amount"],
        )
        for row in normalized_rows
    ])
    locked.bank_statement = statement
    locked.status = BankStatementParseJob.Status.IMPORTED
    locked.save(update_fields=["bank_statement", "status", "updated_at"])
    return locked


@transaction.atomic
def provision_company_accounting(run, actor=None):
    locked = CompanyAccountingProvisioningRun.objects.select_for_update().get(pk=run.pk)
    if locked.status != "pending":
        raise ValidationError("Only pending provisioning runs can be executed.")
    company = locked.company
    core_definitions = [
        ("1000", "Cash and Bank", Account.AccountType.ASSET),
        ("1100", "Trade Receivable", Account.AccountType.ASSET),
        ("2100", "Trade Payable", Account.AccountType.LIABILITY),
        ("2200", "Tax Payable", Account.AccountType.LIABILITY),
        ("3000", "Retained Earnings", Account.AccountType.EQUITY),
        ("4000", "Revenue", Account.AccountType.INCOME),
        ("5000", "Cost and Expense", Account.AccountType.EXPENSE),
        ("4200", "Exchange / Disposal Gain", Account.AccountType.INCOME),
        ("5200", "Exchange / Disposal Loss", Account.AccountType.EXPENSE),
    ]
    extension_accounts, extension_journals = accounting_blueprint()
    definitions = core_definitions + [
        (item.code, item.name, item.account_type) for item in extension_accounts
    ]
    reconciliation_codes = {"1000", "1100", "2100"} | {
        item.code for item in extension_accounts if item.allow_reconciliation
    }
    existing_codes = set(Account.objects.filter(company=company, code__in=[item[0] for item in definitions]).values_list("code", flat=True))
    new_accounts = [
        Account(
            company=company,
            code=code,
            name=name,
            account_type=account_type,
            allow_reconciliation=code in reconciliation_codes,
        )
        for code, name, account_type in definitions if code not in existing_codes
    ]
    Account.objects.bulk_create(new_accounts)
    accounts = {account.code: account for account in Account.objects.filter(company=company, code__in=[item[0] for item in definitions])}
    journal_defs = [
        ("GEN", "General Journal", AccountJournal.JournalType.GENERAL, "5000", "4000"),
        ("BNK", "Bank Journal", AccountJournal.JournalType.BANK, "1000", "1000"),
    ] + [
        (
            item.code,
            item.name,
            item.journal_type,
            item.debit_account_code,
            item.credit_account_code,
        )
        for item in extension_journals
    ]
    existing_journals = set(AccountJournal.objects.filter(company=company, code__in=[item[0] for item in journal_defs]).values_list("code", flat=True))
    new_journals = [
        AccountJournal(
            company=company, code=code, name=name, journal_type=journal_type,
            default_debit_account=accounts[debit_code], default_credit_account=accounts[credit_code],
        )
        for code, name, journal_type, debit_code, credit_code in journal_defs if code not in existing_journals
    ]
    AccountJournal.objects.bulk_create(new_journals)
    journals = {
        journal.code: journal
        for journal in AccountJournal.objects.filter(company=company, code__in=[item[0] for item in journal_defs])
    }
    if not AccountingSettings.objects.filter(company=company).exists():
        AccountingSettings.objects.create(
            company=company, default_receivable_account=accounts["1100"], default_payable_account=accounts["2100"],
            exchange_gain_account=accounts["4200"], exchange_loss_account=accounts["5200"],
            retained_earnings_account=accounts["3000"],
        )
    pack = country_pack(locked.country_code)
    tax_defs = pack.taxes if pack else ()
    tax_codes = [definition.code for definition in tax_defs]
    existing_tax_codes = set(
        AccountTax.objects.filter(company=company, code__in=tax_codes).values_list("code", flat=True)
    )
    AccountTax.objects.bulk_create([
        AccountTax(
            company=company,
            code=definition.code,
            name=definition.name,
            tax_scope=(
                AccountTax.TaxScope.PURCHASE
                if definition.tax_type == "withholding"
                else AccountTax.TaxScope.SALES
            ),
            rate=definition.rate,
            account=accounts["2200"],
        )
        for definition in tax_defs if definition.code not in existing_tax_codes
    ])
    localization_profile = company.country_tax_profiles.filter(
        country_code__iexact=locked.country_code
    ).first()
    localization_results = [provision_country_profile(localization_profile)] if localization_profile else []
    extension_results = run_company_accounting_provisioners(
        company=company,
        accounts=accounts,
        journals=journals,
        actor=actor,
    )
    extension_account_count = sum(result.get("accounts_created", 0) for result in extension_results)
    locked.status = "completed"
    locked.created_accounts = len(new_accounts) + extension_account_count
    locked.created_journals = len(new_journals)
    locked.created_taxes = len(tax_defs) - len(existing_tax_codes)
    locked.details = {
        "country_code": locked.country_code,
        "account_codes": list(accounts),
        "localization_profiles": localization_results,
        "extensions": extension_results,
    }
    locked.save(update_fields=["status", "created_accounts", "created_journals", "created_taxes", "details", "updated_at"])
    _audit_for_company(company.pk, locked, "company_accounting_provisioned", actor, locked.details)
    return locked


@transaction.atomic
def post_deposit_conversion(conversion, actor=None):
    locked = AccountDepositConversion.objects.select_for_update().select_related(
        "deposit__journal", "deposit__partner"
    ).get(pk=conversion.pk)
    if locked.status != AccountDepositConversion.Status.DRAFT:
        raise ValidationError("Only draft deposit conversions can be posted.")
    rows = list(locked.lines.select_related("invoice"))
    if not rows:
        raise ValidationError("Add at least one invoice to the deposit conversion.")
    total = sum((row.amount for row in rows), Decimal("0"))
    deposit = AccountDeposit.objects.select_for_update().get(pk=locked.deposit_id)
    if total > deposit.unapplied_amount:
        raise ValidationError("Conversion total exceeds the unapplied deposit amount.")
    invoice_ids = {row.invoice_id for row in rows}
    invoices = {invoice.pk: invoice for invoice in Invoice.objects.select_for_update().filter(pk__in=invoice_ids)}
    expected_invoice_type = (
        Invoice.InvoiceType.CUSTOMER
        if deposit.deposit_type == AccountDeposit.DepositType.CUSTOMER
        else Invoice.InvoiceType.VENDOR
    )
    for row in rows:
        invoice = invoices[row.invoice_id]
        if invoice.invoice_type != expected_invoice_type:
            raise ValidationError("Deposit conversion invoices must match the customer/vendor deposit type.")
        if row.amount > invoice.balance:
            raise ValidationError(f"Conversion exceeds invoice balance for {invoice}.")
    settings = AccountingSettings.objects.select_related("default_receivable_account", "default_payable_account").get(
        company_id=deposit.company_id
    )
    clearing_account = deposit.journal.default_credit_account
    if clearing_account is None:
        raise ValidationError("Configure the deposit journal credit account before conversion.")
    entry = JournalEntry.objects.create(
        company_id=deposit.company_id, journal=deposit.journal, number=f"DCV-{locked.number}",
        entry_date=locked.conversion_date, description=locked.note[:240],
        source_type="deposit_conversion", source_reference=str(locked.pk),
    )
    customer_deposit = deposit.deposit_type == AccountDeposit.DepositType.CUSTOMER
    journal_lines = [JournalLine(
        entry=entry,
        account=clearing_account,
        partner=deposit.partner,
        debit=total if customer_deposit else Decimal("0"),
        credit=total if not customer_deposit else Decimal("0"),
    )]
    for row in rows:
        invoice = invoices[row.invoice_id]
        partner_account = (
            settings.default_receivable_account
            if invoice.invoice_type == invoice.InvoiceType.CUSTOMER
            else settings.default_payable_account
        )
        if partner_account is None:
            raise ValidationError("Configure receivable and payable accounts before deposit conversion.")
        journal_lines.append(JournalLine(
            entry=entry,
            account=partner_account,
            partner=deposit.partner,
            debit=row.amount if not customer_deposit else Decimal("0"),
            credit=row.amount if customer_deposit else Decimal("0"),
        ))
        invoice.paid_amount += row.amount
        if invoice.paid_amount >= invoice.total:
            invoice.status = invoice.Status.PAID
    JournalLine.objects.bulk_create(journal_lines)
    entry = post_journal_entry(entry, actor)
    Invoice.objects.bulk_update(list(invoices.values()), ["paid_amount", "status", "updated_at"])
    deposit.unapplied_amount -= total
    deposit.status = AccountDeposit.Status.APPLIED if deposit.unapplied_amount == 0 else AccountDeposit.Status.RECEIVED
    deposit.save(update_fields=["unapplied_amount", "status", "updated_at"])
    locked.total_amount = total
    locked.status = AccountDepositConversion.Status.POSTED
    locked.journal_entry = entry
    locked.save(update_fields=["total_amount", "status", "journal_entry", "updated_at"])
    AccountDepositHistory.objects.create(
        deposit=deposit, action="convert", amount=total, previous_status=AccountDeposit.Status.RECEIVED,
        new_status=deposit.status, actor=actor, details={"conversion_id": locked.pk},
    )
    _audit(deposit, "deposit_converted", actor, {"conversion_id": locked.pk, "amount": str(total)})
    return locked


@transaction.atomic
def reverse_deposit_conversion(conversion, actor=None):
    locked = AccountDepositConversion.objects.select_for_update().select_related("deposit").get(pk=conversion.pk)
    if locked.status != AccountDepositConversion.Status.POSTED or locked.journal_entry_id is None:
        raise ValidationError("Only posted deposit conversions can be reversed.")
    rows = list(locked.lines.all())
    invoice_ids = {row.invoice_id for row in rows}
    invoices = {invoice.pk: invoice for invoice in Invoice.objects.select_for_update().filter(pk__in=invoice_ids)}
    reverse_journal_entry(locked.journal_entry, actor)
    for row in rows:
        invoice = invoices[row.invoice_id]
        invoice.paid_amount = max(Decimal("0"), invoice.paid_amount - row.amount)
        if invoice.status == invoice.Status.PAID and invoice.paid_amount < invoice.total:
            invoice.status = invoice.Status.POSTED
    Invoice.objects.bulk_update(list(invoices.values()), ["paid_amount", "status", "updated_at"])
    deposit = AccountDeposit.objects.select_for_update().get(pk=locked.deposit_id)
    previous_status = deposit.status
    deposit.unapplied_amount += locked.total_amount
    deposit.status = AccountDeposit.Status.RECEIVED
    deposit.save(update_fields=["unapplied_amount", "status", "updated_at"])
    locked.status = AccountDepositConversion.Status.REVERSED
    locked.save(update_fields=["status", "updated_at"])
    AccountDepositHistory.objects.create(
        deposit=deposit, action="reverse_conversion", amount=locked.total_amount, previous_status=previous_status,
        new_status=deposit.status, actor=actor, details={"conversion_id": locked.pk},
    )
    return locked


@transaction.atomic
def generate_bank_cash_valuation(valuation, actor=None):
    locked = BankCashValuation.objects.select_for_update().get(pk=valuation.pk)
    if locked.status != BankCashValuation.Status.DRAFT:
        raise ValidationError("Only draft bank/cash valuations can be generated.")
    rows = list(locked.lines.all())
    if not rows:
        raise ValidationError("Add at least one bank/cash valuation line.")
    invalid = [row.pk for row in rows if row.revalued_value - row.book_value != row.gain_loss]
    if invalid:
        raise ValidationError("One or more valuation lines have an invalid gain/loss amount.")
    locked.total_gain_loss = sum((row.gain_loss for row in rows), Decimal("0"))
    locked.status = BankCashValuation.Status.GENERATED
    locked.save(update_fields=["total_gain_loss", "status", "updated_at"])
    _audit(locked, "bank_cash_valuation_generated", actor, {"line_count": len(rows)})
    return locked


@transaction.atomic
def post_bank_cash_valuation(valuation, actor=None):
    locked = BankCashValuation.objects.select_for_update().get(pk=valuation.pk)
    if locked.status != BankCashValuation.Status.GENERATED:
        raise ValidationError("Generate the bank/cash valuation before posting.")
    rows = list(locked.lines.select_related("bank_account__journal"))
    settings = AccountingSettings.objects.select_related("exchange_gain_account", "exchange_loss_account").get(
        company_id=locked.company_id
    )
    if not settings.exchange_gain_account_id or not settings.exchange_loss_account_id:
        raise ValidationError("Configure exchange gain and loss accounts before posting valuation.")
    entry = JournalEntry.objects.create(
        company_id=locked.company_id, journal=_general_journal(locked.company_id), number=f"BCV-{locked.number}",
        entry_date=locked.valuation_date, source_type="bank_cash_valuation", source_reference=str(locked.pk),
    )
    lines = []
    for row in rows:
        bank_ledger = row.bank_account.journal.default_debit_account
        if bank_ledger is None:
            raise ValidationError(f"Configure a debit account for bank journal {row.bank_account.journal}.")
        amount = abs(row.gain_loss)
        if row.gain_loss > 0:
            lines.extend([
                JournalLine(entry=entry, account=bank_ledger, debit=amount),
                JournalLine(entry=entry, account=settings.exchange_gain_account, credit=amount),
            ])
        elif row.gain_loss < 0:
            lines.extend([
                JournalLine(entry=entry, account=settings.exchange_loss_account, debit=amount),
                JournalLine(entry=entry, account=bank_ledger, credit=amount),
            ])
    if not lines:
        raise ValidationError("Valuation does not contain a non-zero gain or loss.")
    JournalLine.objects.bulk_create(lines)
    entry = post_journal_entry(entry, actor)
    locked.journal_entry = entry
    locked.status = BankCashValuation.Status.POSTED
    locked.save(update_fields=["journal_entry", "status", "updated_at"])
    _audit(locked, "bank_cash_valuation_posted", actor, {"journal_entry_id": entry.pk})
    return locked


def _active_ar_allocated_by_line(line_ids):
    allocated = {line_id: Decimal("0") for line_id in line_ids}
    rows = ARReconciliationAllocation.objects.filter(
        Q(debit_line_id__in=line_ids) | Q(credit_line_id__in=line_ids),
        status=ARReconciliationAllocation.Status.ACTIVE,
    ).values("debit_line_id", "credit_line_id", "amount")
    for row in rows:
        if row["debit_line_id"] in allocated:
            allocated[row["debit_line_id"]] += row["amount"]
        if row["credit_line_id"] in allocated:
            allocated[row["credit_line_id"]] += row["amount"]
    return allocated


@transaction.atomic
def suggest_ar_reconciliation_session(session, actor=None):
    """Populate a bounded FIFO suggestion from opposite open AR lines."""
    locked = ARReconciliationSession.objects.select_for_update().get(pk=session.pk)
    if locked.status != ARReconciliationSession.Status.DRAFT:
        raise ValidationError("Only draft AR reconciliation sessions can be suggested.")
    if locked.lines.exists():
        raise ValidationError("Suggestions can only be generated for an empty reconciliation session.")
    candidates = list(
        JournalLine.objects.filter(
            entry__company_id=locked.company_id,
            entry__status=JournalEntry.Status.POSTED,
            partner_id=locked.partner_id,
            account__allow_reconciliation=True,
            reconciled=False,
        )
        .exclude(Q(debit=0) & Q(credit=0))
        .select_related("entry", "account")
        .order_by("entry__entry_date", "id")[:200]
    )
    if not candidates:
        raise ValidationError("No open receivable lines are available for this partner.")
    allocated = _active_ar_allocated_by_line([line.pk for line in candidates])
    remaining = {
        line.pk: max((line.debit or line.credit) - allocated[line.pk], Decimal("0"))
        for line in candidates
    }
    suggested = {}
    grouped = {}
    for line in candidates:
        if remaining[line.pk] <= 0:
            continue
        key = (line.account_id, line.currency or line.entry.currency)
        bucket = grouped.setdefault(key, {"debit": [], "credit": []})
        bucket["debit" if line.debit > 0 else "credit"].append(line)
    for bucket in grouped.values():
        debit_index = credit_index = 0
        while debit_index < len(bucket["debit"]) and credit_index < len(bucket["credit"]):
            debit_line = bucket["debit"][debit_index]
            credit_line = bucket["credit"][credit_index]
            amount = min(remaining[debit_line.pk], remaining[credit_line.pk])
            if amount > 0:
                suggested[debit_line.pk] = suggested.get(debit_line.pk, Decimal("0")) + amount
                suggested[credit_line.pk] = suggested.get(credit_line.pk, Decimal("0")) + amount
                remaining[debit_line.pk] -= amount
                remaining[credit_line.pk] -= amount
            if remaining[debit_line.pk] == 0:
                debit_index += 1
            if remaining[credit_line.pk] == 0:
                credit_index += 1
    if not suggested:
        raise ValidationError("No opposite open receivable lines can be paired for this partner.")
    creates = []
    for line_id, amount in suggested.items():
        creates.append(ARReconciliationLine(session=locked, journal_line_id=line_id, amount=amount))
    ARReconciliationLine.objects.bulk_create(creates)
    _audit(locked, "ar_reconciliation_suggested", actor, {"journal_line_ids": sorted(suggested)})
    return locked


@transaction.atomic
def reconcile_ar_session(session, actor=None):
    locked = ARReconciliationSession.objects.select_for_update().get(pk=session.pk)
    if locked.status != ARReconciliationSession.Status.DRAFT:
        raise ValidationError("Only draft AR reconciliation sessions can be reconciled.")
    if actor is None:
        raise ValidationError("An authenticated actor is required to reconcile receivables.")
    requested_rows = list(locked.lines.all())
    if not requested_rows:
        raise ValidationError("Add at least one journal item to reconcile.")
    line_ids = [row.journal_line_id for row in requested_rows]
    journal_lines = {
        line.pk: line
        for line in JournalLine.objects.select_for_update().filter(pk__in=line_ids).select_related("entry", "account")
    }
    if len(journal_lines) != len(set(line_ids)):
        raise ValidationError("One or more selected journal lines no longer exists.")
    allocated = _active_ar_allocated_by_line(line_ids)
    requested = {row.journal_line_id: row.amount for row in requested_rows}
    available = {}
    grouped = {}
    for line_id, amount in requested.items():
        line = journal_lines[line_id]
        if line.entry.status != JournalEntry.Status.POSTED or not line.account.allow_reconciliation:
            raise ValidationError("Only posted reconcilable journal lines can be reconciled.")
        if line.partner_id != locked.partner_id or line.entry.company_id != locked.company_id:
            raise ValidationError("Every journal line must match the session company and partner.")
        residual = max((line.debit or line.credit) - allocated[line_id], Decimal("0"))
        if amount <= 0 or amount > residual:
            raise ValidationError(f"Requested amount exceeds the residual for journal line {line_id}.")
        available[line_id] = amount
        key = (line.account_id, line.currency or line.entry.currency)
        grouped.setdefault(key, {"debit": [], "credit": []})[
            "debit" if line.debit > 0 else "credit"
        ].append(line)
    allocations = []
    for bucket in grouped.values():
        debit_index = credit_index = 0
        while debit_index < len(bucket["debit"]) and credit_index < len(bucket["credit"]):
            debit_line = bucket["debit"][debit_index]
            credit_line = bucket["credit"][credit_index]
            amount = min(available[debit_line.pk], available[credit_line.pk])
            if amount > 0:
                allocations.append(ARReconciliationAllocation(
                    session=locked, debit_line=debit_line, credit_line=credit_line,
                    amount=amount, reconciliation_date=locked.reconciliation_date, actor=actor,
                ))
                available[debit_line.pk] -= amount
                available[credit_line.pk] -= amount
            if available[debit_line.pk] == 0:
                debit_index += 1
            if available[credit_line.pk] == 0:
                credit_index += 1
    if not allocations or any(amount != 0 for amount in available.values()):
        raise ValidationError("AR reconciliation must balance by account and currency.")
    ARReconciliationAllocation.objects.bulk_create(allocations)
    total = sum((allocation.amount for allocation in allocations), Decimal("0"))
    new_allocated = _active_ar_allocated_by_line(line_ids)
    now = timezone.now()
    line_updates = []
    for line in journal_lines.values():
        line.reconciled = new_allocated[line.pk] >= (line.debit or line.credit)
        line.updated_at = now
        line_updates.append(line)
    JournalLine.objects.bulk_update(line_updates, ["reconciled", "updated_at"])
    locked.total_debit = total
    locked.total_credit = total
    locked.status = ARReconciliationSession.Status.RECONCILED
    locked.save(update_fields=["total_debit", "total_credit", "status", "updated_at"])
    _audit(locked, "ar_reconciled", actor, {"journal_line_ids": line_ids, "allocation_count": len(allocations)})
    return locked


@transaction.atomic
def reverse_ar_session(session, actor=None):
    locked = ARReconciliationSession.objects.select_for_update().get(pk=session.pk)
    if locked.status != ARReconciliationSession.Status.RECONCILED:
        raise ValidationError("Only reconciled AR sessions can be reversed.")
    if actor is None:
        raise ValidationError("An authenticated actor is required to reverse reconciliation.")
    allocations = list(locked.allocations.select_for_update().filter(status=ARReconciliationAllocation.Status.ACTIVE))
    line_ids = sorted({line_id for allocation in allocations for line_id in (allocation.debit_line_id, allocation.credit_line_id)})
    now = timezone.now()
    for allocation in allocations:
        allocation.status = ARReconciliationAllocation.Status.REVERSED
        allocation.reversed_at = now
        allocation.reversed_by = actor
        allocation.updated_at = now
    if allocations:
        ARReconciliationAllocation.objects.bulk_update(
            allocations, ["status", "reversed_at", "reversed_by", "updated_at"]
        )
    remaining = _active_ar_allocated_by_line(line_ids)
    journal_lines = list(JournalLine.objects.select_for_update().filter(pk__in=line_ids))
    for line in journal_lines:
        line.reconciled = remaining[line.pk] >= (line.debit or line.credit)
        line.updated_at = now
    if journal_lines:
        JournalLine.objects.bulk_update(journal_lines, ["reconciled", "updated_at"])
    locked.status = ARReconciliationSession.Status.REVERSED
    locked.save(update_fields=["status", "updated_at"])
    _audit(locked, "ar_reconciliation_reversed", actor, {"journal_line_ids": line_ids})
    return locked


@transaction.atomic
def generate_accounting_vision(report, actor=None):
    locked = AccountingVisionReport.objects.select_for_update().get(pk=report.pk)
    if locked.status != AccountingVisionReport.Status.DRAFT:
        raise ValidationError("Only draft accounting vision reports can be generated.")
    entries = JournalEntry.objects.filter(
        company_id=locked.company_id, status=JournalEntry.Status.POSTED,
        entry_date__range=(locked.period_start, locked.period_end),
    )
    journal_metrics = entries.aggregate(count=Count("id"), debit=Sum("total_debit"), credit=Sum("total_credit"))
    open_invoices = Invoice.objects.filter(
        company_id=locked.company_id, status__in=[Invoice.Status.POSTED, Invoice.Status.PAID]
    )
    today = timezone.localdate()
    ar = open_invoices.filter(invoice_type=Invoice.InvoiceType.CUSTOMER).aggregate(
        total=Sum("total"), paid=Sum("paid_amount")
    )
    ap = open_invoices.filter(invoice_type=Invoice.InvoiceType.VENDOR).aggregate(
        total=Sum("total"), paid=Sum("paid_amount")
    )
    ar_overdue = open_invoices.filter(
        invoice_type=Invoice.InvoiceType.CUSTOMER, due_date__lt=today, paid_amount__lt=F("total")
    ).aggregate(count=Count("id"), balance=Sum(F("total") - F("paid_amount")))
    ap_overdue = open_invoices.filter(
        invoice_type=Invoice.InvoiceType.VENDOR, due_date__lt=today, paid_amount__lt=F("total")
    ).aggregate(count=Count("id"), balance=Sum(F("total") - F("paid_amount")))
    imbalance = entries.exclude(total_debit=F("total_credit")).count()
    line_metrics = JournalLine.objects.filter(entry__in=entries).aggregate(
        revenue_credit=Sum("credit", filter=Q(account__account_type=Account.AccountType.INCOME)),
        revenue_debit=Sum("debit", filter=Q(account__account_type=Account.AccountType.INCOME)),
        expense_debit=Sum("debit", filter=Q(account__account_type=Account.AccountType.EXPENSE)),
        expense_credit=Sum("credit", filter=Q(account__account_type=Account.AccountType.EXPENSE)),
        asset_debit=Sum("debit", filter=Q(account__account_type=Account.AccountType.ASSET)),
        asset_credit=Sum("credit", filter=Q(account__account_type=Account.AccountType.ASSET)),
        liability_credit=Sum("credit", filter=Q(account__account_type=Account.AccountType.LIABILITY)),
        liability_debit=Sum("debit", filter=Q(account__account_type=Account.AccountType.LIABILITY)),
    )
    revenue = (line_metrics["revenue_credit"] or Decimal("0")) - (line_metrics["revenue_debit"] or Decimal("0"))
    expense = (line_metrics["expense_debit"] or Decimal("0")) - (line_metrics["expense_credit"] or Decimal("0"))
    net_profit = revenue - expense
    assets = (line_metrics["asset_debit"] or Decimal("0")) - (line_metrics["asset_credit"] or Decimal("0"))
    liabilities = (line_metrics["liability_credit"] or Decimal("0")) - (line_metrics["liability_debit"] or Decimal("0"))
    ar_balance = (ar["total"] or Decimal("0")) - (ar["paid"] or Decimal("0"))
    ap_balance = (ap["total"] or Decimal("0")) - (ap["paid"] or Decimal("0"))
    ar_overdue_balance = ar_overdue["balance"] or Decimal("0")
    ap_overdue_balance = ap_overdue["balance"] or Decimal("0")
    unreconciled = JournalLine.objects.filter(
        entry__company_id=locked.company_id,
        entry__status=JournalEntry.Status.POSTED,
        account__allow_reconciliation=True,
        reconciled=False,
    ).count()
    duplicates = list(
        open_invoices.values("invoice_type", "partner_id", "total")
        .annotate(duplicate_count=Count("id"))
        .filter(duplicate_count__gt=1)
        .order_by("-duplicate_count")[:10]
    )
    budget_metrics = Budget.objects.filter(
        company_id=locked.company_id,
        status__in=[Budget.Status.APPROVED, Budget.Status.CLOSED],
        start_date__lte=locked.period_end,
        end_date__gte=locked.period_start,
    ).aggregate(allocated=Sum("amount"), committed=Sum("committed"), actual=Sum("actual"))
    allocated = budget_metrics["allocated"] or Decimal("0")
    budget_used = (budget_metrics["committed"] or Decimal("0")) + (budget_metrics["actual"] or Decimal("0"))
    settings = AccountingIntelligenceSettings.objects.filter(company_id=locked.company_id).first()
    thresholds = (settings.risk_thresholds if settings else {}) or {}
    scoring = (settings.health_scoring if settings else {}) or {}
    ar_high_share = Decimal(str(thresholds.get("ar_overdue_high_share", "0.20")))
    ap_high_share = Decimal(str(thresholds.get("ap_overdue_high_share", "0.20")))
    margin_warning = Decimal(str(thresholds.get("net_margin_warning", "0.10")))
    budget_warning = Decimal(str(thresholds.get("budget_expense_ratio_warning", "0.85")))
    ar_share = ar_overdue_balance / ar_balance if ar_balance > 0 else Decimal("0")
    ap_share = ap_overdue_balance / ap_balance if ap_balance > 0 else Decimal("0")
    net_margin = net_profit / revenue if revenue else Decimal("0")
    budget_ratio = budget_used / allocated if allocated else Decimal("0")
    signals = []

    def add_signal(code, severity, title, evidence, impact, action, owner, confidence="high"):
        signals.append({
            "code": code,
            "severity": severity,
            "title": title,
            "evidence": evidence,
            "impact": impact,
            "confidence": confidence,
            "recommended_action": action,
            "owner": owner,
            "status": "in_review",
        })

    routing = (settings.routing if settings else {}) or {}
    if imbalance:
        add_signal(
            "LEDGER_IMBALANCE", "critical", "Ledger integrity exception",
            f"{imbalance} posted journal entries are not balanced.",
            "Financial statements may be materially misstated.",
            "Review and reverse or correct each unbalanced journal entry.",
            routing.get("accounting_owner", "Accounting Manager"),
        )
    if ar_share >= ar_high_share:
        add_signal(
            "AR_OVERDUE", "high", "Collection pressure is elevated",
            f"Overdue AR is {ar_share:.1%} ({ar_overdue_balance}) of open receivables.",
            "Cash conversion and short-term liquidity are at risk.",
            "Prioritize the largest overdue customer balances and issue reminders.",
            routing.get("ar_owner", "AR Lead"),
        )
    if ap_share >= ap_high_share:
        add_signal(
            "AP_OVERDUE", "high", "Supplier payment pressure is elevated",
            f"Overdue AP is {ap_share:.1%} ({ap_overdue_balance}) of open payables.",
            "Supplier continuity and payment terms may deteriorate.",
            "Sequence overdue payments by operational criticality and due date.",
            routing.get("ap_owner", "AP Lead"),
        )
    if revenue and net_margin < margin_warning:
        add_signal(
            "MARGIN_WARNING", "medium", "Net margin is below policy threshold",
            f"Net margin is {net_margin:.1%}; configured warning threshold is {margin_warning:.1%}.",
            "Current revenue may not generate sufficient operating return.",
            "Review expense drivers and low-margin revenue lines.",
            routing.get("accounting_owner", "Accounting Manager"),
            "medium",
        )
    if allocated and budget_ratio >= budget_warning:
        add_signal(
            "BUDGET_PRESSURE", "medium", "Budget utilization is approaching its limit",
            f"Committed plus actual spend is {budget_ratio:.1%} of allocation.",
            "Remaining budget may be insufficient for the period.",
            "Review commitments and submit a controlled budget change if required.",
            routing.get("budget_owner", "Budget Owner"),
        )
    if unreconciled:
        add_signal(
            "RECONCILIATION_BACKLOG", "medium", "Reconciliation exceptions remain open",
            f"{unreconciled} posted reconcilable journal lines remain unmatched.",
            "Cash, receivable, and payable balances may contain unresolved items.",
            "Open reconciliation sessions for the oldest unmatched items.",
            routing.get("accounting_owner", "Accounting Manager"),
        )
    if duplicates:
        add_signal(
            "DUPLICATE_EXPOSURE", "high", "Potential duplicate invoices detected",
            f"{len(duplicates)} repeated partner/type/amount groups were found.",
            "Duplicate billing or payment exposure requires review.",
            "Compare source references and cancel confirmed duplicates.",
            routing.get("accounting_owner", "Accounting Manager"),
            "medium",
        )
    penalties = {
        "critical": Decimal(str(scoring.get("critical_penalty", 30))),
        "high": Decimal(str(scoring.get("high_penalty", 15))),
        "medium": Decimal(str(scoring.get("medium_penalty", 8))),
        "low": Decimal(str(scoring.get("low_penalty", 3))),
    }
    risk_points = min(Decimal("100"), sum((penalties[item["severity"]] for item in signals), Decimal("0")))
    locked.health_score = Decimal("100") - risk_points
    locked.risk_level = "high" if risk_points >= 60 else "medium" if risk_points >= 25 else "low"
    locked.metrics = {
        "posted_entries": journal_metrics["count"] or 0,
        "debit": str(journal_metrics["debit"] or 0), "credit": str(journal_metrics["credit"] or 0),
        "unbalanced_entries": imbalance,
        "ar_outstanding": str(ar_balance), "ar_overdue": str(ar_overdue_balance),
        "ap_outstanding": str(ap_balance), "ap_overdue": str(ap_overdue_balance),
        "revenue": str(revenue), "expense": str(expense), "net_profit": str(net_profit),
        "net_margin": str(net_margin), "working_capital": str(assets - liabilities),
        "unreconciled_lines": unreconciled, "duplicate_groups": len(duplicates),
        "budget_allocated": str(allocated), "budget_used": str(budget_used),
    }
    locked.insights = signals
    locked.summary = {
        "headline": "Immediate review required" if any(item["severity"] in {"critical", "high"} for item in signals) else "Accounting position is within configured thresholds",
        "financial_health": str(locked.health_score),
        "signal_count": len(signals),
        "period": {"start": locked.period_start.isoformat(), "end": locked.period_end.isoformat()},
    }
    locked.recommended_actions = [
        {"code": item["code"], "action": item["recommended_action"], "owner": item["owner"], "approval_required": item["severity"] in {"critical", "high"}}
        for item in signals
    ]
    locked.data_quality = {
        "source_coverage": (settings.source_coverage if settings else {}) or {},
        "posted_entry_count": journal_metrics["count"] or 0,
        "duplicate_candidates": duplicates,
        "limitations": ["Forecast uses posted period revenue as its baseline."],
    }
    period_days = max((locked.period_end - locked.period_start).days + 1, 1)
    monthly_revenue = (revenue / Decimal(period_days) * Decimal("30")).quantize(Decimal("0.01"))
    locked.forecast = {
        "method": ((settings.forecast_settings if settings else {}) or {}).get("default_method", "simple_average"),
        "monthly_revenue_baseline": str(monthly_revenue),
        "next_three_months_revenue": [str(monthly_revenue)] * 3,
        "confidence": "medium" if journal_metrics["count"] else "low",
    }
    locked.status = AccountingVisionReport.Status.GENERATED
    locked.save(update_fields=[
        "health_score", "risk_level", "metrics", "insights", "summary", "recommended_actions",
        "data_quality", "forecast", "status", "updated_at",
    ])
    _audit(locked, "accounting_vision_generated", actor, locked.metrics)
    return locked


@transaction.atomic
def approve_source_document_cancellation(cancellation, actor=None):
    locked = SourceDocumentCancellation.objects.select_for_update().get(pk=cancellation.pk)
    if locked.status != SourceDocumentCancellation.Status.REQUESTED:
        raise ValidationError("Only requested source document cancellations can be approved.")
    if actor is None:
        raise ValidationError("An authenticated approver is required for source cancellation.")
    from .source_cancellation import apply_source_cancellation, prepare_source_cancellation

    source, adapter = prepare_source_cancellation(locked)
    source_types = set(adapter.journal_source_types) | {locked.source_model}
    entry = JournalEntry.objects.filter(
        company_id=locked.company_id, source_type__in=source_types,
        source_reference__in=[str(locked.source_id), locked.source_reference], status=JournalEntry.Status.POSTED,
    ).order_by("-entry_date", "-id").first()
    if entry is None:
        raise ValidationError("No posted journal entry was found for the source document.")
    entry.cancellation_reason = locked.reason
    entry.save(update_fields=["cancellation_reason", "updated_at"])
    reversal = reverse_journal_entry(entry, actor)
    apply_source_cancellation(source, adapter)
    if source._meta.label_lower == "core.invoice":
        notify_invoice_cancelled(source, actor)
    locked.status = SourceDocumentCancellation.Status.COMPLETED
    locked.approved_by = actor
    locked.reversal_entry = reversal
    locked.save(update_fields=["status", "approved_by", "reversal_entry", "updated_at"])
    _audit(locked, "source_document_cancelled", actor, {"journal_entry_id": entry.pk, "reversal_entry_id": reversal.pk})
    return locked


def handle_accounting_workflow(entity, instance, action, actor=None):
    if entity == "source-accounting-postings" and action == "post":
        return post_source_accounting_posting(instance, actor)
    if entity == "source-accounting-postings" and action == "reverse":
        return reverse_source_accounting_posting(instance, actor)
    if entity == "account-cheques" and action in {"issue", "clear", "bounce"}:
        return process_account_cheque(instance, action, actor)
    if entity == "reconciliation-rule-tests" and action == "run":
        return run_reconciliation_rule_test(instance, actor)
    if entity == "account-payments" and action == "post":
        return post_payment(instance, actor)
    if entity == "internal-bank-transfers" and action == "post":
        return post_bank_transfer(instance, actor)
    if entity == "other-income-expenses" and action == "post":
        return post_other_income_expense(instance, actor)
    if entity == "other-income-expenses" and action == "reverse":
        return reverse_other_income_expense(instance, actor)
    if entity == "payment-vouchers" and action == "post":
        return post_payment_voucher(instance, actor)
    if entity == "multi-payment-batches" and action == "post":
        return post_multi_payment_batch(instance, actor)
    if entity == "withholding-tax-transactions" and action == "validate":
        return validate_withholding_tax(instance, actor)
    if entity == "withholding-tax-transactions" and action == "reverse":
        return reverse_withholding_tax(instance, actor)
    if entity == "closing-entries" and action == "generate":
        return generate_closing_entry(instance, actor)
    if entity == "closing-entries" and action == "post":
        return post_closing_entry(instance, actor)
    if entity == "closing-entries" and action == "reverse":
        if instance.journal_entry_id is None:
            raise ValidationError("This closing entry does not have a posted journal entry.")
        reverse_journal_entry(instance.journal_entry, actor)
        instance.status = ClosingEntry.Status.REVERSED
        instance.save(update_fields=["status", "updated_at"])
        return instance
    if entity == "currency-revaluations" and action == "post":
        return post_currency_revaluation(instance, actor)
    if entity == "currency-revaluations" and action == "reverse":
        if instance.journal_entry_id is None:
            raise ValidationError("This revaluation does not have a posted journal entry.")
        reverse_journal_entry(instance.journal_entry, actor)
        instance.status = CurrencyRevaluation.Status.REVERSED
        instance.save(update_fields=["status", "updated_at"])
        return instance
    if entity == "recurring-journals" and action == "run":
        run_recurring_journal(instance, actor)
        return RecurringJournal.objects.get(pk=instance.pk)
    if entity == "deposit-applications" and action == "apply":
        return apply_deposit(instance, actor)
    if entity == "deposit-applications" and action == "reverse":
        return reverse_deposit_application(instance, actor)
    if entity == "prepayment-applications" and action == "apply":
        return apply_prepayment(instance, actor)
    if entity == "prepayment-applications" and action == "reverse":
        return reverse_prepayment_application(instance, actor)
    if entity == "asset-depreciation-lines" and action == "post":
        return post_asset_depreciation(instance, actor)
    if entity == "asset-capitalizations" and action == "approve":
        return approve_asset_capitalization(instance, actor)
    if entity == "asset-capitalizations" and action == "capitalize":
        return capitalize_assets(instance, actor)
    if entity == "inventory-accounting-documents" and action == "approve":
        return approve_inventory_accounting_document(instance, actor)
    if entity == "inventory-accounting-documents" and action == "post":
        return post_inventory_accounting_document(instance, actor)
    if entity == "inventory-accounting-documents" and action == "reverse":
        return reverse_inventory_accounting_document(instance, actor)
    if entity == "asset-revaluations" and action == "post":
        return post_asset_revaluation(instance, actor)
    if entity == "asset-disposals" and action == "post":
        return post_asset_disposal(instance, actor)
    if entity == "asset-disposals" and action == "create_invoice":
        return create_asset_disposal_invoice(instance, actor)
    if entity == "asset-disposals" and action == "retry":
        return retry_asset_disposal_accounting(instance, actor)
    if entity == "rental-order-accounting" and action in {"create_downpayment", "create_settlement"}:
        from .rental_extension import create_rental_downpayment_invoice, create_rental_settlement_invoice

        if action == "create_downpayment":
            return create_rental_downpayment_invoice(instance, actor)
        return create_rental_settlement_invoice(instance, actor)
    if entity == "service-work-order-accounting" and action in {"create_bill", "retry"}:
        from .service_operation_extension import (
            create_service_work_order_vendor_bill,
            retry_service_work_order_accounting,
        )

        if action == "create_bill":
            return create_service_work_order_vendor_bill(instance, actor)
        return retry_service_work_order_accounting(instance, actor)
    if entity == "external-accounting-connectors" and action == "validate":
        return validate_external_accounting_connector(instance, actor)
    if entity == "external-invoice-syncs" and action == "dispatch":
        return dispatch_external_invoice_sync(instance, actor)
    if entity == "marketplace-accounting-orders" and action in {"post_order", "settle"}:
        return post_marketplace_accounting(instance, action, actor)
    if entity == "marketplace-accounting-orders" and action == "reverse":
        return reverse_marketplace_accounting(instance, actor)
    if entity == "sale-order-invoice-accounting" and action in {
        "create_downpayment", "create_settlement", "retry"
    }:
        from .sale_invoice_extension import (
            create_sales_downpayment_invoice,
            create_sales_settlement_invoice,
            retry_sales_invoice_accounting,
        )

        if action == "create_downpayment":
            return create_sales_downpayment_invoice(instance, actor)
        if action == "create_settlement":
            return create_sales_settlement_invoice(instance, actor)
        return retry_sales_invoice_accounting(instance, actor)
    if entity == "student-fee-schedules" and action in {"generate_lines", "generate_invoices", "cancel"}:
        from .school_accounting_extension import (
            cancel_student_fee_schedule,
            generate_due_school_invoices,
            generate_student_fee_schedule_lines,
        )

        if action == "generate_lines":
            return generate_student_fee_schedule_lines(instance, actor)
        if action == "cancel":
            return cancel_student_fee_schedule(instance, actor)
        generate_due_school_invoices(instance, actor)
        return StudentFeeSchedule.objects.get(pk=instance.pk)
    if entity == "school-installment-requests" and action in {"submit", "approve", "reject"}:
        from .school_accounting_extension import (
            approve_school_installment_request,
            reject_school_installment_request,
            submit_school_installment_request,
        )

        if action == "submit":
            return submit_school_installment_request(instance, actor)
        if action == "approve":
            return approve_school_installment_request(instance, actor)
        return reject_school_installment_request(instance, actor)
    if entity == "school-installment-plans" and action in {"start_reschedule", "confirm_reschedule"}:
        from .school_accounting_extension import (
            confirm_school_installment_reschedule,
            start_school_installment_reschedule,
        )

        if action == "start_reschedule":
            return start_school_installment_reschedule(instance, actor)
        return confirm_school_installment_reschedule(instance, actor)
    if entity == "school-library-fines" and action == "process_return":
        from .school_accounting_extension import process_school_library_return_with_failure_state

        return process_school_library_return_with_failure_state(instance, actor)
    if entity in {"asset-depreciation-lines", "asset-revaluations", "asset-disposals"} and action == "reverse":
        return reverse_asset_accounting(instance, actor)
    if entity == "budget-transactions" and action == "approve":
        return approve_budget_transaction(instance, actor)
    if entity == "budget-change-requests" and action == "approve":
        return approve_budget_change_request(instance, actor)
    if entity == "budget-transfers" and action == "approve":
        return approve_budget_transfer(instance, actor)
    if entity == "budget-carry-overs" and action == "approve":
        return approve_budget_carry_over(instance, actor)
    if entity == "petty-cash-vouchers" and action == "post":
        return post_petty_cash_voucher(instance, actor)
    if entity == "petty-cash-replenishments" and action == "post":
        return post_petty_cash_replenishment(instance, actor)
    if entity == "petty-cash-reconciliations" and action == "post":
        return post_petty_cash_reconciliation(instance, actor)
    if entity in {"petty-cash-vouchers", "petty-cash-replenishments", "petty-cash-reconciliations"} and action == "reverse":
        return reverse_petty_cash_document(instance, actor)
    if entity == "accounting-integration-events" and action == "post":
        return post_integration_event(instance, actor)
    if entity == "accounting-integration-events" and action == "reverse":
        return reverse_integration_event(instance, actor)
    if entity == "compliance-reports" and action == "generate":
        return generate_compliance_report(instance, actor)
    if entity == "compliance-reports" and action == "validate":
        return validate_compliance_report(instance, actor)
    if entity == "compliance-reports" and action == "submit":
        return queue_tax_submission(instance, actor)
    if entity == "external-tax-submissions" and action == "dispatch":
        return dispatch_tax_submission(instance, actor)
    if entity == "tax-authority-callback-events" and action == "apply_callback":
        return apply_tax_authority_callback(instance, actor)
    if entity == "tax-authority-sync-cursors" and action == "sync":
        return sync_received_tax_documents(instance, actor)
    if entity == "regulatory-rate-sync-runs" and action == "sync_rates":
        return sync_regulatory_rates(instance, actor)
    if entity == "bank-statement-parse-jobs" and action == "parse":
        return parse_bank_statement_job(instance)
    if entity == "bank-statement-parse-jobs" and action == "import":
        return import_bank_statement_job(instance)
    if entity == "company-accounting-provisioning-runs" and action == "run":
        return provision_company_accounting(instance, actor)
    if entity == "account-deposit-conversions" and action == "post":
        return post_deposit_conversion(instance, actor)
    if entity == "account-deposit-conversions" and action == "reverse":
        return reverse_deposit_conversion(instance, actor)
    if entity == "bank-cash-valuations" and action == "generate":
        return generate_bank_cash_valuation(instance, actor)
    if entity == "bank-cash-valuations" and action == "post":
        return post_bank_cash_valuation(instance, actor)
    if entity == "bank-cash-valuations" and action == "reverse":
        if instance.journal_entry_id is None:
            raise ValidationError("This valuation has no posted journal entry.")
        reverse_journal_entry(instance.journal_entry, actor)
        instance.status = BankCashValuation.Status.REVERSED
        instance.save(update_fields=["status", "updated_at"])
        return instance
    if entity == "ar-reconciliation-sessions" and action == "reconcile":
        return reconcile_ar_session(instance, actor)
    if entity == "ar-reconciliation-sessions" and action == "suggest":
        return suggest_ar_reconciliation_session(instance, actor)
    if entity == "ar-reconciliation-sessions" and action == "reverse":
        return reverse_ar_session(instance, actor)
    if entity == "accounting-vision-reports" and action == "generate":
        return generate_accounting_vision(instance, actor)
    if entity == "source-document-cancellations" and action == "approve":
        return approve_source_document_cancellation(instance, actor)
    if entity == "pos-order-accounting" and action == "process":
        return process_pos_order_accounting(instance, actor)
    if entity == "pos-order-accounting" and action == "reverse":
        return reverse_pos_order_accounting(instance, actor)
    if entity == "sale-order-localizations" and action == "confirm_taxes":
        return confirm_sale_order_localization(instance, actor)
    if entity == "expenses" and action in {"approve", "pay", "return", "reverse"}:
        return post_expense_claim_accounting(instance, action, actor)
    if entity == "tax-middleware-connectors" and action == "validate":
        return validate_tax_connector(instance, actor)
    if entity == "tax-authority-documents" and action == "queue":
        return queue_tax_authority_document(instance, actor)
    if entity == "tax-authority-documents" and action == "send":
        return dispatch_tax_authority_document(instance, actor)
    if entity == "tax-identifier-lookups" and action == "lookup":
        return queue_tax_identifier_lookup(instance, actor)
    if action == "reverse" and getattr(instance, "journal_entry_id", None):
        reverse_journal_entry(instance.journal_entry, actor)
        if hasattr(instance, "status"):
            instance.status = "reversed"
            instance.save(update_fields=["status", "updated_at"])
        return instance
    raise ValidationError("This accounting workflow action has no transactional service.")
