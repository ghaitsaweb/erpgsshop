from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta

from core.models import Company, Employee
from modules.hr.change_requests import transition_employee_change_request
from modules.hr.models import EmployeeChangeRequest
from modules.organization.models import UserOrganizationAccess

from .models import (
    ApprovalAction,
    ApprovalDelegation,
    ApprovalInstance,
    ApprovalNotification,
    ApprovalOutbox,
    ApprovalRule,
    ApprovalTimeoutWhitelist,
)
from .runtime import complete_due_timeout, drain_approval_outbox
from .services import can_user_act, perform_approval_action, submit_for_approval
from .tasks import process_mass_approval_item


class EmployeeChangeApprovalTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.requester = user_model.objects.create_user("approval-requester", password="test-password-123")
        self.approver = user_model.objects.create_user("approval-manager", password="test-password-123")
        self.delegate = user_model.objects.create_user("approval-delegate", password="test-password-123")
        self.company = Company.objects.create(code="APR", name="Approval Company")
        self.employee = Employee.objects.create(
            company=self.company,
            user=self.requester,
            employee_id="APR-1",
            name="Approval Employee",
            phone="100",
        )
        self.approver.groups.add(Group.objects.get(name="HR Manager"))
        access = UserOrganizationAccess.objects.create(user=self.approver)
        access.companies.add(self.company)
        self.rule = ApprovalRule.objects.get(name="Employee Change Request Default")

    def _request(self, suffix=""):
        return EmployeeChangeRequest.objects.create(
            employee=self.employee,
            field_name="phone",
            current_value="100",
            requested_value=f"200{suffix}",
        )

    def _submit(self, change_request):
        return submit_for_approval(
            change_request,
            self.requester,
            entity="employee-change-requests",
            apply_submit=lambda target: transition_employee_change_request(target, "submit", self.requester),
        )

    def _act(self, approval, actor, action, reason=""):
        return perform_approval_action(
            approval,
            actor,
            action,
            reason=reason,
            apply_result=lambda target, result: transition_employee_change_request(target, result, actor),
        )

    def test_submit_freezes_rule_and_approver_then_approval_applies_domain_change(self):
        change_request = self._request()
        approval = self._submit(change_request)

        change_request.refresh_from_db()
        self.assertEqual(change_request.status, "submitted")
        self.assertEqual(approval.status, ApprovalInstance.Status.PENDING)
        self.assertEqual(approval.trigger_snapshot["employee"], self.employee.pk)
        self.assertIn(self.approver.pk, approval.active_level["resolved_user_ids"])
        self.assertEqual(approval.actions.get().action, ApprovalAction.Action.SUBMIT)
        self.assertEqual(approval.outbox_messages.get().event, "submitted")

        self.rule.approval_levels = [{"name": "Changed later", "user_ids": [self.delegate.pk], "minimum_approvals": 1}]
        self.rule.save(update_fields=["approval_levels", "updated_at"])
        outcome = self._act(approval, self.approver, "approve")

        change_request.refresh_from_db()
        self.employee.refresh_from_db()
        outcome.instance.refresh_from_db()
        self.assertTrue(outcome.completed)
        self.assertEqual(outcome.instance.status, ApprovalInstance.Status.APPROVED)
        self.assertEqual(change_request.status, "approved")
        self.assertEqual(self.employee.phone, "200")
        self.assertEqual(outcome.instance.actions.filter(action="approve").count(), 1)

    def test_duplicate_pending_submission_is_blocked(self):
        change_request = self._request()
        self._submit(change_request)
        with self.assertRaisesMessage(ValidationError, "already has a pending approval"):
            self._submit(change_request)
        self.assertEqual(ApprovalInstance.objects.filter(object_id=change_request.pk).count(), 1)

    def test_reject_requires_reason_and_preserves_employee_value(self):
        change_request = self._request()
        approval = self._submit(change_request)
        with self.assertRaisesMessage(ValidationError, "rejection reason is required"):
            self._act(approval, self.approver, "reject")

        outcome = self._act(approval, self.approver, "reject", "Evidence does not match")
        change_request.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertEqual(outcome.instance.status, ApprovalInstance.Status.REJECTED)
        self.assertEqual(change_request.status, "rejected")
        self.assertEqual(self.employee.phone, "100")

    def test_active_delegation_records_actor_and_effective_approver(self):
        change_request = self._request()
        approval = self._submit(change_request)
        ApprovalDelegation.objects.create(
            delegator=self.approver,
            delegate=self.delegate,
            company=self.company,
        )

        self._act(approval, self.delegate, "approve")

        action = approval.actions.get(action=ApprovalAction.Action.APPROVE)
        self.assertEqual(action.performed_by, self.delegate)
        self.assertEqual(action.effective_approver, self.approver)
        self.assertEqual(action.delegated_from, self.approver)

    def test_unconfigured_user_cannot_approve(self):
        outsider = get_user_model().objects.create_user("approval-outsider", password="test-password-123")
        approval = self._submit(self._request())
        with self.assertRaisesMessage(ValidationError, "not an approver"):
            self._act(approval, outsider, "approve")

    def test_multiple_levels_and_threshold_advance_in_order(self):
        second = get_user_model().objects.create_user("approval-second", password="test-password-123")
        second.groups.add(Group.objects.get(name="HR Manager"))
        access = UserOrganizationAccess.objects.create(user=second)
        access.companies.add(self.company)
        self.rule.approval_levels = [
            {
                "name": "Two person review",
                "user_ids": [self.approver.pk, second.pk],
                "minimum_approvals": 2,
            },
            {"name": "Final review", "user_ids": [self.approver.pk], "minimum_approvals": 1},
        ]
        self.rule.save(update_fields=["approval_levels", "updated_at"])
        approval = self._submit(self._request())

        first = self._act(approval, self.approver, "approve")
        self.assertFalse(first.completed)
        second_result = self._act(approval, second, "approve")
        self.assertFalse(second_result.completed)
        second_result.instance.refresh_from_db()
        self.assertEqual(second_result.instance.current_level, 1)
        final = self._act(second_result.instance, self.approver, "approve")

        self.assertTrue(final.completed)
        self.assertEqual(final.instance.status, ApprovalInstance.Status.APPROVED)

    def test_submit_query_count_does_not_grow_with_approval_levels(self):
        def measured(level_count):
            self.rule.approval_levels = [
                {
                    "name": f"Level {index + 1}",
                    "user_ids": [self.approver.pk],
                    "minimum_approvals": 1,
                }
                for index in range(level_count)
            ]
            self.rule.save(update_fields=["approval_levels", "updated_at"])
            change_request = self._request(f"-levels-{level_count}")
            with CaptureQueriesContext(connection) as queries:
                self._submit(change_request)
            return len(queries)

        self.assertEqual(measured(2), measured(12))

    def test_master_approver_requires_reason_and_completes_all_levels(self):
        self.rule.master_approver = self.approver
        self.rule.approval_levels = [
            {"name": "First", "user_ids": [self.delegate.pk], "minimum_approvals": 1},
            {"name": "Second", "user_ids": [self.delegate.pk], "minimum_approvals": 1},
        ]
        self.rule.save(update_fields=["master_approver", "approval_levels", "updated_at"])
        approval = self._submit(self._request())

        with self.assertRaisesMessage(ValidationError, "approval reason is required"):
            self._act(approval, self.approver, "approve")
        outcome = self._act(approval, self.approver, "approve", "Reviewed as master approver")

        self.assertTrue(outcome.completed)
        self.assertTrue(approval.actions.filter(action=ApprovalAction.Action.MASTER_APPROVE).exists())

    def test_mass_item_uses_same_approval_engine(self):
        self.rule.workflow.enable_mass_approval = True
        self.rule.workflow.save(update_fields=["enable_mass_approval", "updated_at"])
        change_request = self._request()
        approval = self._submit(change_request)

        result = process_mass_approval_item(approval.pk, self.approver.pk, "approve")

        change_request.refresh_from_db()
        self.assertTrue(result["success"])
        self.assertTrue(result["completed"])
        self.assertEqual(change_request.status, "approved")

    def test_delayed_delegation_uses_frozen_due_time(self):
        self.rule.use_delegate = True
        self.rule.delegate_after = 1
        self.rule.delegate_period = ApprovalRule.Period.MINUTES
        self.rule.save(update_fields=["use_delegate", "delegate_after", "delegate_period", "updated_at"])
        approval = self._submit(self._request())
        ApprovalDelegation.objects.create(
            delegator=self.approver,
            delegate=self.delegate,
            company=self.company,
            while_away=False,
        )

        self.assertFalse(can_user_act(approval, self.delegate))
        approval.delegate_due_at = timezone.now() - timedelta(seconds=1)
        approval.save(update_fields=["delegate_due_at", "updated_at"])
        self.rule.use_delegate = False
        self.rule.save(update_fields=["use_delegate", "updated_at"])

        self.assertTrue(can_user_act(approval, self.delegate))

    def test_whitelisted_timeout_rejects_with_immutable_snapshot(self):
        content_type = ContentType.objects.get_for_model(EmployeeChangeRequest)
        ApprovalTimeoutWhitelist.objects.create(content_type=content_type, reason="Covered by automated HR policy")
        self.rule.timeout_action = ApprovalRule.TimeoutAction.REJECT
        self.rule.timeout_value = 1
        self.rule.timeout_period = ApprovalRule.Period.MINUTES
        self.rule.save(
            update_fields=["timeout_action", "timeout_value", "timeout_period", "updated_at"]
        )
        change_request = self._request()
        approval = self._submit(change_request)
        approval.timeout_due_at = timezone.now() - timedelta(seconds=1)
        approval.save(update_fields=["timeout_due_at", "updated_at"])
        self.rule.timeout_action = ApprovalRule.TimeoutAction.APPROVE
        self.rule.save(update_fields=["timeout_action", "updated_at"])

        self.assertTrue(complete_due_timeout(approval.pk))

        approval.refresh_from_db()
        change_request.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertEqual(approval.status, ApprovalInstance.Status.REJECTED)
        self.assertEqual(change_request.status, "rejected")
        self.assertEqual(self.employee.phone, "100")
        self.assertTrue(approval.actions.filter(action=ApprovalAction.Action.AUTO_REJECT).exists())

    def test_outbox_delivery_is_idempotent(self):
        approval = self._submit(self._request())

        self.assertEqual(drain_approval_outbox(), 1)
        self.assertEqual(drain_approval_outbox(), 0)
        self.assertEqual(
            ApprovalNotification.objects.filter(instance=approval, recipient=self.approver).count(),
            1,
        )
        message = ApprovalOutbox.objects.get(instance=approval, event="submitted")
        self.assertEqual(message.status, ApprovalOutbox.Status.SENT)

    def test_inbox_and_detail_show_only_authorized_approval_data(self):
        approval = self._submit(self._request())
        drain_approval_outbox()
        self.client.force_login(self.approver)

        inbox = self.client.get("/approvals/")
        detail = self.client.get(f"/approvals/{approval.pk}/")

        self.assertEqual(inbox.status_code, 200)
        self.assertContains(inbox, approval.reference)
        self.assertContains(inbox, "Approval Submitted")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Approve")

        outsider = get_user_model().objects.create_user("approval-view-outsider", password="test-password-123")
        self.client.force_login(outsider)
        outsider_inbox = self.client.get("/approvals/")
        outsider_detail = self.client.get(f"/approvals/{approval.pk}/")
        self.assertNotContains(outsider_inbox, approval.reference)
        self.assertRedirects(outsider_detail, "/approvals/")

    def test_outbox_query_count_does_not_grow_with_rows(self):
        def measured(row_count):
            requests = [self._request(str(index)) for index in range(row_count)]
            approvals = [self._submit(change_request) for change_request in requests]
            with CaptureQueriesContext(connection) as queries:
                drain_approval_outbox(batch_size=row_count)
            ApprovalNotification.objects.filter(instance__in=approvals).delete()
            return len(queries)

        self.assertEqual(measured(2), measured(12))
