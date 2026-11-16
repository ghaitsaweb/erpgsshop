from django.core.management.base import BaseCommand, CommandError

from core.models import StockMove
from modules.inventory.services import (
    MAX_STOCK_MOVE_SYNC_BATCH,
    apply_stock_moves_to_balances,
)


class Command(BaseCommand):
    help = "Idempotently replay immutable Stock Moves into Inventory Stock Balances."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help=f"Moves per transaction (maximum {MAX_STOCK_MOVE_SYNC_BATCH}).",
        )
        parser.add_argument(
            "--after-id",
            type=int,
            default=0,
            help="Only process Stock Moves with a larger primary key.",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size < 1 or batch_size > MAX_STOCK_MOVE_SYNC_BATCH:
            raise CommandError(
                f"--batch-size must be between 1 and {MAX_STOCK_MOVE_SYNC_BATCH}."
            )
        cursor = max(options["after_id"], 0)
        processed = 0
        while True:
            # scale-guard: allow SG001 — complete-data replay uses bounded keyset batches
            moves = list(
                StockMove.objects.filter(pk__gt=cursor)
                .select_related("product", "warehouse")
                .order_by("pk")[:batch_size]
            )
            if not moves:
                break
            processed += apply_stock_moves_to_balances(moves)
            cursor = moves[-1].pk
            self.stdout.write(f"Processed through Stock Move {cursor}.")
        self.stdout.write(self.style.SUCCESS(f"Inventory balance replay complete: {processed} new moves."))
