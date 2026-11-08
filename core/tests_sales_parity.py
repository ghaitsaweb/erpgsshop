from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from modules.finance.models import (
    Account,
    AccountJournal,
    AccountingIntegrationEvent,
    AccountingSettings,
    AccountTax,
    CommercialAccountingSettings,
    CompanyAccountingProvisioningRun,
    JournalEntry,
    ProductAccountMapping,
    SaleOrderInvoiceAccounting,
    SalesInvoiceLineProvenance,
)
from modules.finance.sale_invoice_extension import (
    create_and_post_sales_order_invoice,
    create_sales_order_invoice,
)
from modules.finance.services import (
    post_invoice,
    post_sale_order_accounting,
    provision_company_accounting,
)
from modules.organization.models import UserOrganizationAccess

from .models import (
    Company,
    Contact,
    Invoice,
    Product,
    SaleOrder,
    SaleOrderLine,
    recalculate_sale_order_totals,
)


def provision_sales_accounting(company, product, *, auto_post_sales=False):
    receivable = Account.objects.create(
        company=company, code="1100", name="Receivable", account_type=Account.AccountType.ASSET
    )
    revenue = Account.objects.create(
        company=company, code="4100", name="Revenue", account_type=Account.AccountType.INCOME
    )
    expense = Account.objects.create(
        company=company, code="5000", name="Expense", account_type=Account.AccountType.EXPENSE
    )
    payable = Account.objects.create(
        company=company, code="2100", name="Payable", account_type=Account.AccountType.LIABILITY
    )
    prepayment = Account.objects.create(
        company=company, code="1300", name="Prepayment", account_type=Account.AccountType.ASSET
    )
    tax_account = Account.objects.create(
        company=company, code="2200", name="Sales Tax Payable", account_type=Account.AccountType.LIABILITY
    )
    sales_journal = AccountJournal.objects.create(
        company=company,
        code="SAL",
        name="Sales Journal",
        journal_type=AccountJournal.JournalType.SALES,
        default_debit_account=receivable,
        default_credit_account=revenue,
    )
    purchase_journal = AccountJournal.objects.create(
        company=company,
        code="PUR",
        name="Purchase Journal",
        journal_type=AccountJournal.JournalType.PURCHASE,
        default_debit_account=expense,
        default_credit_account=payable,
    )
    AccountingSettings.objects.create(
        company=company,
        default_receivable_account=receivable,
        default_payable_account=payable,
    )
    CommercialAccountingSettings.objects.create(
        company=company,
        sales_journal=sales_journal,
        purchase_journal=purchase_journal,
        sales_downpayment_account=payable,
        purchase_downpayment_account=prepayment,
        auto_post_sales=auto_post_sales,
    )
    ProductAccountMapping.objects.create(
        company=company,
        product=product,
        revenue_account=revenue,
        expense_account=expense,
    )
    AccountTax.objects.create(
        company=company,
        code="VAT11",
        name="VAT 11%",
        tax_scope=AccountTax.TaxScope.SALES,
        method=AccountTax.TaxMethod.PERCENT,
        rate=Decimal("11.0000"),
        account=tax_account,
    )
    return {"receivable": receivable, "revenue": revenue, "tax": tax_account}


class SalesOrderParityTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(code="SALE-PAR", name="Sales Parity", currency="IDR")
        self.customer = Contact.objects.create(
            company=self.company,
            code="CUS-PAR",
            name="Parity Customer",
            address="Customer Address",
        )
        self.product = Product.objects.create(
            company=self.company,
            sku="PRD-PAR",
            name="Parity Product",
            unit="Pcs",
            sale_price=Decimal("100.00"),
            purchase_price=Decimal("0.00"),
        )
        self.order = SaleOrder.objects.create(
            company=self.company,
            number="SO-PAR-001",
            customer=self.customer,
            global_discount_percent=Decimal("5.00"),
        )

    def test_sales_totals_include_line_discount_global_discount_and_tax(self):
        SaleOrderLine.objects.create(
            order=self.order,
            product=self.product,
            quantity=Decimal("2.000"),
            unit_price=Decimal("100.00"),
            discount_percent=Decimal("10.00"),
            tax_percent=Decimal("11.0000"),
        )

        self.order.refresh_from_db()

        self.assertEqual(self.order.subtotal_amount, Decimal("200.00"))
        self.assertEqual(self.order.discount_amount, Decimal("29.00"))
        self.assertEqual(self.order.untaxed_amount, Decimal("171.00"))
        self.assertEqual(self.order.tax_amount, Decimal("18.81"))
        self.assertEqual(self.order.total, Decimal("189.81"))

    def test_sales_total_query_count_does_not_grow_with_line_count(self):
        def replace_lines(count):
            SaleOrderLine.objects.filter(order=self.order).delete()
            SaleOrderLine.objects.bulk_create([
                SaleOrderLine(
                    order=self.order,
                    product=self.product,
                    quantity=1,
                    unit_price=10,
                    discount_percent=0,
                    tax_percent=11,
                )
                for _index in range(count)
            ])

        replace_lines(2)
        with CaptureQueriesContext(connection) as small_context:
            recalculate_sale_order_totals(self.order)
        replace_lines(40)
        with CaptureQueriesContext(connection) as large_context:
            recalculate_sale_order_totals(self.order)

        self.assertEqual(len(small_context), len(large_context))

    def test_explicit_sales_completion_posts_journal_when_auto_post_is_disabled(self):
        receivable = Account.objects.create(
            company=self.company,
            code="1100",
            name="Receivable",
            account_type=Account.AccountType.ASSET,
        )
        revenue = Account.objects.create(
            company=self.company,
            code="4100",
            name="Revenue",
            account_type=Account.AccountType.INCOME,
        )
        expense = Account.objects.create(
            company=self.company,
            code="5000",
            name="Expense",
            account_type=Account.AccountType.EXPENSE,
        )
        payable = Account.objects.create(
            company=self.company,
            code="2100",
            name="Payable",
            account_type=Account.AccountType.LIABILITY,
        )
        prepayment = Account.objects.create(
            company=self.company,
            code="1300",
            name="Prepayment",
            account_type=Account.AccountType.ASSET,
        )
        tax_account = Account.objects.create(
            company=self.company,
            code="2200",
            name="Sales Tax Payable",
            account_type=Account.AccountType.LIABILITY,
        )
        sales_journal = AccountJournal.objects.create(
            company=self.company,
            code="SAL",
            name="Sales Journal",
            journal_type=AccountJournal.JournalType.SALES,
            default_debit_account=receivable,
            default_credit_account=revenue,
        )
        purchase_journal = AccountJournal.objects.create(
            company=self.company,
            code="PUR",
            name="Purchase Journal",
            journal_type=AccountJournal.JournalType.PURCHASE,
            default_debit_account=expense,
            default_credit_account=payable,
        )
        AccountingSettings.objects.create(
            company=self.company,
            default_receivable_account=receivable,
            default_payable_account=payable,
        )
        CommercialAccountingSettings.objects.create(
            company=self.company,
            sales_journal=sales_journal,
            purchase_journal=purchase_journal,
            sales_downpayment_account=payable,
            purchase_downpayment_account=prepayment,
            auto_post_sales=False,
        )
        ProductAccountMapping.objects.create(
            company=self.company,
            product=self.product,
            revenue_account=revenue,
            expense_account=expense,
        )
        AccountTax.objects.create(
            company=self.company,
            code="VAT11",
            name="VAT 11%",
            tax_scope=AccountTax.TaxScope.SALES,
            method=AccountTax.TaxMethod.PERCENT,
            rate=Decimal("11.0000"),
            account=tax_account,
        )
        SaleOrderLine.objects.create(
            order=self.order,
            product=self.product,
            quantity=2,
            unit_price=100,
            discount_percent=10,
            tax_percent=11,
        )
        self.order.status = SaleOrder.Status.SALE
        self.order.save(update_fields=["status", "updated_at"])

        completed = post_sale_order_accounting(self.order)

        event = AccountingIntegrationEvent.objects.select_related("journal_entry").get(
            company=self.company,
            source_model="core.saleorder",
            source_id=self.order.pk,
            event_type="sale_order_complete",
        )
        self.assertEqual(completed.status, SaleOrder.Status.SALE)
        self.assertEqual(completed.total, Decimal("189.81"))
        self.assertEqual(event.status, AccountingIntegrationEvent.Status.POSTED)
        self.assertEqual(event.journal_entry.status, JournalEntry.Status.POSTED)
        self.assertEqual(event.journal_entry.total_debit, Decimal("189.81"))
        self.assertEqual(event.journal_entry.total_credit, Decimal("189.81"))

    def test_create_and_post_invoice_links_order_lines_and_journal(self):
        provision_sales_accounting(self.company, self.product, auto_post_sales=False)
        source_line = SaleOrderLine.objects.create(
            order=self.order,
            product=self.product,
            quantity=2,
            unit_price=100,
            discount_percent=10,
            tax_percent=11,
        )
        self.order.status = SaleOrder.Status.SALE
        self.order.save(update_fields=["status", "updated_at"])

        completed = create_and_post_sales_order_invoice(self.order)

        link = SaleOrderInvoiceAccounting.objects.select_related("settlement_invoice").get(order=self.order)
        invoice = link.settlement_invoice
        journal = JournalEntry.objects.get(
            company=self.company,
            source_type="account_invoice",
            source_reference=invoice.number,
        )
        line_provenance = SalesInvoiceLineProvenance.objects.get(sale_order_line=source_line)
        source_line.refresh_from_db()
        self.assertEqual(completed.status, SaleOrder.Status.SALE)
        self.assertEqual(invoice.status, Invoice.Status.POSTED)
        self.assertEqual(invoice.total, Decimal("189.81"))
        self.assertEqual(journal.status, JournalEntry.Status.POSTED)
        self.assertEqual(journal.total_debit, Decimal("189.81"))
        self.assertEqual(journal.total_credit, Decimal("189.81"))
        self.assertEqual(line_provenance.invoice_line.invoice_id, invoice.pk)
        self.assertEqual(source_line.invoiced_quantity, Decimal("2.000"))

    def test_hmx_sales_flow_creates_draft_invoice_before_posting_journal(self):
        SaleOrderLine.objects.create(
            order=self.order,
            product=self.product,
            quantity=1,
            unit_price=100,
        )
        self.order.status = SaleOrder.Status.SALE
        self.order.save(update_fields=["status", "updated_at"])

        invoiced_order = create_sales_order_invoice(self.order)

        link = SaleOrderInvoiceAccounting.objects.select_related("settlement_invoice").get(order=self.order)
        self.assertEqual(invoiced_order.status, SaleOrder.Status.SALE)
        self.assertEqual(link.settlement_invoice.status, Invoice.Status.DRAFT)
        self.assertFalse(JournalEntry.objects.filter(
            company=self.company,
            source_type="account_invoice",
            source_reference=link.settlement_invoice.number,
        ).exists())

        with self.assertRaisesMessage(
            ValidationError,
            "Configure an active Sales journal before posting this invoice.",
        ):
            post_invoice(link.settlement_invoice)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SaleOrder.Status.SALE)
        link.settlement_invoice.refresh_from_db()
        self.assertEqual(link.settlement_invoice.status, Invoice.Status.DRAFT)

    def test_company_accounting_provisioning_enables_hmx_invoice_post_flow(self):
        actor = get_user_model().objects.create_user(
            username="sales-provisioning-actor",
            password="test-password",
        )
        SaleOrderLine.objects.create(
            order=self.order,
            product=self.product,
            quantity=1,
            unit_price=100,
        )
        self.order.status = SaleOrder.Status.SALE
        self.order.save(update_fields=["status", "updated_at"])
        run = CompanyAccountingProvisioningRun.objects.create(
            company=self.company,
            country_code="ID",
            requested_by=actor,
        )

        provision_company_accounting(run, actor)
        create_sales_order_invoice(self.order, actor)
        link = SaleOrderInvoiceAccounting.objects.select_related("settlement_invoice").get(order=self.order)
        posted_invoice = post_invoice(link.settlement_invoice, actor)

        self.order.refresh_from_db()
        journal = JournalEntry.objects.get(
            company=self.company,
            source_type="account_invoice",
            source_reference=posted_invoice.number,
        )
        self.assertEqual(posted_invoice.status, Invoice.Status.POSTED)
        self.assertEqual(journal.journal.journal_type, AccountJournal.JournalType.SALES)
        self.assertEqual(self.order.status, SaleOrder.Status.SALE)

    def test_invoice_and_journal_query_count_does_not_grow_with_order_lines(self):
        provision_sales_accounting(self.company, self.product, auto_post_sales=False)

        def confirmed_order(sequence, line_count):
            order = SaleOrder.objects.create(
                company=self.company,
                number=f"SO-SCALE-{sequence}",
                customer=self.customer,
                status=SaleOrder.Status.SALE,
            )
            SaleOrderLine.objects.bulk_create([
                SaleOrderLine(
                    order=order,
                    product=self.product,
                    quantity=1,
                    unit_price=10,
                    tax_percent=11,
                )
                for _index in range(line_count)
            ])
            return order

        create_and_post_sales_order_invoice(confirmed_order(1, 1))
        small_order = confirmed_order(2, 2)
        with CaptureQueriesContext(connection) as small_context:
            create_and_post_sales_order_invoice(small_order)
        large_order = confirmed_order(12, 12)
        with CaptureQueriesContext(connection) as large_context:
            create_and_post_sales_order_invoice(large_order)

        self.assertEqual(len(small_context), len(large_context))


class SalesOrderFormParityTests(TestCase):
    def test_sales_form_exposes_hmx_commercial_line_fields(self):
        user = get_user_model().objects.create_superuser(
            username="sales-parity-admin",
            password="test-password",
            email="sales@example.com",
        )
        company = Company.objects.create(code="SALE-UI", name="Sales UI")
        customer = Contact.objects.create(company=company, code="CUS-UI", name="UI Customer")
        order = SaleOrder.objects.create(company=company, number="SO-UI-001", customer=customer)
        self.client.force_login(user)

        response = self.client.get(f"/app/sales/{order.pk}/edit/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Order Lines, Taxes &amp; Fulfilment")
        self.assertContains(response, "Delivered Qty")
        self.assertContains(response, "Invoiced Qty")
        self.assertContains(response, "Tax Amount")
        self.assertContains(response, "Sales Journal")

    def test_create_invoice_action_matches_hmx_draft_then_post_flow(self):
        user = get_user_model().objects.create_superuser(
            username="sales-hmx-flow-admin",
            password="test-password",
            email="sales-hmx-flow@example.com",
        )
        company = Company.objects.create(code="SALE-HMX", name="Sales HMX Flow")
        customer = Contact.objects.create(company=company, code="CUS-HMX", name="HMX Customer")
        product = Product.objects.create(
            company=company,
            sku="PRD-HMX",
            name="HMX Product",
            sale_price=Decimal("125.00"),
        )
        order = SaleOrder.objects.create(
            company=company,
            number="SO-HMX-001",
            customer=customer,
            status=SaleOrder.Status.SALE,
        )
        SaleOrderLine.objects.create(
            order=order,
            product=product,
            quantity=2,
            unit_price=Decimal("125.00"),
        )
        self.client.force_login(user)

        response = self.client.post(f"/app/sales/{order.pk}/action/invoice/")

        link = SaleOrderInvoiceAccounting.objects.select_related("settlement_invoice").get(order=order)
        invoice = link.settlement_invoice
        self.assertRedirects(response, f"/app/invoices/{invoice.pk}/edit/")
        order.refresh_from_db()
        self.assertEqual(order.status, SaleOrder.Status.SALE)
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertFalse(JournalEntry.objects.filter(
            company=company,
            source_type="account_invoice",
            source_reference=invoice.number,
        ).exists())
        sales_response = self.client.get(f"/app/sales/{order.pk}/edit/")
        self.assertNotContains(sales_response, ">Create Invoice<")
        self.assertContains(sales_response, invoice.number)

    def test_manual_sales_create_resolves_unique_customer_display_value(self):
        user = get_user_model().objects.create_superuser(
            username="sales-manual-admin",
            password="test-password",
            email="sales-manual@example.com",
        )
        company = Company.objects.create(code="SALE-MAN", name="Sales Manual")
        customer = Contact.objects.create(
            company=company,
            code="CUS-MAN",
            name="Manual Customer",
            contact_type=Contact.ContactType.CUSTOMER,
        )
        self.client.force_login(user)

        response = self.client.post(
            "/app/sales/new/",
            {
                "company": company.pk,
                "number": "SO-MAN-001",
                "organization_branch": company.branches.get(is_main=True).pk,
                "customer": "",
                "customer_display": "Manual Customer",
                "order_date": timezone.localdate().isoformat(),
                "document_lines-TOTAL_FORMS": "0",
                "document_lines-INITIAL_FORMS": "0",
                "document_lines-MIN_NUM_FORMS": "0",
                "document_lines-MAX_NUM_FORMS": "500",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
            str(response.context["form"].errors) if response.context else "",
        )
        order = SaleOrder.objects.get(number="SO-MAN-001")
        self.assertEqual(order.customer, customer)

    def test_sales_user_can_search_customer_without_contacts_permission(self):
        user = get_user_model().objects.create_user(
            username="sales-customer-search",
            password="test-password",
        )
        user.user_permissions.add(Permission.objects.get(codename="add_saleorder"))
        access = UserOrganizationAccess.objects.create(
            user=user,
            all_companies=True,
            all_branches=True,
        )
        company = Company.objects.create(code="SALE-SEARCH", name="Sales Search")
        customer = Contact.objects.create(
            company=company,
            code="CUS-SEARCH",
            name="Searchable Customer",
            contact_type=Contact.ContactType.CUSTOMER,
        )
        self.client.force_login(user)

        response = self.client.get("/api/customers/search/?q=Searchable")

        self.assertFalse(user.has_perm("core.view_contact"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [
            {"id": customer.pk, "text": "CUS-SEARCH — Searchable Customer"}
        ])
