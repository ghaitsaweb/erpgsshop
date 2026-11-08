from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("gsshop_purchase", "0007_purchase_inventory_transfer_stock_moves"),
    ]

    # Reserved migration number: vendor-bill cardinality is validated in the
    # posting service so existing installations are not forced through a
    # potentially incompatible conditional uniqueness migration.
    operations = []
