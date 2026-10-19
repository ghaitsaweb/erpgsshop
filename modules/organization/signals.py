from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import Company

from .models import Branch


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
