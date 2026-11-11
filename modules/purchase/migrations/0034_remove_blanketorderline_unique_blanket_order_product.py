from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("gsshop_purchase", "0033_purchaseorderrevision_original_order_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="blanketorderline",
            name="unique_blanket_order_product",
        ),
    ]
