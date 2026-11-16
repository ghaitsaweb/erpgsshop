from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from core.models import PurchaseOrder
from modules.approval_workflow.services import can_user_act, pending_approval_for

from .access import (
    can_purchase_entity_action,
    can_purchase_workflow_action,
    scope_purchase_queryset,
    scope_purchase_form,
)
from .forms import (
    BlanketOrderRFQLineFormSet,
    PurchaseAttachmentManagementForm,
    PurchaseRequestApprovalLineFormSet,
    PurchaseRequestBlanketForm,
    PurchaseRequestConversionLineFormSet,
    PurchaseRequestDirectForm,
    PurchaseRequestRFQForm,
    PurchaseRequestRFQLineFormSet,
    PurchaseRequestReRequestLineFormSet,
    PurchaseRequestTenderForm,
    PurchaseRFQMailForm,
    PurchaseTenderRFQVendorForm,
    PurchaseTenderLineDocumentForm,
)
from .models import (
    BlanketOrder,
    BlanketOrderLine,
    PurchaseDocumentAttachment,
    PurchaseQuotation,
    PurchaseOrderTemplate,
    PurchaseOrderTemplateLine,
    PurchaseRequest,
    PurchaseRequestLine,
    PurchaseTender,
)
from .rfq_comparison import compare_purchase_quotations, confirm_quotation_from_comparison
from .purchase_analysis import (
    build_purchase_order_analysis,
    build_purchase_request_analysis,
)
from .services import (
    MAX_PURCHASE_DOCUMENT_LINES,
    approve_purchase_request_lines,
    create_blanket_order_rfq,
    create_purchase_request_blanket_order,
    create_purchase_request_direct_purchase,
    create_purchase_request_rfqs,
    create_purchase_request_re_request,
    create_purchase_request_tender,
    create_purchase_tender_rfqs,
    duplicate_purchase_order_template,
    purchase_tender_rfq_candidates,
    save_tender_award_allocations,
    send_purchase_quotation_email,
    transition_purchase_tender,
)
from .tender_award_allocation import build_tender_award_allocation_workspace
from .tender_bid_comparison import build_tender_bid_comparison


def _comparison_selection(raw_ids):
    selected = []
    seen = set()
    for raw_id in raw_ids:
        if not str(raw_id).isdigit():
            raise ValidationError("The RFQ selection is invalid.")
        value = int(raw_id)
        if value < 1 or value in seen:
            raise ValidationError("The RFQ selection is invalid.")
        selected.append(value)
        seen.add(value)
    return selected


@login_required
@require_GET
def purchase_document_attachment_download(request, entity, pk, attachment_pk):
    parent_config = {
        "purchase-requests": (PurchaseRequest, "purchase_request_id"),
        "purchases": (PurchaseOrder, "purchase_order_id"),
    }.get(entity)
    if parent_config is None or not can_purchase_entity_action(request.user, entity, "view"):
        raise Http404
    parent_model, attachment_parent_field = parent_config
    parent = get_object_or_404(
        scope_purchase_queryset(request.user, entity, parent_model.objects.all()),
        pk=pk,
    )
    attachment = get_object_or_404(
        PurchaseDocumentAttachment.objects.all(),
        pk=attachment_pk,
        **{attachment_parent_field: parent.pk},
    )
    try:
        attachment.file.open("rb")
    except (FileNotFoundError, OSError) as error:
        raise Http404 from error
    content_type = {
        "pdf": "application/pdf",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
    }.get(attachment.original_name.rsplit(".", 1)[-1].casefold(), "application/octet-stream")
    response = FileResponse(
        attachment.file,
        as_attachment=True,
        filename=attachment.original_name,
        content_type=content_type,
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_POST
def purchase_document_attachment_manage(request, entity, pk):
    parent_config = {
        "purchase-requests": (
            PurchaseRequest,
            "purchase_request",
            {PurchaseRequest.Status.DRAFT, PurchaseRequest.Status.REJECTED},
        ),
        "purchases": (
            PurchaseOrder,
            "purchase_order",
            {
                PurchaseOrder.Status.DRAFT,
                PurchaseOrder.Status.SENT,
                PurchaseOrder.Status.WAITING_APPROVAL,
            },
        ),
    }.get(entity)
    if parent_config is None or not can_purchase_entity_action(request.user, entity, "change"):
        raise Http404
    parent_model, parent_field, editable_statuses = parent_config
    parent = get_object_or_404(
        scope_purchase_queryset(request.user, entity, parent_model.objects.all()),
        pk=pk,
    )
    if parent.status not in editable_statuses:
        messages.error(request, "Attachments are locked by this document's workflow status.")
        return redirect(f"{reverse('entity_edit', args=[entity, parent.pk])}#tab-attachment-file")
    form = PurchaseAttachmentManagementForm(
        request.POST,
        request.FILES,
        parent=parent,
        parent_field=parent_field,
        actor=request.user,
    )
    if form.is_valid():
        with transaction.atomic():
            form.save()
        messages.success(request, "Purchase attachment files updated.")
    else:
        details = "; ".join(
            f"{field}: {', '.join(str(error) for error in errors)}"
            for field, errors in form.errors.items()
        )
        messages.error(request, details or "Purchase attachment files could not be updated.")
    return redirect(f"{reverse('entity_edit', args=[entity, parent.pk])}#tab-attachment-file")


@login_required
@require_GET
def purchase_tender_onchange(request):
    """Return bounded, company-scoped values for Tender edit-time onchange."""
    if not can_purchase_entity_action(request.user, "purchase-tenders", "change"):
        return JsonResponse({"detail": "You do not have permission to edit Tenders."}, status=403)
    template_id = request.GET.get("order_template_id", "").strip()
    product_id = request.GET.get("product_id", "").strip()
    company_id = request.GET.get("company_id", "").strip()
    if bool(template_id) == bool(product_id):
        return JsonResponse({"detail": "Select one Tender onchange source."}, status=400)

    if template_id:
        if not template_id.isdigit():
            return JsonResponse({"detail": "Order Template is invalid."}, status=400)
        template = get_object_or_404(
            scope_purchase_queryset(
                request.user,
                "purchase-order-templates",
                PurchaseOrderTemplate.objects.select_related("order_type").filter(active=True),
            ),
            pk=int(template_id),
        )
        if company_id.isdigit() and template.company_id != int(company_id):
            return JsonResponse(
                {"detail": "Order Template must belong to the Tender company."},
                status=400,
            )
        lines = list(
            PurchaseOrderTemplateLine.objects.filter(order_template_id=template.pk)
            .select_related("product")
            .order_by("sequence", "pk")[: MAX_PURCHASE_DOCUMENT_LINES + 1]
        )
        if len(lines) > MAX_PURCHASE_DOCUMENT_LINES:
            return JsonResponse(
                {
                    "detail": (
                        f"Purchase Order Template exceeds the operational limit of "
                        f"{MAX_PURCHASE_DOCUMENT_LINES} lines."
                    )
                },
                status=400,
            )
        return JsonResponse(
            {
                "order_type_id": template.order_type_id,
                "lines": [
                    {
                        "product_id": line.product_id,
                        "description": line.description or line.product.name,
                        "quantity": str(line.quantity),
                        "target_unit_price": str(line.price),
                        "unit": line.uom or line.product.unit,
                    }
                    for line in lines
                ],
            }
        )

    if not product_id.isdigit():
        return JsonResponse({"detail": "Product is invalid."}, status=400)
    product_form = PurchaseTenderLineDocumentForm()
    scope_purchase_form(product_form, request.user, "purchase-tenders")
    product_queryset = product_form.fields["product"].queryset.filter(active=True)
    product = get_object_or_404(product_queryset, pk=int(product_id))
    if company_id.isdigit() and product.company_id != int(company_id):
        return JsonResponse(
            {"detail": "Product must belong to the Tender company."},
            status=400,
        )
    return JsonResponse(
        {
            "product": {
                "id": product.pk,
                "description": product.name,
                "unit": product.unit,
                "target_unit_price": str(product.purchase_price),
            }
        }
    )


def _purchase_analysis_response(request, *, entity, builder):
    if not can_purchase_entity_action(request.user, entity, "view"):
        messages.error(request, "You do not have permission to view this Purchase analysis.")
        return redirect("dashboard")
    try:
        analysis = builder(request.user, request.GET)
    except ValidationError as exc:
        analysis = {"rows": [], "contract": {"title": "Purchase Analysis", "measures": []}}
        messages.error(request, " ".join(exc.messages))
    measure_labels = {
        "quantity": "Quantity",
        "approved_qty": "Approved Qty",
        "rfq_qty": "RFQ Qty",
        "po_qty": "PO Qty",
        "received_qty": "Received Qty",
        "total_discount_line_amount": "Discount",
        "untaxed_amount": "Untaxed",
        "taxes_amount": "Tax",
        "total_amount": "Total",
        "subtotal_lines": "Gross Subtotal",
    }
    measures = analysis["contract"]["measures"]
    table_rows = [
        {
            "product": row.get("product__name") or "Unspecified Product",
            "sku": row.get("product__sku") or "",
            "month": row.get("expected_month"),
            "values": [row.get(measure) or 0 for measure in measures],
        }
        for row in analysis["rows"]
    ]
    return render(
        request,
        "purchase/purchase_analysis.html",
        {
            "title": analysis["contract"]["title"],
            "analysis": analysis,
            "measure_headers": [measure_labels.get(measure, measure) for measure in measures],
            "table_rows": table_rows,
            "breadcrumbs": [
                {"label": "Home", "url": reverse("dashboard")},
                {"label": "Purchase Reports", "url": ""},
                {"label": analysis["contract"]["title"], "url": ""},
            ],
        },
    )


@login_required
def purchase_order_analysis(request):
    return _purchase_analysis_response(
        request,
        entity="purchases",
        builder=build_purchase_order_analysis,
    )


@login_required
def purchase_request_analysis(request):
    return _purchase_analysis_response(
        request,
        entity="purchase-requests",
        builder=build_purchase_request_analysis,
    )


@login_required
@require_GET
def purchase_tender_bid_comparison(request, pk):
    if not can_purchase_entity_action(request.user, "purchase-tenders", "view"):
        messages.error(request, "You do not have permission to view Tender bid comparisons.")
        return redirect("entity_list", entity="purchase-tenders")
    tender = get_object_or_404(
        scope_purchase_queryset(
            request.user,
            "purchase-tenders",
            PurchaseTender.objects.select_related("company", "award_strategy"),
        ),
        pk=pk,
    )
    try:
        comparison = build_tender_bid_comparison(tender)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("entity_edit", entity="purchase-tenders", pk=tender.pk)

    award_action = comparison["award_action"]
    award_permission_action = (
        "award" if award_action == "manage-award-allocation" else award_action
    )
    comparison["can_award"] = bool(
        award_action
        and can_purchase_entity_action(request.user, "purchase-tenders", "change")
        and can_purchase_workflow_action(
            request.user,
            "purchase-tenders",
            award_permission_action,
        )
    )
    return render(
        request,
        "purchase/tender_bid_comparison.html",
        {
            "title": f"Bid Comparison · {tender.number}",
            "tender": tender,
            "comparison": comparison,
            "breadcrumbs": [
                {"label": "Home", "url": reverse("dashboard")},
                {
                    "label": "Purchase Tenders",
                    "url": reverse("entity_list", args=["purchase-tenders"]),
                },
                {
                    "label": tender.number,
                    "url": reverse("entity_edit", args=["purchase-tenders", tender.pk]),
                },
                {"label": "Bid Comparison", "url": ""},
            ],
        },
    )


def _parse_tender_allocation_quantities(post_data):
    quantities = {}
    prefix = "allocation_"
    for key in post_data:
        if not key.startswith(prefix):
            continue
        raw_id = key[len(prefix):]
        values = post_data.getlist(key)
        if not raw_id.isdigit() or int(raw_id) < 1 or len(values) != 1:
            raise ValidationError("The Tender allocation matrix contains an invalid Bid line.")
        bid_line_id = int(raw_id)
        if bid_line_id in quantities:
            raise ValidationError("The Tender allocation matrix contains a duplicate Bid line.")
        quantities[bid_line_id] = values[0]
    return quantities


def _can_manage_tender_allocations(user):
    return (
        can_purchase_entity_action(user, "purchase-tenders", "change")
        and can_purchase_workflow_action(user, "purchase-tenders", "award")
    )


@login_required
@require_POST
def purchase_tender_start_award(request, pk):
    if not _can_manage_tender_allocations(request.user):
        messages.error(request, "You do not have permission to award this Tender.")
        return redirect("entity_list", entity="purchase-tenders")
    tender = get_object_or_404(
        scope_purchase_queryset(
            request.user,
            "purchase-tenders",
            PurchaseTender.objects.select_related("company", "award_strategy"),
        ),
        pk=pk,
    )
    previous_status = tender.status
    try:
        with transaction.atomic():
            tender = transition_purchase_tender(tender, "award", request.user)
            from .workflow_extensions import purchase_tender_action_audit

            purchase_tender_action_audit(tender, "award", request.user, previous_status)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("purchase_tender_bid_comparison", pk=tender.pk)
    messages.success(request, "Tender moved to Awarded. Complete the Bid Allocation Matrix.")
    return redirect("purchase_tender_award_allocation", pk=tender.pk)


@login_required
def purchase_tender_award_allocation(request, pk):
    if not _can_manage_tender_allocations(request.user):
        messages.error(request, "You do not have permission to manage Tender allocations.")
        return redirect("entity_list", entity="purchase-tenders")
    tender = get_object_or_404(
        scope_purchase_queryset(
            request.user,
            "purchase-tenders",
            PurchaseTender.objects.select_related("company", "award_strategy"),
        ),
        pk=pk,
    )
    if request.method == "POST":
        try:
            quantities = _parse_tender_allocation_quantities(request.POST)
            save_tender_award_allocations(tender, quantities, request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "Tender Bid Allocation Matrix saved and audited.")
            return redirect("purchase_tender_award_allocation", pk=tender.pk)
    try:
        allocation_matrix = build_tender_award_allocation_workspace(tender)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("entity_edit", entity="purchase-tenders", pk=tender.pk)
    return render(
        request,
        "purchase/tender_award_allocation.html",
        {
            "title": f"Bid Allocation Matrix · {tender.number}",
            "tender": tender,
            "allocation_matrix": allocation_matrix,
            "breadcrumbs": [
                {"label": "Home", "url": reverse("dashboard")},
                {
                    "label": "Purchase Tenders",
                    "url": reverse("entity_list", args=["purchase-tenders"]),
                },
                {
                    "label": tender.number,
                    "url": reverse("entity_edit", args=["purchase-tenders", tender.pk]),
                },
                {"label": "Bid Allocation Matrix", "url": ""},
            ],
        },
    )


@login_required
def purchase_tender_submit_quotation(request, pk):
    if not can_purchase_entity_action(request.user, "purchase-tenders", "change"):
        messages.error(request, "You do not have permission to create Tender RFQs.")
        return redirect("entity_list", entity="purchase-tenders")
    tender = get_object_or_404(
        scope_purchase_queryset(
            request.user,
            "purchase-tenders",
            PurchaseTender.objects.select_related("company"),
        ),
        pk=pk,
    )
    try:
        vendor_scope, candidates = purchase_tender_rfq_candidates(tender)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("entity_edit", entity="purchase-tenders", pk=tender.pk)
    form = PurchaseTenderRFQVendorForm(
        request.POST or None,
        candidates=candidates,
        vendor_scope=vendor_scope,
    )
    if request.method == "POST" and form.is_valid():
        try:
            quotations = create_purchase_tender_rfqs(
                tender,
                vendor_ids=[vendor.pk for vendor in form.cleaned_data["vendors"]],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
        else:
            messages.success(
                request,
                f"Created {len(quotations)} linked Tender RFQ(s): "
                + ", ".join(quotation.number for quotation in quotations),
            )
            return redirect("entity_list", entity="purchase-quotations")
    return render(
        request,
        "purchase/tender_submit_quotation.html",
        {
            "title": f"Submit Quotation · {tender.number}",
            "tender": tender,
            "vendor_scope": vendor_scope,
            "vendor_scope_display": {
                "open_registered": "Open to Registered Vendors",
                "invited": "Invited Vendors",
                "open_public": "Open Public / Prequalification",
            }[vendor_scope],
            "candidate_count": len(candidates),
            "form": form,
            "breadcrumbs": [
                {"label": "Home", "url": reverse("dashboard")},
                {
                    "label": "Purchase Tenders",
                    "url": reverse("entity_list", args=["purchase-tenders"]),
                },
                {
                    "label": tender.number,
                    "url": reverse("entity_edit", args=["purchase-tenders", tender.pk]),
                },
                {"label": "Submit Quotation", "url": ""},
            ],
        },
    )


@login_required
def purchase_quotation_email(request, pk):
    if not can_purchase_entity_action(request.user, "purchase-quotations", "change"):
        messages.error(request, "You do not have permission to email RFQs.")
        return redirect("entity_list", entity="purchase-quotations")
    quotation = get_object_or_404(
        scope_purchase_queryset(
            request.user,
            "purchase-quotations",
            PurchaseQuotation.objects.select_related("company", "vendor", "warehouse"),
        ),
        pk=pk,
    )
    form = PurchaseRFQMailForm(
        request.POST or None,
        quotation=quotation,
        template_code=request.GET.get("template"),
    )
    if request.method == "POST" and form.is_valid():
        try:
            communication = send_purchase_quotation_email(
                quotation,
                template=form.cleaned_data["template"],
                email_to=form.cleaned_data["email_to"],
                email_cc=form.cleaned_data["email_cc"],
                subject=form.cleaned_data["subject"],
                body_html=form.cleaned_data["body_html"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
        else:
            messages.success(
                request,
                f"RFQ email sent to {communication.email_to} and recorded in communication history.",
            )
            return redirect("entity_edit", entity="purchase-quotations", pk=pk)
    return render(
        request,
        "purchase/rfq_email_form.html",
        {
            "quotation": quotation,
            "form": form,
            "title": f"Send RFQ {quotation.number}",
            "breadcrumbs": [
                {"label": "Home", "url": reverse("dashboard")},
                {
                    "label": "Purchase Quotations",
                    "url": reverse("entity_list", kwargs={"entity": "purchase-quotations"}),
                },
                {"label": quotation.number, "url": reverse("entity_edit", kwargs={"entity": "purchase-quotations", "pk": quotation.pk})},
                {"label": "Send Email", "url": ""},
            ],
        },
    )


@require_POST
@login_required
def purchase_order_template_duplicate(request):
    if not can_purchase_entity_action(request.user, "purchase-order-templates", "add"):
        messages.error(request, "You do not have permission to duplicate Purchase Order Templates.")
        return redirect("entity_list", entity="purchase-order-templates")
    try:
        selected_ids = [
            int(value)
            for value in request.POST.getlist("selected_ids")
            if str(value).isdigit() and int(value) > 0
        ]
        if len(selected_ids) != 1 or len(set(selected_ids)) != 1:
            raise ValidationError("Select exactly one Purchase Order Template to duplicate.")
        scoped = scope_purchase_queryset(
            request.user,
            "purchase-order-templates",
            PurchaseOrderTemplate.objects.filter(pk=selected_ids[0]),
        ).first()
        if scoped is None:
            raise ValidationError("The Purchase Order Template is outside your access scope.")
        duplicate = duplicate_purchase_order_template(scoped, request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("entity_list", entity="purchase-order-templates")
    messages.success(request, f"Purchase Order Template duplicated as {duplicate.name}.")
    return redirect("entity_edit", entity="purchase-order-templates", pk=duplicate.pk)


@require_POST
@login_required
def purchase_quotation_compare(request):
    if not can_purchase_entity_action(request.user, "purchase-quotations", "change"):
        messages.error(request, "You do not have permission to compare RFQs.")
        return redirect("entity_list", entity="purchase-quotations")
    try:
        selected_ids = _comparison_selection(request.POST.getlist("selected_ids"))
        scoped_ids = set(
            scope_purchase_queryset(
                request.user,
                "purchase-quotations",
                PurchaseQuotation.objects.filter(pk__in=selected_ids),
            ).values_list("pk", flat=True)
        )
        if scoped_ids != set(selected_ids):
            raise ValidationError("The RFQ selection changed or is outside your access scope.")
        comparison = compare_purchase_quotations(selected_ids)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("entity_list", entity="purchase-quotations")
    return render(
        request,
        "purchase/rfq_comparison.html",
        {
            "comparison": comparison,
            "title": comparison["title"],
            "breadcrumbs": [
                {"label": "Home", "url": reverse("dashboard")},
                {
                    "label": "Purchase Quotations",
                    "url": reverse("entity_list", kwargs={"entity": "purchase-quotations"}),
                },
                {"label": "RFQ Comparison"},
            ],
        },
    )


@require_POST
@login_required
def purchase_quotation_compare_confirm(request, pk):
    if not can_purchase_entity_action(request.user, "purchase-quotations", "change"):
        messages.error(request, "You do not have permission to confirm an RFQ comparison.")
        return redirect("entity_list", entity="purchase-quotations")
    quotation = get_object_or_404(
        scope_purchase_queryset(
            request.user,
            "purchase-quotations",
            PurchaseQuotation.objects.all(),
        ),
        pk=pk,
    )
    try:
        order = confirm_quotation_from_comparison(quotation, request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("entity_edit", entity="purchase-quotations", pk=pk)
    messages.success(
        request,
        f"RFQ {quotation.number} created PO {order.number} with status {order.get_status_display()}.",
    )
    return redirect("entity_edit", entity="purchases", pk=order.pk)


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
    approval = pending_approval_for(purchase_request)
    if approval is None or not can_user_act(approval, request.user):
        messages.error(request, "You are not an approver for the active Purchase Request approval level.")
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
            approved_request = approve_purchase_request_lines(
                purchase_request,
                decisions=decisions,
                actor=request.user,
                reason=request.POST.get("reason", ""),
            )
        except ValidationError as exc:
            line_formset._non_form_errors = line_formset.error_class(exc.messages)
        else:
            if getattr(approved_request, "_approval_advanced", False):
                messages.success(request, "Approval recorded and advanced to the next level.")
            else:
                messages.success(
                    request,
                    f"Purchase Request {purchase_request.number} approved with line decisions.",
                )
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
    current_date = timezone.localdate()
    if blanket_order.date_start > current_date:
        messages.error(request, "RFQ release is only available on or after the Blanket Order Start Date.")
        return redirect("entity_edit", entity="blanket-orders", pk=pk)
    if blanket_order.date_end <= current_date:
        messages.error(request, "RFQ release is only available before the Blanket Order End Date.")
        return redirect("entity_edit", entity="blanket-orders", pk=pk)
    quantity_controlled = (
        blanket_order.agreement_policy == BlanketOrder.AgreementPolicy.QUANTITY_PERIOD
    )
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
        if not quantity_controlled or remaining > 0:
            initial.append(
                {
                    "include": True,
                    "line_id": line.pk,
                    "product": str(line.product),
                    "description": line.description,
                    "agreed_quantity": line.agreed_quantity,
                    "released_quantity": line.released_quantity,
                    "remaining_quantity": remaining,
                    "quantity": remaining if quantity_controlled else line.agreed_quantity,
                }
            )
    if not initial:
        messages.error(request, "No remaining Blanket Order quantity is available to release.")
        return redirect("entity_edit", entity="blanket-orders", pk=pk)
    line_formset = BlanketOrderRFQLineFormSet(
        request.POST or None,
        prefix="release_lines",
        initial=initial,
        form_kwargs={"quantity_controlled": quantity_controlled},
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
            "quantity_controlled": quantity_controlled,
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
