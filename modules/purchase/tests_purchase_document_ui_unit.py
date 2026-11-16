from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from .forms import PurchaseMultipleFileField
from .models import (
    PurchaseOrderInvoiceAccounting,
    PurchaseTender,
    validate_purchase_document_attachment,
)
from .services import _configured_purchase_downpayment_amount
from .workflow_extensions import (
    purchase_tender_action_audit,
    purchase_tender_completion_tab_visible,
    purchase_tender_scoring_tab_visible,
)


class PurchaseDocumentUIUnitTests(SimpleTestCase):
    def test_multi_file_field_accepts_hmx_formats_and_rejects_spoofed_content(self):
        field = PurchaseMultipleFileField(required=False)
        field.validators.append(validate_purchase_document_attachment)
        files = field.clean(
            [
                SimpleUploadedFile("scope.pdf", b"%PDF-1.4\nscope"),
                SimpleUploadedFile("photo.jpg", b"\xff\xd8\xffphoto"),
                SimpleUploadedFile("evidence.png", b"\x89PNG\r\n\x1a\nevidence"),
            ]
        )
        self.assertEqual([item.name for item in files], ["scope.pdf", "photo.jpg", "evidence.png"])

        with self.assertRaisesMessage(ValidationError, "must contain a valid"):
            field.clean([SimpleUploadedFile("spoofed.pdf", b"plain text")])

    def test_downpayment_percentage_uses_the_supplied_vat_or_untaxed_base(self):
        plan = SimpleNamespace(
            downpayment_type=PurchaseOrderInvoiceAccounting.DownpaymentType.PERCENTAGE,
            downpayment_percentage=Decimal("25"),
            downpayment_amount=Decimal("0"),
        )

        self.assertEqual(
            _configured_purchase_downpayment_amount(plan, Decimal("1200")),
            Decimal("300.00"),
        )
        self.assertEqual(
            _configured_purchase_downpayment_amount(plan, Decimal("1000")),
            Decimal("250.00"),
        )

    def test_tender_matrix_tabs_follow_hmx_method_and_status_gates(self):
        tender = SimpleNamespace(
            method=PurchaseTender.Method.PQTE,
            status=PurchaseTender.Status.BID_SELECTION,
        )
        self.assertTrue(purchase_tender_completion_tab_visible(tender, None))
        self.assertTrue(purchase_tender_scoring_tab_visible(tender, None))

        tender.method = PurchaseTender.Method.OTE
        tender.status = PurchaseTender.Status.DRAFT
        self.assertFalse(purchase_tender_completion_tab_visible(tender, None))
        self.assertFalse(purchase_tender_scoring_tab_visible(tender, None))

    def test_tender_action_audit_persists_the_workflow_transition(self):
        tender = SimpleNamespace(
            award_cycle=3,
            decision_approval_status=PurchaseTender.DecisionStatus.APPROVED,
            status=PurchaseTender.Status.AWARDED,
            _tender_audit_details={"source": "workflow"},
        )
        actor = object()

        with patch(
            "modules.purchase.workflow_extensions.TenderAuditLog.objects.create"
        ) as create_audit:
            purchase_tender_action_audit(
                tender,
                "approve-decision",
                actor,
                PurchaseTender.Status.BID_SELECTION,
            )

        create_audit.assert_called_once_with(
            tender=tender,
            action="approve-decision",
            previous_status=PurchaseTender.Status.BID_SELECTION,
            new_status=PurchaseTender.Status.AWARDED,
            actor=actor,
            details={
                "source": "workflow",
                "award_cycle": 3,
                "decision_approval_status": PurchaseTender.DecisionStatus.APPROVED,
            },
        )
