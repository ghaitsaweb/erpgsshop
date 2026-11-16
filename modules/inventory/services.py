from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.models import Product, StockMove, Warehouse

from .models import (
    InventoryBalanceEvent,
    InventoryLocation,
    InventoryLotSerial,
    InventoryPicking,
    InventoryPickingLine,
    InventoryReorderRule,
    InventoryReservation,
    InventoryReservationAllocation,
    InventoryReplenishmentSnapshot,
    InventoryStockCount,
    InventoryStockCountLine,
    InventoryStockFreeze,
    InventoryStockBalance,
)


ZERO = Decimal("0")
MAX_STOCK_MOVE_SYNC_BATCH = 1000
MAX_REPLENISHMENT_RULE_BATCH = 250
MAX_STOCK_COUNT_LINES = 1000
MAX_STOCK_COUNT_LOCATIONS = 100
MAX_STOCK_COUNT_PRODUCTS = 1000
MAX_RESERVATION_CANDIDATES = 1000
MAX_RESERVATION_BATCH = 250
MAX_BATCH_RESERVATION_CANDIDATES = 5000
MAX_PICKING_LINES = 250
DEFAULT_LOCATION_ROOT = "Stock"
DEFAULT_LOCATION_NAME = "Main Stock"


def _assert_warehouses_not_frozen(warehouse_ids):
    frozen = list(
        InventoryStockFreeze.objects.filter(warehouse_id__in=sorted(set(warehouse_ids)))
        .select_related("stock_count", "warehouse")
        .order_by("warehouse_id")[:1]
    )
    if frozen:
        lock = frozen[0]
        raise ValidationError(
            f"Warehouse {lock.warehouse} is locked by Stock Count {lock.stock_count}."
        )


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

    frozen_warehouse_ids = {move.warehouse_id for move in pending}
    _assert_warehouses_not_frozen(frozen_warehouse_ids)
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

    # Recheck after balance locks. A Stock Count can freeze the warehouse while
    # this transaction waits for a balance row held by another writer.
    _assert_warehouses_not_frozen(frozen_warehouse_ids)

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
        if not allow_negative and resulting < balance.qty_reserved:
            raise ValidationError(
                f"Insufficient stock for {balance.product} in {balance.warehouse}. "
                f"Available: {balance.qty_available}."
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


def _bounded_stock_count_locations(stock_count):
    locations = list(
        stock_count.locations.select_related("warehouse")
        .order_by("pk")[: MAX_STOCK_COUNT_LOCATIONS + 1]
    )
    if len(locations) > MAX_STOCK_COUNT_LOCATIONS:
        raise ValidationError(
            f"A Stock Count supports at most {MAX_STOCK_COUNT_LOCATIONS} selected locations."
        )
    invalid = [
        location
        for location in locations
        if location.warehouse_id != stock_count.warehouse_id
        or location.company_id != stock_count.company_id
    ]
    if invalid:
        raise ValidationError("Every selected location must belong to the Stock Count warehouse.")
    return locations


def _bounded_stock_count_products(stock_count):
    product_ids = list(
        stock_count.products.order_by("pk").values_list("pk", flat=True)[
            : MAX_STOCK_COUNT_PRODUCTS + 1
        ]
    )
    if len(product_ids) > MAX_STOCK_COUNT_PRODUCTS:
        raise ValidationError(
            f"A Stock Count supports at most {MAX_STOCK_COUNT_PRODUCTS} selected products."
        )
    if stock_count.inventoried_product == InventoryStockCount.ProductScope.SELECTED:
        if not product_ids:
            raise ValidationError("Select at least one product for a Specific Products Stock Count.")
        company_product_count = Product.objects.filter(
            pk__in=product_ids,
            company_id=stock_count.company_id,
            product_type=Product.ProductType.STOCKABLE,
        ).count()
        if company_product_count != len(product_ids):
            raise ValidationError(
                "Every selected product must be a stockable product in the Stock Count company."
            )
    return product_ids


def _location_scope_filter(locations):
    if not locations:
        return Q()
    scope = Q(pk__in=[])
    for location in locations:
        scope |= Q(location_id=location.pk) | Q(
            location__ancestor_ids__contains=[location.pk]
        )
    return scope


def _zero_stock_anchor(stock_count, locations):
    if len(locations) == 1 and locations[0].usage == InventoryLocation.Usage.INTERNAL:
        return locations[0]
    if locations:
        scope = Q(pk__in=[])
        for location in locations:
            scope |= Q(pk=location.pk) | Q(ancestor_ids__contains=[location.pk])
        anchor = (
            InventoryLocation.objects.filter(
                scope,
                warehouse_id=stock_count.warehouse_id,
                usage=InventoryLocation.Usage.INTERNAL,
                active=True,
            )
            .order_by("sequence", "pk")
            .first()
        )
        if anchor:
            return anchor
    return ensure_default_internal_locations([stock_count.warehouse_id])[
        stock_count.warehouse_id
    ]


@transaction.atomic
def confirm_stock_count(stock_count, actor):
    """Freeze one warehouse and create a bounded physical-count snapshot."""
    if actor is None:
        raise ValidationError("A user is required to confirm a Stock Count.")
    stock_count_id = getattr(stock_count, "pk", stock_count)
    locked = (
        InventoryStockCount.objects.select_for_update(of=("self",))
        .select_related("company", "branch", "warehouse")
        .get(pk=stock_count_id)
    )
    if locked.status != InventoryStockCount.Status.DRAFT:
        raise ValidationError("Only Draft Stock Counts can be confirmed.")
    Warehouse.objects.select_for_update(of=("self",)).get(pk=locked.warehouse_id)
    if InventoryStockFreeze.objects.filter(warehouse_id=locked.warehouse_id).exists():
        raise ValidationError("This warehouse is already locked by another Stock Count.")

    locations = _bounded_stock_count_locations(locked)
    product_ids = _bounded_stock_count_products(locked)
    InventoryStockFreeze.objects.create(
        company_id=locked.company_id,
        warehouse_id=locked.warehouse_id,
        stock_count=locked,
        location_ids=[location.pk for location in locations],
        product_ids=(
            product_ids
            if locked.inventoried_product == InventoryStockCount.ProductScope.SELECTED
            else []
        ),
    )

    balances = InventoryStockBalance.objects.select_for_update(of=("self",)).filter(
        company_id=locked.company_id,
        warehouse_id=locked.warehouse_id,
        location__usage=InventoryLocation.Usage.INTERNAL,
        ownership_type=InventoryStockBalance.OwnershipType.COMPANY,
        owner_vendor__isnull=True,
    ).exclude(qty_on_hand=ZERO)
    if locations:
        balances = balances.filter(_location_scope_filter(locations))
    if locked.inventoried_product == InventoryStockCount.ProductScope.SELECTED:
        balances = balances.filter(product_id__in=product_ids)
    snapshot_balances = list(
        balances.select_related("product", "location", "lot")
        .order_by("product_id", "location_id", "lot_id", "pk")[: MAX_STOCK_COUNT_LINES + 1]
    )
    if len(snapshot_balances) > MAX_STOCK_COUNT_LINES:
        raise ValidationError(
            f"This Stock Count would create more than {MAX_STOCK_COUNT_LINES} lines. "
            "Narrow the location or product scope."
        )

    lines = []
    for balance in snapshot_balances:
        counted = (
            ZERO
            if locked.counted_qty_default == InventoryStockCount.CountedDefault.ZERO
            else balance.qty_on_hand
        )
        lines.append(
            InventoryStockCountLine(
                stock_count=locked,
                product_id=balance.product_id,
                location_id=balance.location_id,
                lot_id=balance.lot_id,
                system_qty=balance.qty_on_hand,
                counted_qty=counted,
                difference_qty=counted - balance.qty_on_hand,
                line_source=InventoryStockCountLine.LineSource.BALANCE,
            )
        )

    if locked.include_zero_stock_product:
        remaining_capacity = MAX_STOCK_COUNT_LINES - len(lines)
        present_product_ids = {balance.product_id for balance in snapshot_balances}
        zero_products = Product.objects.filter(
            company_id=locked.company_id,
            product_type=Product.ProductType.STOCKABLE,
            active=True,
        ).exclude(pk__in=present_product_ids)
        if locked.inventoried_product == InventoryStockCount.ProductScope.SELECTED:
            zero_products = zero_products.filter(pk__in=product_ids)
        zero_product_ids = list(
            zero_products.order_by("pk").values_list("pk", flat=True)[
                : remaining_capacity + 1
            ]
        )
        if len(zero_product_ids) > remaining_capacity:
            raise ValidationError(
                f"This Stock Count would create more than {MAX_STOCK_COUNT_LINES} lines. "
                "Narrow the product scope."
            )
        anchor = _zero_stock_anchor(locked, locations)
        lines.extend(
            InventoryStockCountLine(
                stock_count=locked,
                product_id=product_id,
                location_id=anchor.pk,
                system_qty=ZERO,
                counted_qty=ZERO,
                difference_qty=ZERO,
                line_source=InventoryStockCountLine.LineSource.NO_STOCK,
            )
            for product_id in zero_product_ids
        )

    if not lines:
        raise ValidationError("The selected Stock Count scope contains no countable products.")
    InventoryStockCountLine.objects.bulk_create(lines, batch_size=500)
    now = timezone.now()
    locked.status = InventoryStockCount.Status.IN_PROGRESS
    locked.confirmed_by = actor
    locked.confirmed_at = now
    locked.save(
        update_fields=["status", "confirmed_by", "confirmed_at", "updated_at"]
    )
    return locked


@transaction.atomic
def complete_stock_count(stock_count, actor):
    if actor is None:
        raise ValidationError("A user is required to complete a Stock Count.")
    stock_count_id = getattr(stock_count, "pk", stock_count)
    locked = InventoryStockCount.objects.select_for_update(of=("self",)).get(
        pk=stock_count_id
    )
    if locked.status != InventoryStockCount.Status.IN_PROGRESS:
        raise ValidationError("Only In Progress Stock Counts can be completed.")
    if not InventoryStockFreeze.objects.filter(stock_count=locked).exists():
        raise ValidationError("The Stock Count warehouse freeze is missing.")
    lines = list(
        locked.lines.select_related("lot")
        .order_by("pk")[: MAX_STOCK_COUNT_LINES + 1]
    )
    if not lines:
        raise ValidationError("Add counting lines before completing the Stock Count.")
    if len(lines) > MAX_STOCK_COUNT_LINES:
        raise ValidationError("The Stock Count exceeds the supported line limit.")
    for line in lines:
        if line.counted_qty < ZERO:
            raise ValidationError("Counted quantity cannot be negative.")
        if (
            line.lot_id
            and line.lot.tracking_type == InventoryLotSerial.TrackingType.SERIAL
            and line.counted_qty > 1
        ):
            raise ValidationError("A serial-number count cannot exceed one unit.")
    now = timezone.now()
    locked.status = InventoryStockCount.Status.COMPLETED
    locked.completed_by = actor
    locked.completed_at = now
    locked.save(
        update_fields=["status", "completed_by", "completed_at", "updated_at"]
    )
    return locked


def _stock_count_position_filter(lines):
    position_filter = Q(pk__in=[])
    for line in lines:
        lot_filter = Q(lot_id=line.lot_id) if line.lot_id else Q(lot__isnull=True)
        position_filter |= Q(
            product_id=line.product_id,
            location_id=line.location_id,
        ) & lot_filter
    return position_filter


@transaction.atomic
def validate_stock_count(stock_count, actor):
    """Post exact location/lot variances once and release the warehouse freeze."""
    if actor is None:
        raise ValidationError("A user is required to validate a Stock Count.")
    stock_count_id = getattr(stock_count, "pk", stock_count)
    locked = (
        InventoryStockCount.objects.select_for_update(of=("self",))
        .select_related("warehouse")
        .get(pk=stock_count_id)
    )
    if locked.status != InventoryStockCount.Status.COMPLETED:
        raise ValidationError("Only Completed Stock Counts can be validated.")
    Warehouse.objects.select_for_update(of=("self",)).get(pk=locked.warehouse_id)
    freeze = InventoryStockFreeze.objects.select_for_update(of=("self",)).filter(
        stock_count=locked,
        warehouse_id=locked.warehouse_id,
    ).first()
    if freeze is None:
        raise ValidationError("The Stock Count warehouse freeze is missing.")
    lines = list(
        locked.lines.select_for_update(of=("self",))
        .select_related("product", "location", "lot")
        .order_by("pk")[: MAX_STOCK_COUNT_LINES + 1]
    )
    if not lines:
        raise ValidationError("The Stock Count has no counting lines.")
    if len(lines) > MAX_STOCK_COUNT_LINES:
        raise ValidationError("The Stock Count exceeds the supported line limit.")
    if any(line.adjustment_stock_move_id for line in lines):
        raise ValidationError("This Stock Count already contains posted adjustments.")

    position_filter = _stock_count_position_filter(lines)
    balance_queryset = InventoryStockBalance.objects.select_for_update(of=("self",)).filter(
        position_filter,
        company_id=locked.company_id,
        warehouse_id=locked.warehouse_id,
        ownership_type=InventoryStockBalance.OwnershipType.COMPANY,
        owner_vendor__isnull=True,
    )
    balances = {
        (row.product_id, row.location_id, row.lot_id): row
        # scale-guard: allow SG002 — exact positions are bounded by 1,000 Stock Count lines
        for row in balance_queryset.order_by("pk")
    }
    missing = []
    for line in lines:
        key = (line.product_id, line.location_id, line.lot_id)
        if key in balances:
            continue
        if line.system_qty != ZERO:
            raise ValidationError(
                f"Stock changed after the snapshot for {line.product} at {line.location}."
            )
        missing.append(
            InventoryStockBalance(
                company_id=locked.company_id,
                product_id=line.product_id,
                warehouse_id=locked.warehouse_id,
                location_id=line.location_id,
                lot_id=line.lot_id,
                qty_on_hand=ZERO,
                qty_reserved=ZERO,
                qty_available=ZERO,
            )
        )
    if missing:
        InventoryStockBalance.objects.bulk_create(
            missing,
            ignore_conflicts=True,
            batch_size=500,
        )
        balances = {
            (row.product_id, row.location_id, row.lot_id): row
            # scale-guard: allow SG002 — exact positions are bounded by 1,000 Stock Count lines
            for row in InventoryStockBalance.objects.select_for_update(of=("self",))
            .filter(
                position_filter,
                company_id=locked.company_id,
                warehouse_id=locked.warehouse_id,
                ownership_type=InventoryStockBalance.OwnershipType.COMPANY,
                owner_vendor__isnull=True,
            )
            .order_by("pk")
        }

    adjustment_lines = []
    for line in lines:
        balance = balances[(line.product_id, line.location_id, line.lot_id)]
        if balance.qty_on_hand != line.system_qty:
            raise ValidationError(
                f"Stock changed after the snapshot for {line.product} at {line.location}."
            )
        if line.counted_qty < balance.qty_reserved:
            raise ValidationError(
                f"Counted quantity for {line.product} cannot be lower than its reserved quantity."
            )
        line.difference_qty = line.counted_qty - line.system_qty
        if line.difference_qty != ZERO:
            adjustment_lines.append(line)

    product_ids = sorted({line.product_id for line in adjustment_lines})
    products = Product.objects.select_for_update(of=("self",)).filter(
        pk__in=product_ids
    ).in_bulk()
    product_deltas = defaultdict(Decimal)
    for line in adjustment_lines:
        product_deltas[line.product_id] += line.difference_qty
    for product_id, delta in product_deltas.items():
        resulting = products[product_id].current_stock + delta
        if resulting < ZERO:
            raise ValidationError(
                f"Stock Count would make global stock negative for {products[product_id]}."
            )
        products[product_id].current_stock = resulting

    stock_moves = [
        StockMove(
            company_id=locked.company_id,
            product_id=line.product_id,
            warehouse_id=locked.warehouse_id,
            direction=(
                StockMove.Direction.IN
                if line.difference_qty > ZERO
                else StockMove.Direction.OUT
            ),
            quantity=abs(line.difference_qty),
            reference=locked.name,
            movement_date=timezone.localdate(),
            note=f"Physical Stock Count adjustment for {line.location}.",
            created_by=actor,
        )
        for line in adjustment_lines
    ]
    if stock_moves:
        StockMove.objects.bulk_create(stock_moves, batch_size=500)

    now = timezone.now()
    for product in products.values():
        product.updated_at = now
    changed_balances = []
    balance_by_move_id = {}
    for line, move in zip(adjustment_lines, stock_moves):
        balance = balances[(line.product_id, line.location_id, line.lot_id)]
        balance.qty_on_hand = line.counted_qty
        balance.qty_available = line.counted_qty - balance.qty_reserved
        balance.last_move_id = move.pk
        balance.updated_at = now
        line.adjustment_stock_move_id = move.pk
        line.updated_at = now
        changed_balances.append(balance)
        balance_by_move_id[move.pk] = balance
    if changed_balances:
        InventoryStockBalance.objects.bulk_update(
            changed_balances,
            ["qty_on_hand", "qty_available", "last_move", "updated_at"],
            batch_size=500,
        )
        Product.objects.bulk_update(
            list(products.values()),
            ["current_stock", "updated_at"],
            batch_size=500,
        )
        InventoryBalanceEvent.objects.bulk_create(
            [
                InventoryBalanceEvent(
                    stock_move=move,
                    balance=balance_by_move_id[move.pk],
                    direction=move.direction,
                    quantity=move.quantity,
                )
                for move in stock_moves
            ],
            batch_size=500,
        )
    InventoryStockCountLine.objects.bulk_update(
        lines,
        ["difference_qty", "adjustment_stock_move", "updated_at"],
        batch_size=500,
    )

    affected_lot_ids = sorted({line.lot_id for line in adjustment_lines if line.lot_id})
    if affected_lot_ids:
        lot_totals = {
            row["lot_id"]: row["quantity"] or ZERO
            for row in InventoryStockBalance.objects.filter(lot_id__in=affected_lot_ids)
            .order_by()
            .values("lot_id")
            .annotate(quantity=Sum("qty_on_hand"))
        }
        lots = list(
            InventoryLotSerial.objects.select_for_update(of=("self",))
            .filter(pk__in=affected_lot_ids)
            .order_by("pk")
        )
        for lot in lots:
            lot.quantity = lot_totals.get(lot.pk, ZERO)
            lot.updated_at = now
        InventoryLotSerial.objects.bulk_update(
            lots,
            ["quantity", "updated_at"],
            batch_size=500,
        )

    freeze.delete()
    locked.status = InventoryStockCount.Status.VALIDATED
    locked.validated_by = actor
    locked.validated_at = now
    locked.save(
        update_fields=["status", "validated_by", "validated_at", "updated_at"]
    )
    return locked


@transaction.atomic
def cancel_stock_count(stock_count, actor):
    if actor is None:
        raise ValidationError("A user is required to cancel a Stock Count.")
    stock_count_id = getattr(stock_count, "pk", stock_count)
    locked = InventoryStockCount.objects.select_for_update(of=("self",)).get(
        pk=stock_count_id
    )
    if locked.status not in {
        InventoryStockCount.Status.IN_PROGRESS,
        InventoryStockCount.Status.COMPLETED,
    }:
        raise ValidationError("Only In Progress or Completed Stock Counts can be cancelled.")
    InventoryStockFreeze.objects.filter(stock_count=locked).delete()
    now = timezone.now()
    locked.status = InventoryStockCount.Status.CANCELLED
    locked.cancelled_by = actor
    locked.cancelled_at = now
    locked.save(
        update_fields=["status", "cancelled_by", "cancelled_at", "updated_at"]
    )
    return locked


def confirm_stock_count_action(instance, actor):
    return confirm_stock_count(instance.pk, actor)


def complete_stock_count_action(instance, actor):
    return complete_stock_count(instance.pk, actor)


def validate_stock_count_action(instance, actor):
    return validate_stock_count(instance.pk, actor)


def cancel_stock_count_action(instance, actor):
    return cancel_stock_count(instance.pk, actor)


def _reservation_candidate_sort_key(balance):
    """Apply location priority first, then its configured removal strategy."""
    location = balance.location
    if location.removal_strategy == InventoryLocation.RemovalStrategy.FEFO:
        removal_date = (
            balance.lot.expiration_date
            if balance.lot_id and balance.lot.expiration_date
            else date.max
        )
    else:
        removal_date = balance.incoming_date or date.max
    return (location.sequence, location.pk, removal_date, balance.pk)


def _locked_reservation_allocations(reservation):
    allocations = list(
        InventoryReservationAllocation.objects.select_for_update(of=("self",))
        .filter(reservation=reservation)
        .order_by("balance_id", "pk")[: MAX_RESERVATION_CANDIDATES + 1]
    )
    if len(allocations) > MAX_RESERVATION_CANDIDATES:
        raise ValidationError(
            f"A Reservation supports at most {MAX_RESERVATION_CANDIDATES} allocations."
        )
    if not allocations:
        raise ValidationError("The Reservation has no stock allocations.")
    balance_ids = [allocation.balance_id for allocation in allocations]
    balances = {
        balance.pk: balance
        for balance in InventoryStockBalance.objects.select_for_update(of=("self",))
        .filter(pk__in=balance_ids)
        .select_related("location", "lot")
        .order_by("pk")
    }
    if len(balances) != len(set(balance_ids)):
        raise ValidationError("One or more reserved stock positions no longer exist.")
    return allocations, balances


@transaction.atomic
def reserve_inventory(reservation, actor):
    """Reserve company-owned stock once with deterministic row locking and FIFO/FEFO."""
    if actor is None:
        raise ValidationError("A user is required to reserve inventory.")
    reservation_id = getattr(reservation, "pk", reservation)
    locked = (
        InventoryReservation.objects.select_for_update(of=("self",))
        .select_related("product", "warehouse", "location", "lot")
        .get(pk=reservation_id)
    )
    if locked.status != InventoryReservation.Status.DRAFT:
        raise ValidationError("Only Draft Reservations can reserve inventory.")
    if locked.product.product_type != Product.ProductType.STOCKABLE:
        raise ValidationError("Only stockable products can be reserved.")

    # Stock Count confirmation also locks this warehouse row before creating its
    # freeze, so no reservation can cross the freeze boundary unnoticed.
    Warehouse.objects.select_for_update(of=("self",)).get(pk=locked.warehouse_id)
    _assert_warehouses_not_frozen([locked.warehouse_id])

    candidate_query = InventoryStockBalance.objects.filter(
        company_id=locked.company_id,
        warehouse_id=locked.warehouse_id,
        product_id=locked.product_id,
        ownership_type=InventoryStockBalance.OwnershipType.COMPANY,
        owner_vendor__isnull=True,
        location__usage=InventoryLocation.Usage.INTERNAL,
        location__active=True,
        location__is_expired_location=False,
        qty_available__gt=ZERO,
    ).filter(
        Q(lot__isnull=True)
        | Q(
            lot__status=InventoryLotSerial.Status.ACTIVE,
            lot__expiration_date__isnull=True,
        )
        | Q(
            lot__status=InventoryLotSerial.Status.ACTIVE,
            lot__expiration_date__gte=timezone.localdate(),
        )
    )
    if locked.location_id:
        candidate_query = candidate_query.filter(location_id=locked.location_id)
    if locked.lot_id:
        candidate_query = candidate_query.filter(lot_id=locked.lot_id)
    candidates = list(
        candidate_query.select_for_update(of=("self",))
        .select_related("location", "lot")
        .order_by("pk")[: MAX_RESERVATION_CANDIDATES + 1]
    )
    if len(candidates) > MAX_RESERVATION_CANDIDATES:
        raise ValidationError(
            f"Reservation allocation supports at most {MAX_RESERVATION_CANDIDATES} "
            "candidate stock positions. Narrow the location or lot scope."
        )
    _assert_warehouses_not_frozen([locked.warehouse_id])

    candidates.sort(key=_reservation_candidate_sort_key)
    remaining = locked.requested_quantity
    allocations = []
    changed_balances = []
    now = timezone.now()
    for balance in candidates:
        available = min(
            balance.qty_available,
            balance.qty_on_hand - balance.qty_reserved,
        )
        if available <= ZERO:
            continue
        quantity = min(remaining, available)
        balance.qty_reserved += quantity
        balance.qty_available = balance.qty_on_hand - balance.qty_reserved
        balance.updated_at = now
        changed_balances.append(balance)
        allocations.append(
            InventoryReservationAllocation(
                reservation=locked,
                balance=balance,
                quantity=quantity,
                allocated_at=now,
            )
        )
        remaining -= quantity
        if remaining <= ZERO:
            break

    reserved_quantity = locked.requested_quantity - remaining
    if reserved_quantity <= ZERO:
        raise ValidationError("No available stock matches this Reservation scope.")
    if remaining > ZERO and not locked.allow_partial:
        raise ValidationError(
            f"Insufficient available stock. Requested: {locked.requested_quantity}; "
            f"available: {reserved_quantity}. Enable partial reservation to continue."
        )

    InventoryStockBalance.objects.bulk_update(
        changed_balances,
        ["qty_reserved", "qty_available", "updated_at"],
        batch_size=500,
    )
    InventoryReservationAllocation.objects.bulk_create(
        allocations,
        batch_size=500,
    )
    locked.reserved_quantity = reserved_quantity
    locked.status = (
        InventoryReservation.Status.RESERVED
        if remaining <= ZERO
        else InventoryReservation.Status.PARTIAL
    )
    locked.reserved_by = actor
    locked.reserved_at = now
    locked.save(
        update_fields=[
            "reserved_quantity",
            "status",
            "reserved_by",
            "reserved_at",
            "updated_at",
        ]
    )
    return locked


@transaction.atomic
def release_inventory_reservation(reservation, actor):
    if actor is None:
        raise ValidationError("A user is required to release a Reservation.")
    reservation_id = getattr(reservation, "pk", reservation)
    locked = InventoryReservation.objects.select_for_update(of=("self",)).get(
        pk=reservation_id
    )
    if locked.status not in {
        InventoryReservation.Status.RESERVED,
        InventoryReservation.Status.PARTIAL,
    }:
        raise ValidationError("Only active Reservations can be released.")
    Warehouse.objects.select_for_update(of=("self",)).get(pk=locked.warehouse_id)
    _assert_warehouses_not_frozen([locked.warehouse_id])
    allocations, balances = _locked_reservation_allocations(locked)
    _assert_warehouses_not_frozen([locked.warehouse_id])

    now = timezone.now()
    for allocation in allocations:
        balance = balances[allocation.balance_id]
        if balance.qty_reserved < allocation.quantity:
            raise ValidationError("Reserved stock changed unexpectedly; release was rolled back.")
        balance.qty_reserved -= allocation.quantity
        balance.qty_available = balance.qty_on_hand - balance.qty_reserved
        balance.updated_at = now
    InventoryStockBalance.objects.bulk_update(
        list(balances.values()),
        ["qty_reserved", "qty_available", "updated_at"],
        batch_size=500,
    )
    locked.status = InventoryReservation.Status.RELEASED
    locked.released_by = actor
    locked.released_at = now
    locked.save(
        update_fields=["status", "released_by", "released_at", "updated_at"]
    )
    return locked


@transaction.atomic
def consume_inventory_reservation(reservation, actor):
    """Post one immutable outbound move per allocation and consume the reservation."""
    if actor is None:
        raise ValidationError("A user is required to consume a Reservation.")
    reservation_id = getattr(reservation, "pk", reservation)
    locked = (
        InventoryReservation.objects.select_for_update(of=("self",))
        .select_related("product")
        .get(pk=reservation_id)
    )
    if locked.status not in {
        InventoryReservation.Status.RESERVED,
        InventoryReservation.Status.PARTIAL,
    }:
        raise ValidationError("Only active Reservations can be consumed.")
    Warehouse.objects.select_for_update(of=("self",)).get(pk=locked.warehouse_id)
    _assert_warehouses_not_frozen([locked.warehouse_id])
    allocations, balances = _locked_reservation_allocations(locked)
    _assert_warehouses_not_frozen([locked.warehouse_id])

    total_quantity = sum(
        (allocation.quantity for allocation in allocations),
        ZERO,
    )
    if total_quantity != locked.reserved_quantity:
        raise ValidationError("Reservation allocation total no longer matches its reserved quantity.")
    for allocation in allocations:
        balance = balances[allocation.balance_id]
        if allocation.stock_move_id:
            raise ValidationError("This Reservation allocation was already consumed.")
        if (
            balance.company_id != locked.company_id
            or balance.product_id != locked.product_id
            or balance.warehouse_id != locked.warehouse_id
        ):
            raise ValidationError("A Reservation allocation moved outside its original scope.")
        if not balance.location.active or balance.location.is_expired_location:
            raise ValidationError("Reserved stock is in an inactive or expired location.")
        if balance.lot_id and (
            balance.lot.status != InventoryLotSerial.Status.ACTIVE
            or balance.lot.is_expired
        ):
            raise ValidationError("Reserved stock belongs to an inactive or expired lot/serial.")
        if (
            balance.qty_reserved < allocation.quantity
            or balance.qty_on_hand < allocation.quantity
        ):
            raise ValidationError("Reserved stock is no longer sufficient; consumption was rolled back.")

    product = Product.objects.select_for_update(of=("self",)).get(pk=locked.product_id)
    if product.current_stock < total_quantity:
        raise ValidationError(
            f"Global product stock is insufficient. Available: {product.current_stock}."
        )
    stock_moves = [
        StockMove(
            company_id=locked.company_id,
            product_id=locked.product_id,
            warehouse_id=locked.warehouse_id,
            direction=StockMove.Direction.OUT,
            quantity=allocation.quantity,
            reference=locked.reference,
            movement_date=timezone.localdate(),
            note=f"Consumed Inventory Reservation {locked.reference}.",
            created_by=actor,
        )
        for allocation in allocations
    ]
    StockMove.objects.bulk_create(stock_moves, batch_size=500)

    now = timezone.now()
    events = []
    affected_lot_ids = set()
    for allocation, move in zip(allocations, stock_moves):
        balance = balances[allocation.balance_id]
        balance.qty_on_hand -= allocation.quantity
        balance.qty_reserved -= allocation.quantity
        balance.qty_available = balance.qty_on_hand - balance.qty_reserved
        balance.last_move_id = move.pk
        balance.updated_at = now
        allocation.stock_move_id = move.pk
        if balance.lot_id:
            affected_lot_ids.add(balance.lot_id)
        events.append(
            InventoryBalanceEvent(
                stock_move=move,
                balance=balance,
                direction=StockMove.Direction.OUT,
                quantity=allocation.quantity,
            )
        )
    InventoryStockBalance.objects.bulk_update(
        list(balances.values()),
        ["qty_on_hand", "qty_reserved", "qty_available", "last_move", "updated_at"],
        batch_size=500,
    )
    InventoryBalanceEvent.objects.bulk_create(events, batch_size=500)
    InventoryReservationAllocation.objects.bulk_update(
        allocations,
        ["stock_move"],
        batch_size=500,
    )
    product.current_stock -= total_quantity
    product.updated_at = now
    Product.objects.bulk_update([product], ["current_stock", "updated_at"])

    if affected_lot_ids:
        lot_totals = {
            row["lot_id"]: row["quantity"] or ZERO
            for row in InventoryStockBalance.objects.filter(lot_id__in=affected_lot_ids)
            .order_by()
            .values("lot_id")
            .annotate(quantity=Sum("qty_on_hand"))
        }
        lots = list(
            InventoryLotSerial.objects.select_for_update(of=("self",))
            .filter(pk__in=affected_lot_ids)
            .order_by("pk")
        )
        for lot in lots:
            lot.quantity = lot_totals.get(lot.pk, ZERO)
            lot.status = (
                InventoryLotSerial.Status.ACTIVE
                if lot.quantity > ZERO
                else InventoryLotSerial.Status.OUT
            )
            lot.updated_at = now
        InventoryLotSerial.objects.bulk_update(
            lots,
            ["quantity", "status", "updated_at"],
            batch_size=500,
        )

    locked.status = InventoryReservation.Status.CONSUMED
    locked.consumed_by = actor
    locked.consumed_at = now
    locked.save(
        update_fields=["status", "consumed_by", "consumed_at", "updated_at"]
    )
    return locked


@transaction.atomic
def cancel_inventory_reservation(reservation, actor):
    if actor is None:
        raise ValidationError("A user is required to cancel a Reservation.")
    reservation_id = getattr(reservation, "pk", reservation)
    locked = InventoryReservation.objects.select_for_update(of=("self",)).get(
        pk=reservation_id
    )
    if locked.status != InventoryReservation.Status.DRAFT:
        raise ValidationError("Only Draft Reservations can be cancelled.")
    now = timezone.now()
    locked.status = InventoryReservation.Status.CANCELLED
    locked.cancelled_by = actor
    locked.cancelled_at = now
    locked.save(
        update_fields=["status", "cancelled_by", "cancelled_at", "updated_at"]
    )
    return locked


def reserve_inventory_action(instance, actor):
    return reserve_inventory(instance.pk, actor)


def release_inventory_reservation_action(instance, actor):
    return release_inventory_reservation(instance.pk, actor)


def consume_inventory_reservation_action(instance, actor):
    return consume_inventory_reservation(instance.pk, actor)


def cancel_inventory_reservation_action(instance, actor):
    return cancel_inventory_reservation(instance.pk, actor)


def _bounded_reservation_ids(reservations):
    reservation_ids = sorted(
        {
            int(getattr(reservation, "pk", reservation))
            for reservation in reservations
            if getattr(reservation, "pk", reservation)
        }
    )
    if len(reservation_ids) > MAX_RESERVATION_BATCH:
        raise ValidationError(
            f"Reservation batch operations support at most {MAX_RESERVATION_BATCH} "
            "requests per transaction."
        )
    if not reservation_ids:
        raise ValidationError("Select at least one Reservation.")
    return reservation_ids


@transaction.atomic
def reserve_inventory_batch(reservations, actor, *, allow_zero=False):
    """Reserve a bounded demand batch with fixed-query row locking."""
    if actor is None:
        raise ValidationError("A user is required to reserve inventory.")
    reservation_ids = _bounded_reservation_ids(reservations)
    locked = list(
        InventoryReservation.objects.select_for_update(of=("self",))
        .filter(pk__in=reservation_ids)
        .select_related("product", "warehouse", "location", "lot")
        .order_by("pk")
    )
    if len(locked) != len(reservation_ids):
        raise ValidationError("One or more Reservations no longer exist.")
    if any(row.status != InventoryReservation.Status.DRAFT for row in locked):
        raise ValidationError("Batch reservation requires Draft Reservations.")
    if any(row.product.product_type != Product.ProductType.STOCKABLE for row in locked):
        raise ValidationError("Only stockable products can be reserved.")

    warehouse_ids = sorted({row.warehouse_id for row in locked})
    list(
        Warehouse.objects.select_for_update(of=("self",))
        .filter(pk__in=warehouse_ids)
        .order_by("pk")
    )
    _assert_warehouses_not_frozen(warehouse_ids)

    scope_filter = Q(pk__in=[])
    for row in locked:
        scope_filter |= Q(
            company_id=row.company_id,
            warehouse_id=row.warehouse_id,
            product_id=row.product_id,
        )
    candidates = list(
        InventoryStockBalance.objects.select_for_update(of=("self",))
        .filter(
            scope_filter,
            ownership_type=InventoryStockBalance.OwnershipType.COMPANY,
            owner_vendor__isnull=True,
            location__usage=InventoryLocation.Usage.INTERNAL,
            location__active=True,
            location__is_expired_location=False,
            qty_available__gt=ZERO,
        )
        .filter(
            Q(lot__isnull=True)
            | Q(
                lot__status=InventoryLotSerial.Status.ACTIVE,
                lot__expiration_date__isnull=True,
            )
            | Q(
                lot__status=InventoryLotSerial.Status.ACTIVE,
                lot__expiration_date__gte=timezone.localdate(),
            )
        )
        .select_related("location", "lot")
        .order_by("pk")[: MAX_BATCH_RESERVATION_CANDIDATES + 1]
    )
    if len(candidates) > MAX_BATCH_RESERVATION_CANDIDATES:
        raise ValidationError(
            f"Batch allocation supports at most {MAX_BATCH_RESERVATION_CANDIDATES} "
            "candidate stock positions. Narrow the Picking location or lot scope."
        )
    _assert_warehouses_not_frozen(warehouse_ids)

    candidates_by_scope = defaultdict(list)
    for balance in candidates:
        candidates_by_scope[
            (balance.company_id, balance.warehouse_id, balance.product_id)
        ].append(balance)
    for scope_candidates in candidates_by_scope.values():
        scope_candidates.sort(key=_reservation_candidate_sort_key)

    now = timezone.now()
    changed_balances = {}
    allocations = []
    changed_reservations = []
    for reservation in locked:
        remaining = reservation.requested_quantity
        scope_candidates = candidates_by_scope.get(
            (reservation.company_id, reservation.warehouse_id, reservation.product_id),
            (),
        )
        for balance in scope_candidates:
            if reservation.location_id and balance.location_id != reservation.location_id:
                continue
            if reservation.lot_id and balance.lot_id != reservation.lot_id:
                continue
            available = min(
                balance.qty_available,
                balance.qty_on_hand - balance.qty_reserved,
            )
            if available <= ZERO:
                continue
            quantity = min(remaining, available)
            balance.qty_reserved += quantity
            balance.qty_available = balance.qty_on_hand - balance.qty_reserved
            balance.updated_at = now
            changed_balances[balance.pk] = balance
            allocations.append(
                InventoryReservationAllocation(
                    reservation=reservation,
                    balance=balance,
                    quantity=quantity,
                    allocated_at=now,
                )
            )
            remaining -= quantity
            if remaining <= ZERO:
                break
        reserved_quantity = reservation.requested_quantity - remaining
        if reserved_quantity <= ZERO:
            if allow_zero:
                continue
            raise ValidationError(
                f"No available stock matches Reservation {reservation.reference}."
            )
        if remaining > ZERO and not reservation.allow_partial:
            raise ValidationError(
                f"Insufficient available stock for Reservation {reservation.reference}."
            )
        reservation.reserved_quantity = reserved_quantity
        reservation.status = (
            InventoryReservation.Status.RESERVED
            if remaining <= ZERO
            else InventoryReservation.Status.PARTIAL
        )
        reservation.reserved_by = actor
        reservation.reserved_at = now
        reservation.updated_at = now
        changed_reservations.append(reservation)

    if changed_balances:
        InventoryStockBalance.objects.bulk_update(
            list(changed_balances.values()),
            ["qty_reserved", "qty_available", "updated_at"],
            batch_size=500,
        )
    if allocations:
        InventoryReservationAllocation.objects.bulk_create(
            allocations,
            batch_size=500,
        )
    if changed_reservations:
        InventoryReservation.objects.bulk_update(
            changed_reservations,
            [
                "reserved_quantity",
                "status",
                "reserved_by",
                "reserved_at",
                "updated_at",
            ],
            batch_size=MAX_RESERVATION_BATCH,
        )
    return {row.pk: row for row in locked}


@transaction.atomic
def release_inventory_reservations_batch(reservations, actor):
    if actor is None:
        raise ValidationError("A user is required to release Reservations.")
    reservation_ids = _bounded_reservation_ids(reservations)
    locked = list(
        InventoryReservation.objects.select_for_update(of=("self",))
        .filter(pk__in=reservation_ids)
        .order_by("pk")
    )
    if len(locked) != len(reservation_ids):
        raise ValidationError("One or more Reservations no longer exist.")
    active_statuses = {
        InventoryReservation.Status.RESERVED,
        InventoryReservation.Status.PARTIAL,
    }
    if any(row.status not in active_statuses for row in locked):
        raise ValidationError("Batch release requires active Reservations.")
    warehouse_ids = sorted({row.warehouse_id for row in locked})
    list(
        Warehouse.objects.select_for_update(of=("self",))
        .filter(pk__in=warehouse_ids)
        .order_by("pk")
    )
    _assert_warehouses_not_frozen(warehouse_ids)
    allocations = list(
        InventoryReservationAllocation.objects.select_for_update(of=("self",))
        .filter(reservation_id__in=reservation_ids)
        .order_by("balance_id", "pk")[: MAX_BATCH_RESERVATION_CANDIDATES + 1]
    )
    if len(allocations) > MAX_BATCH_RESERVATION_CANDIDATES:
        raise ValidationError("Reservation allocation batch exceeds the supported limit.")
    allocation_totals = defaultdict(Decimal)
    release_by_balance = defaultdict(Decimal)
    for allocation in allocations:
        allocation_totals[allocation.reservation_id] += allocation.quantity
        release_by_balance[allocation.balance_id] += allocation.quantity
    if any(
        allocation_totals[row.pk] != row.reserved_quantity
        for row in locked
    ):
        raise ValidationError("Reservation allocation totals changed unexpectedly.")
    balances = list(
        InventoryStockBalance.objects.select_for_update(of=("self",))
        .filter(pk__in=sorted(release_by_balance))
        .order_by("pk")
    )
    if len(balances) != len(release_by_balance):
        raise ValidationError("One or more reserved stock positions no longer exist.")
    _assert_warehouses_not_frozen(warehouse_ids)
    now = timezone.now()
    for balance in balances:
        quantity = release_by_balance[balance.pk]
        if balance.qty_reserved < quantity:
            raise ValidationError("Reserved stock changed unexpectedly; release was rolled back.")
        balance.qty_reserved -= quantity
        balance.qty_available = balance.qty_on_hand - balance.qty_reserved
        balance.updated_at = now
    InventoryStockBalance.objects.bulk_update(
        balances,
        ["qty_reserved", "qty_available", "updated_at"],
        batch_size=500,
    )
    for reservation in locked:
        reservation.status = InventoryReservation.Status.RELEASED
        reservation.released_by = actor
        reservation.released_at = now
        reservation.updated_at = now
    InventoryReservation.objects.bulk_update(
        locked,
        ["status", "released_by", "released_at", "updated_at"],
        batch_size=MAX_RESERVATION_BATCH,
    )
    return locked


@transaction.atomic
def consume_inventory_reservations_batch(reservations, actor):
    """Consume a bounded set of full reservations with fixed-query ledger writes."""
    if actor is None:
        raise ValidationError("A user is required to consume Reservations.")
    reservation_ids = _bounded_reservation_ids(reservations)
    locked = list(
        InventoryReservation.objects.select_for_update(of=("self",))
        .filter(pk__in=reservation_ids)
        .order_by("pk")
    )
    if len(locked) != len(reservation_ids):
        raise ValidationError("One or more Reservations no longer exist.")
    active_statuses = {
        InventoryReservation.Status.RESERVED,
        InventoryReservation.Status.PARTIAL,
    }
    if any(row.status not in active_statuses for row in locked):
        raise ValidationError("Batch consumption requires active Reservations.")
    warehouse_ids = sorted({row.warehouse_id for row in locked})
    list(
        Warehouse.objects.select_for_update(of=("self",))
        .filter(pk__in=warehouse_ids)
        .order_by("pk")
    )
    _assert_warehouses_not_frozen(warehouse_ids)
    allocations = list(
        InventoryReservationAllocation.objects.select_for_update(of=("self",))
        .filter(reservation_id__in=reservation_ids)
        .order_by("balance_id", "pk")[: MAX_BATCH_RESERVATION_CANDIDATES + 1]
    )
    if not allocations or len(allocations) > MAX_BATCH_RESERVATION_CANDIDATES:
        raise ValidationError("Reservation allocation batch is empty or exceeds the limit.")
    if any(allocation.stock_move_id for allocation in allocations):
        raise ValidationError("One or more Reservation allocations were already consumed.")
    allocation_totals = defaultdict(Decimal)
    quantity_by_balance = defaultdict(Decimal)
    for allocation in allocations:
        allocation_totals[allocation.reservation_id] += allocation.quantity
        quantity_by_balance[allocation.balance_id] += allocation.quantity
    if any(
        allocation_totals[row.pk] != row.reserved_quantity
        for row in locked
    ):
        raise ValidationError("Reservation allocation totals changed unexpectedly.")
    balances = {
        row.pk: row
        for row in InventoryStockBalance.objects.select_for_update(of=("self",))
        .filter(pk__in=sorted(quantity_by_balance))
        .select_related("location", "lot")
        .order_by("pk")
    }
    if len(balances) != len(quantity_by_balance):
        raise ValidationError("One or more reserved stock positions no longer exist.")
    _assert_warehouses_not_frozen(warehouse_ids)
    reservation_by_id = {row.pk: row for row in locked}
    for allocation in allocations:
        reservation = reservation_by_id[allocation.reservation_id]
        balance = balances[allocation.balance_id]
        if (
            balance.company_id != reservation.company_id
            or balance.product_id != reservation.product_id
            or balance.warehouse_id != reservation.warehouse_id
        ):
            raise ValidationError("A Reservation allocation moved outside its original scope.")
        if not balance.location.active or balance.location.is_expired_location:
            raise ValidationError("Reserved stock is in an inactive or expired location.")
        if balance.lot_id and (
            balance.lot.status != InventoryLotSerial.Status.ACTIVE
            or balance.lot.is_expired
        ):
            raise ValidationError("Reserved stock belongs to an inactive or expired lot/serial.")
    for balance_id, quantity in quantity_by_balance.items():
        balance = balances[balance_id]
        if balance.qty_reserved < quantity or balance.qty_on_hand < quantity:
            raise ValidationError("Reserved stock is no longer sufficient.")

    product_deltas = defaultdict(Decimal)
    for reservation in locked:
        product_deltas[reservation.product_id] += reservation.reserved_quantity
    products = Product.objects.select_for_update(of=("self",)).filter(
        pk__in=sorted(product_deltas)
    ).in_bulk()
    for product_id, quantity in product_deltas.items():
        if products[product_id].current_stock < quantity:
            raise ValidationError(
                f"Global product stock is insufficient for {products[product_id]}."
            )

    stock_moves = []
    for allocation in allocations:
        reservation = reservation_by_id[allocation.reservation_id]
        stock_moves.append(
            StockMove(
                company_id=reservation.company_id,
                product_id=reservation.product_id,
                warehouse_id=reservation.warehouse_id,
                direction=StockMove.Direction.OUT,
                quantity=allocation.quantity,
                reference=reservation.reference,
                movement_date=timezone.localdate(),
                note=f"Consumed Inventory Reservation {reservation.reference}.",
                created_by=actor,
            )
        )
    StockMove.objects.bulk_create(stock_moves, batch_size=500)

    now = timezone.now()
    events = []
    affected_lot_ids = set()
    for allocation, move in zip(allocations, stock_moves):
        balance = balances[allocation.balance_id]
        balance.qty_on_hand -= allocation.quantity
        balance.qty_reserved -= allocation.quantity
        balance.qty_available = balance.qty_on_hand - balance.qty_reserved
        balance.last_move_id = move.pk
        balance.updated_at = now
        allocation.stock_move_id = move.pk
        if balance.lot_id:
            affected_lot_ids.add(balance.lot_id)
        events.append(
            InventoryBalanceEvent(
                stock_move=move,
                balance=balance,
                direction=StockMove.Direction.OUT,
                quantity=allocation.quantity,
            )
        )
    InventoryStockBalance.objects.bulk_update(
        list(balances.values()),
        ["qty_on_hand", "qty_reserved", "qty_available", "last_move", "updated_at"],
        batch_size=500,
    )
    InventoryBalanceEvent.objects.bulk_create(events, batch_size=500)
    InventoryReservationAllocation.objects.bulk_update(
        allocations,
        ["stock_move"],
        batch_size=500,
    )
    for product_id, quantity in product_deltas.items():
        product = products[product_id]
        product.current_stock -= quantity
        product.updated_at = now
    Product.objects.bulk_update(
        list(products.values()),
        ["current_stock", "updated_at"],
        batch_size=MAX_RESERVATION_BATCH,
    )

    if affected_lot_ids:
        lot_totals = {
            row["lot_id"]: row["quantity"] or ZERO
            for row in InventoryStockBalance.objects.filter(lot_id__in=affected_lot_ids)
            .order_by()
            .values("lot_id")
            .annotate(quantity=Sum("qty_on_hand"))
        }
        lots = list(
            InventoryLotSerial.objects.select_for_update(of=("self",))
            .filter(pk__in=affected_lot_ids)
            .order_by("pk")
        )
        for lot in lots:
            lot.quantity = lot_totals.get(lot.pk, ZERO)
            lot.status = (
                InventoryLotSerial.Status.ACTIVE
                if lot.quantity > ZERO
                else InventoryLotSerial.Status.OUT
            )
            lot.updated_at = now
        InventoryLotSerial.objects.bulk_update(
            lots,
            ["quantity", "status", "updated_at"],
            batch_size=500,
        )
    for reservation in locked:
        reservation.status = InventoryReservation.Status.CONSUMED
        reservation.consumed_by = actor
        reservation.consumed_at = now
        reservation.updated_at = now
    InventoryReservation.objects.bulk_update(
        locked,
        ["status", "consumed_by", "consumed_at", "updated_at"],
        batch_size=MAX_RESERVATION_BATCH,
    )
    return locked


def _locked_picking_lines(picking):
    lines = list(
        InventoryPickingLine.objects.select_for_update(of=("self",))
        .filter(picking=picking)
        .select_related("product", "location", "lot", "current_reservation")
        .order_by("pk")[: MAX_PICKING_LINES + 1]
    )
    if not lines:
        raise ValidationError("The Picking has no demand lines.")
    if len(lines) > MAX_PICKING_LINES:
        raise ValidationError(f"A Picking supports at most {MAX_PICKING_LINES} lines.")
    return lines


def _cancel_draft_picking_reservations(lines, actor, *, now=None):
    draft_reservations = [
        line.current_reservation
        for line in lines
        if line.current_reservation
        and line.current_reservation.status == InventoryReservation.Status.DRAFT
    ]
    if not draft_reservations:
        return 0
    now = now or timezone.now()
    for reservation in draft_reservations:
        reservation.status = InventoryReservation.Status.CANCELLED
        reservation.cancelled_by = actor
        reservation.cancelled_at = now
        reservation.updated_at = now
    InventoryReservation.objects.bulk_update(
        draft_reservations,
        ["status", "cancelled_by", "cancelled_at", "updated_at"],
        batch_size=MAX_RESERVATION_BATCH,
    )
    return len(draft_reservations)


@transaction.atomic
def confirm_inventory_picking(picking, actor):
    if actor is None:
        raise ValidationError("A user is required to confirm a Picking.")
    picking_id = getattr(picking, "pk", picking)
    locked = InventoryPicking.objects.select_for_update(of=("self",)).get(pk=picking_id)
    if locked.status != InventoryPicking.Status.DRAFT:
        raise ValidationError("Only Draft Pickings can be confirmed.")
    lines = _locked_picking_lines(locked)
    if any(line.reserved_quantity != ZERO or line.done_quantity != ZERO for line in lines):
        raise ValidationError("Draft Picking lines cannot already contain processed quantities.")
    now = timezone.now()
    locked.status = InventoryPicking.Status.CONFIRMED
    locked.confirmed_by = actor
    locked.confirmed_at = now
    locked.save(update_fields=["status", "confirmed_by", "confirmed_at", "updated_at"])
    return locked


@transaction.atomic
def check_inventory_picking_availability(picking, actor):
    if actor is None:
        raise ValidationError("A user is required to check Picking availability.")
    picking_id = getattr(picking, "pk", picking)
    locked = InventoryPicking.objects.select_for_update(of=("self",)).get(pk=picking_id)
    if locked.status not in {
        InventoryPicking.Status.CONFIRMED,
        InventoryPicking.Status.PARTIAL,
    }:
        raise ValidationError("Only Waiting or Partially Available Pickings can check availability.")
    lines = _locked_picking_lines(locked)
    active_statuses = {
        InventoryReservation.Status.RESERVED,
        InventoryReservation.Status.PARTIAL,
    }
    active_ids = [
        line.current_reservation_id
        for line in lines
        if line.current_reservation
        and line.current_reservation.status in active_statuses
    ]
    if active_ids:
        release_inventory_reservations_batch(active_ids, actor)
    _cancel_draft_picking_reservations(lines, actor)

    reservations = [
        InventoryReservation(
            company_id=locked.company_id,
            branch_id=locked.branch_id,
            warehouse_id=locked.warehouse_id,
            product_id=line.product_id,
            location_id=line.location_id,
            lot_id=line.lot_id,
            reference=f"{locked.reference}-L{line.pk}-{uuid4().hex[:6].upper()}",
            requested_quantity=line.requested_quantity,
            allow_partial=True,
            notes=f"Generated by Picking {locked.reference}.",
        )
        for line in lines
    ]
    InventoryReservation.objects.bulk_create(
        reservations,
        batch_size=MAX_RESERVATION_BATCH,
    )
    reservation_map = reserve_inventory_batch(reservations, actor, allow_zero=True)
    total_reserved = ZERO
    fully_reserved = True
    for line, reservation in zip(lines, reservations):
        reservation = reservation_map[reservation.pk]
        line.current_reservation_id = reservation.pk
        line.reserved_quantity = reservation.reserved_quantity
        line.done_quantity = min(line.done_quantity, line.reserved_quantity)
        line.updated_at = timezone.now()
        total_reserved += line.reserved_quantity
        if line.reserved_quantity != line.requested_quantity:
            fully_reserved = False
    InventoryPickingLine.objects.bulk_update(
        lines,
        ["current_reservation", "reserved_quantity", "done_quantity", "updated_at"],
        batch_size=MAX_PICKING_LINES,
    )
    now = timezone.now()
    if fully_reserved:
        locked.status = InventoryPicking.Status.READY
    elif total_reserved > ZERO:
        locked.status = InventoryPicking.Status.PARTIAL
    else:
        locked.status = InventoryPicking.Status.CONFIRMED
    locked.availability_checked_by = actor
    locked.availability_checked_at = now
    locked.save(
        update_fields=[
            "status",
            "availability_checked_by",
            "availability_checked_at",
            "updated_at",
        ]
    )
    return locked


@transaction.atomic
def unreserve_inventory_picking(picking, actor):
    if actor is None:
        raise ValidationError("A user is required to unreserve a Picking.")
    picking_id = getattr(picking, "pk", picking)
    locked = InventoryPicking.objects.select_for_update(of=("self",)).get(pk=picking_id)
    if locked.status not in {
        InventoryPicking.Status.PARTIAL,
        InventoryPicking.Status.READY,
    }:
        raise ValidationError("Only Partially Available or Ready Pickings can be unreserved.")
    lines = _locked_picking_lines(locked)
    active_ids = [
        line.current_reservation_id
        for line in lines
        if line.current_reservation
        and line.current_reservation.status
        in {InventoryReservation.Status.RESERVED, InventoryReservation.Status.PARTIAL}
    ]
    if active_ids:
        release_inventory_reservations_batch(active_ids, actor)
    _cancel_draft_picking_reservations(lines, actor)
    now = timezone.now()
    for line in lines:
        line.reserved_quantity = ZERO
        line.done_quantity = ZERO
        line.updated_at = now
    InventoryPickingLine.objects.bulk_update(
        lines,
        ["reserved_quantity", "done_quantity", "updated_at"],
        batch_size=MAX_PICKING_LINES,
    )
    locked.status = InventoryPicking.Status.CONFIRMED
    locked.save(update_fields=["status", "updated_at"])
    return locked


@transaction.atomic
def set_inventory_picking_done_quantities(picking, actor):
    if actor is None:
        raise ValidationError("A user is required to set Picking done quantities.")
    picking_id = getattr(picking, "pk", picking)
    locked = InventoryPicking.objects.select_for_update(of=("self",)).get(pk=picking_id)
    if locked.status not in {
        InventoryPicking.Status.PARTIAL,
        InventoryPicking.Status.READY,
    }:
        raise ValidationError("Done quantities require a Partially Available or Ready Picking.")
    lines = _locked_picking_lines(locked)
    now = timezone.now()
    for line in lines:
        line.done_quantity = line.reserved_quantity
        line.updated_at = now
    InventoryPickingLine.objects.bulk_update(
        lines,
        ["done_quantity", "updated_at"],
        batch_size=MAX_PICKING_LINES,
    )
    return locked


@transaction.atomic
def validate_inventory_picking(picking, actor):
    if actor is None:
        raise ValidationError("A user is required to validate a Picking.")
    picking_id = getattr(picking, "pk", picking)
    locked = InventoryPicking.objects.select_for_update(of=("self",)).get(pk=picking_id)
    if locked.status not in {
        InventoryPicking.Status.PARTIAL,
        InventoryPicking.Status.READY,
    }:
        raise ValidationError("Only Partially Available or Ready Pickings can be validated.")
    lines = _locked_picking_lines(locked)
    if not any(line.done_quantity > ZERO for line in lines):
        raise ValidationError("Set at least one done quantity before Validate.")
    for line in lines:
        if line.done_quantity != line.reserved_quantity:
            raise ValidationError(
                "Done quantity must match the reserved quantity; use Unreserve and "
                "Check Availability to change the allocation."
            )
    reservation_ids = [
        line.current_reservation_id
        for line in lines
        if line.done_quantity > ZERO and line.current_reservation_id
    ]
    if not reservation_ids:
        raise ValidationError("The Picking has no active reservations to consume.")
    consume_inventory_reservations_batch(reservation_ids, actor)
    _cancel_draft_picking_reservations(lines, actor)

    backorder_lines = [
        line for line in lines if line.requested_quantity > line.done_quantity
    ]
    if backorder_lines:
        backorder = InventoryPicking.objects.create(
            company_id=locked.company_id,
            branch_id=locked.branch_id,
            warehouse_id=locked.warehouse_id,
            reference="",
            scheduled_date=locked.scheduled_date,
            responsible_id=locked.responsible_id,
            destination_name=locked.destination_name,
            destination_address=locked.destination_address,
            backorder_of=locked,
            notes=f"Backorder of {locked.reference}.",
        )
        InventoryPickingLine.objects.bulk_create(
            [
                InventoryPickingLine(
                    picking=backorder,
                    product_id=line.product_id,
                    location_id=line.location_id,
                    lot_id=line.lot_id,
                    requested_quantity=line.requested_quantity - line.done_quantity,
                )
                for line in backorder_lines
            ],
            batch_size=MAX_PICKING_LINES,
        )
    now = timezone.now()
    locked.status = InventoryPicking.Status.DONE
    locked.completed_by = actor
    locked.completed_at = now
    locked.save(
        update_fields=["status", "completed_by", "completed_at", "updated_at"]
    )
    return locked


@transaction.atomic
def cancel_inventory_picking(picking, actor):
    if actor is None:
        raise ValidationError("A user is required to cancel a Picking.")
    picking_id = getattr(picking, "pk", picking)
    locked = InventoryPicking.objects.select_for_update(of=("self",)).get(pk=picking_id)
    if locked.status not in {
        InventoryPicking.Status.DRAFT,
        InventoryPicking.Status.CONFIRMED,
        InventoryPicking.Status.PARTIAL,
        InventoryPicking.Status.READY,
    }:
        raise ValidationError("This Picking can no longer be cancelled.")
    lines = _locked_picking_lines(locked)
    active_ids = [
        line.current_reservation_id
        for line in lines
        if line.current_reservation
        and line.current_reservation.status
        in {InventoryReservation.Status.RESERVED, InventoryReservation.Status.PARTIAL}
    ]
    if active_ids:
        release_inventory_reservations_batch(active_ids, actor)
    _cancel_draft_picking_reservations(lines, actor)
    now = timezone.now()
    for line in lines:
        line.reserved_quantity = ZERO
        line.done_quantity = ZERO
        line.updated_at = now
    InventoryPickingLine.objects.bulk_update(
        lines,
        ["reserved_quantity", "done_quantity", "updated_at"],
        batch_size=MAX_PICKING_LINES,
    )
    locked.status = InventoryPicking.Status.CANCELLED
    locked.cancelled_by = actor
    locked.cancelled_at = now
    locked.save(
        update_fields=["status", "cancelled_by", "cancelled_at", "updated_at"]
    )
    return locked


@transaction.atomic
def reset_inventory_picking_to_draft(picking, actor):
    if actor is None:
        raise ValidationError("A user is required to reset a Picking.")
    picking_id = getattr(picking, "pk", picking)
    locked = InventoryPicking.objects.select_for_update(of=("self",)).get(pk=picking_id)
    if locked.status != InventoryPicking.Status.CANCELLED:
        raise ValidationError("Only Cancelled Pickings can be reset to Draft.")
    lines = _locked_picking_lines(locked)
    now = timezone.now()
    for line in lines:
        line.current_reservation_id = None
        line.reserved_quantity = ZERO
        line.done_quantity = ZERO
        line.updated_at = now
    InventoryPickingLine.objects.bulk_update(
        lines,
        ["current_reservation", "reserved_quantity", "done_quantity", "updated_at"],
        batch_size=MAX_PICKING_LINES,
    )
    locked.status = InventoryPicking.Status.DRAFT
    locked.confirmed_by = None
    locked.confirmed_at = None
    locked.availability_checked_by = None
    locked.availability_checked_at = None
    locked.cancelled_by = None
    locked.cancelled_at = None
    locked.save(
        update_fields=[
            "status",
            "confirmed_by",
            "confirmed_at",
            "availability_checked_by",
            "availability_checked_at",
            "cancelled_by",
            "cancelled_at",
            "updated_at",
        ]
    )
    return locked


def confirm_inventory_picking_action(instance, actor):
    return confirm_inventory_picking(instance.pk, actor)


def check_inventory_picking_availability_action(instance, actor):
    return check_inventory_picking_availability(instance.pk, actor)


def unreserve_inventory_picking_action(instance, actor):
    return unreserve_inventory_picking(instance.pk, actor)


def set_inventory_picking_done_quantities_action(instance, actor):
    return set_inventory_picking_done_quantities(instance.pk, actor)


def validate_inventory_picking_action(instance, actor):
    return validate_inventory_picking(instance.pk, actor)


def cancel_inventory_picking_action(instance, actor):
    return cancel_inventory_picking(instance.pk, actor)


def reset_inventory_picking_to_draft_action(instance, actor):
    return reset_inventory_picking_to_draft(instance.pk, actor)


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
