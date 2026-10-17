"""Asset disposal accounting extension for invoice posting and provisioning."""

from django.core.exceptions import ValidationError

from .invoice_posting import (
    InvoicePostingLine,
    InvoicePostingOverride,
    register_invoice_cancellation_observer,
    register_invoice_posting_contributor,
    register_invoice_posting_failure_observer,
)
from .models import Account, AssetAccountingSettings, AssetDisposal
from .provisioning import (
    AccountDefinition,
    register_accounting_blueprint,
    register_company_accounting_provisioner,
)


def _sold_asset_invoice_posting(*, invoice, rows, partner_account, total, journal, actor=None):
    disposal = (
        AssetDisposal.objects.select_for_update(of=("self",))
        .select_related(
            "asset", "partner", "customer_invoice", "category_accounting_rule__disposal_journal",
            "category_accounting_rule__fixed_asset_account",
            "category_accounting_rule__accumulated_depreciation_account",
            "category_accounting_rule__disposal_gain_account",
            "category_accounting_rule__disposal_loss_account",
        )
        .filter(customer_invoice_id=invoice.pk, disposal_type=AssetDisposal.DisposalType.SOLD)
        .first()
    )
    if disposal is None:
        return None
    if invoice.invoice_type != invoice.InvoiceType.CUSTOMER:
        raise ValidationError("A sold asset must be linked to a customer invoice.")
    if disposal.status != AssetDisposal.Status.APPROVED:
        raise ValidationError("The asset disposal must be approved before its invoice can be posted.")
    if disposal.partner_id != invoice.partner_id:
        raise ValidationError("The asset disposal customer does not match the invoice customer.")
    if total != disposal.proceeds:
        raise ValidationError("The asset disposal invoice total must equal the sale proceeds.")

    settings = AssetAccountingSettings.objects.select_related(
        "journal",
        "fixed_asset_account",
        "accumulated_depreciation_account",
        "disposal_gain_account",
        "disposal_loss_account",
    ).get(company_id=invoice.company_id)
    category = disposal.category_accounting_rule
    disposal_journal = category.disposal_journal if category and category.disposal_journal_id else settings.journal
    fixed_asset_account = (
        category.fixed_asset_account if category and category.fixed_asset_account_id else settings.fixed_asset_account
    )
    accumulated_account = (
        category.accumulated_depreciation_account
        if category and category.accumulated_depreciation_account_id
        else settings.accumulated_depreciation_account
    )
    gain_account = (
        category.disposal_gain_account if category and category.disposal_gain_account_id else settings.disposal_gain_account
    )
    loss_account = (
        category.disposal_loss_account if category and category.disposal_loss_account_id else settings.disposal_loss_account
    )
    asset_cost = disposal.asset.acquisition_value
    if asset_cost <= 0:
        raise ValidationError("The disposed asset must have a positive acquisition value.")
    if disposal.book_value < 0 or disposal.book_value > asset_cost:
        raise ValidationError("Asset book value must be between zero and acquisition value.")
    accumulated = asset_cost - disposal.book_value
    gain_loss = disposal.proceeds - disposal.book_value
    if disposal.gain_loss_amount != gain_loss:
        raise ValidationError("Gain/loss must equal proceeds less book value.")

    label = f"Asset Sale: {disposal.asset.code} - {disposal.asset.name}"
    lines = []
    if accumulated:
        lines.append(InvoicePostingLine(
            account=accumulated_account,
            debit=accumulated,
            description=label,
        ))
    lines.extend((
        InvoicePostingLine(
            account=partner_account,
            debit=disposal.proceeds,
            partner=invoice.partner,
            description=invoice.number,
            due_date=invoice.due_date,
        ),
        InvoicePostingLine(
            account=fixed_asset_account,
            credit=asset_cost,
            description=label,
        ),
    ))
    if gain_loss > 0:
        lines.append(InvoicePostingLine(
            account=gain_account,
            credit=gain_loss,
            description=label,
        ))
    elif gain_loss < 0:
        lines.append(InvoicePostingLine(
            account=loss_account,
            debit=abs(gain_loss),
            description=label,
        ))

    def complete(entry):
        disposal.status = AssetDisposal.Status.POSTED
        disposal.accounting_status = AssetDisposal.AccountingStatus.JOURNAL_POSTED
        disposal.accounting_error_message = ""
        disposal.journal_entry = entry
        disposal.save(update_fields=[
            "status", "accounting_status", "accounting_error_message", "journal_entry", "updated_at"
        ])
        disposal.asset.status = disposal.asset.Status.RETIRED
        disposal.asset.save(update_fields=["status", "updated_at"])

    return InvoicePostingOverride(
        journal=disposal_journal,
        lines=tuple(lines),
        description=label,
        after_post=complete,
    )


def _record_asset_invoice_failure(*, invoice, error):
    AssetDisposal.objects.filter(
        customer_invoice_id=invoice.pk,
        disposal_type=AssetDisposal.DisposalType.SOLD,
    ).update(
        accounting_status=AssetDisposal.AccountingStatus.ACCOUNTING_ERROR,
        accounting_error_message=str(error)[:2000],
    )


def _asset_invoice_cancelled(*, invoice, actor=None):
    disposal = AssetDisposal.objects.select_related("asset").filter(customer_invoice_id=invoice.pk).first()
    if disposal is None:
        return
    disposal.status = AssetDisposal.Status.REVERSED
    disposal.accounting_status = AssetDisposal.AccountingStatus.NOT_STARTED
    disposal.accounting_error_message = ""
    disposal.save(update_fields=["status", "accounting_status", "accounting_error_message", "updated_at"])
    disposal.asset.status = disposal.asset.Status.AVAILABLE
    disposal.asset.save(update_fields=["status", "updated_at"])


def provision_asset_disposal_accounting(*, company, accounts, journals, actor=None):
    settings, settings_created = AssetAccountingSettings.objects.get_or_create(
        company=company,
        defaults={
            "journal": journals["GEN"],
            "fixed_asset_account": accounts["1500"],
            "accumulated_depreciation_account": accounts["1590"],
            "depreciation_expense_account": accounts["5100"],
            "disposal_gain_account": accounts["4200"],
            "disposal_loss_account": accounts["5200"],
            "disposal_clearing_account": accounts["1509"],
        },
    )
    settings_updated = not settings_created and settings.disposal_clearing_account_id is None
    if settings_updated:
        clearing = accounts["1509"]
        settings.disposal_clearing_account = clearing
        settings.save(update_fields=["disposal_clearing_account", "updated_at"])
    return {
        "extension": "asset_disposal_accounting",
        "settings_created": settings_created,
        "settings_updated": settings_updated,
    }


def register_asset_disposal_extension():
    register_accounting_blueprint(accounts=(
        AccountDefinition("1500", "Fixed Assets", Account.AccountType.ASSET),
        AccountDefinition("1509", "Asset Disposal Clearing", Account.AccountType.ASSET, True),
        AccountDefinition("1590", "Accumulated Depreciation", Account.AccountType.ASSET),
        AccountDefinition("5100", "Depreciation Expense", Account.AccountType.EXPENSE),
    ))
    register_invoice_posting_contributor(_sold_asset_invoice_posting)
    register_invoice_posting_failure_observer(_record_asset_invoice_failure)
    register_invoice_cancellation_observer(_asset_invoice_cancelled)
    register_company_accounting_provisioner(provision_asset_disposal_accounting)
