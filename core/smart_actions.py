from dataclasses import dataclass
from typing import Callable

from django.core.exceptions import ImproperlyConfigured
from django.db.models import ForeignKey, OneToOneField
from django.db.models import Q, QuerySet


SmartActionFilter = Callable[[QuerySet, object], QuerySet]
SmartActionAvailability = Callable[[object], bool]


@dataclass(frozen=True)
class SmartActionDefinition:
    """One explicitly allowed cross-model navigation from an ERP record."""

    key: str
    source_entity: str
    target_entity: str
    label: str
    icon: str
    filter_queryset: SmartActionFilter
    show_count: bool = True
    hide_when_empty: bool = True
    allow_create: bool = False
    allow_edit: bool = True
    allow_delete: bool = False
    allow_select: bool = False
    is_available: SmartActionAvailability | None = None


def _children_on(field_name):
    """Build a bounded relation filter for an explicitly registered smart action."""

    def filter_queryset(queryset, source):
        return queryset.filter(**{f"{field_name}_id": source.pk})

    return filter_queryset


def _parent_on(field_name):
    """Resolve one explicitly registered foreign-key parent from a source record."""

    def filter_queryset(queryset, source):
        return queryset.filter(pk=getattr(source, f"{field_name}_id"))

    return filter_queryset


def _has_parent_on(field_name):
    def is_available(source):
        return getattr(source, f"{field_name}_id", None) is not None

    return is_available


def _relation_pair(
    *,
    parent_entity,
    child_entity,
    child_field,
    parent_key,
    child_key,
    children_label,
    parent_label,
    children_icon="▤",
):
    """Declare both directions of one known foreign-key relationship."""

    return (
        SmartActionDefinition(
            key=parent_key,
            source_entity=parent_entity,
            target_entity=child_entity,
            label=children_label,
            icon=children_icon,
            filter_queryset=_children_on(child_field),
        ),
        SmartActionDefinition(
            key=child_key,
            source_entity=child_entity,
            target_entity=parent_entity,
            label=parent_label,
            icon="↩",
            filter_queryset=_parent_on(child_field),
        ),
    )


def _sales_invoices(queryset, source):
    return queryset.filter(
        sales_provenance__order_accounting__order_id=source.pk,
    )


def _sales_revisions(queryset, source):
    root_id = source.revision_root_id or source.pk
    return queryset.filter(revision_root_id=root_id)


def _all_scoped_records(queryset, _source):
    return queryset


def _invoice_payments(queryset, source):
    return queryset.filter(allocations__invoice_id=source.pk)


def _payment_invoices(queryset, source):
    return queryset.filter(payment_allocations__payment_id=source.pk)


def _purchase_receipts(queryset, source):
    return queryset.filter(order_id=source.pk)


def _purchase_order_lines(queryset, source):
    return queryset.filter(order_id=source.pk)


def _purchase_order_line_order(queryset, source):
    return queryset.filter(pk=source.order_id)


def _purchase_request_lines(queryset, source):
    return queryset.filter(request_id=source.pk)


def _purchase_request_line_request(queryset, source):
    return queryset.filter(pk=source.request_id)


def _quotation_lines(queryset, source):
    return queryset.filter(quotation_id=source.pk)


def _quotation_line_quotation(queryset, source):
    return queryset.filter(pk=source.quotation_id)


def _direct_purchase_lines(queryset, source):
    return queryset.filter(direct_purchase_id=source.pk)


def _direct_purchase_line_parent(queryset, source):
    return queryset.filter(pk=source.direct_purchase_id)


def _service_work_order_lines(queryset, source):
    return queryset.filter(work_order_id=source.pk)


def _service_work_order_line_parent(queryset, source):
    return queryset.filter(pk=source.work_order_id)


def _purchase_tender_lines(queryset, source):
    return queryset.filter(tender_id=source.pk)


def _purchase_tender_line_parent(queryset, source):
    return queryset.filter(pk=source.tender_id)


def _blanket_order_lines(queryset, source):
    return queryset.filter(blanket_order_id=source.pk)


def _blanket_order_line_parent(queryset, source):
    return queryset.filter(pk=source.blanket_order_id)


def _goods_receipt_lines(queryset, source):
    return queryset.filter(receipt_id=source.pk)


def _goods_receipt_line_parent(queryset, source):
    return queryset.filter(pk=source.receipt_id)


def _purchase_accounting_lines(queryset, source):
    return queryset.filter(document_id=source.pk)


def _purchase_accounting_line_parent(queryset, source):
    return queryset.filter(pk=source.document_id)


def _purchase_inventory_transfer_lines(queryset, source):
    return queryset.filter(transfer_id=source.pk)


def _purchase_inventory_transfer_line_parent(queryset, source):
    return queryset.filter(pk=source.transfer_id)


def _purchase_order_controls(queryset, source):
    return queryset.filter(order_id=source.pk)


def _purchase_order_control_parent(queryset, source):
    return queryset.filter(pk=source.order_id)


def _purchase_order_milestones(queryset, source):
    return queryset.filter(order_id=source.pk)


def _purchase_order_milestone_parent(queryset, source):
    return queryset.filter(pk=source.order_id)


def _purchase_order_communications(queryset, source):
    return queryset.filter(order_id=source.pk)


def _purchase_order_communication_parent(queryset, source):
    return queryset.filter(pk=source.order_id)


def _purchase_order_revisions(queryset, source):
    return queryset.filter(order_id=source.pk)


def _purchase_order_revision_parent(queryset, source):
    return queryset.filter(pk=source.order_id)


def _vendor_category_profiles(queryset, source):
    return queryset.filter(category_id=source.pk)


def _vendor_profile_category(queryset, source):
    return queryset.filter(pk=source.category_id) if source.category_id else queryset.none()


def _vendor_legal_documents(queryset, source):
    return queryset.filter(vendor_id=source.pk)


def _vendor_legal_document_requirements(queryset, source):
    additional_match = Q(
        apply_to__in=["vendor", "both"],
        applicable_vendors=source,
    )
    if source.category_id:
        additional_match |= Q(
            apply_to__in=["category", "both"],
            applicable_categories=source.category_id,
        )
    return (
        queryset.filter(
            company_id=source.company_id,
            active=True,
            document_usage="standard_vendor",
        )
        .filter(
            Q(requirement_type__in=["required", "optional"])
            | (Q(requirement_type="additional") & additional_match)
        )
        .distinct()
    )


def _vendor_legal_document_profile(queryset, source):
    return queryset.filter(pk=source.vendor_id)


def _vendor_prequalifications(queryset, source):
    return queryset.filter(vendor_id=source.pk)


def _vendor_prequalification_profile(queryset, source):
    return queryset.filter(pk=source.vendor_id)


def _vendor_pricelists(queryset, source):
    return queryset.filter(vendor_id=source.pk)


def _vendor_pricelist_profile(queryset, source):
    return queryset.filter(pk=source.vendor_id)


def _vendor_performance_evaluations(queryset, source):
    return queryset.filter(vendor_id=source.pk)


def _vendor_performance_profile(queryset, source):
    return queryset.filter(pk=source.vendor_id)


def _vendor_communications(queryset, source):
    return queryset.filter(vendor_id=source.pk)


def _vendor_communication_profile(queryset, source):
    return queryset.filter(pk=source.vendor_id)


def _vendor_transaction_history(queryset, source):
    return queryset.filter(vendor_id=source.pk)


def _vendor_transaction_profile(queryset, source):
    return queryset.filter(pk=source.vendor_id)


def _prequalification_document_reviews(queryset, source):
    return queryset.filter(prequalification_id=source.pk)


def _document_review_prequalification(queryset, source):
    return queryset.filter(pk=source.prequalification_id)


def _legal_document_reviews(queryset, source):
    return queryset.filter(document_id=source.pk)


def _document_review_legal_document(queryset, source):
    return queryset.filter(pk=source.document_id)


def _pricelist_amendments(queryset, source):
    return queryset.filter(pricelist_id=source.pk)


def _pricelist_amendment_parent(queryset, source):
    return queryset.filter(pk=source.pricelist_id)


def _purchase_team_members(queryset, source):
    return queryset.filter(team_id=source.pk)


def _purchase_team_member_parent(queryset, source):
    return queryset.filter(pk=source.team_id)


def _purchase_team_kpi_targets(queryset, source):
    return queryset.filter(team_id=source.pk)


def _purchase_kpi_target_team(queryset, source):
    return queryset.filter(pk=source.team_id)


def _purchase_kpi_template_targets(queryset, source):
    return queryset.filter(template_id=source.pk)


def _purchase_kpi_target_template(queryset, source):
    return queryset.filter(pk=source.template_id)


def _purchase_team_product_categories(queryset, source):
    return queryset.filter(purchase_teams__pk=source.pk)


def _purchase_product_category_teams(queryset, source):
    return queryset.filter(product_categories__pk=source.pk)


def _purchase_team_order_types(queryset, source):
    return queryset.filter(purchase_teams__pk=source.pk)


def _purchase_order_type_teams(queryset, source):
    return queryset.filter(order_types__pk=source.pk)


def _purchase_team_vendor_categories(queryset, source):
    return queryset.filter(purchase_teams__pk=source.pk)


def _vendor_category_purchase_teams(queryset, source):
    return queryset.filter(vendor_categories__pk=source.pk)


def _purchase_team_member_categories(queryset, source):
    return queryset.filter(purchase_team_member_assignments__pk=source.pk)


def _purchase_product_category_members(queryset, source):
    return queryset.filter(categories__pk=source.pk)


def _purchase_order_type_templates(queryset, source):
    return queryset.filter(order_type_id=source.pk)


def _purchase_order_template_type(queryset, source):
    return queryset.filter(pk=source.order_type_id)


def _purchase_order_template_milestones(queryset, source):
    return queryset.filter(order_template_id=source.pk)


def _purchase_milestone_template_parent(queryset, source):
    return queryset.filter(pk=source.order_template_id)


def _purchase_order_template_contract_terms(queryset, source):
    return queryset.filter(order_template_id=source.pk)


def _purchase_contract_term_template(queryset, source):
    return queryset.filter(pk=source.order_template_id)


def _receipt_purchase_order(queryset, source):
    return queryset.filter(pk=source.order_id)


def _purchase_stock_moves(queryset, source):
    return queryset.filter(
        Q(purchase_receipt_line__receipt__order_id=source.pk)
        | Q(purchase_return_line__receipt__order_id=source.pk)
    ).distinct()


def _receipt_stock_moves(queryset, source):
    return queryset.filter(
        Q(purchase_receipt_line__receipt_id=source.pk)
        | Q(purchase_return_line__receipt_id=source.pk)
    ).distinct()


def _stock_move_receipt(queryset, source):
    return queryset.filter(
        Q(lines__stock_move_id=source.pk) | Q(lines__return_stock_move_id=source.pk)
    ).distinct()


def _receipt_returns(queryset, source):
    return queryset.filter(receipt_id=source.pk)


def _return_receipt(queryset, source):
    return queryset.filter(pk=source.receipt_id)


def _receipt_inventory_transfers(queryset, source):
    return queryset.filter(receipt_id=source.pk)


def _inventory_transfer_receipt(queryset, source):
    return queryset.filter(pk=source.receipt_id) if source.receipt_id else queryset.none()


def _inventory_transfer_stock_moves(queryset, source):
    return queryset.filter(
        Q(purchase_transfer_source_line__transfer_id=source.pk)
        | Q(purchase_transfer_destination_line__transfer_id=source.pk)
    ).distinct()


def _stock_move_inventory_transfer(queryset, source):
    return queryset.filter(
        Q(lines__source_stock_move_id=source.pk)
        | Q(lines__destination_stock_move_id=source.pk)
    ).distinct()


def _purchase_accounting_documents(queryset, source):
    return queryset.filter(order_id=source.pk)


def _accounting_document_purchase(queryset, source):
    return queryset.filter(pk=source.order_id)


def _purchase_vendor_bills(queryset, source):
    return queryset.filter(purchase_accounting_documents__order_id=source.pk).distinct()


def _invoice_purchase_orders(queryset, source):
    return queryset.filter(accounting_documents__invoice_id=source.pk).distinct()


def _purchase_request_quotations(queryset, source):
    return queryset.filter(request_id=source.pk)


def _quotation_purchase_request(queryset, source):
    return queryset.filter(pk=source.request_id) if source.request_id else queryset.none()


def _quotation_purchase_orders(queryset, source):
    return queryset.filter(purchase_control__quotation_id=source.pk)


def _purchase_order_quotation(queryset, source):
    return queryset.filter(orders__order_id=source.pk)


def _purchase_request_blanket_orders(queryset, source):
    return queryset.filter(lines__request_line__request_id=source.pk).distinct()


def _blanket_order_purchase_requests(queryset, source):
    return queryset.filter(lines__blanket_order_lines__blanket_order_id=source.pk).distinct()


def _purchase_request_tenders(queryset, source):
    return queryset.filter(lines__request_line__request_id=source.pk).distinct()


def _tender_purchase_requests(queryset, source):
    return queryset.filter(lines__tender_lines__tender_id=source.pk).distinct()


def _purchase_request_direct_purchases(queryset, source):
    return queryset.filter(request_id=source.pk)


def _purchase_request_re_requests(queryset, source):
    return queryset.filter(lines__origin_request_line__request_id=source.pk).distinct()


def _purchase_request_origins(queryset, source):
    return queryset.filter(lines__re_requested_lines__request_id=source.pk).distinct()


def _direct_purchase_request(queryset, source):
    return queryset.filter(pk=source.request_id) if source.request_id else queryset.none()


def _blanket_order_quotations(queryset, source):
    return queryset.filter(blanket_order_id=source.pk)


def _quotation_blanket_order(queryset, source):
    return queryset.filter(pk=source.blanket_order_id) if source.blanket_order_id else queryset.none()


def _direct_purchase_orders(queryset, source):
    return queryset.filter(pk=source.purchase_order_id) if source.purchase_order_id else queryset.none()


def _purchase_order_direct_purchase(queryset, source):
    return queryset.filter(purchase_order_id=source.pk)


def _direct_purchase_receipts(queryset, source):
    return queryset.filter(order_id=source.purchase_order_id) if source.purchase_order_id else queryset.none()


def _receipt_direct_purchase(queryset, source):
    return queryset.filter(purchase_order_id=source.order_id)


def _direct_purchase_vendor_bills(queryset, source):
    if not source.purchase_order_id:
        return queryset.none()
    return queryset.filter(purchase_accounting_documents__order_id=source.purchase_order_id).distinct()


def _invoice_direct_purchases(queryset, source):
    return queryset.filter(purchase_order__accounting_documents__invoice_id=source.pk).distinct()


def _tender_purchase_orders(queryset, source):
    return queryset.filter(purchase_control__tender_id=source.pk)


def _purchase_order_tender(queryset, source):
    return queryset.filter(orders__order_id=source.pk)


def _tender_bids(queryset, source):
    return queryset.filter(tender_id=source.pk)


def _bid_tender(queryset, source):
    return queryset.filter(pk=source.tender_id)


def _tender_invitations(queryset, source):
    return queryset.filter(tender_id=source.pk)


def _invitation_tender(queryset, source):
    return queryset.filter(pk=source.tender_id)


def _tender_documents(queryset, source):
    return queryset.filter(tender_id=source.pk)


def _document_tender(queryset, source):
    return queryset.filter(pk=source.tender_id)


EXPLICIT_SMART_ACTIONS = (
    SmartActionDefinition(
        key="sales.invoices",
        source_entity="sales",
        target_entity="invoices",
        label="Invoices",
        icon="▤",
        filter_queryset=_sales_invoices,
    ),
    SmartActionDefinition(
        key="sales.revised-documents",
        source_entity="sales",
        target_entity="sales",
        label="Revised Documents",
        icon="↻",
        filter_queryset=_sales_revisions,
        show_count=False,
    ),
    SmartActionDefinition(
        key="sales-order-types.products",
        source_entity="sales-order-types",
        target_entity="products",
        label="List of Products",
        icon="▣",
        filter_queryset=_all_scoped_records,
        show_count=False,
        hide_when_empty=False,
        allow_edit=False,
    ),
    SmartActionDefinition(
        key="invoices.payments",
        source_entity="invoices",
        target_entity="account-payments",
        label="Payments",
        icon="◈",
        filter_queryset=_invoice_payments,
    ),
    SmartActionDefinition(
        key="account-payments.invoices",
        source_entity="account-payments",
        target_entity="invoices",
        label="Invoices",
        icon="▤",
        filter_queryset=_payment_invoices,
    ),
    SmartActionDefinition(
        key="purchases.goods-receipts",
        source_entity="purchases",
        target_entity="goods-receipts",
        label="Goods Receipts",
        icon="▣",
        filter_queryset=_purchase_receipts,
    ),
    SmartActionDefinition(
        key="purchase-requests.lines",
        source_entity="purchase-requests",
        target_entity="purchase-request-lines",
        label="Request Lines",
        icon="☷",
        filter_queryset=_purchase_request_lines,
    ),
    SmartActionDefinition(
        key="purchase-request-lines.purchase-request",
        source_entity="purchase-request-lines",
        target_entity="purchase-requests",
        label="Purchase Request",
        icon="↩",
        filter_queryset=_purchase_request_line_request,
    ),
    SmartActionDefinition(
        key="purchases.order-lines",
        source_entity="purchases",
        target_entity="purchase-order-lines",
        label="Order Lines",
        icon="☷",
        filter_queryset=_purchase_order_lines,
    ),
    SmartActionDefinition(
        key="purchases.order-controls",
        source_entity="purchases",
        target_entity="purchase-order-controls",
        label="Order Control",
        icon="◎",
        filter_queryset=_purchase_order_controls,
    ),
    SmartActionDefinition(
        key="purchase-order-controls.purchase-order",
        source_entity="purchase-order-controls",
        target_entity="purchases",
        label="Purchase Order",
        icon="↩",
        filter_queryset=_purchase_order_control_parent,
    ),
    SmartActionDefinition(
        key="purchases.milestones",
        source_entity="purchases",
        target_entity="purchase-order-milestones",
        label="Milestones",
        icon="◇",
        filter_queryset=_purchase_order_milestones,
    ),
    SmartActionDefinition(
        key="purchase-order-milestones.purchase-order",
        source_entity="purchase-order-milestones",
        target_entity="purchases",
        label="Purchase Order",
        icon="↩",
        filter_queryset=_purchase_order_milestone_parent,
    ),
    SmartActionDefinition(
        key="purchases.communications",
        source_entity="purchases",
        target_entity="purchase-order-communications",
        label="Communications",
        icon="✉",
        filter_queryset=_purchase_order_communications,
    ),
    SmartActionDefinition(
        key="purchase-order-communications.purchase-order",
        source_entity="purchase-order-communications",
        target_entity="purchases",
        label="Purchase Order",
        icon="↩",
        filter_queryset=_purchase_order_communication_parent,
    ),
    SmartActionDefinition(
        key="purchases.revisions",
        source_entity="purchases",
        target_entity="purchase-order-revisions",
        label="Revisions",
        icon="↻",
        filter_queryset=_purchase_order_revisions,
    ),
    SmartActionDefinition(
        key="purchase-order-revisions.purchase-order",
        source_entity="purchase-order-revisions",
        target_entity="purchases",
        label="Purchase Order",
        icon="↩",
        filter_queryset=_purchase_order_revision_parent,
    ),
    SmartActionDefinition(
        key="purchase-order-lines.purchase-order",
        source_entity="purchase-order-lines",
        target_entity="purchases",
        label="Purchase Order",
        icon="↩",
        filter_queryset=_purchase_order_line_order,
    ),
    SmartActionDefinition(
        key="purchases.stock-moves",
        source_entity="purchases",
        target_entity="stock-moves",
        label="Stock Moves",
        icon="⇅",
        filter_queryset=_purchase_stock_moves,
    ),
    SmartActionDefinition(
        key="purchases.accounting-documents",
        source_entity="purchases",
        target_entity="purchase-accounting-documents",
        label="Accounting",
        icon="▤",
        filter_queryset=_purchase_accounting_documents,
    ),
    SmartActionDefinition(
        key="purchases.vendor-bills",
        source_entity="purchases",
        target_entity="invoices",
        label="Vendor Bills",
        icon="▤",
        filter_queryset=_purchase_vendor_bills,
    ),
    SmartActionDefinition(
        key="purchases.rfq",
        source_entity="purchases",
        target_entity="purchase-quotations",
        label="Source RFQ",
        icon="↩",
        filter_queryset=_purchase_order_quotation,
    ),
    SmartActionDefinition(
        key="purchase-requests.quotations",
        source_entity="purchase-requests",
        target_entity="purchase-quotations",
        label="RFQs",
        icon="▤",
        filter_queryset=_purchase_request_quotations,
    ),
    SmartActionDefinition(
        key="purchase-quotations.lines",
        source_entity="purchase-quotations",
        target_entity="purchase-quotation-lines",
        label="RFQ Lines",
        icon="☷",
        filter_queryset=_quotation_lines,
    ),
    SmartActionDefinition(
        key="purchase-quotation-lines.quotation",
        source_entity="purchase-quotation-lines",
        target_entity="purchase-quotations",
        label="RFQ",
        icon="↩",
        filter_queryset=_quotation_line_quotation,
    ),
    SmartActionDefinition(
        key="purchase-requests.blanket-orders",
        source_entity="purchase-requests",
        target_entity="blanket-orders",
        label="Blanket Orders",
        icon="▣",
        filter_queryset=_purchase_request_blanket_orders,
    ),
    SmartActionDefinition(
        key="purchase-requests.tenders",
        source_entity="purchase-requests",
        target_entity="purchase-tenders",
        label="Tenders",
        icon="◇",
        filter_queryset=_purchase_request_tenders,
    ),
    SmartActionDefinition(
        key="purchase-requests.direct-purchases",
        source_entity="purchase-requests",
        target_entity="direct-purchases",
        label="Direct Purchases",
        icon="▣",
        filter_queryset=_purchase_request_direct_purchases,
    ),
    SmartActionDefinition(
        key="purchase-requests.re-requests",
        source_entity="purchase-requests",
        target_entity="purchase-requests",
        label="Re-Requests",
        icon="↻",
        filter_queryset=_purchase_request_re_requests,
    ),
    SmartActionDefinition(
        key="purchase-requests.original-requests",
        source_entity="purchase-requests",
        target_entity="purchase-requests",
        label="Original Requests",
        icon="↩",
        filter_queryset=_purchase_request_origins,
    ),
    SmartActionDefinition(
        key="purchase-quotations.purchase-request",
        source_entity="purchase-quotations",
        target_entity="purchase-requests",
        label="Purchase Request",
        icon="↩",
        filter_queryset=_quotation_purchase_request,
    ),
    SmartActionDefinition(
        key="purchase-quotations.purchase-orders",
        source_entity="purchase-quotations",
        target_entity="purchases",
        label="Purchase Orders",
        icon="▣",
        filter_queryset=_quotation_purchase_orders,
    ),
    SmartActionDefinition(
        key="blanket-orders.purchase-requests",
        source_entity="blanket-orders",
        target_entity="purchase-requests",
        label="Purchase Requests",
        icon="↩",
        filter_queryset=_blanket_order_purchase_requests,
    ),
    SmartActionDefinition(
        key="purchase-tenders.purchase-requests",
        source_entity="purchase-tenders",
        target_entity="purchase-requests",
        label="Purchase Requests",
        icon="↩",
        filter_queryset=_tender_purchase_requests,
    ),
    SmartActionDefinition(
        key="direct-purchases.purchase-request",
        source_entity="direct-purchases",
        target_entity="purchase-requests",
        label="Purchase Request",
        icon="↩",
        filter_queryset=_direct_purchase_request,
    ),
    SmartActionDefinition(
        key="blanket-orders.rfqs",
        source_entity="blanket-orders",
        target_entity="purchase-quotations",
        label="RFQs",
        icon="▤",
        filter_queryset=_blanket_order_quotations,
    ),
    SmartActionDefinition(
        key="purchase-quotations.blanket-order",
        source_entity="purchase-quotations",
        target_entity="blanket-orders",
        label="Blanket Order",
        icon="↩",
        filter_queryset=_quotation_blanket_order,
    ),
    SmartActionDefinition(
        key="direct-purchases.purchase-order",
        source_entity="direct-purchases",
        target_entity="purchases",
        label="Purchase Order",
        icon="▣",
        filter_queryset=_direct_purchase_orders,
    ),
    SmartActionDefinition(
        key="direct-purchases.lines",
        source_entity="direct-purchases",
        target_entity="direct-purchase-lines",
        label="Direct Purchase Lines",
        icon="☷",
        filter_queryset=_direct_purchase_lines,
    ),
    SmartActionDefinition(
        key="direct-purchase-lines.direct-purchase",
        source_entity="direct-purchase-lines",
        target_entity="direct-purchases",
        label="Direct Purchase",
        icon="↩",
        filter_queryset=_direct_purchase_line_parent,
    ),
    SmartActionDefinition(
        key="service-work-orders.lines",
        source_entity="service-work-orders",
        target_entity="service-work-order-lines",
        label="Service Lines",
        icon="☷",
        filter_queryset=_service_work_order_lines,
    ),
    SmartActionDefinition(
        key="service-work-order-lines.service-work-order",
        source_entity="service-work-order-lines",
        target_entity="service-work-orders",
        label="Service Work Order",
        icon="↩",
        filter_queryset=_service_work_order_line_parent,
    ),
    SmartActionDefinition(
        key="direct-purchases.goods-receipts",
        source_entity="direct-purchases",
        target_entity="goods-receipts",
        label="Goods Receipts",
        icon="▣",
        filter_queryset=_direct_purchase_receipts,
    ),
    SmartActionDefinition(
        key="direct-purchases.vendor-bills",
        source_entity="direct-purchases",
        target_entity="invoices",
        label="Vendor Bills",
        icon="▤",
        filter_queryset=_direct_purchase_vendor_bills,
    ),
    SmartActionDefinition(
        key="purchases.direct-purchase",
        source_entity="purchases",
        target_entity="direct-purchases",
        label="Direct Purchase",
        icon="↩",
        filter_queryset=_purchase_order_direct_purchase,
    ),
    SmartActionDefinition(
        key="goods-receipts.direct-purchase",
        source_entity="goods-receipts",
        target_entity="direct-purchases",
        label="Direct Purchase",
        icon="↩",
        filter_queryset=_receipt_direct_purchase,
    ),
    SmartActionDefinition(
        key="invoices.direct-purchases",
        source_entity="invoices",
        target_entity="direct-purchases",
        label="Direct Purchases",
        icon="▣",
        filter_queryset=_invoice_direct_purchases,
    ),
    SmartActionDefinition(
        key="purchase-tenders.purchase-orders",
        source_entity="purchase-tenders",
        target_entity="purchases",
        label="Purchase Orders",
        icon="▣",
        filter_queryset=_tender_purchase_orders,
    ),
    SmartActionDefinition(
        key="purchase-tenders.lines",
        source_entity="purchase-tenders",
        target_entity="purchase-tender-lines",
        label="Tender Lines",
        icon="☷",
        filter_queryset=_purchase_tender_lines,
    ),
    SmartActionDefinition(
        key="purchase-tender-lines.tender",
        source_entity="purchase-tender-lines",
        target_entity="purchase-tenders",
        label="Tender",
        icon="↩",
        filter_queryset=_purchase_tender_line_parent,
    ),
    SmartActionDefinition(
        key="purchases.tender",
        source_entity="purchases",
        target_entity="purchase-tenders",
        label="Tender",
        icon="↩",
        filter_queryset=_purchase_order_tender,
    ),
    SmartActionDefinition(
        key="purchase-tenders.bids",
        source_entity="purchase-tenders",
        target_entity="tender-bids",
        label="Bids",
        icon="◇",
        filter_queryset=_tender_bids,
    ),
    SmartActionDefinition(
        key="tender-bids.tender",
        source_entity="tender-bids",
        target_entity="purchase-tenders",
        label="Tender",
        icon="↩",
        filter_queryset=_bid_tender,
    ),
    SmartActionDefinition(
        key="purchase-tenders.invitations",
        source_entity="purchase-tenders",
        target_entity="tender-invitations",
        label="Invitations",
        icon="✉",
        filter_queryset=_tender_invitations,
    ),
    SmartActionDefinition(
        key="tender-invitations.tender",
        source_entity="tender-invitations",
        target_entity="purchase-tenders",
        label="Tender",
        icon="↩",
        filter_queryset=_invitation_tender,
    ),
    SmartActionDefinition(
        key="purchase-tenders.documents",
        source_entity="purchase-tenders",
        target_entity="tender-documents",
        label="Documents",
        icon="▤",
        filter_queryset=_tender_documents,
    ),
    SmartActionDefinition(
        key="blanket-orders.lines",
        source_entity="blanket-orders",
        target_entity="blanket-order-lines",
        label="Agreement Lines",
        icon="☷",
        filter_queryset=_blanket_order_lines,
    ),
    SmartActionDefinition(
        key="blanket-order-lines.blanket-order",
        source_entity="blanket-order-lines",
        target_entity="blanket-orders",
        label="Blanket Order",
        icon="↩",
        filter_queryset=_blanket_order_line_parent,
    ),
    SmartActionDefinition(
        key="tender-documents.tender",
        source_entity="tender-documents",
        target_entity="purchase-tenders",
        label="Tender",
        icon="↩",
        filter_queryset=_document_tender,
    ),
    SmartActionDefinition(
        key="goods-receipts.purchase-order",
        source_entity="goods-receipts",
        target_entity="purchases",
        label="Purchase Order",
        icon="↩",
        filter_queryset=_receipt_purchase_order,
    ),
    SmartActionDefinition(
        key="goods-receipts.lines",
        source_entity="goods-receipts",
        target_entity="goods-receipt-lines",
        label="Receipt Lines",
        icon="☷",
        filter_queryset=_goods_receipt_lines,
    ),
    SmartActionDefinition(
        key="goods-receipt-lines.goods-receipt",
        source_entity="goods-receipt-lines",
        target_entity="goods-receipts",
        label="Goods Receipt",
        icon="↩",
        filter_queryset=_goods_receipt_line_parent,
    ),
    SmartActionDefinition(
        key="goods-receipts.stock-moves",
        source_entity="goods-receipts",
        target_entity="stock-moves",
        label="Stock Moves",
        icon="⇅",
        filter_queryset=_receipt_stock_moves,
    ),
    SmartActionDefinition(
        key="goods-receipts.returns",
        source_entity="goods-receipts",
        target_entity="purchase-return-requests",
        label="Vendor Returns",
        icon="↩",
        filter_queryset=_receipt_returns,
    ),
    SmartActionDefinition(
        key="goods-receipts.inventory-transfers",
        source_entity="goods-receipts",
        target_entity="purchase-inventory-transfers",
        label="Inventory Transfers",
        icon="⇄",
        filter_queryset=_receipt_inventory_transfers,
    ),
    SmartActionDefinition(
        key="stock-moves.goods-receipt",
        source_entity="stock-moves",
        target_entity="goods-receipts",
        label="Goods Receipt",
        icon="▣",
        filter_queryset=_stock_move_receipt,
    ),
    SmartActionDefinition(
        key="stock-moves.inventory-transfer",
        source_entity="stock-moves",
        target_entity="purchase-inventory-transfers",
        label="Inventory Transfer",
        icon="⇄",
        filter_queryset=_stock_move_inventory_transfer,
    ),
    SmartActionDefinition(
        key="purchase-inventory-transfers.goods-receipt",
        source_entity="purchase-inventory-transfers",
        target_entity="goods-receipts",
        label="Goods Receipt",
        icon="▣",
        filter_queryset=_inventory_transfer_receipt,
    ),
    SmartActionDefinition(
        key="purchase-inventory-transfers.lines",
        source_entity="purchase-inventory-transfers",
        target_entity="purchase-inventory-transfer-lines",
        label="Transfer Lines",
        icon="☷",
        filter_queryset=_purchase_inventory_transfer_lines,
    ),
    SmartActionDefinition(
        key="purchase-inventory-transfer-lines.transfer",
        source_entity="purchase-inventory-transfer-lines",
        target_entity="purchase-inventory-transfers",
        label="Inventory Transfer",
        icon="↩",
        filter_queryset=_purchase_inventory_transfer_line_parent,
    ),
    SmartActionDefinition(
        key="purchase-inventory-transfers.stock-moves",
        source_entity="purchase-inventory-transfers",
        target_entity="stock-moves",
        label="Stock Moves",
        icon="⇅",
        filter_queryset=_inventory_transfer_stock_moves,
    ),
    SmartActionDefinition(
        key="purchase-return-requests.receipt",
        source_entity="purchase-return-requests",
        target_entity="goods-receipts",
        label="Goods Receipt",
        icon="▣",
        filter_queryset=_return_receipt,
    ),
    SmartActionDefinition(
        key="purchase-accounting-documents.purchase-order",
        source_entity="purchase-accounting-documents",
        target_entity="purchases",
        label="Purchase Order",
        icon="↩",
        filter_queryset=_accounting_document_purchase,
    ),
    SmartActionDefinition(
        key="purchase-accounting-documents.lines",
        source_entity="purchase-accounting-documents",
        target_entity="purchase-accounting-lines",
        label="Accounting Lines",
        icon="☷",
        filter_queryset=_purchase_accounting_lines,
    ),
    SmartActionDefinition(
        key="purchase-accounting-lines.document",
        source_entity="purchase-accounting-lines",
        target_entity="purchase-accounting-documents",
        label="Accounting Document",
        icon="↩",
        filter_queryset=_purchase_accounting_line_parent,
    ),
    SmartActionDefinition(
        key="vendor-categories.vendor-profiles",
        source_entity="vendor-categories",
        target_entity="vendor-profiles",
        label="Vendors",
        icon="▣",
        filter_queryset=_vendor_category_profiles,
    ),
    SmartActionDefinition(
        key="vendor-profiles.category",
        source_entity="vendor-profiles",
        target_entity="vendor-categories",
        label="Vendor Category",
        icon="↩",
        filter_queryset=_vendor_profile_category,
    ),
    SmartActionDefinition(
        key="vendor-profiles.legal-documents",
        source_entity="vendor-profiles",
        target_entity="vendor-legal-documents",
        label="Legal Documents",
        icon="▤",
        filter_queryset=_vendor_legal_documents,
    ),
    SmartActionDefinition(
        key="vendor-profiles.legal-document-requirements",
        source_entity="vendor-profiles",
        target_entity="purchase-legal-documents",
        label="Legal Document Requirements",
        icon="✓",
        filter_queryset=_vendor_legal_document_requirements,
        allow_edit=False,
    ),
    SmartActionDefinition(
        key="vendor-legal-documents.vendor",
        source_entity="vendor-legal-documents",
        target_entity="vendor-profiles",
        label="Vendor",
        icon="↩",
        filter_queryset=_vendor_legal_document_profile,
    ),
    SmartActionDefinition(
        key="vendor-profiles.prequalifications",
        source_entity="vendor-profiles",
        target_entity="vendor-prequalifications",
        label="Prequalifications",
        icon="✓",
        filter_queryset=_vendor_prequalifications,
    ),
    SmartActionDefinition(
        key="vendor-prequalifications.vendor",
        source_entity="vendor-prequalifications",
        target_entity="vendor-profiles",
        label="Vendor",
        icon="↩",
        filter_queryset=_vendor_prequalification_profile,
    ),
    SmartActionDefinition(
        key="vendor-profiles.pricelists",
        source_entity="vendor-profiles",
        target_entity="vendor-pricelists",
        label="Pricelists",
        icon="▤",
        filter_queryset=_vendor_pricelists,
    ),
    SmartActionDefinition(
        key="vendor-pricelists.vendor",
        source_entity="vendor-pricelists",
        target_entity="vendor-profiles",
        label="Vendor",
        icon="↩",
        filter_queryset=_vendor_pricelist_profile,
    ),
    SmartActionDefinition(
        key="vendor-profiles.evaluations",
        source_entity="vendor-profiles",
        target_entity="vendor-performance-evaluations",
        label="Evaluations",
        icon="★",
        filter_queryset=_vendor_performance_evaluations,
    ),
    SmartActionDefinition(
        key="vendor-performance-evaluations.vendor",
        source_entity="vendor-performance-evaluations",
        target_entity="vendor-profiles",
        label="Vendor",
        icon="↩",
        filter_queryset=_vendor_performance_profile,
    ),
    SmartActionDefinition(
        key="vendor-profiles.communications",
        source_entity="vendor-profiles",
        target_entity="vendor-communications",
        label="Communications",
        icon="✉",
        filter_queryset=_vendor_communications,
    ),
    SmartActionDefinition(
        key="vendor-communications.vendor",
        source_entity="vendor-communications",
        target_entity="vendor-profiles",
        label="Vendor",
        icon="↩",
        filter_queryset=_vendor_communication_profile,
    ),
    SmartActionDefinition(
        key="vendor-profiles.transaction-history",
        source_entity="vendor-profiles",
        target_entity="vendor-transaction-history",
        label="Transactions",
        icon="↻",
        filter_queryset=_vendor_transaction_history,
    ),
    SmartActionDefinition(
        key="vendor-transaction-history.vendor",
        source_entity="vendor-transaction-history",
        target_entity="vendor-profiles",
        label="Vendor",
        icon="↩",
        filter_queryset=_vendor_transaction_profile,
    ),
    SmartActionDefinition(
        key="vendor-prequalifications.document-reviews",
        source_entity="vendor-prequalifications",
        target_entity="vendor-document-reviews",
        label="Document Reviews",
        icon="▤",
        filter_queryset=_prequalification_document_reviews,
    ),
    SmartActionDefinition(
        key="vendor-document-reviews.prequalification",
        source_entity="vendor-document-reviews",
        target_entity="vendor-prequalifications",
        label="Prequalification",
        icon="↩",
        filter_queryset=_document_review_prequalification,
    ),
    SmartActionDefinition(
        key="vendor-legal-documents.reviews",
        source_entity="vendor-legal-documents",
        target_entity="vendor-document-reviews",
        label="Reviews",
        icon="▤",
        filter_queryset=_legal_document_reviews,
    ),
    SmartActionDefinition(
        key="vendor-document-reviews.legal-document",
        source_entity="vendor-document-reviews",
        target_entity="vendor-legal-documents",
        label="Legal Document",
        icon="↩",
        filter_queryset=_document_review_legal_document,
    ),
    SmartActionDefinition(
        key="vendor-pricelists.amendments",
        source_entity="vendor-pricelists",
        target_entity="vendor-pricelist-amendments",
        label="Amendments",
        icon="↻",
        filter_queryset=_pricelist_amendments,
    ),
    SmartActionDefinition(
        key="vendor-pricelist-amendments.pricelist",
        source_entity="vendor-pricelist-amendments",
        target_entity="vendor-pricelists",
        label="Pricelist",
        icon="↩",
        filter_queryset=_pricelist_amendment_parent,
    ),
    SmartActionDefinition(
        key="purchase-teams.members",
        source_entity="purchase-teams",
        target_entity="purchase-team-members",
        label="Team Members",
        icon="▣",
        filter_queryset=_purchase_team_members,
    ),
    SmartActionDefinition(
        key="purchase-team-members.team",
        source_entity="purchase-team-members",
        target_entity="purchase-teams",
        label="Purchase Team",
        icon="↩",
        filter_queryset=_purchase_team_member_parent,
    ),
    SmartActionDefinition(
        key="purchase-teams.kpi-targets",
        source_entity="purchase-teams",
        target_entity="purchase-kpi-targets",
        label="KPI Targets",
        icon="◎",
        filter_queryset=_purchase_team_kpi_targets,
    ),
    SmartActionDefinition(
        key="purchase-kpi-targets.team",
        source_entity="purchase-kpi-targets",
        target_entity="purchase-teams",
        label="Purchase Team",
        icon="↩",
        filter_queryset=_purchase_kpi_target_team,
    ),
    SmartActionDefinition(
        key="purchase-kpi-templates.targets",
        source_entity="purchase-kpi-templates",
        target_entity="purchase-kpi-targets",
        label="KPI Targets",
        icon="◎",
        filter_queryset=_purchase_kpi_template_targets,
    ),
    SmartActionDefinition(
        key="purchase-kpi-targets.template",
        source_entity="purchase-kpi-targets",
        target_entity="purchase-kpi-templates",
        label="KPI Template",
        icon="↩",
        filter_queryset=_purchase_kpi_target_template,
    ),
    *_relation_pair(
        parent_entity="purchase-kpi-templates",
        child_entity="purchase-kpi-template-lines",
        child_field="template",
        parent_key="purchase-kpi-templates.lines",
        child_key="purchase-kpi-template-lines.template",
        children_label="KPI Parameters",
        parent_label="KPI Template",
    ),
    *_relation_pair(
        parent_entity="purchase-kpi-targets",
        child_entity="purchase-kpi-target-lines",
        child_field="target",
        parent_key="purchase-kpi-targets.lines",
        child_key="purchase-kpi-target-lines.target",
        children_label="KPI Results",
        parent_label="KPI Target",
    ),
    SmartActionDefinition(
        key="purchase-teams.product-categories",
        source_entity="purchase-teams",
        target_entity="purchase-product-categories",
        label="Product Categories",
        icon="▦",
        filter_queryset=_purchase_team_product_categories,
    ),
    SmartActionDefinition(
        key="purchase-product-categories.purchase-teams",
        source_entity="purchase-product-categories",
        target_entity="purchase-teams",
        label="Purchase Teams",
        icon="▣",
        filter_queryset=_purchase_product_category_teams,
    ),
    SmartActionDefinition(
        key="purchase-teams.order-types",
        source_entity="purchase-teams",
        target_entity="purchase-order-types",
        label="Order Types",
        icon="▤",
        filter_queryset=_purchase_team_order_types,
    ),
    SmartActionDefinition(
        key="purchase-order-types.purchase-teams",
        source_entity="purchase-order-types",
        target_entity="purchase-teams",
        label="Purchase Teams",
        icon="▣",
        filter_queryset=_purchase_order_type_teams,
    ),
    SmartActionDefinition(
        key="purchase-teams.vendor-categories",
        source_entity="purchase-teams",
        target_entity="vendor-categories",
        label="Vendor Categories",
        icon="▦",
        filter_queryset=_purchase_team_vendor_categories,
    ),
    SmartActionDefinition(
        key="vendor-categories.purchase-teams",
        source_entity="vendor-categories",
        target_entity="purchase-teams",
        label="Purchase Teams",
        icon="▣",
        filter_queryset=_vendor_category_purchase_teams,
    ),
    SmartActionDefinition(
        key="purchase-team-members.categories",
        source_entity="purchase-team-members",
        target_entity="purchase-product-categories",
        label="Assigned Categories",
        icon="▦",
        filter_queryset=_purchase_team_member_categories,
    ),
    SmartActionDefinition(
        key="purchase-product-categories.team-members",
        source_entity="purchase-product-categories",
        target_entity="purchase-team-members",
        label="Assigned Buyers",
        icon="▣",
        filter_queryset=_purchase_product_category_members,
    ),
    *_relation_pair(
        parent_entity="purchase-order-types",
        child_entity="purchase-order-type-sequences",
        child_field="order_type",
        parent_key="purchase-order-types.sequences",
        child_key="purchase-order-type-sequences.order-type",
        children_label="Company Sequences",
        parent_label="Order Type",
    ),
    *_relation_pair(
        parent_entity="purchase-order-templates",
        child_entity="purchase-order-template-lines",
        child_field="order_template",
        parent_key="purchase-order-templates.lines",
        child_key="purchase-order-template-lines.template",
        children_label="Template Products",
        parent_label="Order Template",
    ),
    *_relation_pair(
        parent_entity="products",
        child_entity="purchase-order-template-lines",
        child_field="product",
        parent_key="products.purchase-order-template-lines",
        child_key="purchase-order-template-lines.product",
        children_label="Purchase Template Lines",
        parent_label="Product",
    ),
    *_relation_pair(
        parent_entity="contacts",
        child_entity="purchase-order-templates",
        child_field="vendor",
        parent_key="contacts.purchase-order-templates",
        child_key="purchase-order-templates.vendor",
        children_label="Purchase Order Templates",
        parent_label="Vendor",
    ),
    *_relation_pair(
        parent_entity="purchase-milestone-templates",
        child_entity="purchase-milestone-template-lines",
        child_field="template",
        parent_key="purchase-milestone-templates.lines",
        child_key="purchase-milestone-template-lines.template",
        children_label="Milestone Lines",
        parent_label="Milestone Template",
    ),
    *_relation_pair(
        parent_entity="purchase-order-types",
        child_entity="purchase-requests",
        child_field="order_type",
        parent_key="purchase-order-types.purchase-requests",
        child_key="purchase-requests.order-type",
        children_label="Purchase Requests",
        parent_label="Order Type",
    ),
    *_relation_pair(
        parent_entity="purchase-order-types",
        child_entity="purchase-quotations",
        child_field="order_type",
        parent_key="purchase-order-types.rfqs",
        child_key="purchase-quotations.order-type",
        children_label="RFQs",
        parent_label="Order Type",
    ),
    *_relation_pair(
        parent_entity="purchase-order-types",
        child_entity="direct-purchases",
        child_field="order_type",
        parent_key="purchase-order-types.direct-purchases",
        child_key="direct-purchases.order-type",
        children_label="Direct Purchases",
        parent_label="Order Type",
    ),
    *_relation_pair(
        parent_entity="purchase-order-types",
        child_entity="purchase-tenders",
        child_field="order_type",
        parent_key="purchase-order-types.tenders",
        child_key="purchase-tenders.order-type",
        children_label="Tenders",
        parent_label="Order Type",
    ),
    *_relation_pair(
        parent_entity="purchase-order-types",
        child_entity="blanket-orders",
        child_field="order_type",
        parent_key="purchase-order-types.blanket-orders",
        child_key="blanket-orders.order-type",
        children_label="Blanket Orders",
        parent_label="Order Type",
    ),
    *_relation_pair(
        parent_entity="purchase-order-templates",
        child_entity="purchase-quotations",
        child_field="order_template",
        parent_key="purchase-order-templates.rfqs",
        child_key="purchase-quotations.order-template",
        children_label="RFQs",
        parent_label="Order Template",
    ),
    *_relation_pair(
        parent_entity="purchase-order-templates",
        child_entity="direct-purchases",
        child_field="order_template",
        parent_key="purchase-order-templates.direct-purchases",
        child_key="direct-purchases.order-template",
        children_label="Direct Purchases",
        parent_label="Order Template",
    ),
    *_relation_pair(
        parent_entity="purchase-order-templates",
        child_entity="purchase-tenders",
        child_field="order_template",
        parent_key="purchase-order-templates.tenders",
        child_key="purchase-tenders.order-template",
        children_label="Tenders",
        parent_label="Order Template",
    ),
    *_relation_pair(
        parent_entity="purchase-order-templates",
        child_entity="blanket-orders",
        child_field="order_template",
        parent_key="purchase-order-templates.blanket-orders",
        child_key="blanket-orders.order-template",
        children_label="Blanket Orders",
        parent_label="Order Template",
    ),
    SmartActionDefinition(
        key="purchase-order-types.templates",
        source_entity="purchase-order-types",
        target_entity="purchase-order-templates",
        label="Order Templates",
        icon="▤",
        filter_queryset=_purchase_order_type_templates,
    ),
    SmartActionDefinition(
        key="purchase-order-templates.order-type",
        source_entity="purchase-order-templates",
        target_entity="purchase-order-types",
        label="Order Type",
        icon="↩",
        filter_queryset=_purchase_order_template_type,
    ),
    SmartActionDefinition(
        key="purchase-order-templates.milestone-templates",
        source_entity="purchase-order-templates",
        target_entity="purchase-milestone-templates",
        label="Milestone Templates",
        icon="◇",
        filter_queryset=_purchase_order_template_milestones,
    ),
    SmartActionDefinition(
        key="purchase-milestone-templates.order-template",
        source_entity="purchase-milestone-templates",
        target_entity="purchase-order-templates",
        label="Order Template",
        icon="↩",
        filter_queryset=_purchase_milestone_template_parent,
    ),
    SmartActionDefinition(
        key="purchase-order-templates.contract-terms",
        source_entity="purchase-order-templates",
        target_entity="purchase-contract-terms",
        label="Contract Terms",
        icon="▤",
        filter_queryset=_purchase_order_template_contract_terms,
    ),
    SmartActionDefinition(
        key="purchase-contract-terms.order-template",
        source_entity="purchase-contract-terms",
        target_entity="purchase-order-templates",
        label="Order Template",
        icon="↩",
        filter_queryset=_purchase_contract_term_template,
    ),
    *_relation_pair(
        parent_entity="purchase-request-lines",
        child_entity="blanket-order-lines",
        child_field="request_line",
        parent_key="purchase-request-lines.blanket-order-lines",
        child_key="blanket-order-lines.request-line",
        children_label="Blanket Lines",
        parent_label="Request Line",
    ),
    *_relation_pair(
        parent_entity="purchase-request-lines",
        child_entity="direct-purchase-lines",
        child_field="request_line",
        parent_key="purchase-request-lines.direct-purchase-lines",
        child_key="direct-purchase-lines.request-line",
        children_label="Direct Purchase Lines",
        parent_label="Request Line",
    ),
    *_relation_pair(
        parent_entity="purchase-request-lines",
        child_entity="purchase-quotation-lines",
        child_field="request_line",
        parent_key="purchase-request-lines.quotation-lines",
        child_key="purchase-quotation-lines.request-line",
        children_label="RFQ Lines",
        parent_label="Request Line",
    ),
    *_relation_pair(
        parent_entity="blanket-order-lines",
        child_entity="purchase-quotation-lines",
        child_field="blanket_order_line",
        parent_key="blanket-order-lines.quotation-lines",
        child_key="purchase-quotation-lines.blanket-order-line",
        children_label="Released RFQ Lines",
        parent_label="Blanket Line",
    ),
    *_relation_pair(
        parent_entity="purchase-request-lines",
        child_entity="purchase-tender-lines",
        child_field="request_line",
        parent_key="purchase-request-lines.tender-lines",
        child_key="purchase-tender-lines.request-line",
        children_label="Tender Lines",
        parent_label="Request Line",
    ),
    *_relation_pair(
        parent_entity="purchase-request-lines",
        child_entity="purchase-stock-demands",
        child_field="request_line",
        parent_key="purchase-request-lines.stock-demands",
        child_key="purchase-stock-demands.request-line",
        children_label="Stock Demands",
        parent_label="Request Line",
    ),
    *_relation_pair(
        parent_entity="purchase-request-lines",
        child_entity="purchase-request-lines",
        child_field="origin_request_line",
        parent_key="purchase-request-lines.re-request-lines",
        child_key="purchase-request-lines.original-request-line",
        children_label="Re-Request Lines",
        parent_label="Original Request Line",
    ),
    *_relation_pair(
        parent_entity="product-budget-groups",
        child_entity="product-budgets",
        child_field="group",
        parent_key="product-budget-groups.budgets",
        child_key="product-budgets.group",
        children_label="Product Budgets",
        parent_label="Budget Group",
    ),
    *_relation_pair(
        parent_entity="product-budgets",
        child_entity="budget-product-transfers",
        child_field="source_budget",
        parent_key="product-budgets.outgoing-transfers",
        child_key="budget-product-transfers.source-budget",
        children_label="Outgoing Transfers",
        parent_label="Source Budget",
    ),
    *_relation_pair(
        parent_entity="product-budgets",
        child_entity="budget-product-transfers",
        child_field="target_budget",
        parent_key="product-budgets.incoming-transfers",
        child_key="budget-product-transfers.target-budget",
        children_label="Incoming Transfers",
        parent_label="Target Budget",
    ),
    *_relation_pair(
        parent_entity="product-budgets",
        child_entity="budget-product-carry-overs",
        child_field="source_budget",
        parent_key="product-budgets.carry-overs",
        child_key="budget-product-carry-overs.source-budget",
        children_label="Carry Overs",
        parent_label="Source Budget",
    ),
    *_relation_pair(
        parent_entity="product-budgets",
        child_entity="budget-product-change-requests",
        child_field="budget",
        parent_key="product-budgets.change-requests",
        child_key="budget-product-change-requests.budget",
        children_label="Change Requests",
        parent_label="Product Budget",
    ),
    *_relation_pair(
        parent_entity="product-budgets",
        child_entity="product-budget-movements",
        child_field="budget",
        parent_key="product-budgets.movements",
        child_key="product-budget-movements.budget",
        children_label="Budget Movements",
        parent_label="Product Budget",
    ),
    *_relation_pair(
        parent_entity="purchase-order-lines",
        child_entity="goods-receipt-lines",
        child_field="order_line",
        parent_key="purchase-order-lines.receipt-lines",
        child_key="goods-receipt-lines.order-line",
        children_label="Receipt Lines",
        parent_label="Purchase Order Line",
    ),
    *_relation_pair(
        parent_entity="stock-moves",
        child_entity="goods-receipt-lines",
        child_field="stock_move",
        parent_key="stock-moves.receipt-lines",
        child_key="goods-receipt-lines.stock-move",
        children_label="Receipt Lines",
        parent_label="Stock Move",
    ),
    *_relation_pair(
        parent_entity="stock-moves",
        child_entity="goods-receipt-lines",
        child_field="return_stock_move",
        parent_key="stock-moves.return-receipt-lines",
        child_key="goods-receipt-lines.return-stock-move",
        children_label="Return Receipt Lines",
        parent_label="Return Stock Move",
    ),
    *_relation_pair(
        parent_entity="invoices",
        child_entity="purchase-accounting-documents",
        child_field="invoice",
        parent_key="invoices.purchase-accounting-documents",
        child_key="purchase-accounting-documents.invoice",
        children_label="Purchase Accounting Documents",
        parent_label="Vendor Bill",
    ),
    *_relation_pair(
        parent_entity="purchases",
        child_entity="purchase-contract-terms",
        child_field="order",
        parent_key="purchases.contract-terms",
        child_key="purchase-contract-terms.purchase-order",
        children_label="Contract Terms",
        parent_label="Purchase Order",
    ),
    *_relation_pair(
        parent_entity="goods-receipt-lines",
        child_entity="purchase-inventory-transfer-lines",
        child_field="receipt_line",
        parent_key="goods-receipt-lines.inventory-transfer-lines",
        child_key="purchase-inventory-transfer-lines.receipt-line",
        children_label="Inventory Transfer Lines",
        parent_label="Receipt Line",
    ),
    *_relation_pair(
        parent_entity="stock-moves",
        child_entity="purchase-inventory-transfer-lines",
        child_field="source_stock_move",
        parent_key="stock-moves.source-transfer-lines",
        child_key="purchase-inventory-transfer-lines.source-stock-move",
        children_label="Source Transfer Lines",
        parent_label="Source Stock Move",
    ),
    *_relation_pair(
        parent_entity="stock-moves",
        child_entity="purchase-inventory-transfer-lines",
        child_field="destination_stock_move",
        parent_key="stock-moves.destination-transfer-lines",
        child_key="purchase-inventory-transfer-lines.destination-stock-move",
        children_label="Destination Transfer Lines",
        parent_label="Destination Stock Move",
    ),
    *_relation_pair(
        parent_entity="purchase-order-types",
        child_entity="purchase-order-controls",
        child_field="order_type",
        parent_key="purchase-order-types.order-controls",
        child_key="purchase-order-controls.order-type",
        children_label="Order Controls",
        parent_label="Order Type",
    ),
    *_relation_pair(
        parent_entity="purchase-order-templates",
        child_entity="purchase-order-controls",
        child_field="order_template",
        parent_key="purchase-order-templates.order-controls",
        child_key="purchase-order-controls.order-template",
        children_label="Order Controls",
        parent_label="Order Template",
    ),
    *_relation_pair(
        parent_entity="purchase-milestone-templates",
        child_entity="purchase-order-controls",
        child_field="milestone_template",
        parent_key="purchase-milestone-templates.order-controls",
        child_key="purchase-order-controls.milestone-template",
        children_label="Order Controls",
        parent_label="Milestone Template",
    ),
    *_relation_pair(
        parent_entity="purchase-quotations",
        child_entity="purchase-order-controls",
        child_field="quotation",
        parent_key="purchase-quotations.order-controls",
        child_key="purchase-order-controls.quotation",
        children_label="Order Controls",
        parent_label="RFQ",
    ),
    *_relation_pair(
        parent_entity="blanket-orders",
        child_entity="purchase-order-controls",
        child_field="blanket_order",
        parent_key="blanket-orders.order-controls",
        child_key="purchase-order-controls.blanket-order",
        children_label="Order Controls",
        parent_label="Blanket Order",
    ),
    *_relation_pair(
        parent_entity="purchase-tenders",
        child_entity="purchase-order-controls",
        child_field="tender",
        parent_key="purchase-tenders.order-controls",
        child_key="purchase-order-controls.tender",
        children_label="Order Controls",
        parent_label="Tender",
    ),
    *_relation_pair(
        parent_entity="vendor-pricelists",
        child_entity="purchase-order-controls",
        child_field="vendor_pricelist",
        parent_key="vendor-pricelists.order-controls",
        child_key="purchase-order-controls.vendor-pricelist",
        children_label="Order Controls",
        parent_label="Vendor Pricelist",
    ),
    *_relation_pair(
        parent_entity="purchase-teams",
        child_entity="purchase-order-controls",
        child_field="team",
        parent_key="purchase-teams.order-controls",
        child_key="purchase-order-controls.team",
        children_label="Order Controls",
        parent_label="Purchase Team",
    ),
    *_relation_pair(
        parent_entity="purchases",
        child_entity="purchase-peppol-submissions",
        child_field="order",
        parent_key="purchases.peppol-submissions",
        child_key="purchase-peppol-submissions.purchase-order",
        children_label="Peppol Submissions",
        parent_label="Purchase Order",
    ),
    *_relation_pair(
        parent_entity="purchase-peppol-submissions",
        child_entity="purchase-peppol-events",
        child_field="submission",
        parent_key="purchase-peppol-submissions.events",
        child_key="purchase-peppol-events.submission",
        children_label="Peppol Events",
        parent_label="Peppol Submission",
    ),
    *_relation_pair(
        parent_entity="purchase-product-categories",
        child_entity="purchase-product-categories",
        child_field="parent",
        parent_key="purchase-product-categories.children",
        child_key="purchase-product-categories.parent",
        children_label="Child Categories",
        parent_label="Parent Category",
    ),
    *_relation_pair(
        parent_entity="purchase-product-categories",
        child_entity="purchase-product-variants",
        child_field="category",
        parent_key="purchase-product-categories.variants",
        child_key="purchase-product-variants.category",
        children_label="Product Variants",
        parent_label="Purchase Category",
    ),
    *_relation_pair(
        parent_entity="award-strategies",
        child_entity="purchase-tenders",
        child_field="award_strategy",
        parent_key="award-strategies.tenders",
        child_key="purchase-tenders.award-strategy",
        children_label="Purchase Tenders",
        parent_label="Award Strategy",
    ),
    *_relation_pair(
        parent_entity="purchase-tenders",
        child_entity="tender-audit-logs",
        child_field="tender",
        parent_key="purchase-tenders.audit-logs",
        child_key="tender-audit-logs.tender",
        children_label="Audit Logs",
        parent_label="Tender",
    ),
    *_relation_pair(
        parent_entity="purchase-tenders",
        child_entity="vendor-prequalifications",
        child_field="tender",
        parent_key="purchase-tenders.prequalifications",
        child_key="vendor-prequalifications.tender",
        children_label="Prequalifications",
        parent_label="Tender",
    ),
    *_relation_pair(
        parent_entity="vendor-legal-documents",
        child_entity="vendor-legal-documents",
        child_field="previous_version",
        parent_key="vendor-legal-documents.renewal-versions",
        child_key="vendor-legal-documents.previous-version",
        children_label="Renewal Versions",
        parent_label="Previous Version",
        children_icon="↻",
    ),
    *_relation_pair(
        parent_entity="vendor-pricelist-imports",
        child_entity="vendor-pricelist-import-previews",
        child_field="import_log",
        parent_key="vendor-pricelist-imports.preview",
        child_key="vendor-pricelist-import-previews.import",
        children_label="Validation Preview",
        parent_label="Pricelist Import",
    ),
    *_relation_pair(
        parent_entity="vendor-pricelist-imports",
        child_entity="vendor-pricelists",
        child_field="source_import",
        parent_key="vendor-pricelist-imports.pricelists",
        child_key="vendor-pricelists.source-import",
        children_label="Imported Pricelists",
        parent_label="Source Import",
    ),
    *_relation_pair(
        parent_entity="tender-bids",
        child_entity="tender-documents",
        child_field="bid",
        parent_key="tender-bids.documents",
        child_key="tender-documents.bid",
        children_label="Tender Documents",
        parent_label="Tender Bid",
    ),
    *_relation_pair(
        parent_entity="vendor-performance-templates",
        child_entity="vendor-performance-evaluations",
        child_field="template",
        parent_key="vendor-performance-templates.evaluations",
        child_key="vendor-performance-evaluations.template",
        children_label="Evaluations",
        parent_label="Performance Template",
    ),
    *_relation_pair(
        parent_entity="vendor-performance-templates",
        child_entity="vendor-profiles",
        child_field="latest_performance_score_template",
        parent_key="vendor-performance-templates.latest-vendors",
        child_key="vendor-profiles.latest-performance-template",
        children_label="Latest Scored Vendors",
        parent_label="Latest Performance Template",
    ),
    *_relation_pair(
        parent_entity="vendor-performance-templates",
        child_entity="purchase-settings",
        child_field="vendor_performance_default_template",
        parent_key="vendor-performance-templates.purchase-settings",
        child_key="purchase-settings.vendor-performance-template",
        children_label="Purchase Settings",
        parent_label="Default Performance Template",
    ),
    *_relation_pair(
        parent_entity="vendor-performance-templates",
        child_entity="vendor-performance-template-lines",
        child_field="template",
        parent_key="vendor-performance-templates.lines",
        child_key="vendor-performance-template-lines.template",
        children_label="Evaluation Parameters",
        parent_label="Performance Template",
    ),
    *_relation_pair(
        parent_entity="vendor-performance-evaluations",
        child_entity="vendor-performance-evaluation-lines",
        child_field="evaluation",
        parent_key="vendor-performance-evaluations.lines",
        child_key="vendor-performance-evaluation-lines.evaluation",
        children_label="Evaluation Lines",
        parent_label="Vendor Evaluation",
    ),
    *_relation_pair(
        parent_entity="vendor-profiles",
        child_entity="vendor-performance-score-history",
        child_field="vendor",
        parent_key="vendor-profiles.performance-score-history",
        child_key="vendor-performance-score-history.vendor",
        children_label="Score History",
        parent_label="Vendor",
    ),
    *_relation_pair(
        parent_entity="vendor-performance-templates",
        child_entity="vendor-performance-score-history",
        child_field="template",
        parent_key="vendor-performance-templates.score-history",
        child_key="vendor-performance-score-history.template",
        children_label="Score History",
        parent_label="Performance Template",
    ),
    *_relation_pair(
        parent_entity="vendor-performance-evaluations",
        child_entity="vendor-performance-score-history",
        child_field="qualitative_evaluation",
        parent_key="vendor-performance-evaluations.score-history",
        child_key="vendor-performance-score-history.evaluation",
        children_label="Score History",
        parent_label="Qualitative Evaluation",
    ),
    *_relation_pair(
        parent_entity="purchases",
        child_entity="service-work-orders",
        child_field="source_order",
        parent_key="purchases.service-work-orders",
        child_key="service-work-orders.purchase-order",
        children_label="Service Work Orders",
        parent_label="Purchase Order",
    ),
    *_relation_pair(
        parent_entity="purchase-order-milestones",
        child_entity="service-work-orders",
        child_field="milestone",
        parent_key="purchase-order-milestones.service-work-orders",
        child_key="service-work-orders.milestone",
        children_label="Service Work Orders",
        parent_label="Milestone",
    ),
    *_relation_pair(
        parent_entity="invoices",
        child_entity="service-work-orders",
        child_field="billing_invoice",
        parent_key="invoices.service-work-orders",
        child_key="service-work-orders.vendor-bill",
        children_label="Service Work Orders",
        parent_label="Vendor Bill",
    ),
    *_relation_pair(
        parent_entity="purchase-order-lines",
        child_entity="service-work-order-lines",
        child_field="purchase_order_line",
        parent_key="purchase-order-lines.service-work-order-lines",
        child_key="service-work-order-lines.purchase-order-line",
        children_label="Service Work Order Lines",
        parent_label="Purchase Order Line",
    ),
    *_relation_pair(
        parent_entity="vendor-pricelists",
        child_entity="vendor-pricelist-items",
        child_field="pricelist",
        parent_key="vendor-pricelists.items",
        child_key="vendor-pricelist-items.pricelist",
        children_label="Pricelist Items",
        parent_label="Vendor Pricelist",
    ),
    *_relation_pair(
        parent_entity="products",
        child_entity="vendor-pricelist-items",
        child_field="product",
        parent_key="products.vendor-pricelist-items",
        child_key="vendor-pricelist-items.product",
        children_label="Vendor Pricelist Items",
        parent_label="Product",
    ),
    *_relation_pair(
        parent_entity="vendor-pricelist-items",
        child_entity="vendor-pricelist-tiers",
        child_field="item",
        parent_key="vendor-pricelist-items.tiers",
        child_key="vendor-pricelist-tiers.item",
        children_label="Pricelist Tiers",
        parent_label="Pricelist Item",
    ),
    *_relation_pair(
        parent_entity="vendor-pricelist-tiers",
        child_entity="vendor-pricelist-tiers",
        child_field="discount_from_tier",
        parent_key="vendor-pricelist-tiers.discount-dependents",
        child_key="vendor-pricelist-tiers.discount-source",
        children_label="Discount Dependents",
        parent_label="Discount Source Tier",
    ),
    *_relation_pair(
        parent_entity="products",
        child_entity="vendor-pricelist-tiers",
        child_field="free_product",
        parent_key="products.vendor-pricelist-free-tiers",
        child_key="vendor-pricelist-tiers.free-product",
        children_label="Pricelist Free-Product Tiers",
        parent_label="Free Product",
    ),
    *_relation_pair(
        parent_entity="vendor-pricelist-amendments",
        child_entity="vendor-pricelist-amendment-lines",
        child_field="amendment",
        parent_key="vendor-pricelist-amendments.lines",
        child_key="vendor-pricelist-amendment-lines.amendment",
        children_label="Amendment Lines",
        parent_label="Pricelist Amendment",
    ),
    *_relation_pair(
        parent_entity="vendor-pricelist-items",
        child_entity="vendor-pricelist-amendment-lines",
        child_field="item",
        parent_key="vendor-pricelist-items.amendment-lines",
        child_key="vendor-pricelist-amendment-lines.item",
        children_label="Amendment Lines",
        parent_label="Pricelist Item",
    ),
    *_relation_pair(
        parent_entity="vendor-pricelist-tiers",
        child_entity="vendor-pricelist-amendment-lines",
        child_field="tier",
        parent_key="vendor-pricelist-tiers.amendment-lines",
        child_key="vendor-pricelist-amendment-lines.tier",
        children_label="Amendment Lines",
        parent_label="Pricelist Tier",
    ),
    *_relation_pair(
        parent_entity="purchases",
        child_entity="vendor-performance-evaluations",
        child_field="order",
        parent_key="purchases.vendor-evaluations",
        child_key="vendor-performance-evaluations.purchase-order",
        children_label="Vendor Evaluations",
        parent_label="Purchase Order",
    ),
    *_relation_pair(
        parent_entity="purchases",
        child_entity="vendor-transaction-history",
        child_field="order",
        parent_key="purchases.vendor-transaction-history",
        child_key="vendor-transaction-history.purchase-order",
        children_label="Vendor Transactions",
        parent_label="Purchase Order",
    ),
    *_relation_pair(
        parent_entity="tender-bids",
        child_entity="tender-bid-lines",
        child_field="bid",
        parent_key="tender-bids.lines",
        child_key="tender-bid-lines.bid",
        children_label="Bid Lines",
        parent_label="Tender Bid",
    ),
    *_relation_pair(
        parent_entity="purchase-tender-lines",
        child_entity="tender-bid-lines",
        child_field="tender_line",
        parent_key="purchase-tender-lines.bid-lines",
        child_key="tender-bid-lines.tender-line",
        children_label="Vendor Bid Lines",
        parent_label="Tender Line",
    ),
    *_relation_pair(
        parent_entity="purchase-tenders",
        child_entity="tender-award-allocations",
        child_field="tender",
        parent_key="purchase-tenders.award-allocations",
        child_key="tender-award-allocations.tender",
        children_label="Award Allocations",
        parent_label="Tender",
    ),
    *_relation_pair(
        parent_entity="purchase-tender-lines",
        child_entity="tender-award-allocations",
        child_field="tender_line",
        parent_key="purchase-tender-lines.award-allocations",
        child_key="tender-award-allocations.tender-line",
        children_label="Award Allocations",
        parent_label="Tender Line",
    ),
    *_relation_pair(
        parent_entity="tender-bid-lines",
        child_entity="tender-award-allocations",
        child_field="bid_line",
        parent_key="tender-bid-lines.award-allocations",
        child_key="tender-award-allocations.bid-line",
        children_label="Award Allocations",
        parent_label="Bid Line",
    ),
    *_relation_pair(
        parent_entity="purchase-tenders",
        child_entity="tender-award-responses",
        child_field="tender",
        parent_key="purchase-tenders.award-responses",
        child_key="tender-award-responses.tender",
        children_label="Award Responses",
        parent_label="Tender",
    ),
    *_relation_pair(
        parent_entity="purchases",
        child_entity="purchase-order-billing-plans",
        child_field="order",
        parent_key="purchases.billing-plan",
        child_key="purchase-order-billing-plans.purchase-order",
        children_label="Billing Plan",
        parent_label="Purchase Order",
        children_icon="◈",
    ),
    *_relation_pair(
        parent_entity="invoices",
        child_entity="purchase-order-billing-plans",
        child_field="downpayment_invoice",
        parent_key="invoices.purchase-downpayment-plans",
        child_key="purchase-order-billing-plans.downpayment-bill",
        children_label="Purchase Down-Payment Plans",
        parent_label="Down-Payment Bill",
        children_icon="◈",
    ),
    *_relation_pair(
        parent_entity="invoices",
        child_entity="purchase-order-billing-plans",
        child_field="settlement_invoice",
        parent_key="invoices.purchase-settlement-plans",
        child_key="purchase-order-billing-plans.settlement-bill",
        children_label="Purchase Settlement Plans",
        parent_label="Settlement Bill",
        children_icon="◈",
    ),
    SmartActionDefinition(
        key="invoices.purchase-orders",
        source_entity="invoices",
        target_entity="purchases",
        label="Purchase Orders",
        icon="▣",
        filter_queryset=_invoice_purchase_orders,
    ),
)


SMART_ACTIONS = list(EXPLICIT_SMART_ACTIONS)
SMART_ACTIONS_BY_KEY = {definition.key: definition for definition in SMART_ACTIONS}


def _relation_key_part(value):
    return value.replace("_", "-").strip("-")


def _entity_label(config, *, plural):
    key = "title" if plural else "singular"
    label = config.get(key)
    if label:
        return str(label)
    model = config["model"]
    model_label = model._meta.verbose_name_plural if plural else model._meta.verbose_name
    return str(model_label).title()


def build_standard_smart_actions(entity_config, explicit_actions=()):
    """Derive bidirectional actions for registered concrete model relations."""

    model_entities = {}
    for entity, config in sorted(entity_config.items()):
        model = config.get("model")
        if model is None or config.get("auto_smart_actions", True) is False:
            continue
        existing = model_entities.get(model)
        if existing is not None:
            raise ImproperlyConfigured(
                f"Smart action discovery is ambiguous for {model._meta.label}: "
                f"entities '{existing}' and '{entity}' use the same model."
            )
        model_entities[model] = entity

    occupied_keys = {definition.key for definition in explicit_actions}
    explicit_pairs = {
        (definition.source_entity, definition.target_entity)
        for definition in explicit_actions
    }
    generated = []

    def add(definition):
        if definition.key in occupied_keys:
            raise ImproperlyConfigured(
                f"Generated smart action key '{definition.key}' collides with another definition."
            )
        occupied_keys.add(definition.key)
        generated.append(definition)

    for child_entity, child_config in sorted(entity_config.items()):
        child_model = child_config.get("model")
        if child_model not in model_entities:
            continue
        excluded_fields = set(child_config.get("smart_action_exclude_fields", ()))
        for field in child_model._meta.concrete_fields:
            if not isinstance(field, (ForeignKey, OneToOneField)) or field.name in excluded_fields:
                continue
            parent_entity = model_entities.get(field.remote_field.model)
            if parent_entity is None:
                continue
            parent_config = entity_config[parent_entity]

            if (child_entity, parent_entity) not in explicit_pairs:
                add(
                    SmartActionDefinition(
                        key=f"{child_entity}.{_relation_key_part(field.name)}",
                        source_entity=child_entity,
                        target_entity=parent_entity,
                        label=_entity_label(parent_config, plural=False),
                        icon="↩",
                        filter_queryset=_parent_on(field.name),
                        show_count=False,
                        hide_when_empty=False,
                        is_available=_has_parent_on(field.name),
                    )
                )

            related_name = field.remote_field.related_name
            if related_name == "+" or (parent_entity, child_entity) in explicit_pairs:
                continue
            reverse_name = related_name or field.related_query_name()
            add(
                SmartActionDefinition(
                    key=f"{parent_entity}.{_relation_key_part(reverse_name)}",
                    source_entity=parent_entity,
                    target_entity=child_entity,
                    label=_entity_label(child_config, plural=True),
                    icon="▤",
                    filter_queryset=_children_on(field.name),
                    show_count=False,
                    hide_when_empty=False,
                )
            )
    return tuple(generated)


def configure_standard_smart_actions(entity_config):
    """Idempotently combine explicit exceptions with model-derived defaults."""

    generated = build_standard_smart_actions(entity_config, EXPLICIT_SMART_ACTIONS)
    definitions = [*EXPLICIT_SMART_ACTIONS, *generated]
    by_key = {}
    for definition in definitions:
        if definition.key in by_key:
            raise ImproperlyConfigured(f"Duplicate smart action key '{definition.key}'.")
        by_key[definition.key] = definition
    SMART_ACTIONS[:] = definitions
    SMART_ACTIONS_BY_KEY.clear()
    SMART_ACTIONS_BY_KEY.update(by_key)


def smart_actions_for_source(entity):
    return tuple(
        definition
        for definition in SMART_ACTIONS
        if definition.source_entity == entity
    )


def get_smart_action(key):
    return SMART_ACTIONS_BY_KEY.get(key)
