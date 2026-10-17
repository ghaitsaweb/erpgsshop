"""Transactional Construction document services.

The functions in this module keep queries independent of recordset size where
possible and centralise the cross-module effects used by document workflows.
"""

from collections import defaultdict
import csv
from decimal import Decimal
import hashlib
from io import StringIO
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Invoice, InvoiceLine, Product, StockMove
from modules.finance.models import JournalEntry, JournalLine
from modules.purchase.models import PurchaseRequest, PurchaseRequestLine

from .models import (
    ConstructionAccountingPosting,
    ConstructionBOQ,
    ConstructionBOQCostLine,
    ConstructionBOQImportLog,
    ConstructionBOQItem,
    ConstructionBOQTemplate,
    ConstructionBudgetChangeRequest,
    ConstructionContract,
    ConstructionContractDPLine,
    ConstructionCostSheet,
    ConstructionCostSheetItem,
    ConstructionCostSheetResource,
    ConstructionInventoryTransfer,
    ConstructionMaterialDemand,
    ConstructionOpeningBalance,
    ConstructionOpeningBalanceImport,
    ConstructionProgressiveClaim,
    ConstructionProgressiveClaimCumulative,
    ConstructionProgressiveClaimLine,
    ConstructionProjectAccountSnapshotLine,
    ConstructionProjectAccountSettings,
    ConstructionQuantitySurvey,
    ConstructionQuantitySurveyJOSource,
    ConstructionQuantitySurveyLine,
    ConstructionResourceActualization,
)


ZERO = Decimal("0")
HUNDRED = Decimal("100")


def _adjust(base, adjustment_type, value, *, add=True):
    value = value or ZERO
    adjustment = base * value / HUNDRED if adjustment_type == "percent" else value
    return base + adjustment if add else base - adjustment


@transaction.atomic
def recalculate_boq(boq_id):
    boq = ConstructionBOQ.objects.select_for_update().get(pk=boq_id)
    items = list(ConstructionBOQItem.objects.filter(boq_id=boq_id).order_by("sequence", "id"))
    cost_lines = list(ConstructionBOQCostLine.objects.filter(boq_item__boq_id=boq_id))

    costs_by_item = defaultdict(Decimal)
    changed_cost_lines = []
    for line in cost_lines:
        amount = (line.quantity or ZERO) * (line.unit_rate or ZERO)
        revenue = _adjust(amount, line.markup_type, line.markup_value, add=True)
        revenue = _adjust(revenue, line.discount_type, line.discount_value, add=False)
        revenue_with_tax = revenue + (revenue * (line.tax_percent or ZERO) / HUNDRED)
        costs_by_item[line.boq_item_id] += amount
        if line.amount != amount or line.revenue_including_tax != revenue_with_tax:
            line.amount = amount
            line.revenue_including_tax = revenue_with_tax
            changed_cost_lines.append(line)
    if changed_cost_lines:
        ConstructionBOQCostLine.objects.bulk_update(changed_cost_lines, ["amount", "revenue_including_tax", "updated_at"])

    changed_items = []
    total_cost = total_revenue = ZERO
    for item in items:
        base = (item.quantity or ZERO) * (item.unit_price or ZERO)
        cost = costs_by_item[item.pk] or ((item.quantity or ZERO) * (item.budget_cost or ZERO))
        revenue = _adjust(base, item.commercial_markup_type, item.commercial_markup_value, add=True)
        revenue = _adjust(revenue, item.commercial_discount_type, item.commercial_discount_value, add=False)
        revenue_with_tax = revenue + (revenue * (item.commercial_tax_percent or ZERO) / HUNDRED)
        item_cost = cost
        if item.budget_cost != item_cost or item.commercial_revenue != revenue_with_tax:
            item.budget_cost = item_cost
            item.commercial_revenue = revenue_with_tax
            changed_items.append(item)
        total_cost += item_cost
        total_revenue += revenue
    if changed_items:
        ConstructionBOQItem.objects.bulk_update(changed_items, ["budget_cost", "commercial_revenue", "updated_at"])

    total_revenue = _adjust(total_revenue, boq.global_markup_type, boq.global_markup_value, add=True)
    total_revenue = _adjust(total_revenue, boq.global_discount_type, boq.global_discount_value, add=False)
    tax = total_revenue * (boq.global_tax_percent or ZERO) / HUNDRED
    boq.total_cost = total_cost
    boq.total_revenue_before_tax = total_revenue
    boq.total_tax_amount = tax
    boq.total_revenue_including_tax = total_revenue + tax
    boq.total_margin = total_revenue - total_cost
    boq.save(update_fields=[
        "total_cost", "total_revenue_before_tax", "total_tax_amount",
        "total_revenue_including_tax", "total_margin", "updated_at",
    ])
    return boq


@transaction.atomic
def apply_boq_template(boq_id, template_id, user=None):
    boq = ConstructionBOQ.objects.select_for_update().get(pk=boq_id)
    template = ConstructionBOQTemplate.objects.get(pk=template_id, company_id=boq.project.company_id)
    template_items = list(template.items.select_related("product_group", "product").order_by("sequence", "id"))
    template_costs = list(
        ConstructionBOQCostLine.objects.none()
    )
    from .models import ConstructionBOQTemplateCostLine
    template_costs = list(ConstructionBOQTemplateCostLine.objects.filter(template_item__template=template).order_by("sequence", "id"))

    item_map = {}
    created_items = []
    for source in template_items:
        item = ConstructionBOQItem(
            boq=boq,
            sequence=source.sequence,
            wbs_code=source.code,
            level=source.level,
            is_leaf_node=source.is_leaf_node,
            is_direct_amount=source.is_direct_amount,
            direct_amount_info=source.direct_amount_info,
            product_group=source.product_group,
            product=source.product,
            description=source.name or source.description,
            unit=source.unit,
            quantity=source.quantity,
            unit_price=source.unit_cost,
            budget_cost=source.unit_cost,
        )
        created_items.append(item)
    ConstructionBOQItem.objects.bulk_create(created_items)
    for source, created in zip(template_items, created_items):
        item_map[source.pk] = created
    hierarchy_updates = []
    for source in template_items:
        if source.parent_item_id:
            created = item_map[source.pk]
            created.parent_item = item_map[source.parent_item_id]
            hierarchy_updates.append(created)
    if hierarchy_updates:
        ConstructionBOQItem.objects.bulk_update(hierarchy_updates, ["parent_item"])

    ConstructionBOQCostLine.objects.bulk_create([
        ConstructionBOQCostLine(
            boq_item=item_map[source.template_item_id], sequence=source.sequence,
            input_type=source.input_type, cost_category=source.cost_category,
            product_id=source.product_id, job_position_id=source.job_position_id,
            vendor_id=source.vendor_id, work_package_id=source.work_package_id,
            description=source.description, ownership=source.ownership,
            quantity=source.quantity, unit=source.unit, unit_rate=source.unit_rate,
            amount=source.amount, notes=source.notes,
        )
        for source in template_costs
    ])
    boq.template = template
    boq.template_loaded_at = timezone.now()
    boq.template_loaded_by = user
    boq.save(update_fields=["template", "template_loaded_at", "template_loaded_by", "updated_at"])
    return recalculate_boq(boq.pk)


@transaction.atomic
def recalculate_cost_sheet(cost_sheet_id):
    cost_sheet = ConstructionCostSheet.objects.select_for_update().get(pk=cost_sheet_id)
    items = list(ConstructionCostSheetItem.objects.filter(cost_sheet_id=cost_sheet_id))
    resources = list(ConstructionCostSheetResource.objects.filter(cost_sheet_item__cost_sheet_id=cost_sheet_id))
    resource_costs = defaultdict(Decimal)
    for resource in resources:
        resource_costs[resource.cost_sheet_item_id] += resource.revised_cost or resource.total_cost

    changed = []
    revenue = cost = utilised = ZERO
    for item in items:
        revised_revenue = item.quantity * item.unit_cost + item.revenue_adjustment
        revised_cost = resource_costs[item.pk] or (item.quantity * item.unit_cost + item.budget_cost_adjustment)
        if item.revised_revenue != revised_revenue or item.revised_cost != revised_cost:
            item.revised_revenue = revised_revenue
            item.revised_cost = revised_cost
            changed.append(item)
        revenue += revised_revenue
        cost += revised_cost
        utilised += item.utilised_budget
    if changed:
        ConstructionCostSheetItem.objects.bulk_update(changed, ["revised_revenue", "revised_cost", "updated_at"])
    cost_sheet.revised_revenue = revenue
    cost_sheet.revised_cost = cost
    cost_sheet.utilised_amount = utilised
    cost_sheet.estimated_margin = revenue - cost
    cost_sheet.margin_percent = (cost_sheet.estimated_margin / revenue * HUNDRED) if revenue else ZERO
    cost_sheet.save(update_fields=[
        "revised_revenue", "revised_cost", "utilised_amount", "estimated_margin", "margin_percent", "updated_at",
    ])
    return cost_sheet


@transaction.atomic
def recalculate_budget_change(request_id):
    request = ConstructionBudgetChangeRequest.objects.select_for_update().get(pk=request_id)
    impacts = [line.variance for line in request.lines.only("previous_amount", "proposed_amount")]
    increase = sum((value for value in impacts if value > 0), ZERO)
    decrease = -sum((value for value in impacts if value < 0), ZERO)
    request.total_increase = increase
    request.total_decrease = decrease
    request.amount = increase - decrease
    request.revised_total_cost = request.current_total_cost + request.amount
    request.margin_impact = -request.amount
    request.save(update_fields=["total_increase", "total_decrease", "amount", "revised_total_cost", "margin_impact", "updated_at"])
    return request


@transaction.atomic
def recalculate_claim(claim_id):
    claim = ConstructionProgressiveClaim.objects.select_for_update().get(pk=claim_id)
    lines = list(ConstructionProgressiveClaimLine.objects.filter(claim_id=claim_id).select_related("boq_item"))
    changed = []
    gross = ZERO
    cumulative_rows = []
    for line in lines:
        qty = line.certified_quantity or line.current_quantity
        amount = qty * line.unit_price
        progress = ((line.previous_quantity + qty) / line.contract_quantity * HUNDRED) if line.contract_quantity else ZERO
        if line.amount != amount or line.progress_percent != progress:
            line.amount = amount
            line.progress_percent = progress
            changed.append(line)
        gross += amount
        cumulative_rows.append(ConstructionProgressiveClaimCumulative(
            claim=claim, sequence=line.sequence, boq_item=line.boq_item,
            previous_quantity=line.previous_quantity, this_period_quantity=qty,
            cumulative_quantity=line.previous_quantity + qty, contract_quantity=line.contract_quantity,
            progress_percent=progress, cumulative_amount=(line.previous_quantity + qty) * line.unit_price,
            display_status=claim.status, is_current_claim=True,
        ))
    if changed:
        ConstructionProgressiveClaimLine.objects.bulk_update(changed, ["amount", "progress_percent", "updated_at"])
    ConstructionProgressiveClaimCumulative.objects.filter(claim_id=claim_id, is_current_claim=True).update(
        is_current_claim=False, updated_at=timezone.now()
    )
    ConstructionProgressiveClaimCumulative.objects.bulk_create(cumulative_rows)
    claim.gross_amount = gross
    claim.certified_amount = gross
    claim.save(update_fields=["gross_amount", "certified_amount", "updated_at"])
    return claim


@transaction.atomic
def refresh_quantity_survey(survey_id):
    survey = ConstructionQuantitySurvey.objects.select_for_update().get(pk=survey_id)
    sources = list(
        ConstructionQuantitySurveyJOSource.objects.filter(survey_line__survey_id=survey_id)
        .select_related("survey_line", "progress_log")
    )
    totals = defaultdict(Decimal)
    for source in sources:
        totals[source.survey_line_id] += source.approved_quantity
    lines = list(ConstructionQuantitySurveyLine.objects.filter(survey_id=survey_id))
    changed = []
    for line in lines:
        approved = totals[line.pk] if line.pk in totals else line.accepted_quantity
        amount = approved * line.unit_rate
        progress = ((line.previous_quantity + approved) / line.contract_quantity * HUNDRED) if line.contract_quantity else ZERO
        if line.accepted_quantity != approved or line.amount != amount or line.progress_percent != progress:
            line.accepted_quantity = approved
            line.amount = amount
            line.progress_percent = progress
            changed.append(line)
    if changed:
        ConstructionQuantitySurveyLine.objects.bulk_update(changed, ["accepted_quantity", "amount", "progress_percent", "updated_at"])
    return survey


@transaction.atomic
def snapshot_project_accounts(project_id):
    settings = ConstructionProjectAccountSettings.objects.select_related(
        "project", "revenue_account", "cost_account", "work_in_progress_account", "retention_account"
    ).get(project_id=project_id, active=True)
    category_accounts = {
        "owner_revenue": settings.revenue_account,
        "construction_cost": settings.cost_account,
        "work_in_progress": settings.work_in_progress_account,
        "retention": settings.retention_account,
    }
    category_accounts.update({line.resource_category: line.account for line in settings.cip_lines.select_related("account")})
    existing = {
        row.resource_category: row
        for row in ConstructionProjectAccountSnapshotLine.objects.filter(project_id=project_id)
    }
    creates, updates = [], []
    for category, account in category_accounts.items():
        values = {
            "account": account,
            "account_display": str(account),
            "account_type_label": account.get_account_type_display(),
        }
        row = existing.get(category)
        if row is None:
            creates.append(ConstructionProjectAccountSnapshotLine(
                project=settings.project, resource_category=category, **values,
            ))
        else:
            for field, value in values.items():
                setattr(row, field, value)
            updates.append(row)
    if creates:
        ConstructionProjectAccountSnapshotLine.objects.bulk_create(creates)
    if updates:
        ConstructionProjectAccountSnapshotLine.objects.bulk_update(
            updates, ["account", "account_display", "account_type_label", "updated_at"]
        )
    return creates + updates


def procurement_budget_decision(resource, requested_quantity, unit_rate):
    requested = requested_quantity * unit_rate
    available = max(resource.revised_cost - resource.utilised_budget, ZERO)
    exposure = requested - available
    ratio = (requested / resource.revised_cost * HUNDRED) if resource.revised_cost else HUNDRED
    policy = resource.cost_sheet_item.cost_sheet
    if policy.procurement_validation_level == "block" and (exposure > 0 or ratio > policy.procurement_block_threshold):
        decision = "block"
    elif policy.procurement_validation_level in {"warning", "block"} and ratio >= policy.procurement_warning_threshold:
        decision = "warn"
    else:
        decision = "allow"
    snapshot = {"requested": str(requested), "available": str(available), "exposure": str(exposure), "ratio": str(ratio)}
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return decision, snapshot, digest


@transaction.atomic
def post_inventory_transfer(transfer_id, user):
    transfer = ConstructionInventoryTransfer.objects.select_for_update().select_related(
        "project", "source_warehouse", "destination_warehouse"
    ).get(pk=transfer_id)
    if transfer.status != ConstructionInventoryTransfer.Status.IN_TRANSIT:
        raise ValidationError("Only an in-transit construction transfer can be completed.")
    lines = list(transfer.lines.select_related("product"))
    if not lines:
        raise ValidationError("Add at least one transfer line before completing the transfer.")
    quantities_by_product = defaultdict(Decimal)
    for line in lines:
        quantities_by_product[line.product_id] += line.quantity
    locked_products = {
        product.pk: product
        for product in Product.objects.select_for_update().filter(pk__in=quantities_by_product)
    }
    insufficient = [
        f"{locked_products[product_id].sku}: {locked_products[product_id].current_stock} available"
        for product_id, quantity in quantities_by_product.items()
        if locked_products[product_id].current_stock < quantity
    ]
    if insufficient:
        raise ValidationError("Insufficient stock for transfer: " + ", ".join(insufficient))
    moves = []
    for line in lines:
        moves.extend([
            StockMove(
                company=transfer.project.company, product=line.product, warehouse=transfer.source_warehouse,
                direction=StockMove.Direction.OUT, quantity=line.quantity,
                reference=transfer.number, movement_date=transfer.transfer_date, note=line.notes, created_by=user,
            ),
            StockMove(
                company=transfer.project.company, product=line.product, warehouse=transfer.destination_warehouse,
                direction=StockMove.Direction.IN, quantity=line.quantity,
                reference=transfer.number, movement_date=transfer.transfer_date, note=line.notes, created_by=user,
            ),
        ])
    StockMove.objects.bulk_create(moves)
    for index, line in enumerate(lines):
        line.stock_move_out = moves[index * 2]
        line.stock_move_in = moves[index * 2 + 1]
    type(lines[0]).objects.bulk_update(lines, ["stock_move_out", "stock_move_in", "updated_at"])
    transfer.status = ConstructionInventoryTransfer.Status.DONE
    transfer.save(update_fields=["status", "updated_at"])
    return transfer


@transaction.atomic
def post_construction_accounting(posting_id):
    posting = ConstructionAccountingPosting.objects.select_for_update().select_related("project").get(pk=posting_id)
    if posting.journal_entry_id:
        return posting
    settings = ConstructionProjectAccountSettings.objects.select_related(
        "revenue_account", "cost_account", "work_in_progress_account", "retention_account"
    ).get(project=posting.project, active=True)
    number = f"CST-{posting.pk:06d}"
    entry = JournalEntry.objects.create(
        company=posting.project.company, number=number, entry_date=posting.posting_date,
        reference=posting.reference, description=posting.get_posting_type_display(),
    )
    if posting.posting_type == ConstructionAccountingPosting.PostingType.RESOURCE_ACTUALIZATION:
        debit_account, credit_account = settings.cost_account, settings.work_in_progress_account
    else:
        debit_account, credit_account = settings.retention_account, settings.revenue_account
    JournalLine.objects.bulk_create([
        JournalLine(entry=entry, account=debit_account, description=posting.reference, debit=posting.amount),
        JournalLine(entry=entry, account=credit_account, description=posting.reference, credit=posting.amount),
    ])
    entry.total_debit = posting.amount
    entry.total_credit = posting.amount
    entry.status = JournalEntry.Status.POSTED
    entry.save(update_fields=["total_debit", "total_credit", "status", "updated_at"])
    posting.journal_entry = entry
    posting.save(update_fields=["journal_entry", "updated_at"])
    return posting


@transaction.atomic
def load_cost_sheet_from_boq(cost_sheet_id):
    cost_sheet = ConstructionCostSheet.objects.select_for_update().select_related("boq").get(pk=cost_sheet_id)
    if not cost_sheet.boq_id:
        raise ValidationError("Select a BOQ before loading the cost sheet.")
    existing_boq_item_ids = set(
        ConstructionCostSheetItem.objects.filter(cost_sheet_id=cost_sheet_id, boq_item_id__isnull=False)
        .values_list("boq_item_id", flat=True)
    )
    boq_items = list(
        ConstructionBOQItem.objects.filter(boq_id=cost_sheet.boq_id)
        .exclude(pk__in=existing_boq_item_ids)
        .order_by("sequence", "id")
    )
    created_items = ConstructionCostSheetItem.objects.bulk_create([
        ConstructionCostSheetItem(
            cost_sheet=cost_sheet, boq_item=item, sequence=item.sequence, wbs_code=item.wbs_code,
            level=item.level, is_leaf_node=item.is_leaf_node, is_direct_amount=item.is_direct_amount,
            product=item.product, description=item.description, quantity=item.quantity,
            unit_cost=item.unit_price, revised_revenue=item.sales_amount,
        )
        for item in boq_items
    ])
    created_by_boq = {item.boq_item_id: item for item in created_items}
    boq_resources = list(
        ConstructionBOQCostLine.objects.filter(boq_item_id__in=created_by_boq)
        .select_related("product", "vendor", "work_package")
    )
    ConstructionCostSheetResource.objects.bulk_create([
        ConstructionCostSheetResource(
            cost_sheet_item=created_by_boq[resource.boq_item_id], boq_resource=resource,
            sequence=resource.sequence, cost_category=resource.cost_category, input_type=resource.input_type,
            product_id=resource.product_id, job_position_id=resource.job_position_id,
            vendor_id=resource.vendor_id, work_package_id=resource.work_package_id,
            description=resource.description, unit=resource.unit, quantity=resource.quantity,
            unit_rate=resource.unit_rate, revised_cost=resource.amount,
            ownership=resource.ownership, inherited_from_boq=True,
        )
        for resource in boq_resources
    ])
    return recalculate_cost_sheet(cost_sheet.pk)


@transaction.atomic
def apply_budget_change(request_id):
    request = ConstructionBudgetChangeRequest.objects.select_for_update().get(pk=request_id)
    lines = list(request.lines.all())
    item_ids = {line.cost_sheet_item_id for line in lines if line.cost_sheet_item_id}
    resource_ids = {line.cost_sheet_resource_id for line in lines if line.cost_sheet_resource_id}
    items = {item.pk: item for item in ConstructionCostSheetItem.objects.select_for_update().filter(pk__in=item_ids)}
    resources = {resource.pk: resource for resource in ConstructionCostSheetResource.objects.select_for_update().filter(pk__in=resource_ids)}
    changed_items, changed_resources = [], []
    new_items, new_resources = [], []
    for line in lines:
        if line.cost_sheet_resource_id and line.cost_sheet_resource_id in resources:
            resource = resources[line.cost_sheet_resource_id]
            resource.quantity = ZERO if line.zeroed else line.revised_quantity
            resource.unit_rate = line.revised_unit_rate
            resource.revised_cost = line.proposed_amount
            changed_resources.append(resource)
        elif line.cost_sheet_item_id and line.cost_sheet_item_id in items:
            item = items[line.cost_sheet_item_id]
            item.quantity = ZERO if line.zeroed else line.revised_quantity
            item.unit_cost = line.revised_unit_rate
            item.revised_cost = line.proposed_amount
            changed_items.append(item)
        elif line.line_type == "new_wbs" and request.cost_sheet_id:
            new_items.append(ConstructionCostSheetItem(
                cost_sheet=request.cost_sheet, sequence=line.sequence, wbs_code=line.new_wbs_code,
                description=line.new_wbs_name or line.description, quantity=line.revised_quantity,
                unit_cost=line.revised_unit_rate, revised_cost=line.proposed_amount,
                planned_start=line.new_plan_start, planned_end=line.new_plan_end, is_cost_sheet_only=True,
            ))
        elif line.line_type == "new_resource" and line.cost_sheet_item_id:
            new_resources.append(ConstructionCostSheetResource(
                cost_sheet_item_id=line.cost_sheet_item_id, sequence=line.sequence,
                cost_category=line.cost_category or "other", input_type="product" if line.product_id else "manual",
                product_id=line.product_id, job_position_id=line.job_position_id,
                vendor_id=line.vendor_id, work_package_id=line.work_package_id,
                description=line.description, quantity=line.revised_quantity,
                unit_rate=line.revised_unit_rate, revised_cost=line.proposed_amount,
            ))
    if changed_items:
        ConstructionCostSheetItem.objects.bulk_update(changed_items, ["quantity", "unit_cost", "revised_cost", "updated_at"])
    if changed_resources:
        ConstructionCostSheetResource.objects.bulk_update(changed_resources, ["quantity", "unit_rate", "revised_cost", "updated_at"])
    if new_items:
        ConstructionCostSheetItem.objects.bulk_create(new_items)
    if new_resources:
        ConstructionCostSheetResource.objects.bulk_create(new_resources)
    if request.cost_sheet_id:
        recalculate_cost_sheet(request.cost_sheet_id)
    return request


def _invoice_for_amount(*, company, partner, number, invoice_date, amount, description, invoice_type):
    invoice, created = Invoice.objects.get_or_create(
        company=company, number=number,
        defaults={
            "invoice_type": invoice_type, "partner": partner, "invoice_date": invoice_date,
            "due_date": invoice_date, "total": amount, "notes": description,
        },
    )
    if created:
        InvoiceLine.objects.create(invoice=invoice, description=description, quantity=1, unit_price=amount)
    return invoice


@transaction.atomic
def create_contract_down_payment_invoices(contract_id):
    contract = ConstructionContract.objects.select_for_update().select_related("project__company", "customer").get(pk=contract_id)
    lines = list(ConstructionContractDPLine.objects.select_for_update().filter(contract=contract).order_by("sequence", "id"))
    if not lines:
        raise ValidationError("Define at least one down-payment schedule line.")
    invoice_numbers = [f"CST-DP-{contract.pk}-{line.sequence}" for line in lines]
    invoices_by_number = {
        invoice.number: invoice
        for invoice in Invoice.objects.filter(company=contract.project.company, number__in=invoice_numbers)
    }
    new_invoices = []
    amount_by_number = {}
    for line, number in zip(lines, invoice_numbers):
        amount = line.invoice_amount or (contract.contract_value * line.portion_percent / HUNDRED)
        amount_by_number[number] = amount
        if number not in invoices_by_number:
            new_invoices.append(Invoice(
                company=contract.project.company, number=number, invoice_type=Invoice.InvoiceType.CUSTOMER,
                partner=contract.customer, invoice_date=line.invoice_date or timezone.localdate(),
                due_date=line.invoice_date or timezone.localdate(), total=amount,
                notes=f"Down payment {contract.number}",
            ))
    if new_invoices:
        Invoice.objects.bulk_create(new_invoices)
        invoices_by_number.update({invoice.number: invoice for invoice in new_invoices})
        InvoiceLine.objects.bulk_create([
            InvoiceLine(
                invoice=invoice, description=f"Down payment {contract.number}", quantity=1,
                unit_price=amount_by_number[invoice.number],
            )
            for invoice in new_invoices
        ])
    for line, number in zip(lines, invoice_numbers):
        line.invoice = invoices_by_number[number]
        line.invoice_amount = amount_by_number[number]
        line.payment_status = "invoiced"
    ConstructionContractDPLine.objects.bulk_update(lines, ["invoice", "invoice_amount", "payment_status", "updated_at"])
    contract.down_payment_status = "invoiced"
    contract.save(update_fields=["down_payment_status", "updated_at"])
    return contract


@transaction.atomic
def create_contract_retention_invoice(contract_id, stage):
    contract = ConstructionContract.objects.select_for_update().select_related("project__company", "customer").get(pk=contract_id)
    percent = contract.retention_percent if stage == 1 else contract.retention2_percent
    if percent <= 0:
        raise ValidationError(f"Retention {stage} percentage must be greater than zero.")
    amount = contract.contract_value * percent / HUNDRED
    release_date = contract.retention1_release_date if stage == 1 else contract.retention2_release_date
    invoice = _invoice_for_amount(
        company=contract.project.company, partner=contract.customer,
        number=f"CST-RET{stage}-{contract.pk}", invoice_date=release_date or timezone.localdate(),
        amount=amount, description=f"Retention {stage} release {contract.number}",
        invoice_type=Invoice.InvoiceType.CUSTOMER,
    )
    setattr(contract, f"retention{stage}_invoice", invoice)
    setattr(contract, f"retention{stage}_status", "invoiced")
    contract.save(update_fields=[f"retention{stage}_invoice", f"retention{stage}_status", "updated_at"])
    return contract


@transaction.atomic
def create_claim_invoice(claim_id):
    claim = ConstructionProgressiveClaim.objects.select_for_update().select_related(
        "project__company", "contract__customer", "vendor"
    ).get(pk=claim_id)
    if claim.invoice_id:
        return claim
    partner = claim.vendor if claim.claim_type == "subcontract" and claim.vendor_id else claim.contract.customer
    invoice_type = Invoice.InvoiceType.VENDOR if claim.claim_type == "subcontract" else Invoice.InvoiceType.CUSTOMER
    invoice = _invoice_for_amount(
        company=claim.project.company, partner=partner, number=f"CST-CLM-{claim.pk}",
        invoice_date=claim.claim_date, amount=claim.net_amount,
        description=f"Progressive claim {claim.number}", invoice_type=invoice_type,
    )
    claim.invoice = invoice
    claim.save(update_fields=["invoice", "updated_at"])
    return claim


@transaction.atomic
def create_purchase_request_from_demand(demand_id, user):
    demand = ConstructionMaterialDemand.objects.select_for_update().select_related("project__company").get(pk=demand_id)
    lines = list(demand.lines.select_related("product"))
    if not lines:
        raise ValidationError("Add at least one material demand line.")
    request, created = PurchaseRequest.objects.get_or_create(
        company=demand.project.company, number=f"CST-PR-{demand.pk}",
        defaults={
            "requestor": user, "request_date": demand.request_date, "required_date": demand.required_date,
            "justification": f"Construction material demand {demand.number}", "notes": demand.notes,
        },
    )
    if created:
        PurchaseRequestLine.objects.bulk_create([
            PurchaseRequestLine(
                request=request, product=line.product, description=line.description or line.product.name,
                quantity=line.requested_quantity, estimated_unit_price=line.product.purchase_price,
            )
            for line in lines
        ])
    return request


def _normalise_boq_rows(raw_rows):
    rows, errors = [], []
    for index, raw in enumerate(raw_rows, start=1):
        try:
            description = str(raw.get("description") or raw.get("name") or "").strip()
            if not description:
                raise ValueError("description is required")
            rows.append({
                "sequence": int(raw.get("sequence") or index * 10),
                "wbs_code": str(raw.get("wbs_code") or raw.get("code") or "").strip(),
                "description": description,
                "unit": str(raw.get("unit") or "Unit"),
                "quantity": str(Decimal(str(raw.get("quantity") or 0))),
                "unit_price": str(Decimal(str(raw.get("unit_price") or raw.get("rate") or 0))),
                "budget_cost": str(Decimal(str(raw.get("budget_cost") or raw.get("cost") or 0))),
            })
        except (ValueError, TypeError, ArithmeticError) as exc:
            errors.append({"row": index, "error": str(exc)})
    return rows, errors


@transaction.atomic
def validate_boq_import(import_id):
    job = ConstructionBOQImportLog.objects.select_for_update().get(pk=import_id)
    raw_rows = job.payload
    if job.source_file:
        content = job.source_file.read().decode("utf-8-sig")
        job.source_checksum = hashlib.sha256(content.encode()).hexdigest()
        if job.source_file.name.lower().endswith(".json"):
            raw_rows = json.loads(content)
        else:
            raw_rows = list(csv.DictReader(StringIO(content)))
    if not isinstance(raw_rows, list):
        raise ValidationError("BOQ import payload must be a list of rows.")
    normalized, errors = _normalise_boq_rows(raw_rows)
    job.attempted_count = len(raw_rows)
    job.normalized_payload = normalized
    job.validation_errors = errors
    job.state = ConstructionBOQImportLog.State.FAILED if errors else ConstructionBOQImportLog.State.VALIDATED
    job.save(update_fields=["source_checksum", "attempted_count", "normalized_payload", "validation_errors", "state", "updated_at"])
    return job


@transaction.atomic
def execute_boq_import(import_id):
    job = validate_boq_import(import_id)
    if job.state != ConstructionBOQImportLog.State.VALIDATED:
        raise ValidationError("Resolve BOQ import validation errors before importing.")
    ConstructionBOQItem.objects.bulk_create([
        ConstructionBOQItem(
            boq=job.boq, sequence=row["sequence"], wbs_code=row["wbs_code"], description=row["description"],
            unit=row["unit"], quantity=Decimal(row["quantity"]), unit_price=Decimal(row["unit_price"]),
            budget_cost=Decimal(row["budget_cost"]),
        )
        for row in job.normalized_payload
    ])
    job.imported_count = len(job.normalized_payload)
    job.state = ConstructionBOQImportLog.State.IMPORTED
    job.save(update_fields=["imported_count", "state", "updated_at"])
    recalculate_boq(job.boq_id)
    return job


@transaction.atomic
def execute_opening_balance_import(import_id):
    job = ConstructionOpeningBalanceImport.objects.select_for_update().get(pk=import_id)
    rows = job.canonical_payload.get("balances", []) if isinstance(job.canonical_payload, dict) else []
    if not isinstance(rows, list):
        raise ValidationError("Opening balance payload must contain a balances list.")
    references = {str(row.get("reference", "")).strip() for row in rows}
    existing = set(
        ConstructionOpeningBalance.objects.filter(project=job.project, reference__in=references)
        .values_list("reference", flat=True)
    )
    creates, errors = [], []
    for index, row in enumerate(rows, start=1):
        reference = str(row.get("reference", "")).strip()
        if not reference:
            errors.append({"row": index, "error": "reference is required"})
            continue
        if reference in existing:
            continue
        try:
            creates.append(ConstructionOpeningBalance(
                project=job.project, reference=reference, balance_date=job.cutoff_date,
                budget_amount=Decimal(str(row.get("budget_amount", 0))),
                committed_amount=Decimal(str(row.get("committed_amount", 0))),
                actual_amount=Decimal(str(row.get("actual_amount", 0))),
                source_note=job.source_reference,
            ))
        except ArithmeticError as exc:
            errors.append({"row": index, "error": str(exc)})
    if errors:
        job.state = ConstructionOpeningBalanceImport.State.FAILED
        job.error_report = errors
    else:
        ConstructionOpeningBalance.objects.bulk_create(creates)
        job.state = ConstructionOpeningBalanceImport.State.COMPLETED
        job.created_count = len(creates)
        job.created_links = [row.reference for row in creates]
    job.attempted_count = len(rows)
    job.save(update_fields=["state", "error_report", "attempted_count", "created_count", "created_links", "updated_at"])
    return job


def handle_document_save(entity, instance, user):
    handlers = {
        "construction-boqs": recalculate_boq,
        "construction-cost-sheets": recalculate_cost_sheet,
        "construction-budget-changes": recalculate_budget_change,
        "construction-quantity-surveys": refresh_quantity_survey,
        "construction-progressive-claims": recalculate_claim,
    }
    handler = handlers.get(entity)
    return handler(instance.pk) if handler else instance


def handle_document_action(entity, instance, action, user):
    if entity == "construction-boq-imports" and action == "validate":
        return validate_boq_import(instance.pk)
    if entity == "construction-boq-imports" and action == "import":
        return execute_boq_import(instance.pk)
    if entity == "construction-opening-balance-imports" and action == "execute":
        return execute_opening_balance_import(instance.pk)
    if entity == "construction-boqs" and action == "recompute":
        return recalculate_boq(instance.pk)
    if entity == "construction-cost-sheets" and action == "load-boq":
        return load_cost_sheet_from_boq(instance.pk)
    if entity == "construction-cost-sheets" and action == "recompute":
        return recalculate_cost_sheet(instance.pk)
    if entity == "construction-budget-changes" and action == "approve":
        apply_budget_change(instance.pk)
        return recalculate_budget_change(instance.pk)
    if entity == "construction-quantity-surveys" and action == "refresh":
        refresh_quantity_survey(instance.pk)
        return instance
    if entity == "construction-progressive-claims" and action in {"recompute", "certify"}:
        return recalculate_claim(instance.pk)
    if entity == "construction-progressive-claims" and action == "invoice":
        return create_claim_invoice(instance.pk)
    if entity == "construction-contracts" and action == "create-dp-invoices":
        return create_contract_down_payment_invoices(instance.pk)
    if entity == "construction-contracts" and action == "release-retention-1":
        return create_contract_retention_invoice(instance.pk, 1)
    if entity == "construction-contracts" and action == "release-retention-2":
        return create_contract_retention_invoice(instance.pk, 2)
    if entity == "construction-material-demands" and action == "create-purchase-request":
        create_purchase_request_from_demand(instance.pk, user)
        return instance
    if entity == "construction-inventory-transfers" and action == "complete":
        return post_inventory_transfer(instance.pk, user)
    if entity == "construction-accounting-postings" and action == "post":
        return post_construction_accounting(instance.pk)
    if entity == "construction-project-account-settings" and action == "snapshot":
        snapshot_project_accounts(instance.project_id)
    return instance
