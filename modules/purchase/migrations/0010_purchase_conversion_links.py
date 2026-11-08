from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_purchase_receiving_fields"),
        ("gsshop_purchase", "0009_purchase_sourcing_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchasequotation",
            name="blanket_order",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="quotations",
                to="gsshop_purchase.blanketorder",
            ),
        ),
        migrations.AddField(
            model_name="directpurchase",
            name="purchase_order",
            field=models.OneToOneField(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="direct_purchase_source",
                to="core.purchaseorder",
            ),
        ),
        migrations.AddField(
            model_name="blanketorder",
            name="currency",
            field=models.CharField(default="IDR", max_length=3),
        ),
        migrations.AddField(
            model_name="blanketorder",
            name="warehouse",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="blanket_orders",
                to="core.warehouse",
            ),
        ),
        migrations.AlterField(
            model_name="blanketorder",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("waiting_approval", "Waiting Approval"),
                    ("confirmed", "Confirmed"),
                    ("running", "Running"),
                    ("active", "Active"),
                    ("exhausted", "Exhausted"),
                    ("expired", "Expired"),
                    ("closed", "Closed"),
                    ("cancelled", "Cancelled"),
                    ("rejected", "Rejected"),
                ],
                default="draft",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="purchasetender",
            name="bid_start_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="purchasetender",
            name="currency",
            field=models.CharField(default="IDR", max_length=3),
        ),
        migrations.AddField(
            model_name="purchasetender",
            name="method",
            field=models.CharField(
                choices=[
                    ("ote", "Open Tender Evaluation"),
                    ("ite", "Invited Tender Evaluation"),
                    ("pqte", "Prequalification Tender Evaluation"),
                ],
                default="ote",
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name="purchasetender",
            name="validity_days",
            field=models.PositiveIntegerField(default=30),
        ),
        migrations.AddField(
            model_name="purchasetender",
            name="warehouse",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="purchase_tenders",
                to="core.warehouse",
            ),
        ),
        migrations.AlterField(
            model_name="purchasetender",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("waiting_approval", "Waiting Approval"),
                    ("confirmed", "Confirmed"),
                    ("published", "Published"),
                    ("document_submission", "Document Submission"),
                    ("bid_submission", "Bid Submission"),
                    ("bid_selection", "Bid Selection"),
                    ("submission", "Bid Submission"),
                    ("evaluation", "Evaluation"),
                    ("awarded", "Awarded"),
                    ("closed", "Closed"),
                    ("cancelled", "Cancelled"),
                    ("rejected", "Rejected"),
                ],
                default="draft",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="tender",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="orders",
                to="gsshop_purchase.purchasetender",
            ),
        ),
    ]
