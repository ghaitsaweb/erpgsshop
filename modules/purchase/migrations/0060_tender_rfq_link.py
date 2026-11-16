from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("gsshop_purchase", "0059_tender_timeline_parity"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchasequotation",
            name="tender",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="quotations",
                to="gsshop_purchase.purchasetender",
            ),
        ),
        migrations.AddField(
            model_name="purchasequotationline",
            name="tender_line",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="quotation_lines",
                to="gsshop_purchase.purchasetenderline",
            ),
        ),
        migrations.AddConstraint(
            model_name="purchasequotation",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    tender__isnull=False,
                    status__in=["draft", "sent", "received"],
                ),
                fields=("tender", "vendor"),
                name="unique_active_tender_rfq_vendor",
            ),
        ),
    ]
