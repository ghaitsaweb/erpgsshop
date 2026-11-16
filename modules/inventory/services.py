from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import StockMove, Warehouse

from .models import (
    InventoryBalanceEvent,
    InventoryLocation,
    InventoryReorderRule,
    InventoryReplenishmentSnapshot,
    InventoryStockBalance,
)


ZERO = Decimal("0")
MAX_STOCK_MOVE_SYNC_BATCH = 1000
MAX_REPLENISHMENT_RULE_BATCH = 250
DEFAULT_LOCATION_ROOT = "Stock"
DEFAULT_LOCATION_NAME = "Main Stock"


def _require_bounded_moves(stock_moves):
    move_ids = sorted({move.pk for move in stock_moves if move.pk})
    if len(move_ids) > MAX_STOCK_MOVE_SYNC_BATCH:
        raise ValidationError(
            f"Inventory balance synchronization supports at most "
            f"{MAX_STOCK_MOVE_SYNC_BATCH} stock moves per transaction."
        )
    if not move_ids and stock_moves:
        raise ValidationError("Every stock move must be saved before balance synchronization.")
    return move_ids


def ensure_default_internal_locations(warehouse_ids):
    """Return one deterministic internal location per warehouse using fixed queries."""
    warehouse_ids = sorted({int(value) for value in warehouse_ids if value})
    if not warehouse_ids:
        return {}
    warehouses = Warehouse.objects.filter(pk__in=warehouse_ids).select_related(
        "company", "branch"
    ).in_bulk()
    if set(warehouses) != set(warehouse_ids):
        raise ValidationError("Every stock move warehouse must still exist.")

    roots = {
        row.warehouse_id: row
        for row in InventoryLocation.objects.filter(
            warehouse_id__in=warehouse_ids,
            parent__isnull=True,
            name=DEFAULT_LOCATION_ROOT,
        )
    }
    missing_roots = []
    for warehouse_id in warehouse_ids:
        if warehouse_id in roots:
            continue
        warehouse = warehouses[warehouse_id]
        missing_roots.append(
            InventoryLocation(
                company_id=warehouse.company_id,
                branch_id=warehouse.branch_id,
                warehouse_id=warehouse_id,
                name=DEFAULT_LOCATION_ROOT,
                complete_name=DEFAULT_LOCATION_ROOT,
                ancestor_ids=[],
                usage=InventoryLocation.Usage.VIEW,
                sequence=0,
            )
        )
    if missing_roots:
        InventoryLocation.objects.bulk_create(
            missing_roots,
            ignore_conflicts=True,
            batch_size=500,
        )
        roots = {
            row.warehouse_id: row
            for row in InventoryLocation.objects.filter(
                warehouse_id__in=warehouse_ids,
                parent__isnull=True,
                name=DEFAULT_LOCATION_ROOT,
            )
        }

    root_ids = [row.pk for row in roots.values()]
    internal_by_warehouse = {
        row.warehouse_id: row
        for row in InventoryLocation.objects.filter(
            warehouse_id__in=warehouse_ids,
            parent_id__in=root_ids,
            name=DEFAULT_LOCATION_NAME,
        )
    }
    missing_internal = []
    for warehouse_id in warehouse_ids:
        if warehouse_id in internal_by_warehouse:
            continue
        warehouse = warehouses[warehouse_id]
        root = roots[warehouse_id]
        missing_internal.append(
            InventoryLocation(
                company_id=warehouse.company_id,
                branch_id=warehouse.branch_id,
                warehouse_id=warehouse_id,
                parent_id=root.pk,
                name=DEFAULT_LOCATION_NAME,
                complete_name=f"{root.complete_name} / {DEFAULT_LOCATION_NAME}",
                ancestor_ids=[root.pk],
                usage=InventoryLocation.Usage.INTERNAL,
                sequence=10,
            )
        )
    if missing_internal:
        InventoryLocation.objects.bulk_create(
            missing_internal,
            ignore_conflicts=True,
            batch_size=500,
        )
        internal_by_warehouse = {
            row.warehouse_id: row
            for row in InventoryLocation.objects.filter(
                warehouse_id__in=warehouse_ids,
                parent_id__in=root_ids,
                name=DEFAULT_LOCATION_NAME,
            )
        }
    if set(internal_by_warehouse) != set(warehouse_ids):
        raise ValidationError("A default internal location could not be resolved for every warehouse.")
    return internal_by_warehouse


@transaction.atomic
def apply_stock_moves_to_balances(stock_moves, *, allow_negative=False):
    """Apply immutable core stock moves once to HMX-style warehouse balances."""
    supplied = list(stock_moves)
    move_ids = _require_bounded_moves(supplied)
    if not move_ids:
        return 0
    locked_moves = list(
        StockMove.objects.select_for_update(of=("self",))
        .filter(pk__in=move_ids)
        .select_related("product", "warehouse")
        .order_by("pk")
    )
    if len(locked_moves) != len(move_ids):
        raise ValidationError("One or more stock moves no longer exist.")
    processed_ids = set(
        InventoryBalanceEvent.objects.filter(stock_move_id__in=move_ids).values_list(
            "stock_move_id", flat=True
        )
    )
    pending = [move for move in locked_moves if move.pk not in processed_ids]
    if not pending:
        return 0

    locations = ensure_default_internal_locations(move.warehouse_id for move in pending)
    keys = {(move.product_id, locations[move.warehouse_id].pk) for move in pending}
    product_ids = sorted({key[0] for key in keys})
    location_ids = sorted({key[1] for key in keys})
    locations_by_id = {row.pk: row for row in locations.values()}

    balances = {
        (row.product_id, row.location_id): row
        for row in InventoryStockBalance.objects.select_for_update(of=("self",)).filter(
            product_id__in=product_ids,
            location_id__in=location_ids,
            lot__isnull=True,
            ownership_type=InventoryStockBalance.OwnershipType.COMPANY,
            owner_vendor__isnull=True,
        )
    }
    missing_balances = []
    for product_id, location_id in sorted(keys):
        if (product_id, location_id) in balances:
            continue
        location = locations_by_id[location_id]
        missing_balances.append(
            InventoryStockBalance(
                company_id=location.company_id,
                product_id=product_id,
                warehouse_id=location.warehouse_id,
                location_id=location_id,
                qty_on_hand=ZERO,
                qty_reserved=ZERO,
                qty_available=ZERO,
            )
        )
    if missing_balances:
        InventoryStockBalance.objects.bulk_create(
            missing_balances,
            ignore_conflicts=True,
            batch_size=500,
        )
        balances = {
            (row.product_id, row.location_id): row
            for row in InventoryStockBalance.objects.select_for_update(of=("self",)).filter(
                product_id__in=product_ids,
                location_id__in=location_ids,
                lot__isnull=True,
                ownership_type=InventoryStockBalance.OwnershipType.COMPANY,
                owner_vendor__isnull=True,
            )
        }

    deltas = defaultdict(Decimal)
    latest_move = {}
    earliest_incoming = {}
    moves_by_key = {}
    for move in pending:
        location = locations[move.warehouse_id]
        key = (move.product_id, location.pk)
        if move.company_id != location.company_id or move.product.company_id != location.company_id:
            raise ValidationError("Stock move product, warehouse, and company must use one company scope.")
        delta = move.quantity if move.direction == StockMove.Direction.IN else -move.quantity
        deltas[key] += delta
        latest_move[key] = move
        moves_by_key[move.pk] = key
        if move.direction == StockMove.Direction.IN:
            current = earliest_incoming.get(key)
            earliest_incoming[key] = min(current, move.movement_date) if current else move.movement_date

    now = timezone.now()
    changed = []
    for key, delta in deltas.items():
        balance = balances[key]
        resulting = balance.qty_on_hand + delta
        if not allow_negative and resulting < 0:
            raise ValidationError(
                f"Insufficient stock for {balance.product} in {balance.warehouse}. "
                f"Available: {balance.qty_on_hand}."
            )
        balance.qty_on_hand = resulting
        balance.qty_available = resulting - balance.qty_reserved
        balance.last_move_id = latest_move[key].pk
        if not balance.incoming_date and key in earliest_incoming:
            balance.incoming_date = earliest_incoming[key]
        balance.updated_at = now
        changed.append(balance)
    InventoryStockBalance.objects.bulk_update(
        changed,
        ["qty_on_hand", "qty_available", "last_move", "incoming_date", "updated_at"],
        batch_size=500,
    )
    InventoryBalanceEvent.objects.bulk_create(
        [
            InventoryBalanceEvent(
                stock_move_id=move.pk,
                balance=balances[moves_by_key[move.pk]],
                direction=move.direction,
                quantity=move.quantity,
            )
            for move in pending
        ],
        batch_size=500,
    )
    return len(pending)


def _require_bounded_reorder_rules(reorder_rules):
    rule_ids = sorted({rule.pk for rule in reorder_rules if rule.pk})
    if len(rule_ids) > MAX_REPLENISHMENT_RULE_BATCH:
        raise ValidationError(
            f"Inventory replenishment evaluation supports at most "
            f"{MAX_REPLENISHMENT_RULE_BATCH} rules per transaction."
        )
    if not rule_ids and reorder_rules:
        raise ValidationError("Every reorder rule must be saved before evaluation.")
    return rule_ids


@transaction.atomic
def evaluate_replenishment_rules(reorder_rules, *, as_of=None):
    """Refresh read-only replenishment snapshots with fixed-query aggregation."""
    supplied = list(reorder_rules)
    rule_ids = _require_bounded_reorder_rules(supplied)
    if not rule_ids:
        return 0
    rules = list(
        InventoryReorderRule.objects.select_for_update(of=("self",))
        .filter(pk__in=rule_ids)
        .select_related("product", "warehouse", "source_warehouse", "vendor")
        .order_by("pk")
    )
    if len(rules) != len(rule_ids):
        raise ValidationError("One or more reorder rules no longer exist.")

    pair_filter = Q(pk__in=[])
    for rule in rules:
        pair_filter |= Q(product_id=rule.product_id, warehouse_id=rule.warehouse_id)
    available_by_pair = {
        (row["product_id"], row["warehouse_id"]): row["available"] or ZERO
        # scale-guard: allow SG002 — grouped output is bounded by 250 validated rule pairs
        for row in InventoryStockBalance.objects.filter(pair_filter)
        .order_by()
        .values("product_id", "warehouse_id")
        .annotate(available=Sum("qty_available"))
    }
    snapshots = {
        row.rule_id: row
        for row in InventoryReplenishmentSnapshot.objects.select_for_update(of=("self",))
        .filter(rule_id__in=rule_ids)
        .order_by("rule_id")
    }

    evaluated_at = timezone.now()
    evaluation_date = as_of or timezone.localdate()
    due_count = 0
    new_snapshots = []
    changed_snapshots = []
    for rule in rules:
        available = available_by_pair.get((rule.product_id, rule.warehouse_id), ZERO)
        if not rule.active:
            status = InventoryReplenishmentSnapshot.Status.INACTIVE
            suggested = ZERO
        elif available <= rule.minimum_quantity:
            status = InventoryReplenishmentSnapshot.Status.DUE
            suggested = (
                rule.reorder_quantity
                if rule.reorder_quantity > ZERO
                else max(rule.maximum_quantity - available, ZERO)
            )
            due_count += 1
        else:
            status = InventoryReplenishmentSnapshot.Status.HEALTHY
            suggested = ZERO
        values = {
            "company_id": rule.company_id,
            "branch_id": rule.branch_id,
            "product_id": rule.product_id,
            "warehouse_id": rule.warehouse_id,
            "route": rule.route,
            "source_warehouse_id": rule.source_warehouse_id,
            "vendor_id": rule.vendor_id,
            "quantity_available": available,
            "minimum_quantity": rule.minimum_quantity,
            "maximum_quantity": rule.maximum_quantity,
            "suggested_quantity": suggested,
            "need_by_date": evaluation_date + timedelta(days=rule.lead_time_days),
            "status": status,
            "evaluated_at": evaluated_at,
        }
        snapshot = snapshots.get(rule.pk)
        if snapshot is None:
            new_snapshots.append(
                InventoryReplenishmentSnapshot(rule_id=rule.pk, **values)
            )
        else:
            for field_name, value in values.items():
                setattr(snapshot, field_name, value)
            snapshot.updated_at = evaluated_at
            changed_snapshots.append(snapshot)
    if new_snapshots:
        InventoryReplenishmentSnapshot.objects.bulk_create(
            new_snapshots,
            batch_size=MAX_REPLENISHMENT_RULE_BATCH,
        )
    if changed_snapshots:
        InventoryReplenishmentSnapshot.objects.bulk_update(
            changed_snapshots,
            [
                "company",
                "branch",
                "product",
                "warehouse",
                "route",
                "source_warehouse",
                "vendor",
                "quantity_available",
                "minimum_quantity",
                "maximum_quantity",
                "suggested_quantity",
                "need_by_date",
                "status",
                "evaluated_at",
                "updated_at",
            ],
            batch_size=MAX_REPLENISHMENT_RULE_BATCH,
        )
    return due_count
