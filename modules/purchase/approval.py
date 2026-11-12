"""Purchase-owned adapters between domain transitions and Approval Workflow."""

from django.core.exceptions import ValidationError
from django.urls import reverse

from core.models import PurchaseOrder
from modules.approval_workflow.adapters import register_approval_adapter

from .models import PurchaseRequest, PurchaseTender
from .services import (
    cancel_purchase_order,
    confirm_purchase_order,
    purchase_request_before_workflow,
    submit_purchase_order_for_approval,
    transition_blanket_order,
    transition_purchase_kpi_target,
    transition_purchase_tender,
    transition_vendor_performance_evaluation,
    transition_vendor_pricelist,
    transition_vendor_pricelist_amendment,
    transition_vendor_profile,
)


def _unsupported(target, action):
    raise ValidationError(
        f"{target._meta.verbose_name.title()} does not support approval lifecycle action '{action}'."
    )


def apply_purchase_order_approval_submit(order, actor):
    return submit_purchase_order_for_approval(order, actor)


def apply_purchase_order_approval_lifecycle(order, action, actor, reason=""):
    del reason
    if action == "cancel" and order.status == PurchaseOrder.Status.WAITING_APPROVAL:
        return cancel_purchase_order(order, actor)
    if action != "reset" or order.status != PurchaseOrder.Status.REJECTED:
        return _unsupported(order, action)
    order.status = PurchaseOrder.Status.DRAFT
    order.save(update_fields=["status", "updated_at"])
    return order


def apply_purchase_order_approval_result(order, action, actor, reason=""):
    del reason
    if order.status != PurchaseOrder.Status.WAITING_APPROVAL:
        raise ValidationError("Only a Purchase Order waiting for approval can be approved or rejected.")
    if action == "approve":
        return confirm_purchase_order(order, actor)
    if action == "reject":
        order.status = PurchaseOrder.Status.REJECTED
        order.save(update_fields=["status", "updated_at"])
        return order
    return _unsupported(order, action)


def _service_adapter(transition):
    def submit(target, actor):
        return transition(target, "submit", actor)

    def lifecycle(target, action, actor, reason=""):
        del reason
        return transition(target, action, actor)

    def result(target, action, actor, reason=""):
        del reason
        return transition(target, action, actor)

    return submit, lifecycle, result


def _reason_service_adapter(transition):
    def submit(target, actor):
        return transition(target, "submit", actor)

    def lifecycle(target, action, actor, reason=""):
        return transition(target, action, actor, reason=reason)

    def result(target, action, actor, reason=""):
        return transition(target, action, actor, reason=reason)

    return submit, lifecycle, result


def apply_purchase_request_submit(purchase_request, actor):
    if purchase_request.status != PurchaseRequest.Status.DRAFT:
        raise ValidationError("Only a Draft Purchase Request can be submitted for approval.")
    purchase_request_before_workflow(instance=purchase_request, action="submit", actor=actor)
    purchase_request.status = PurchaseRequest.Status.PENDING
    purchase_request.rejection_reason = ""
    purchase_request.save(update_fields=["status", "rejection_reason", "updated_at"])
    return purchase_request


def apply_purchase_request_lifecycle(purchase_request, action, actor, reason=""):
    del reason
    if action != "reset" or purchase_request.status != PurchaseRequest.Status.REJECTED:
        return _unsupported(purchase_request, action)
    purchase_request_before_workflow(instance=purchase_request, action="reset", actor=actor)
    purchase_request.status = PurchaseRequest.Status.DRAFT
    purchase_request.rejection_reason = ""
    purchase_request.save(update_fields=["status", "rejection_reason", "updated_at"])
    return purchase_request


def apply_purchase_request_result(purchase_request, action, actor, reason=""):
    if purchase_request.status not in {
        PurchaseRequest.Status.PENDING,
        PurchaseRequest.Status.PARTIALLY_APPROVED,
    }:
        raise ValidationError("Only a pending Purchase Request can be approved or rejected.")
    if action == "approve":
        purchase_request.status = PurchaseRequest.Status.APPROVED
        purchase_request.rejection_reason = ""
        purchase_request.save(update_fields=["status", "rejection_reason", "updated_at"])
        return purchase_request
    if action == "reject":
        purchase_request_before_workflow(instance=purchase_request, action="reject", actor=actor)
        purchase_request.status = PurchaseRequest.Status.REJECTED
        purchase_request.rejection_reason = reason.strip()
        purchase_request.save(update_fields=["status", "rejection_reason", "updated_at"])
        return purchase_request
    return _unsupported(purchase_request, action)


def purchase_request_decision_url(purchase_request):
    return reverse("purchase_request_approve", kwargs={"pk": purchase_request.pk})


def amendment_company_id(amendment):
    return amendment.pricelist.company_id


def apply_tender_submit(tender, actor):
    if tender.status == PurchaseTender.Status.DRAFT:
        return transition_purchase_tender(tender, "submit", actor)
    if tender.status == PurchaseTender.Status.AWARDED and tender.decision_approval_status in {
        PurchaseTender.DecisionStatus.DRAFT,
        PurchaseTender.DecisionStatus.REJECTED,
    }:
        return transition_purchase_tender(tender, "submit-decision", actor)
    return _unsupported(tender, "submit")


def apply_tender_lifecycle(tender, action, actor, reason=""):
    return transition_purchase_tender(tender, action, actor, reason=reason)


def apply_tender_result(tender, action, actor, reason=""):
    if tender.status == PurchaseTender.Status.WAITING_APPROVAL:
        return transition_purchase_tender(tender, action, actor, reason=reason)
    if (
        tender.status == PurchaseTender.Status.AWARDED
        and tender.decision_approval_status == PurchaseTender.DecisionStatus.WAITING_APPROVAL
    ):
        decision_action = "approve-decision" if action == "approve" else "reject-decision"
        return transition_purchase_tender(tender, decision_action, actor, reason=reason)
    return _unsupported(tender, action)


def register_purchase_approval_adapter():
    register_approval_adapter(
        "core.purchaseorder",
        submit=apply_purchase_order_approval_submit,
        lifecycle=apply_purchase_order_approval_lifecycle,
        result=apply_purchase_order_approval_result,
    )

    for model_label, transition in (
        ("gsshop_purchase.vendorpricelist", transition_vendor_pricelist),
        ("gsshop_purchase.blanketorder", transition_blanket_order),
        ("gsshop_purchase.purchasekpitarget", transition_purchase_kpi_target),
    ):
        submit, lifecycle, result = _service_adapter(transition)
        register_approval_adapter(
            model_label,
            submit=submit,
            lifecycle=lifecycle,
            result=result,
        )

    for model_label, transition in (
        ("gsshop_purchase.vendorprofile", transition_vendor_profile),
        (
            "gsshop_purchase.vendorperformanceevaluation",
            transition_vendor_performance_evaluation,
        ),
    ):
        submit, lifecycle, result = _reason_service_adapter(transition)
        register_approval_adapter(
            model_label,
            submit=submit,
            lifecycle=lifecycle,
            result=result,
        )

    amendment_submit, amendment_lifecycle, amendment_result = _service_adapter(
        transition_vendor_pricelist_amendment
    )
    register_approval_adapter(
        "gsshop_purchase.vendorpricelistamendment",
        submit=amendment_submit,
        lifecycle=amendment_lifecycle,
        result=amendment_result,
        company_id=amendment_company_id,
    )
    register_approval_adapter(
        "gsshop_purchase.purchaserequest",
        submit=apply_purchase_request_submit,
        lifecycle=apply_purchase_request_lifecycle,
        result=apply_purchase_request_result,
        decision_url=purchase_request_decision_url,
        direct_decision=False,
    )
    register_approval_adapter(
        "gsshop_purchase.purchasetender",
        submit=apply_tender_submit,
        lifecycle=apply_tender_lifecycle,
        result=apply_tender_result,
    )
