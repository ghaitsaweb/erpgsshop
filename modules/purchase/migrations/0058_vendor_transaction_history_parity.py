from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0019_company_logo"),
        ("organization", "0002_recordorganizationscope"),
        ("gsshop_purchase", "0057_tender_evaluation_and_award_strategy"),
    ]

    operations = [
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="actual_delivery_date",
            field=models.DateField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="branch_snapshot",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="vendor_transaction_history",
                to="organization.branch",
            ),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="company_snapshot",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="vendor_transaction_history",
                to="core.company",
            ),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="currency",
            field=models.CharField(blank=True, editable=False, max_length=3),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="delivery_status",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="expected_date",
            field=models.DateField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="lead_time_variance",
            field=models.IntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="ordered_quantity",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                editable=False,
                max_digits=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="product",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="vendor_transaction_history",
                to="core.product",
            ),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="received_quantity",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                editable=False,
                max_digits=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="rejection_indicator",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="rejection_reason",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="remaining_quantity",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                editable=False,
                max_digits=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="source_document",
            field=models.CharField(blank=True, editable=False, max_length=255),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="source_model",
            field=models.CharField(blank=True, editable=False, max_length=128),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="source_record_id",
            field=models.PositiveBigIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="summary",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="vendortransactionhistory",
            name="value",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                editable=False,
                max_digits=20,
                null=True,
            ),
        ),
        migrations.AddIndex(
            model_name="vendortransactionhistory",
            index=models.Index(
                fields=["vendor", "-transaction_date", "-id"],
                name="vendor_history_date_idx",
            ),
        ),
    ]
