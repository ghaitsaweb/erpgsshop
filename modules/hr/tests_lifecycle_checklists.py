from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import Company, Employee
from modules.organization.models import UserOrganizationAccess

from .access import HR_MANAGER, HR_USER
from .lifecycle_checklists import complete_lifecycle_task, transition_lifecycle_process
from .document_lifecycle import expire_employee_documents, transition_employee_document
from .forms import EmployeeDocumentForm
from .models import (
    ApprovalStatus,
    DocumentType,
    EmployeeChecklist,
    EmployeeDocument,
    EmployeeDocumentHistory,
    EmployeeLifecycleTask,
    EmployeeOffboarding,
    EmployeeOnboarding,
    HRWorkflowAudit,
)


class EmployeeLifecycleChecklistTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_superuser(
            username="lifecycle-manager", email="lifecycle@example.test", password="test-password-123"
        )
        self.client.force_login(self.manager)
        self.company = Company.objects.create(code="LCA", name="Lifecycle Company A")
        self.other_company = Company.objects.create(code="LCB", name="Lifecycle Company B")
        self.today = timezone.localdate()
        self.employee = Employee.objects.create(
            company=self.company, employee_id="LC-1", name="Lifecycle Employee", join_date=self.today
        )
        self.foreign_employee = Employee.objects.create(
            company=self.other_company, employee_id="LC-2", name="Foreign Employee", join_date=self.today
        )
        self.onboarding = EmployeeOnboarding.objects.create(employee=self.employee, owner=self.manager)

    def _template(self, number, *, lifecycle=EmployeeChecklist.Lifecycle.ONBOARDING,
                  company=None, requires_evidence=False):
        return EmployeeChecklist.objects.create(
            company=company or self.company,
            name=f"Lifecycle task {number}",
            lifecycle=lifecycle,
            sequence=number,
            requires_evidence=requires_evidence,
        )

    def test_onboarding_parent_embeds_bounded_checklist_lines(self):
        for number in range(1, 52):
            self._template(number)
        self.client.post(f"/app/employee-onboarding/{self.onboarding.pk}/action/submit/")
        response = self.client.get(f"/app/employee-onboarding/{self.onboarding.pk}/edit/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Checklist Lines")
        self.assertContains(response, "Lifecycle task 50")
        self.assertNotContains(response, "Lifecycle task 51")
        self.assertContains(response, "Showing the first 50 lines")

    def _document(self, *, employee=None, verified=True, expiry_date=None,
                  attachment_url="https://example.test/evidence.pdf", document_number="EVID-001"):
        employee = employee or self.employee
        kind = DocumentType.objects.get_or_create(
            company=employee.company, code="EVIDENCE", defaults={"name": "Evidence"}
        )[0]
        return EmployeeDocument.objects.create(
            employee=employee, document_type=kind, verified=verified,
            status=EmployeeDocument.Status.ACTIVE, expiry_date=expiry_date,
            attachment_url=attachment_url, document_number=document_number,
        )

    def test_onboarding_snapshots_template_and_completes_from_verified_tasks(self):
        first = self._template(1, requires_evidence=True)
        self._template(2)
        self._template(3, company=self.other_company)
        response = self.client.post(f"/app/employee-onboarding/{self.onboarding.pk}/action/submit/")
        self.assertEqual(response.status_code, 302)
        self.onboarding.refresh_from_db()
        self.assertEqual(self.onboarding.status, ApprovalStatus.SUBMITTED)
        self.assertEqual(self.onboarding.progress, 0)
        tasks = list(self.onboarding.checklist_tasks.order_by("sequence"))
        self.assertEqual(len(tasks), 2)
        self.assertTrue(tasks[0].requires_evidence)

        first.name = "Changed after start"
        first.requires_evidence = False
        first.active = False
        first.save(update_fields=["name", "requires_evidence", "active", "updated_at"])
        tasks[0].refresh_from_db()
        self.assertEqual(tasks[0].name, "Lifecycle task 1")
        self.assertTrue(tasks[0].requires_evidence)
        with self.assertRaises(ValidationError):
            complete_lifecycle_task(tasks[0], self.manager)
        with self.assertRaises(ValidationError):
            transition_lifecycle_process(self.onboarding, "complete", self.manager)

        foreign_document = self._document(employee=self.foreign_employee)
        with self.assertRaises(ValidationError):
            complete_lifecycle_task(tasks[0], self.manager, evidence_document_id=foreign_document.pk)
        unverified = self._document(verified=False)
        with self.assertRaises(ValidationError):
            complete_lifecycle_task(tasks[0], self.manager, evidence_document_id=unverified.pk)
        expired = self._document(expiry_date=self.today - timedelta(days=1))
        with self.assertRaises(ValidationError):
            complete_lifecycle_task(tasks[0], self.manager, evidence_document_id=expired.pk)
        expiring_today = self._document(expiry_date=self.today)
        with self.assertRaises(ValidationError):
            complete_lifecycle_task(tasks[0], self.manager, evidence_document_id=expiring_today.pk)
        evidence = self._document(expiry_date=self.today + timedelta(days=1))
        response = self.client.post(
            f"/app/employee-lifecycle-tasks/{tasks[0].pk}/action/complete/",
            {"evidence_document_id": str(evidence.pk)},
        )
        self.assertEqual(response.status_code, 302)
        self.onboarding.refresh_from_db()
        self.assertEqual(self.onboarding.progress, 50)
        self.assertEqual(self.onboarding.status, ApprovalStatus.SUBMITTED)
        self.assertEqual(EmployeeLifecycleTask.objects.get(pk=tasks[0].pk).evidence_document_id, evidence.pk)
        evidence.attachment_url = "https://example.test/changed.pdf"
        evidence.document_number = "EVID-CHANGED"
        evidence.save(update_fields=["attachment_url", "document_number", "updated_at"])
        tasks[0].refresh_from_db()
        self.assertEqual(tasks[0].evidence_attachment_url, "https://example.test/evidence.pdf")
        self.assertEqual(tasks[0].evidence_document_number, "EVID-001")

        complete_lifecycle_task(tasks[1], self.manager)
        self.onboarding.refresh_from_db()
        self.assertEqual((self.onboarding.progress, self.onboarding.status), (100, ApprovalStatus.APPROVED))
        self.assertTrue(HRWorkflowAudit.objects.filter(
            entity="employee-onboarding", record_id=self.onboarding.pk, action="auto-complete"
        ).exists())
        self.assertEqual(self.onboarding.checklist_tasks.filter(status="completed").count(), 2)

    def test_offboarding_uses_own_company_templates_and_cannot_finish_early(self):
        self._template(1, lifecycle=EmployeeChecklist.Lifecycle.OFFBOARDING)
        self._template(2, lifecycle=EmployeeChecklist.Lifecycle.ONBOARDING)
        offboarding = EmployeeOffboarding.objects.create(
            employee=self.employee, owner=self.manager, last_working_date=self.today,
        )
        transition_lifecycle_process(offboarding, "submit", self.manager)
        self.assertEqual(offboarding.checklist_tasks.count(), 1)
        with self.assertRaises(ValidationError):
            transition_lifecycle_process(offboarding, "complete", self.manager)
        task = offboarding.checklist_tasks.get()
        complete_lifecycle_task(task, self.manager)
        offboarding.refresh_from_db()
        self.assertEqual((offboarding.progress, offboarding.status), (100, ApprovalStatus.APPROVED))
        with self.assertRaises(ValidationError):
            transition_lifecycle_process(offboarding, "cancel", self.manager)

    def test_start_requires_template_and_retry_does_not_reset_progress(self):
        with self.assertRaises(ValidationError):
            transition_lifecycle_process(self.onboarding, "submit", self.manager)
        self._template(1)
        self._template(2)
        transition_lifecycle_process(self.onboarding, "submit", self.manager)
        first = self.onboarding.checklist_tasks.order_by("pk").first()
        complete_lifecycle_task(first, self.manager)
        transition_lifecycle_process(self.onboarding, "submit", self.manager)
        self.onboarding.refresh_from_db()
        self.assertEqual(self.onboarding.progress, 50)
        self.assertEqual(self.onboarding.checklist_tasks.count(), 2)

    def test_role_and_company_scope_reject_foreign_process_and_task(self):
        self._template(1, company=self.other_company)
        foreign_process = EmployeeOnboarding.objects.create(employee=self.foreign_employee, owner=self.manager)
        ordinary = get_user_model().objects.create_user(username="lc-ordinary", password="test-password-123")
        with self.assertRaises(ValidationError):
            transition_lifecycle_process(self.onboarding, "submit", ordinary)
        scoped_manager = get_user_model().objects.create_user(username="lc-scoped", password="test-password-123")
        scoped_manager.groups.add(Group.objects.get_or_create(name=HR_MANAGER)[0])
        branch = self.company.branches.get(is_main=True)
        profile = UserOrganizationAccess.objects.create(
            user=scoped_manager, default_company=self.company, default_branch=branch
        )
        profile.companies.add(self.company)
        profile.branches.add(branch)
        with self.assertRaises(ValidationError):
            transition_lifecycle_process(foreign_process, "submit", scoped_manager)
        transition_lifecycle_process(foreign_process, "submit", self.manager)
        with self.assertRaises(ValidationError):
            complete_lifecycle_task(foreign_process.checklist_tasks.get(), scoped_manager)

    def test_workspace_cannot_forge_or_edit_generated_tasks(self):
        self._template(1)
        self.client.post(f"/app/employee-onboarding/{self.onboarding.pk}/action/submit/")
        task = self.onboarding.checklist_tasks.get()
        self.assertEqual(self.client.post("/app/employee-lifecycle-tasks/new/", {
            "onboarding": self.onboarding.pk, "name": "Forged", "status": "completed",
        }).status_code, 302)
        self.assertEqual(self.onboarding.checklist_tasks.count(), 1)
        self.assertEqual(self.client.post(f"/app/employee-lifecycle-tasks/{task.pk}/edit/", {
            "status": "completed",
        }).status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, EmployeeLifecycleTask.Status.PENDING)
        self.assertEqual(self.client.get(
            f"/app/employee-lifecycle-tasks/?onboarding={self.onboarding.pk}"
        ).status_code, 200)

    def test_employee_cannot_reverify_or_change_verified_document(self):
        document = self._document()
        worker = get_user_model().objects.create_user(
            username="lc-worker", password="test-password-123", is_staff=True
        )
        worker.groups.add(Group.objects.get_or_create(name=HR_USER)[0])
        worker.user_permissions.add(Permission.objects.get(
            content_type__app_label="gsshop_hr", codename="change_employeedocument"
        ))
        self.employee.user = worker
        self.employee.save(update_fields=["user", "updated_at"])
        branch = self.company.branches.get(is_main=True)
        profile = UserOrganizationAccess.objects.create(
            user=worker, default_company=self.company, default_branch=branch
        )
        profile.companies.add(self.company)
        profile.branches.add(branch)
        self.client.force_login(worker)
        response = self.client.post(f"/app/employee-documents/{document.pk}/edit/", {
            "employee": self.employee.pk,
            "document_type": document.document_type_id,
            "attachment_url": "https://example.test/forged.pdf",
            "verified": "",
            "status": EmployeeDocument.Status.EXPIRED,
        })
        self.assertEqual(response.status_code, 302)
        document.refresh_from_db()
        self.assertEqual(document.attachment_url, "https://example.test/evidence.pdf")
        draft = EmployeeDocument.objects.create(
            employee=self.employee, document_type=document.document_type,
            attachment_url="https://example.test/draft.pdf",
        )
        self.assertEqual(self.client.post(
            f"/app/employee-documents/{draft.pk}/action/verify/"
        ).status_code, 302)
        draft.refresh_from_db()
        self.assertEqual((draft.status, draft.verified), (EmployeeDocument.Status.DRAFT, False))
        self.assertFalse(draft.history.exists())
        self.assertTrue(document.verified)
        self.assertEqual(document.status, EmployeeDocument.Status.ACTIVE)
        self.assertEqual(self.client.post(
            f"/admin/gsshop_hr/employeedocument/{document.pk}/change/",
            {"attachment_url": "https://example.test/admin-forged.pdf", "verified": "on"},
        ).status_code, 403)
        document.refresh_from_db()
        self.assertEqual(document.attachment_url, "https://example.test/evidence.pdf")

    def test_start_query_count_is_fixed_for_two_and_twelve_templates(self):
        for number in range(2):
            self._template(number)
        with CaptureQueriesContext(connection) as small:
            transition_lifecycle_process(self.onboarding, "submit", self.manager)
        second = EmployeeOnboarding.objects.create(employee=self.employee, owner=self.manager)
        for number in range(2, 12):
            self._template(number)
        with CaptureQueriesContext(connection) as large:
            transition_lifecycle_process(second, "submit", self.manager)
        self.assertEqual(len(small), len(large))
        self.assertEqual(second.checklist_tasks.count(), 12)

    def test_document_verification_expiry_and_history(self):
        kind = DocumentType.objects.create(company=self.company, code="ID", name="Identity")
        document = EmployeeDocument.objects.create(
            employee=self.employee, document_type=kind,
            expiry_date=self.today + timedelta(days=1),
        )
        with self.assertRaises(ValidationError):
            transition_employee_document(document, "verify", self.manager)
        self.assertFalse(EmployeeDocumentHistory.objects.exists())
        document.attachment_url = "https://example.test/id.pdf"
        document.save(update_fields=["attachment_url", "updated_at"])
        response = self.client.post(f"/app/employee-documents/{document.pk}/action/verify/")
        self.assertEqual(response.status_code, 302)
        document.refresh_from_db()
        self.assertEqual((document.status, document.verified), (EmployeeDocument.Status.ACTIVE, True))
        self.assertEqual(list(document.history.values_list("action", "previous_status", "new_status")), [
            ("verify", "draft", "active"),
        ])
        with self.assertRaises(ValidationError):
            transition_employee_document(document, "expire", self.manager)
        EmployeeDocument.objects.filter(pk=document.pk).update(expiry_date=self.today - timedelta(days=1))
        transition_employee_document(document, "expire", self.manager)
        document.refresh_from_db()
        self.assertEqual(document.status, EmployeeDocument.Status.EXPIRED)
        self.assertEqual(document.history.count(), 2)

    def test_uploaded_document_is_downloadable_and_usable_as_checklist_evidence(self):
        kind = DocumentType.objects.create(company=self.company, code="ID", name="Identity")
        content = b"%PDF-1.4\nlocal evidence\n%%EOF"
        response = self.client.post("/app/employee-documents/new/", {
            "employee": self.employee.pk,
            "document_type": kind.pk,
            "document_number": "ID-1",
            "attachment": SimpleUploadedFile("identity.pdf", content, content_type="application/pdf"),
        })
        self.assertEqual(response.status_code, 302)
        document = EmployeeDocument.objects.get(document_number="ID-1")
        self.assertEqual(document.attachment_name, "identity.pdf")
        download = self.client.get(f"/app/employee-documents/{document.pk}/attachment/")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, content)
        self.assertIn("attachment", download["Content-Disposition"])
        transition_employee_document(document, "verify", self.manager)
        self._template(1, requires_evidence=True)
        transition_lifecycle_process(self.onboarding, "submit", self.manager)
        task = self.onboarding.checklist_tasks.get()
        complete_lifecycle_task(task, self.manager, evidence_document_id=document.pk)
        task.refresh_from_db()
        self.assertEqual(
            task.evidence_attachment_url,
            f"/app/employee-documents/{document.pk}/attachment/",
        )

    def test_document_download_is_scoped_to_employee_company(self):
        local = self._document()
        foreign = self._document(employee=self.foreign_employee)
        EmployeeDocument.objects.filter(pk__in=[local.pk, foreign.pk]).update(
            attachment_name="identity.pdf", attachment_content_type="application/pdf",
            attachment_content=b"%PDF-1.4\nprivate",
        )
        scoped_manager = get_user_model().objects.create_user(
            username="lc-download-scoped", password="test-password-123"
        )
        scoped_manager.groups.add(Group.objects.get_or_create(name=HR_MANAGER)[0])
        scoped_manager.user_permissions.add(Permission.objects.get(
            content_type__app_label="gsshop_hr", codename="view_employeedocument"
        ))
        branch = self.company.branches.get(is_main=True)
        profile = UserOrganizationAccess.objects.create(
            user=scoped_manager, default_company=self.company, default_branch=branch
        )
        profile.companies.add(self.company)
        profile.branches.add(branch)
        self.client.force_login(scoped_manager)
        self.assertEqual(self.client.get(f"/app/employee-documents/{local.pk}/attachment/").status_code, 200)
        self.assertEqual(self.client.get(f"/app/employee-documents/{foreign.pk}/attachment/").status_code, 404)

    def test_document_upload_rejects_mismatched_content(self):
        kind = DocumentType.objects.create(company=self.company, code="ID", name="Identity")
        form = EmployeeDocumentForm({
            "employee": self.employee.pk,
            "document_type": kind.pk,
            "document_number": "ID-2",
        }, {"attachment": SimpleUploadedFile("identity.pdf", b"not a PDF")})
        self.assertFalse(form.is_valid())
        self.assertIn("attachment", form.errors)

    def test_document_dates_follow_hmx_ordering(self):
        kind = DocumentType.objects.create(company=self.company, code="DATE", name="Date limited")
        document = EmployeeDocument(
            employee=self.employee, document_type=kind,
            attachment_url="https://example.test/evidence.pdf",
            issued_date=self.today, expiry_date=self.today,
        )
        with self.assertRaises(ValidationError):
            document.full_clean()
        document.issued_date = self.today + timedelta(days=1)
        document.expiry_date = self.today + timedelta(days=2)
        with self.assertRaises(ValidationError):
            document.full_clean()
        document.issued_date = self.today - timedelta(days=1)
        document.full_clean()

    def test_replacement_requires_matching_draft_and_creates_version_history(self):
        old = self._document()
        wrong_employee = self._document(employee=self.foreign_employee)
        with self.assertRaises(ValidationError):
            transition_employee_document(old, "replace", self.manager, replacement_document_id=wrong_employee.pk)
        unverified = self._document(verified=False)
        with self.assertRaises(ValidationError):
            transition_employee_document(old, "replace", self.manager, replacement_document_id=unverified.pk)
        replacement = self._document(document_number="EVID-002", verified=False)
        replacement.status = EmployeeDocument.Status.DRAFT
        replacement.save(update_fields=["status", "updated_at"])
        transition_employee_document(old, "replace", self.manager, replacement_document_id=replacement.pk)
        old.refresh_from_db()
        replacement.refresh_from_db()
        self.assertEqual((old.status, old.replaced_by_id), (EmployeeDocument.Status.REPLACED, replacement.pk))
        self.assertEqual((replacement.status, replacement.verified), (EmployeeDocument.Status.ACTIVE, True))
        self.assertEqual((replacement.version, replacement.parent_document_id), (old.version + 1, old.pk))
        self.assertEqual(old.history.get().action, "replace")
        self.assertEqual(replacement.history.get().action, "replacement_verified")
        with self.assertRaises(ValidationError):
            transition_employee_document(old, "replace", self.manager, replacement_document_id=replacement.pk)

    def test_single_version_type_rejects_second_verification(self):
        old = self._document()
        draft = self._document(verified=False)
        draft.status = EmployeeDocument.Status.DRAFT
        draft.save(update_fields=["status", "updated_at"])
        with self.assertRaises(ValidationError):
            transition_employee_document(draft, "verify", self.manager)
        draft.document_type.allow_multiple_versions = True
        draft.document_type.save(update_fields=["allow_multiple_versions", "updated_at"])
        transition_employee_document(draft, "verify", self.manager)
        draft.refresh_from_db()
        self.assertEqual((old.status, draft.status), (EmployeeDocument.Status.ACTIVE, EmployeeDocument.Status.ACTIVE))
        with self.assertRaises(ValidationError):
            transition_employee_document(old, "replace", self.manager, replacement_document_id=draft.pk)

    def test_archived_document_cannot_restore_over_another_active_version(self):
        old = self._document()
        transition_employee_document(old, "archive", self.manager)
        old.refresh_from_db()
        self.assertEqual(old.status, EmployeeDocument.Status.ARCHIVED)
        current = self._document(verified=False, document_number="EVID-NEW")
        current.status = EmployeeDocument.Status.DRAFT
        current.save(update_fields=["status", "updated_at"])
        transition_employee_document(current, "verify", self.manager)
        with self.assertRaises(ValidationError):
            transition_employee_document(old, "unarchive", self.manager)
        old.refresh_from_db()
        self.assertEqual(old.status, EmployeeDocument.Status.ARCHIVED)
        transition_employee_document(current, "archive", self.manager)
        transition_employee_document(old, "unarchive", self.manager)
        old.refresh_from_db()
        self.assertEqual(old.status, EmployeeDocument.Status.ACTIVE)
        self.assertEqual(list(old.history.order_by("pk").values_list("action", flat=True)), ["archive", "unarchive"])

    def test_restoring_archived_expired_document_keeps_it_expired(self):
        expired = self._document(expiry_date=self.today)
        transition_employee_document(expired, "archive", self.manager)
        transition_employee_document(expired, "unarchive", self.manager)
        expired.refresh_from_db()
        self.assertEqual(expired.status, EmployeeDocument.Status.EXPIRED)

    def test_document_status_cannot_be_forged_through_edit_form(self):
        kind = DocumentType.objects.create(company=self.company, code="ID", name="Identity")
        document = EmployeeDocument.objects.create(employee=self.employee, document_type=kind)
        response = self.client.post(f"/app/employee-documents/{document.pk}/edit/", {
            "employee": self.employee.pk,
            "document_type": kind.pk,
            "document_number": "ID-001",
            "attachment_url": "https://example.test/id.pdf",
            "verified": "on",
            "status": EmployeeDocument.Status.ACTIVE,
        })
        self.assertEqual(response.status_code, 302)
        document.refresh_from_db()
        self.assertEqual((document.status, document.verified), (EmployeeDocument.Status.DRAFT, False))
        self.assertFalse(document.history.exists())

    def test_document_verification_rejects_wrong_company_type(self):
        foreign_type = DocumentType.objects.create(
            company=self.other_company, code="ID", name="Foreign Identity"
        )
        document = EmployeeDocument.objects.create(
            employee=self.employee, document_type=foreign_type,
            attachment_url="https://example.test/foreign.pdf",
        )
        with self.assertRaises(ValidationError):
            transition_employee_document(document, "verify", self.manager)
        inactive_type = DocumentType.objects.create(
            company=self.company, code="OLD", name="Inactive Identity", active=False
        )
        inactive_document = EmployeeDocument.objects.create(
            employee=self.employee, document_type=inactive_type,
            attachment_url="https://example.test/old.pdf",
        )
        with self.assertRaises(ValidationError):
            transition_employee_document(inactive_document, "verify", self.manager)
        self.assertFalse(EmployeeDocumentHistory.objects.exists())

    def test_due_documents_expire_in_bounded_batches_once(self):
        due_first = self._document(expiry_date=self.today - timedelta(days=1))
        due_today = self._document(expiry_date=self.today)
        due_other_company = self._document(
            employee=self.foreign_employee, expiry_date=self.today
        )
        future = self._document(expiry_date=self.today + timedelta(days=1))
        replaced = self._document(expiry_date=self.today - timedelta(days=1))
        replaced.status = EmployeeDocument.Status.REPLACED
        replaced.save(update_fields=["status", "updated_at"])
        first_count = expire_employee_documents(today=self.today, batch_size=2, max_batches=1)
        self.assertEqual(first_count, 2)
        self.assertEqual(expire_employee_documents(today=self.today, batch_size=2, max_batches=1), 1)
        self.assertEqual(expire_employee_documents(today=self.today, batch_size=2, max_batches=1), 0)
        for document in (due_first, due_today, due_other_company):
            document.refresh_from_db()
            self.assertEqual(document.status, EmployeeDocument.Status.EXPIRED)
            history = document.history.get()
            self.assertEqual((history.action, history.previous_status, history.new_status),
                             ("auto_expire", "active", "expired"))
            self.assertIsNone(history.actor_id)
        future.refresh_from_db()
        replaced.refresh_from_db()
        self.assertEqual(future.status, EmployeeDocument.Status.ACTIVE)
        self.assertEqual(replaced.status, EmployeeDocument.Status.REPLACED)
        self.assertEqual(EmployeeDocumentHistory.objects.count(), 3)

    def test_document_expiry_query_count_is_fixed_for_two_and_twelve(self):
        for _number in range(2):
            self._document(expiry_date=self.today)
        with CaptureQueriesContext(connection) as small:
            self.assertEqual(expire_employee_documents(today=self.today, batch_size=100, max_batches=1), 2)
        for _number in range(12):
            self._document(expiry_date=self.today)
        with CaptureQueriesContext(connection) as large:
            self.assertEqual(expire_employee_documents(today=self.today, batch_size=100, max_batches=1), 12)
        self.assertEqual(len(small), len(large))
