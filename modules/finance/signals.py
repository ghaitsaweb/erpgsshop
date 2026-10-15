from decimal import Decimal

from django.db.models import Sum
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import JournalLine


@receiver([post_save, post_delete], sender=JournalLine)
def update_journal_totals(sender, instance, **kwargs):
    totals = instance.entry.lines.aggregate(debit=Sum("debit"), credit=Sum("credit"))
    instance.entry.__class__.objects.filter(pk=instance.entry_id).update(
        total_debit=totals["debit"] or Decimal("0"),
        total_credit=totals["credit"] or Decimal("0"),
    )

