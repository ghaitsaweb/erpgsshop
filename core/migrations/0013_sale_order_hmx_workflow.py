import django.db.models.deletion
from django.db import migrations, models
from django.db.models import F


def map_legacy_sales_statuses(apps, schema_editor):
    SaleOrder = apps.get_model("core", "SaleOrder")
    database = schema_editor.connection.alias
    SaleOrder.objects.using(database).filter(status="confirmed").update(
        status="sale",
        quotation_reference=F("number"),
        confirmation_date=F("updated_at"),
    )
    SaleOrder.objects.using(database).filter(status="completed").update(
        status="closed",
        quotation_reference=F("number"),
        confirmation_date=F("updated_at"),
        close_reason="legacy_migration",
    )


def restore_legacy_sales_statuses(apps, schema_editor):
    SaleOrder = apps.get_model("core", "SaleOrder")
    database = schema_editor.connection.alias
    SaleOrder.objects.using(database).filter(status="sale").update(status="confirmed")
    SaleOrder.objects.using(database).filter(status="closed").update(status="completed")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_backfill_sales_commercial_totals"),
    ]

    operations = [
        migrations.AlterField(
            model_name="saleorder",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("to_approve", "Waiting for Approval"),
                    ("sent", "Sent"),
                    ("sale", "Sale Order"),
                    ("closed", "Closed"),
                    ("cancelled", "Cancelled"),
                    ("rejected", "Rejected"),
                    ("revised", "Revised"),
                    ("expired", "Expired"),
                ],
                default="draft",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="saleorder",
            name="quotation_reference",
            field=models.CharField(blank=True, editable=False, max_length=40),
        ),
        migrations.AddField(
            model_name="saleorder",
            name="confirmation_date",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="saleorder",
            name="cancel_reason",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="saleorder",
            name="reject_reason",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="saleorder",
            name="close_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("completed", "Completed in full"),
                    ("customer_cancelled", "Cancelled by customer"),
                    ("duplicate", "Duplicate / incorrect entry"),
                    ("not_billed", "Not billed (sample / internal)"),
                    ("partial_release", "Partially closed, remainder released"),
                    ("legacy_migration", "Legacy data migration"),
                ],
                editable=False,
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="saleorder",
            name="close_note",
            field=models.TextField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="saleorder",
            name="revision_number",
            field=models.PositiveIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="saleorder",
            name="revision_root",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="revision_documents",
                to="core.saleorder",
            ),
        ),
        migrations.AddField(
            model_name="saleorder",
            name="previous_revision",
            field=models.OneToOneField(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="revised_document",
                to="core.saleorder",
            ),
        ),
        migrations.RunPython(map_legacy_sales_statuses, restore_legacy_sales_statuses),
    ]
