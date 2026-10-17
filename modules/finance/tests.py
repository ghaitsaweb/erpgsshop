from decimal import Decimal
import hashlib
import hmac
import json

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Company, Contact, Employee, Invoice, InvoiceLine, Product
from modules.apphub.models import ERPApp
from modules.operations.models import Asset

from .access import scope_accounting_queryset
from .dashboard import build_accounting_analytics
from .models import (
    Account,
    AccountJournal,
    AccountPayment,
    AccountPaymentMethod,
    ARReconciliationAllocation,
    ARReconciliationSession,
    AccountingAuditEvent,
    AccountingSettings,
    AccountingIntegrationEvent,
    AssetAccountingSettings,
    AssetDepreciationLine,
    AssetDisposal,
    AssetRevaluation,
    BankStatement,
    BankStatementLine,
    BankStatementParseJob,
    Budget,
    BudgetAccount,
    BudgetAccountGroup,
    BudgetCarryOver,
    BudgetCarryOverLine,
    BudgetChangeReasonCategory,
    BudgetChangeRequest,
    BudgetChangeRequestLine,
    BudgetTransfer,
    BudgetTransferLine,
    BudgetTransaction,
    CompanyBankAccount,
    CompanyAccountingProvisioningRun,
    ComplianceReport,
    CountryTaxProfile,
    CurrencyRevaluation,
    CurrencyRevaluationLine,
    FiscalPeriod,
    FiscalYear,
    JournalEntry,
    JournalLine,
    LocalizationTaxCode,
    LocalizedTaxTransactionLine,
    MultiPaymentBatch,
    MultiPaymentBatchLine,
    PettyCashCategory,
    PettyCashFund,
    PettyCashReplenishment,
    PettyCashVoucher,
    PettyCashVoucherLine,
    PaymentVoucher,
    PaymentVoucherLine,
    ProductAccountMapping,
    StatutoryReportTemplate,
    TaxAuthorityDocument,
    RecurringJournal,
    WithholdingTax,
    WithholdingTaxTransaction,
)
from .services import (
    post_invoice,
    post_journal_entry,
    post_multi_payment_batch,
    post_payment,
    post_payment_voucher,
    post_currency_revaluation,
    post_asset_depreciation,
    post_asset_disposal,
    post_asset_revaluation,
    post_integration_event,
    post_petty_cash_replenishment,
    post_petty_cash_voucher,
    reconcile_statement_line,
    reverse_journal_entry,
    reverse_asset_accounting,
    reverse_petty_cash_document,
    reverse_withholding_tax,
    unreconcile,
    run_recurring_journal,
    approve_budget_transaction,
    approve_budget_carry_over,
    approve_budget_change_request,
    approve_budget_transfer,
    generate_compliance_report,
    parse_bank_statement_job,
    provision_company_accounting,
    reconcile_ar_session,
    reverse_ar_session,
    suggest_ar_reconciliation_session,
    validate_withholding_tax,
)


class AccountingServiceTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(code="ACC", name="Accounting Company", currency="IDR")
        self.other_company = Company.objects.create(code="OTH", name="Other Company", currency="IDR")
        self.user = get_user_model().objects.create_user(username="accountant", password="test-password")
        self.employee = Employee.objects.create(company=self.company, user=self.user, employee_id="EMP-ACC", name="Accountant")
        self.receivable = Account.objects.create(
            company=self.company, code="1100", name="Trade Receivable", account_type=Account.AccountType.ASSET,
            allow_reconciliation=True,
        )
        self.payable = Account.objects.create(
            company=self.company, code="2100", name="Trade Payable", account_type=Account.AccountType.LIABILITY,
            allow_reconciliation=True,
        )
        self.bank = Account.objects.create(
            company=self.company, code="1000", name="Bank", account_type=Account.AccountType.ASSET,
            allow_reconciliation=True,
        )
        self.revenue = Account.objects.create(
            company=self.company, code="4100", name="Revenue", account_type=Account.AccountType.INCOME,
        )
        self.journal = AccountJournal.objects.create(
            company=self.company,
            code="BNK",
            name="Bank Journal",
            journal_type=AccountJournal.JournalType.BANK,
            default_debit_account=self.bank,
            default_credit_account=self.bank,
        )
        self.general_journal = AccountJournal.objects.create(
            company=self.company,
            code="GEN",
            name="General Journal",
            journal_type=AccountJournal.JournalType.GENERAL,
            default_debit_account=self.bank,
            default_credit_account=self.bank,
        )
        AccountingSettings.objects.create(
            company=self.company,
            default_receivable_account=self.receivable,
            default_payable_account=self.payable,
        )
        self.partner = Contact.objects.create(
            company=self.company,
            code="CUST-1",
            name="Customer One",
            contact_type=Contact.ContactType.CUSTOMER,
        )

    def _draft_entry(self, number="JE-TEST"):
        entry = JournalEntry.objects.create(
            company=self.company,
            journal=self.journal,
            number=number,
            entry_date=timezone.localdate(),
        )
        JournalLine.objects.create(entry=entry, account=self.bank, debit=Decimal("125.00"))
        JournalLine.objects.create(entry=entry, account=self.revenue, credit=Decimal("125.00"))
        return entry

    def test_post_and_reverse_journal_create_audited_balanced_entries(self):
        entry = post_journal_entry(self._draft_entry(), self.user)

        self.assertEqual(entry.status, JournalEntry.Status.POSTED)
        self.assertEqual(entry.total_debit, Decimal("125.00"))
        self.assertEqual(entry.total_credit, Decimal("125.00"))
        self.assertEqual(entry.posted_by, self.user)

        reversal = reverse_journal_entry(entry, self.user)
        entry.refresh_from_db()
        self.assertEqual(entry.status, JournalEntry.Status.REVERSED)
        self.assertEqual(reversal.status, JournalEntry.Status.POSTED)
        self.assertEqual(reversal.reversal_of, entry)
        self.assertEqual(reversal.total_debit, Decimal("125.00"))
        self.assertEqual(AccountingAuditEvent.objects.filter(company=self.company).count(), 3)

    def test_post_rejects_closed_period(self):
        today = timezone.localdate()
        fiscal_year = FiscalYear.objects.create(
            company=self.company,
            name=str(today.year),
            start_date=today.replace(month=1, day=1),
            end_date=today.replace(month=12, day=31),
        )
        FiscalPeriod.objects.create(
            fiscal_year=fiscal_year,
            name="Locked Period",
            start_date=today,
            end_date=today,
            status=FiscalPeriod.Status.LOCKED,
        )

        with self.assertRaisesMessage(ValidationError, "closed or locked"):
            post_journal_entry(self._draft_entry("JE-LOCKED"), self.user)

    def test_customer_receipt_posts_two_sided_journal(self):
        payment = AccountPayment.objects.create(
            company=self.company,
            number="RCPT-1",
            direction=AccountPayment.Direction.RECEIVE,
            partner=self.partner,
            journal=self.journal,
            amount=Decimal("250.00"),
        )

        payment = post_payment(payment, self.user)

        self.assertEqual(payment.status, AccountPayment.Status.POSTED)
        self.assertEqual(payment.journal_entry.total_debit, Decimal("250.00"))
        self.assertEqual(payment.journal_entry.total_credit, Decimal("250.00"))
        self.assertEqual(payment.journal_entry.lines.count(), 2)

    def test_non_superuser_queryset_is_limited_to_employee_company(self):
        Account.objects.create(
            company=self.other_company, code="9999", name="Hidden", account_type=Account.AccountType.EXPENSE
        )

        visible = scope_accounting_queryset(self.user, "accounts", Account.objects.all())

        self.assertTrue(visible.exists())
        self.assertFalse(visible.filter(company=self.other_company).exists())

    def test_dashboard_uses_posted_company_scoped_data(self):
        post_journal_entry(self._draft_entry("JE-DASH"), self.user)

        result = build_accounting_analytics("trial-balance", self.user)

        self.assertEqual(result["metrics"][0]["value"], 1)
        self.assertEqual(len(result["breakdown"]), 2)

    def test_invoice_bridge_posts_product_and_receivable_accounts(self):
        product = Product.objects.create(
            company=self.company, sku="SERV-1", name="Consulting", product_type=Product.ProductType.SERVICE
        )
        ProductAccountMapping.objects.create(
            company=self.company, product=product, revenue_account=self.revenue, expense_account=self.revenue
        )
        invoice = Invoice.objects.create(
            company=self.company,
            number="INV-100",
            partner=self.partner,
            invoice_date=timezone.localdate(),
            due_date=timezone.localdate(),
        )
        InvoiceLine.objects.create(
            invoice=invoice, product=product, description="Consulting", quantity=Decimal("2"), unit_price=Decimal("75")
        )

        posted = post_invoice(invoice, self.user)

        self.assertEqual(posted.status, Invoice.Status.POSTED)
        self.assertEqual(posted.total, Decimal("150"))
        entry = JournalEntry.objects.get(source_type="account_invoice", source_reference="INV-100")
        self.assertEqual(entry.total_debit, Decimal("150"))
        self.assertEqual(entry.total_credit, Decimal("150"))

    def test_voucher_and_multi_receipt_create_posted_journals_and_allocations(self):
        voucher = PaymentVoucher.objects.create(
            company=self.company,
            number="PV-1",
            voucher_type=PaymentVoucher.VoucherType.RECEIPT,
            partner=self.partner,
            journal=self.journal,
        )
        PaymentVoucherLine.objects.bulk_create([
            PaymentVoucherLine(voucher=voucher, account=self.bank, debit=Decimal("80")),
            PaymentVoucherLine(voucher=voucher, account=self.revenue, credit=Decimal("80")),
        ])
        voucher = post_payment_voucher(voucher, self.user)
        self.assertEqual(voucher.status, PaymentVoucher.Status.POSTED)
        self.assertEqual(voucher.total_amount, Decimal("80"))

        invoice = Invoice.objects.create(
            company=self.company,
            number="INV-BATCH",
            partner=self.partner,
            invoice_date=timezone.localdate(),
            due_date=timezone.localdate(),
            status=Invoice.Status.POSTED,
            total=Decimal("120"),
        )
        method = AccountPaymentMethod.objects.create(company=self.company, code="TRF", name="Transfer", journal=self.journal)
        batch = MultiPaymentBatch.objects.create(
            company=self.company,
            number="BATCH-1",
            batch_type=MultiPaymentBatch.BatchType.CUSTOMER_RECEIPT,
            partner=self.partner,
            journal=self.journal,
            payment_method=method,
        )
        MultiPaymentBatchLine.objects.create(
            batch=batch, invoice=invoice, due_amount=Decimal("120"), amount=Decimal("100")
        )
        batch = post_multi_payment_batch(batch, self.user)
        invoice.refresh_from_db()
        self.assertEqual(batch.status, MultiPaymentBatch.Status.POSTED)
        self.assertEqual(invoice.paid_amount, Decimal("100"))

    def test_withholding_validation_reversal_and_bank_reconciliation_are_audited(self):
        tax = WithholdingTax.objects.create(
            company=self.company,
            code="WHT23",
            name="PPh 23",
            party_type=WithholdingTax.PartyType.CUSTOMER,
            rate=Decimal("2"),
            account=self.receivable,
        )
        withholding = WithholdingTaxTransaction.objects.create(
            company=self.company,
            number="WHT-1",
            withholding_tax=tax,
            partner=self.partner,
            gross_amount=Decimal("1000"),
            tax_amount=Decimal("20"),
        )
        withholding = validate_withholding_tax(withholding, self.user)
        self.assertEqual(withholding.status, WithholdingTaxTransaction.Status.VALIDATED)
        withholding = reverse_withholding_tax(withholding, self.user)
        self.assertEqual(withholding.status, WithholdingTaxTransaction.Status.REVERSED)

        posted_entry = post_journal_entry(self._draft_entry("JE-REC"), self.user)
        journal_line = posted_entry.lines.get(account=self.bank)
        bank_account = CompanyBankAccount.objects.create(
            company=self.company,
            journal=self.journal,
            bank_name="Test Bank",
            account_number="001",
            account_holder="Accounting Company",
            currency="IDR",
        )
        statement = BankStatement.objects.create(
            company=self.company,
            bank_account=bank_account,
            reference="ST-1",
            date_from=timezone.localdate(),
            date_to=timezone.localdate(),
        )
        statement_line = BankStatementLine.objects.create(
            statement=statement,
            transaction_date=timezone.localdate(),
            amount=Decimal("125"),
        )
        history = reconcile_statement_line(statement_line, journal_line, self.user)
        self.assertFalse(history.reversed)
        history = unreconcile(history, self.user)
        statement_line.refresh_from_db()
        journal_line.refresh_from_db()
        self.assertTrue(history.reversed)
        self.assertFalse(statement_line.reconciled)
        self.assertFalse(journal_line.reconciled)

    def test_recurring_journal_generates_and_auto_posts_from_template(self):
        template = self._draft_entry("JE-RECUR-TEMPLATE")
        schedule = RecurringJournal.objects.create(
            company=self.company,
            name="Monthly Revenue",
            template_entry=template,
            frequency=RecurringJournal.Frequency.MONTHLY,
            next_run_date=timezone.localdate(),
            auto_post=True,
        )

        run_recurring_journal(schedule, self.user)

        generated = JournalEntry.objects.get(source_type="recurring_journal", source_reference=str(schedule.pk))
        schedule.refresh_from_db()
        self.assertEqual(generated.status, JournalEntry.Status.POSTED)
        self.assertGreater(schedule.next_run_date, timezone.localdate())

    def test_currency_revaluation_posts_gain_and_loss_accounts(self):
        exchange_gain = Account.objects.create(
            company=self.company, code="4200", name="FX Gain", account_type=Account.AccountType.INCOME
        )
        exchange_loss = Account.objects.create(
            company=self.company, code="5200", name="FX Loss", account_type=Account.AccountType.EXPENSE
        )
        settings = AccountingSettings.objects.get(company=self.company)
        settings.exchange_gain_account = exchange_gain
        settings.exchange_loss_account = exchange_loss
        settings.save(update_fields=["exchange_gain_account", "exchange_loss_account", "updated_at"])
        revaluation = CurrencyRevaluation.objects.create(
            company=self.company,
            number="FX-1",
            currency="USD",
            rate=Decimal("16000"),
        )
        CurrencyRevaluationLine.objects.create(
            revaluation=revaluation,
            account=self.bank,
            original_amount=Decimal("100"),
            base_amount=Decimal("1500000"),
            revalued_amount=Decimal("1600000"),
            gain_loss_amount=Decimal("100000"),
        )

        revaluation = post_currency_revaluation(revaluation, self.user)

        self.assertEqual(revaluation.status, CurrencyRevaluation.Status.POSTED)
        self.assertEqual(revaluation.unrealized_gain, Decimal("100000"))
        self.assertEqual(revaluation.journal_entry.total_debit, Decimal("100000"))

    def test_asset_accounting_posts_depreciation_revaluation_and_disposal(self):
        asset = Asset.objects.create(
            company=self.company, code="AST-1", name="Machine", acquisition_value=Decimal("1000")
        )
        AssetAccountingSettings.objects.create(
            company=self.company,
            journal=self.general_journal,
            fixed_asset_account=self.bank,
            accumulated_depreciation_account=self.payable,
            depreciation_expense_account=self.revenue,
            disposal_gain_account=self.revenue,
            disposal_loss_account=self.payable,
        )
        depreciation = AssetDepreciationLine.objects.create(
            asset=asset,
            period_date=timezone.localdate(),
            opening_book_value=Decimal("1000"),
            depreciation_amount=Decimal("100"),
            accumulated_depreciation=Decimal("100"),
            closing_book_value=Decimal("900"),
        )
        self.assertEqual(post_asset_depreciation(depreciation, self.user).status, "posted")

        revaluation = AssetRevaluation.objects.create(
            asset=asset, number="ARV-1", current_book_value=Decimal("900"), fair_value=Decimal("950"),
            gain_loss_amount=Decimal("50"),
        )
        self.assertEqual(post_asset_revaluation(revaluation, self.user).status, "posted")

        disposal = AssetDisposal.objects.create(
            asset=asset, number="ADP-1", proceeds=Decimal("800"), book_value=Decimal("900"),
            gain_loss_amount=Decimal("-100"),
        )
        self.assertEqual(post_asset_disposal(disposal, self.user).status, "posted")
        asset.refresh_from_db()
        self.assertEqual(asset.status, Asset.Status.RETIRED)
        reverse_asset_accounting(disposal, self.user)
        asset.refresh_from_db()
        disposal.refresh_from_db()
        self.assertEqual(asset.status, Asset.Status.AVAILABLE)
        self.assertEqual(disposal.status, AssetDisposal.Status.REVERSED)

    def test_budget_and_petty_cash_workflows_update_balances_atomically(self):
        today = timezone.localdate()
        source = Budget.objects.create(
            company=self.company, name="Source", start_date=today, end_date=today,
            amount=Decimal("1000"), status=Budget.Status.APPROVED,
        )
        destination = Budget.objects.create(
            company=self.company, name="Destination", start_date=today, end_date=today,
            amount=Decimal("100"), status=Budget.Status.APPROVED,
        )
        group = BudgetAccountGroup.objects.create(company=self.company, code="OPS", name="Operations")
        budget_account = BudgetAccount.objects.create(company=self.company, group=group, code="EXP", name="Expense")
        movement = BudgetTransaction.objects.create(
            company=self.company, number="BT-1", transaction_type=BudgetTransaction.TransactionType.TRANSFER,
            source_budget=source, destination_budget=destination, budget_account=budget_account,
            amount=Decimal("250"), requested_by=self.user,
        )
        approve_budget_transaction(movement, self.user)
        source.refresh_from_db()
        destination.refresh_from_db()
        self.assertEqual(source.amount, Decimal("750"))
        self.assertEqual(destination.amount, Decimal("350"))

        bank_account = CompanyBankAccount.objects.create(
            company=self.company, journal=self.journal, bank_name="Bank", account_number="PC-1",
            account_holder="Company", currency="IDR",
        )
        fund = PettyCashFund.objects.create(
            company=self.company, code="PC", name="Petty Cash", custodian=self.employee,
            journal=self.general_journal, cash_account=self.bank, imprest_limit=Decimal("500"),
            current_balance=Decimal("200"),
        )
        category = PettyCashCategory.objects.create(
            company=self.company, code="OFF", name="Office", expense_account=self.revenue
        )
        voucher = PettyCashVoucher.objects.create(fund=fund, number="PCV-1", payee="Supplier")
        PettyCashVoucherLine.objects.create(voucher=voucher, category=category, description="Supplies", amount=Decimal("50"))
        post_petty_cash_voucher(voucher, self.user)
        fund.refresh_from_db()
        self.assertEqual(fund.current_balance, Decimal("150"))

        replenishment = PettyCashReplenishment.objects.create(
            fund=fund, number="PCR-1", bank_account=bank_account, amount=Decimal("100")
        )
        post_petty_cash_replenishment(replenishment, self.user)
        fund.refresh_from_db()
        self.assertEqual(fund.current_balance, Decimal("250"))

        reverse_petty_cash_document(replenishment, self.user)
        fund.refresh_from_db()
        replenishment.refresh_from_db()
        self.assertEqual(fund.current_balance, Decimal("150"))
        self.assertEqual(replenishment.status, PettyCashReplenishment.Status.REVERSED)

        reverse_petty_cash_document(voucher, self.user)
        fund.refresh_from_db()
        voucher.refresh_from_db()
        self.assertEqual(fund.current_balance, Decimal("200"))
        self.assertEqual(voucher.status, PettyCashVoucher.Status.REVERSED)

    def test_dedicated_budget_documents_apply_change_transfer_and_carry_over(self):
        today = timezone.localdate()
        source = Budget.objects.create(
            company=self.company, name="FY Source", start_date=today, end_date=today,
            amount=Decimal("1000"), status=Budget.Status.APPROVED,
        )
        destination = Budget.objects.create(
            company=self.company, name="FY Destination", start_date=today, end_date=today,
            amount=Decimal("100"), status=Budget.Status.APPROVED,
        )
        group = BudgetAccountGroup.objects.create(company=self.company, code="CAPEX", name="Capital")
        budget_account = BudgetAccount.objects.create(company=self.company, group=group, code="MACH", name="Machine")
        reason = BudgetChangeReasonCategory.objects.create(company=self.company, code="SCOPE", name="Scope Change")
        request = BudgetChangeRequest.objects.create(
            company=self.company, number="BCR-1", reason_category=reason, reason="Approved scope",
            requested_by=self.user,
        )
        BudgetChangeRequestLine.objects.create(
            request=request, budget=destination, budget_account=budget_account, amount=Decimal("50")
        )
        approve_budget_change_request(request, self.user)
        destination.refresh_from_db()
        self.assertEqual(destination.amount, Decimal("150"))

        transfer = BudgetTransfer.objects.create(company=self.company, number="BTR-1", requested_by=self.user)
        BudgetTransferLine.objects.create(
            transfer=transfer, source_budget=source, destination_budget=destination,
            budget_account=budget_account, amount=Decimal("200"),
        )
        approve_budget_transfer(transfer, self.user)
        source.refresh_from_db()
        destination.refresh_from_db()
        self.assertEqual(source.amount, Decimal("800"))
        self.assertEqual(destination.amount, Decimal("350"))

        next_year = Budget.objects.create(
            company=self.company, name="Next FY", start_date=today, end_date=today,
            amount=Decimal("0"), status=Budget.Status.APPROVED,
        )
        carry = BudgetCarryOver.objects.create(company=self.company, number="BCO-1", requested_by=self.user)
        BudgetCarryOverLine.objects.create(
            carry_over=carry, source_budget=source, destination_budget=next_year,
            budget_account=budget_account, amount=Decimal("125"),
        )
        approve_budget_carry_over(carry, self.user)
        source.refresh_from_db()
        next_year.refresh_from_db()
        self.assertEqual(source.amount, Decimal("675"))
        self.assertEqual(next_year.amount, Decimal("125"))

    def test_integration_compliance_parser_and_provisioning_services(self):
        event = AccountingIntegrationEvent.objects.create(
            company=self.company, source_module="pos", source_model="pos.order", source_id=1,
            event_type="session_close", reference="POS-1", amount=Decimal("75"),
            payload={"lines": [
                {"account_id": self.bank.pk, "debit": "75", "credit": "0"},
                {"account_id": self.revenue.pk, "debit": "0", "credit": "75"},
            ]},
        )
        self.assertEqual(post_integration_event(event, self.user).status, "posted")

        profile = CountryTaxProfile.objects.create(
            company=self.company, country_code="ID", registration_number="01.001.001.1-001.000"
        )
        report = ComplianceReport.objects.create(
            profile=profile, report_type="VAT", period_start=timezone.localdate(),
            period_end=timezone.localdate(), reference="VAT-1", generated_by=self.user,
        )
        report = generate_compliance_report(report, self.user)
        self.assertEqual(report.status, ComplianceReport.Status.GENERATED)
        self.assertIn("debit", report.totals)

        bank_account = CompanyBankAccount.objects.create(
            company=self.company, journal=self.journal, bank_name="Parser Bank", account_number="PARSE-1",
            account_holder="Company", currency="IDR",
        )
        parse_job = BankStatementParseJob.objects.create(
            company=self.company, bank_account=bank_account, filename="statement.csv", parser_type="csv",
            source_text="date,reference,description,amount\n2026-10-17,TX1,Receipt,100.00\n", requested_by=self.user,
        )
        parse_job = parse_bank_statement_job(parse_job)
        self.assertEqual(parse_job.row_count, 1)
        self.assertEqual(parse_job.status, BankStatementParseJob.Status.PARSED)

        run = CompanyAccountingProvisioningRun.objects.create(
            company=self.other_company, country_code="ID", requested_by=self.user
        )
        run = provision_company_accounting(run, self.user)
        self.assertEqual(run.status, "completed")
        self.assertGreaterEqual(run.created_accounts, 10)

    def test_ar_reconciliation_suggests_allocates_and_reverses_partial_pairs(self):
        debit_entry = JournalEntry.objects.create(
            company=self.company, journal=self.journal, number="AR-DEBIT", entry_date=timezone.localdate()
        )
        debit_line = JournalLine.objects.create(
            entry=debit_entry, account=self.receivable, partner=self.partner, debit=Decimal("100")
        )
        JournalLine.objects.create(entry=debit_entry, account=self.revenue, credit=Decimal("100"))
        post_journal_entry(debit_entry, self.user)
        credit_lines = []
        for index, amount in enumerate((Decimal("60"), Decimal("40")), start=1):
            entry = JournalEntry.objects.create(
                company=self.company, journal=self.journal, number=f"AR-CREDIT-{index}",
                entry_date=timezone.localdate(),
            )
            JournalLine.objects.create(entry=entry, account=self.bank, debit=amount)
            credit_lines.append(JournalLine.objects.create(
                entry=entry, account=self.receivable, partner=self.partner, credit=amount
            ))
            post_journal_entry(entry, self.user)
        session = ARReconciliationSession.objects.create(
            company=self.company, reference="AR-REC-1", partner=self.partner, actor=self.user
        )
        suggest_ar_reconciliation_session(session, self.user)
        self.assertEqual(session.lines.count(), 3)
        reconciled = reconcile_ar_session(session, self.user)
        self.assertEqual(reconciled.total_debit, Decimal("100"))
        self.assertEqual(reconciled.allocations.filter(status="active").count(), 2)
        for line in [debit_line, *credit_lines]:
            line.refresh_from_db()
            self.assertTrue(line.reconciled)
        reverse_ar_session(reconciled, self.user)
        self.assertFalse(ARReconciliationAllocation.objects.filter(session=session, status="active").exists())
        for line in [debit_line, *credit_lines]:
            line.refresh_from_db()
            self.assertFalse(line.reconciled)

    def test_country_runtime_and_signed_tax_callback_are_auditable(self):
        profile = CountryTaxProfile.objects.create(
            company=self.company, country_code="ID", registration_number="0123456789012345",
            configuration={
                "legal_name": self.company.name, "npwp": "0123456789012345",
                "business_unit_code": "0123456789012345000000", "callback_secret_alias": "tax-test",
            },
        )
        template = StatutoryReportTemplate.objects.create(
            profile=profile, form_code="VAT-OUT", name="Coretax VAT Out", version="1", output_format="json"
        )
        tax_code = LocalizationTaxCode.objects.get(profile=profile, code="PPN11")
        tax_code.posting_account = self.payable
        tax_code.save(update_fields=["posting_account", "updated_at"])
        LocalizedTaxTransactionLine.objects.create(
            profile=profile, tax_code=tax_code, partner=self.partner,
            transaction_date=timezone.localdate(), document_number="INV-TAX-1", document_type="sale",
            tax_base=Decimal("100"), tax_amount=Decimal("11"), gross_amount=Decimal("111"), currency="IDR",
            authority_fields={"partner_npwp": "0987654321098765"},
        )
        report = ComplianceReport.objects.create(
            profile=profile, report_type=template.form_code, period_start=timezone.localdate(),
            period_end=timezone.localdate(), reference="VAT-OUT-1", generated_by=self.user,
        )
        generated = generate_compliance_report(report, self.user)
        self.assertEqual(generated.totals["PPN"], "11.00")
        self.assertEqual(
            generated.content_sha256, hashlib.sha256(generated.rendered_content.encode("utf-8")).hexdigest()
        )

        authority_document = TaxAuthorityDocument.objects.create(
            profile=profile, direction="outgoing", document_type="VAT-OUT",
            internal_reference="VAT-CALLBACK-1", external_reference="EXT-1",
            status=TaxAuthorityDocument.Status.SENT,
        )
        payload = {"event_id": "EVT-1", "external_reference": "EXT-1", "status": "accepted"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        signature = hmac.new(b"callback-secret", canonical, hashlib.sha256).hexdigest()
        with override_settings(ACCOUNTING_CONNECTOR_CREDENTIALS={"tax-test": {"callback_secret": "callback-secret"}}):
            response = self.client.post(
                f"/accounting/tax/callbacks/{profile.pk}/", data=json.dumps(payload),
                content_type="application/json", HTTP_X_SIGNATURE=f"sha256={signature}",
            )
        self.assertEqual(response.status_code, 200)
        authority_document.refresh_from_db()
        self.assertEqual(authority_document.status, TaxAuthorityDocument.Status.ACCEPTED)


class AccountingWorkspaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin-accounting", email="admin@example.com", password="test-password"
        )
        ERPApp.objects.create(
            slug="accounting", name="Accounting", category="Finance", summary="Accounting", enabled=True, status="beta"
        )

    def test_accounting_workspace_and_report_render(self):
        self.client.force_login(self.user)

        workspace = self.client.get("/apps/accounting/")
        report = self.client.get("/apps/accounting/feature/trial-balance/")

        self.assertEqual(workspace.status_code, 200)
        self.assertContains(workspace, "General Ledger")
        self.assertContains(workspace, "Cash &amp; Bank")
        self.assertEqual(report.status_code, 200)
        self.assertContains(report, "Trial Balance")

    def test_hmx_catalog_menus_and_csv_export_are_available(self):
        self.client.force_login(self.user)

        workspace = self.client.get("/apps/accounting/")
        exported = self.client.get("/apps/accounting/feature/general-ledger/export.csv")
        exported_pdf = self.client.get("/apps/accounting/feature/general-ledger/export.pdf")
        exported_excel = self.client.get("/apps/accounting/feature/general-ledger/export.xls")

        for label in (
            "Account Tags", "Account Type", "Accounting Analysis", "Accounting Reports",
            "Accounting Settings", "Balance Sheet", "Bank / Cash Book", "Bank Accounts",
            "Bank Statements", "Banks", "Business Statements", "Cash &amp; Bank",
            "Cash Flow Statement", "Chart of Account", "Company Account Mapping",
            "Cost Center Ledger", "Currency Revaluation", "Customer Deposits",
            "Customer Multi Receipts", "Customer Prepayment Details", "Customer Withholding Tax",
            "Day Book", "Debt Collection", "Deposit", "Financial Ratio", "Financial Statements",
            "Fiscal Period", "Fiscal Position", "Foreign Exchange Report", "General Ledger",
            "General Ledger Report", "Generate Opening Balance", "Internal Bank Transfer",
            "Journal", "Journal Entries", "Journal Entry Report", "Journal Entry Template",
            "Journal Items", "Other Expense", "Other Income", "Overview", "Partner Aging",
            "Partner Ledger", "Payment Cheque", "Payment Voucher", "Prepayment Report",
            "Product", "Product Category", "Product Variants", "Profit and Loss",
            "Receipt Cheque", "Receipt Voucher", "Reconciliation", "Reconciliation Rules",
            "Recurring Journal Entries", "Statement of Account", "Statement of Changes in Equity",
            "Tax Report", "Trial Balance", "Vendor Deposits", "Vendor Multi Payments",
            "Vendor Prepayment Details", "Vendor Withholding Tax",
        ):
            self.assertContains(workspace, label)
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(exported_pdf.status_code, 200)
        self.assertTrue(exported_pdf.content.startswith(b"%PDF"))
        self.assertEqual(exported_excel.status_code, 200)
        self.assertEqual(exported_excel["Content-Type"], "application/vnd.ms-excel; charset=utf-8")
