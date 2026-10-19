from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.models import Company

from .context import get_current_context
from .models import Branch
from .record_scope import assign_record_scope, delete_record_scope


def main_branch_code(company):
    base = (company.code or f"COMPANY-{company.pk}").strip().upper()
    return f"{base}-MAIN"[:30]


@receiver(post_save, sender=Company, dispatch_uid="organization_create_main_branch")
def create_main_branch(sender, instance, created, **kwargs):
    if not created:
        return
    Branch.objects.get_or_create(
        company=instance,
        is_main=True,
        defaults={
            "code": main_branch_code(instance),
            "name": f"{instance.name} Main Branch",
            "active": instance.active,
            "sequence": 0,
        },
    )


@receiver(post_save, dispatch_uid="organization.assign_record_scope")
def persist_record_organization_scope(sender, instance, raw=False, **kwargs):
    if raw:
        return
    assign_record_scope(instance, context=get_current_context())


@receiver(post_delete, dispatch_uid="organization.delete_record_scope")
def remove_record_organization_scope(sender, instance, **kwargs):
    delete_record_scope(instance)
