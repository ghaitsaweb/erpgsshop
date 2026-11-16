def sync_single_stock_move(sender, instance, created, raw=False, **_kwargs):
    del sender
    if not created or raw:
        return
    from .services import apply_stock_moves_to_balances

    apply_stock_moves_to_balances([instance])
