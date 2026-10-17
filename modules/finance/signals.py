from decimal import Decimal

from django.db.models import Sum
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .localization_packs import provision_country_profile
from .models import CountryTaxProfile, JournalLine, POSOrderAccounting


@receiver([post_save, post_delete], sender=JournalLine)
def update_journal_totals(sender, instance, **kwargs):
    totals = instance.entry.lines.aggregate(debit=Sum("debit"), credit=Sum("credit"))
    instance.entry.__class__.objects.filter(pk=instance.entry_id).update(
        total_debit=totals["debit"] or Decimal("0"),
        total_credit=totals["credit"] or Decimal("0"),
    )


@receiver(post_save, sender=CountryTaxProfile, dispatch_uid="gsshop_finance.provision_localization_catalog")
def provision_localization_catalog(sender, instance, created, **kwargs):
    if not created:
        return
    provision_country_profile(instance)


def _connect_inventory_accounting():
    from core.models import StockMove
    from .services import post_inventory_stock_move

    @receiver(post_save, sender=StockMove, dispatch_uid="gsshop_finance.post_inventory_stock_move")
    def create_inventory_accounting(sender, instance, created, **kwargs):
        if created:
            post_inventory_stock_move(instance)


_connect_inventory_accounting()


def _connect_pos_order_accounting():
    from modules.pos.models import POSOrder

    @receiver(post_save, sender=POSOrder, dispatch_uid="gsshop_finance.prepare_pos_order_accounting")
    def prepare_pos_order_accounting(sender, instance, **kwargs):
        if instance.status == POSOrder.Status.PAID:
            POSOrderAccounting.objects.get_or_create(order_id=instance.pk)


_connect_pos_order_accounting()
