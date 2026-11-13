import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_purchase_order_generated_number"),
        ("gsshop_finance", "0009_paymentallocation_writeoff"),
        ("gsshop_purchase", "0052_purchase_cost_center_distributions"),
    ]

    operations = [
        migrations.CreateModel(
            name="PurchaseJournalLineAnalyticSnapshot",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "cost_center_mode",
                    models.CharField(
                        choices=[
                            ("single", "Single Cost Center"),
                            ("distribution", "Distribution"),
                        ],
                        default="single",
                        max_length=16,
                    ),
                ),
                ("cost_center_distribution_ids", models.JSONField(blank=True, default=list)),
                ("cost_center_distribution_data", models.JSONField(blank=True, default=list)),
                ("cost_center_analytic_tag_ids", models.JSONField(blank=True, default=list)),
                ("cost_center", models.CharField(blank=True, max_length=80)),
                (
                    "journal_line",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="purchase_analytic_snapshot",
                        to="gsshop_finance.journalline",
                    ),
                ),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="journal_line_analytic_snapshots",
                        to="core.purchaseorder",
                    ),
                ),
                (
                    "order_line",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="purchase_journal_analytic_snapshots",
                        to="core.purchaseorderline",
                    ),
                ),
            ],
            options={"ordering": ["journal_line_id"]},
        ),
    ]
