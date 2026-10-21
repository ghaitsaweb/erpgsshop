from django.apps import AppConfig
from django.db.models.signals import post_migrate


class ApprovalWorkflowConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.approval_workflow"
    label = "approval_workflow"
    verbose_name = "GSSHOP Approval Workflow"

    def ready(self):
        from .security import ensure_approval_roles

        post_migrate.connect(
            ensure_approval_roles,
            sender=self,
            dispatch_uid="approval_workflow.ensure_roles",
        )
