from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .access import can_purchase_entity_action, scope_purchase_queryset
from .forms import (
    BlanketOrderRFQLineFormSet,
    PurchaseRequestApprovalLineFormSet,
    PurchaseRequestBlanketForm,
    PurchaseRequestConversionLineFormSet,
    PurchaseRequestDirectForm,
    PurchaseRequestRFQForm,
    PurchaseRequestRFQLineFormSet,
    PurchaseRequestReRequestLineFormSet,
    PurchaseRequestTenderForm,
)
from .models import BlanketOrder, BlanketOrderLine, PurchaseRequest, PurchaseRequestLine
from .services import (
    MAX_PURCHASE_DOCUMENT_LINES,
    approve_purchase_request_lines,
    create_blanket_order_rfq,
    create_purchase_request_blanket_order,
    create_purchase_request_direct_purchase,
    create_purchase_request_rfqs,
    create_purchase_request_re_request,
    create_purchase_request_tender,
)


def _bounded_purchase_request_lines(purchase_request):
    lines = list(
        PurchaseRequestLine.objects.filter(request_id=purchase_request.pk)
        .select_related("product")
        .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
    )
    if len(lines) > MAX_PURCHASE_DOCUMENT_LINES:
        raise ValidationError(
            f"Purchase Request exceeds the operational limit of {MAX_PURCHASE_DOCUMENT_LINES} lines."
        )
    return lines


@login_required
def purchase_request_approve(request, pk):
    if not can_purchase_entity_action(request.user, "purchase-requests", "change"):
        messages.error(request, "You do not have permission to approve Purchase Requests.")
        return redirect("entity_list", entity="purchase-requests")
    purchase_request = get_object_or_404(
        scope_purchase_queryset(
            request.user,
            "purchase-requests",
            PurchaseRequest.objects.select_related("company"),
        ),
        pk=pk,
    )
    if purchase_request.status not in {PurchaseRequest.Status.PENDING, PurchaseRequest.Status.PARTIALLY_APPROVED}:
        messages.error(request, "Only a pending Purchase Request can be approved.")
        return redirect("entity_edit", entity="purchase-requests", pk=pk)
    try:
        lines = _bounded_purchase_request_lines(purchase_request)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("entity_edit", entity="purchase-requests", pk=pk)
    initial = [
        {
            "line_id": line.pk,
            "product": str(line.product) if line.product_id else "—",
            "description": line.description,
            "requested_quantity": line.quantity,
            "action": "reject" if line.line_status == PurchaseRequestLine.Status.REJECTED else "approve",
            "approved_quantity": (
                Decimal("0")
                if line.line_status == PurchaseRequestLine.Status.REJECTED
                else line.approved_quantity or line.quantity
            ),
            "reason": line.approval_justification,
        }
        for line in lines
    ]
    line_formset = PurchaseRequestApprovalLineFormSet(
        request.POST or None,
        prefix="approval_lines",
        initial=initial,
    )
    if request.method == "POST" and line_formset.is_valid():
        decisions = [
            {
                "line_id": line_form.cleaned_data["line_id"],
                "action": line_form.cleaned_data["action"],
                "approved_quantity": line_form.cleaned_data["approved_quantity"],
                "reason": line_form.cleaned_data["reason"],
            }
            for line_form in line_formset
        ]
        try:
            approve_purchase_request_lines(purchase_request, decisions=decisions, actor=request.user)
        except ValidationError as exc:
            line_formset._non_form_errors = line_formset.error_class(exc.messages)
        else:
            messages.success(request, f"Purchase Request {purchase_request.number} approved with line decisions.")
            return redirect("entity_edit", entity="purchase-requests", pk=pk)
    return render(
        request,
        "purchase/purchase_request_approval_form.html",
        {
            "purchase_request": purchase_request,
            "line_formset": line_formset,
            "title": f"Approve {purchase_request.number}",
            "cancel_url": reverse("entity_edit", kwargs={"entity": "purchase-requests", "pk": pk}),
        },
    )


@login_required
def purchase_request_re_request(request, pk):
    if not can_purchase_entity_action(request.user, "purchase-requests", "change"):
        messages.error(request, "You do not have permission to Re-Request Purchase Request lines.")
        return redirect("entity_list", entity="purchase-requests")
    purchase_request = get_object_or_404(
        scope_purchase_queryset(request.user, "purchase-requests", PurchaseRequest.objects.all()),
        pk=pk,
    )
    try:
        lines = [
            line
            for line in _bounded_purchase_request_lines(purchase_request)
            if line.remaining_to_re_request > 0
        ]
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("entity_edit", entity="purchase-requests", pk=pk)
    if not lines:
        messages.error(request, "No Purchase Request lines are available for Re-Request.")
        return redirect("entity_edit", entity="purchase-requests", pk=pk)
    initial = [
        {
            "include": True,
            "line_id": line.pk,
            "product": str(line.product) if line.product_id else "—",
            "description": line.description,
            "original_quantity": line.quantity,
            "approved_quantity": line.approved_quantity,
            "already_re_requested": line.re_request_quantity,
            "remaining_quantity": line.remaining_to_re_request,
            "quantity": line.remaining_to_re_request,
        }
        for line in lines
    ]
    line_formset = PurchaseRequestReRequestLineFormSet(
        request.POST or None,
        prefix="re_request_lines",
        initial=initial,
    )
    if request.method == "POST" and line_formset.is_valid():
        allocations = [
            {
                "line_id": line_form.cleaned_data["line_id"],
                "quantity": line_form.cleaned_data["quantity"],
            }
            for line_form in line_formset
            if line_form.cleaned_data and line_form.cleaned_data.get("include")
        ]
        try:
            new_request = create_purchase_request_re_request(
                purchase_request,
                allocations=allocations,
                actor=request.user,
            )
        except ValidationError as exc:
            line_formset._non_form_errors = line_formset.error_class(exc.messages)
        else:
            messages.success(request, f"Re-Request {new_request.number} created from {purchase_request.number}.")
            return redirect("entity_edit", entity="purchase-requests", pk=new_request.pk)
    return render(
        request,
        "purchase/purchase_request_re_request_form.html",
        {
            "purchase_request": purchase_request,
            "line_formset": line_formset,
            "title": f"Re-Request from {purchase_request.number}",
            "cancel_url": reverse("entity_edit", kwargs={"entity": "purchase-requests", "pk": pk}),
        },
    )


@login_required
def blanket_order_convert_to_rfq(request, pk):
    if not can_purchase_entity_action(request.user, "blanket-orders", "change"):
        messages.error(request, "You do not have permission to release Blanket Orders.")
        return redirect("entity_list", entity="blanket-orders")
    blanket_order = get_object_or_404(
        scope_purchase_queryset(
            request.user,
            "blanket-orders",
            BlanketOrder.objects.select_related("company", "vendor", "warehouse"),
        ),
        pk=pk,
    )
    if blanket_order.status not in {BlanketOrder.Status.CONFIRMED, BlanketOrder.Status.RUNNING}:
        messages.error(request, "RFQ can only be created from a confirmed or running Blanket Order.")
        return redirect("entity_edit", entity="blanket-orders", pk=pk)
    lines = list(
        BlanketOrderLine.objects.filter(blanket_order_id=pk)
        .select_related("product")
        .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
    )
    if len(lines) > MAX_PURCHASE_DOCUMENT_LINES:
        messages.error(request, f"Blanket Order exceeds the operational limit of {MAX_PURCHASE_DOCUMENT_LINES} lines.")
        return redirect("entity_edit", entity="blanket-orders", pk=pk)
    initial = []
    for line in lines:
        remaining = line.agreed_quantity - line.released_quantity
        if remaining > 0:
            initial.append(
                {
                    "include": True,
                    "line_id": line.pk,
                    "product": str(line.product),
                    "description": line.description,
                    "agreed_quantity": line.agreed_quantity,
                    "released_quantity": line.released_quantity,
                    "remaining_quantity": remaining,
                    "quantity": remaining,
                }
            )
    if not initial:
        messages.error(request, "No remaining Blanket Order quantity is available to release.")
        return redirect("entity_edit", entity="blanket-orders", pk=pk)
    line_formset = BlanketOrderRFQLineFormSet(
        request.POST or None,
        prefix="release_lines",
        initial=initial,
    )
    if request.method == "POST" and line_formset.is_valid():
        releases = {
            line_form.cleaned_data["line_id"]: line_form.cleaned_data["quantity"]
            for line_form in line_formset
            if line_form.cleaned_data and line_form.cleaned_data.get("include")
        }
        try:
            quotation = create_blanket_order_rfq(blanket_order, releases=releases, actor=request.user)
        except ValidationError as exc:
            line_formset._non_form_errors = line_formset.error_class(exc.messages)
        else:
            messages.success(request, f"Draft RFQ {quotation.number} created from {blanket_order.number}.")
            return redirect("entity_edit", entity="purchase-quotations", pk=quotation.pk)
    return render(
        request,
        "purchase/blanket_order_rfq_form.html",
        {
            "blanket_order": blanket_order,
            "line_formset": line_formset,
            "title": f"Create RFQ from {blanket_order.number}",
            "cancel_url": reverse("entity_edit", kwargs={"entity": "blanket-orders", "pk": pk}),
        },
    )


def _line_initial(line):
    return {
        "include": True,
        "line_id": line.pk,
        "product": str(line.product) if line.product_id else "—",
        "description": line.description,
        "approved_quantity": line.approval_base_quantity,
        "already_converted": line.rfq_quantity,
        "remaining_quantity": line.remaining_to_source,
        "quantity": line.remaining_to_source,
    }


@login_required
def purchase_request_convert_to_rfq(request, pk):
    if not can_purchase_entity_action(request.user, "purchase-requests", "change"):
        messages.error(request, "You do not have permission to convert Purchase Requests.")
        return redirect("entity_list", entity="purchase-requests")
    purchase_request = get_object_or_404(
        scope_purchase_queryset(
            request.user,
            "purchase-requests",
            PurchaseRequest.objects.select_related("company", "destination_warehouse"),
        ),
        pk=pk,
    )
    eligible_statuses = {
        PurchaseRequest.Status.APPROVED,
        PurchaseRequest.Status.PARTIALLY_RFQ,
        PurchaseRequest.Status.RFQ,
        PurchaseRequest.Status.IN_PROGRESS,
    }
    if purchase_request.status not in eligible_statuses:
        messages.error(request, "Only an approved or in-progress Purchase Request can be converted to RFQ.")
        return redirect("entity_edit", entity="purchase-requests", pk=pk)

    lines = list(
        PurchaseRequestLine.objects.filter(request_id=pk)
        .select_related("product")
        .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
    )
    if len(lines) > MAX_PURCHASE_DOCUMENT_LINES:
        messages.error(
            request,
            f"Purchase Request exceeds the operational limit of {MAX_PURCHASE_DOCUMENT_LINES} lines.",
        )
        return redirect("entity_edit", entity="purchase-requests", pk=pk)
    eligible_lines = [line for line in lines if line.remaining_to_source > 0]
    if not eligible_lines:
        messages.error(request, "No remaining quantity available to convert.")
        return redirect("entity_edit", entity="purchase-requests", pk=pk)

    initial_header = {"response_deadline": max(purchase_request.required_date, purchase_request.request_date) }
    if initial_header["response_deadline"] < purchase_request.request_date:
        initial_header["response_deadline"] = purchase_request.request_date + timedelta(days=7)
    form = PurchaseRequestRFQForm(
        request.POST or None,
        company_id=purchase_request.company_id,
        initial=initial_header,
    )
    line_formset = PurchaseRequestRFQLineFormSet(
        request.POST or None,
        prefix="rfq_lines",
        initial=[_line_initial(line) for line in eligible_lines],
        form_kwargs={"company_id": purchase_request.company_id},
    )
    if request.method == "POST" and form.is_valid() and line_formset.is_valid():
        selected_vendor_ids = list(form.cleaned_data["vendors"].values_list("pk", flat=True))
        allocations = []
        for line_form in line_formset:
            cleaned = line_form.cleaned_data
            if not cleaned or not cleaned.get("include"):
                continue
            line_vendor_ids = list(cleaned["vendors"].values_list("pk", flat=True)) or selected_vendor_ids
            allocations.append(
                {
                    "line_id": cleaned["line_id"],
                    "quantity": cleaned["quantity"],
                    "vendor_ids": line_vendor_ids,
                }
            )
        try:
            quotations = create_purchase_request_rfqs(
                purchase_request,
                allocations=allocations,
                response_deadline=form.cleaned_data["response_deadline"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(request, f"{len(quotations)} draft RFQ(s) created from {purchase_request.number}.")
            if len(quotations) == 1:
                return redirect("entity_edit", entity="purchase-quotations", pk=quotations[0].pk)
            query = urlencode(
                {
                    "smart_action": "purchase-requests.quotations",
                    "source_id": purchase_request.pk,
                }
            )
            return redirect(f"{reverse('entity_list', kwargs={'entity': 'purchase-quotations'})}?{query}")

    return render(
        request,
        "purchase/purchase_request_rfq_form.html",
        {
            "purchase_request": purchase_request,
            "form": form,
            "line_formset": line_formset,
            "title": f"Convert {purchase_request.number} to RFQ",
            "cancel_url": reverse("entity_edit", kwargs={"entity": "purchase-requests", "pk": pk}),
        },
    )


def _conversion_lines(purchase_request):
    lines = list(
        PurchaseRequestLine.objects.filter(request_id=purchase_request.pk)
        .select_related("product")
        .order_by("pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
    )
    if len(lines) > MAX_PURCHASE_DOCUMENT_LINES:
        raise ValidationError(
            f"Purchase Request exceeds the operational limit of {MAX_PURCHASE_DOCUMENT_LINES} lines."
        )
    eligible = [line for line in lines if line.remaining_to_source > 0]
    if not eligible:
        raise ValidationError("No remaining quantity available to convert.")
    return eligible


def _conversion_line_initial(line):
    return {
        "include": True,
        "line_id": line.pk,
        "product": str(line.product) if line.product_id else "—",
        "description": line.description,
        "approved_quantity": line.approval_base_quantity,
        "remaining_quantity": line.remaining_to_source,
        "quantity": line.remaining_to_source,
    }


def _purchase_request_conversion(request, pk, conversion):
    if not can_purchase_entity_action(request.user, "purchase-requests", "change"):
        messages.error(request, "You do not have permission to convert Purchase Requests.")
        return redirect("entity_list", entity="purchase-requests")
    purchase_request = get_object_or_404(
        scope_purchase_queryset(
            request.user,
            "purchase-requests",
            PurchaseRequest.objects.select_related("company", "destination_warehouse"),
        ),
        pk=pk,
    )
    if purchase_request.status not in {
        PurchaseRequest.Status.APPROVED,
        PurchaseRequest.Status.PARTIALLY_RFQ,
        PurchaseRequest.Status.RFQ,
        PurchaseRequest.Status.IN_PROGRESS,
    }:
        messages.error(request, "Only an approved or in-progress Purchase Request can be converted.")
        return redirect("entity_edit", entity="purchase-requests", pk=pk)
    try:
        eligible_lines = _conversion_lines(purchase_request)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("entity_edit", entity="purchase-requests", pk=pk)

    definitions = {
        "blanket": {
            "label": "Blanket Order",
            "form": PurchaseRequestBlanketForm,
            "initial": {
                "date_end": purchase_request.expiry_date or purchase_request.required_date,
            },
        },
        "tender": {
            "label": "Tender",
            "form": PurchaseRequestTenderForm,
            "initial": {
                "title": f"Tender for {purchase_request.number}",
                "close_date": purchase_request.expiry_date or purchase_request.required_date,
            },
        },
        "direct": {
            "label": "Direct Purchase",
            "form": PurchaseRequestDirectForm,
            "initial": {"justification": purchase_request.justification},
        },
    }
    definition = definitions[conversion]
    header_form = definition["form"](
        request.POST or None,
        company_id=purchase_request.company_id,
        initial=definition["initial"],
    )
    line_formset = PurchaseRequestConversionLineFormSet(
        request.POST or None,
        prefix="conversion_lines",
        initial=[_conversion_line_initial(line) for line in eligible_lines],
    )
    if request.method == "POST" and header_form.is_valid() and line_formset.is_valid():
        allocations = [
            {
                "line_id": line_form.cleaned_data["line_id"],
                "quantity": line_form.cleaned_data["quantity"],
            }
            for line_form in line_formset
            if line_form.cleaned_data and line_form.cleaned_data.get("include")
        ]
        try:
            if conversion == "blanket":
                record = create_purchase_request_blanket_order(
                    purchase_request,
                    vendor_id=header_form.cleaned_data["vendor"].pk,
                    allocations=allocations,
                    date_end=header_form.cleaned_data["date_end"],
                    actor=request.user,
                )
                entity = "blanket-orders"
            elif conversion == "tender":
                record = create_purchase_request_tender(
                    purchase_request,
                    allocations=allocations,
                    title=header_form.cleaned_data["title"],
                    tender_type=header_form.cleaned_data["tender_type"],
                    method=header_form.cleaned_data["method"],
                    award_strategy_id=header_form.cleaned_data["award_strategy"].pk,
                    close_date=header_form.cleaned_data["close_date"],
                    actor=request.user,
                )
                entity = "purchase-tenders"
            else:
                record = create_purchase_request_direct_purchase(
                    purchase_request,
                    vendor_id=header_form.cleaned_data["vendor"].pk,
                    allocations=allocations,
                    justification=header_form.cleaned_data["justification"],
                    actor=request.user,
                )
                entity = "direct-purchases"
        except ValidationError as exc:
            header_form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(request, f"{definition['label']} {record} created from {purchase_request.number}.")
            return redirect("entity_edit", entity=entity, pk=record.pk)

    return render(
        request,
        "purchase/purchase_request_conversion_form.html",
        {
            "purchase_request": purchase_request,
            "form": header_form,
            "line_formset": line_formset,
            "conversion_label": definition["label"],
            "title": f"Convert {purchase_request.number} to {definition['label']}",
            "cancel_url": reverse("entity_edit", kwargs={"entity": "purchase-requests", "pk": pk}),
        },
    )


@login_required
def purchase_request_convert_to_blanket(request, pk):
    return _purchase_request_conversion(request, pk, "blanket")


@login_required
def purchase_request_convert_to_tender(request, pk):
    return _purchase_request_conversion(request, pk, "tender")


@login_required
def purchase_request_convert_to_direct(request, pk):
    return _purchase_request_conversion(request, pk, "direct")
