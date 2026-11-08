from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_purchase_receiving_fields"),
        ("gsshop_purchase", "0008_unique_purchase_vendor_bill_document"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaserequest",
            name="currency",
            field=models.CharField(default="IDR", max_length=3),
        ),
        migrations.AddField(
            model_name="purchaserequest",
            name="destination_warehouse",
            field=models.ForeignKey(
                help_text="Default destination warehouse carried into RFQs and purchase orders.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_requests",
                to="core.warehouse",
            ),
        ),
        migrations.AddField(
            model_name="purchaserequest",
            name="expiry_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="purchaserequestline",
            name="approval_justification",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="purchaserequestline",
            name="blanket_quantity",
            field=models.DecimalField(decimal_places=3, default=0, editable=False, max_digits=18),
        ),
        migrations.AddField(
            model_name="purchaserequestline",
            name="direct_purchase_quantity",
            field=models.DecimalField(decimal_places=3, default=0, editable=False, max_digits=18),
        ),
        migrations.AddField(
            model_name="purchaserequestline",
            name="line_status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("pending", "Pending"),
                    ("rejected", "Rejected"),
                    ("partially_approved", "Partially Approved"),
                    ("approved", "Approved"),
                    ("partially_rfq", "Partially RFQ"),
                    ("rfq", "RFQ"),
                    ("partially_ordered", "Partially Ordered"),
                    ("fully_ordered", "Fully Ordered"),
                    ("in_progress", "In Progress"),
                    ("closed", "Closed"),
                    ("expired", "Expired"),
                ],
                default="draft",
                editable=False,
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="purchaserequestline",
            name="rejection_reason",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="purchaserequestline",
            name="rfq_quantity",
            field=models.DecimalField(decimal_places=3, default=0, editable=False, max_digits=18),
        ),
        migrations.AddField(
            model_name="purchaserequestline",
            name="tender_quantity",
            field=models.DecimalField(decimal_places=3, default=0, editable=False, max_digits=18),
        ),
        migrations.AddField(
            model_name="purchasequotation",
            name="expected_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="purchasequotation",
            name="warehouse",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_quotations",
                to="core.warehouse",
            ),
        ),
        migrations.AddField(
            model_name="directpurchase",
            name="request",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="direct_purchases",
                to="gsshop_purchase.purchaserequest",
            ),
        ),
        migrations.AddField(
            model_name="directpurchase",
            name="warehouse",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="direct_purchases",
                to="core.warehouse",
            ),
        ),
        migrations.AddField(
            model_name="directpurchaseline",
            name="request_line",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="direct_purchase_lines",
                to="gsshop_purchase.purchaserequestline",
            ),
        ),
        migrations.AddField(
            model_name="blanketorderline",
            name="request_line",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="blanket_order_lines",
                to="gsshop_purchase.purchaserequestline",
            ),
        ),
    ]
