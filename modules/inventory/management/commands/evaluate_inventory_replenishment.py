from django.core.management.base import BaseCommand, CommandError

from modules.inventory.models import InventoryReorderRule
from modules.inventory.services import (
    MAX_REPLENISHMENT_RULE_BATCH,
    evaluate_replenishment_rules,
)


class Command(BaseCommand):
    help = "Refresh Inventory Replenishment Review from active and inactive reorder rules."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            help=f"Rules per transaction (maximum {MAX_REPLENISHMENT_RULE_BATCH}).",
        )
        parser.add_argument(
            "--after-id",
            type=int,
            default=0,
            help="Only evaluate Reordering Rules with a larger primary key.",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size < 1 or batch_size > MAX_REPLENISHMENT_RULE_BATCH:
            raise CommandError(
                f"--batch-size must be between 1 and {MAX_REPLENISHMENT_RULE_BATCH}."
            )
        cursor = max(options["after_id"], 0)
        evaluated = 0
        due = 0
        while True:
            # scale-guard: allow SG001 — complete evaluation uses bounded keyset batches
            rules = list(
                InventoryReorderRule.objects.filter(pk__gt=cursor)
                .select_related("product", "warehouse", "source_warehouse", "vendor")
                .order_by("pk")[:batch_size]
            )
            if not rules:
                break
            due += evaluate_replenishment_rules(rules)
            evaluated += len(rules)
            cursor = rules[-1].pk
            self.stdout.write(f"Evaluated through Reordering Rule {cursor}.")
        self.stdout.write(
            self.style.SUCCESS(
                f"Inventory replenishment evaluation complete: {evaluated} rules, {due} due."
            )
        )
