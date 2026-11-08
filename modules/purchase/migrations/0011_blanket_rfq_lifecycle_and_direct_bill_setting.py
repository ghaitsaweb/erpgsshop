from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("gsshop_purchase", "0010_purchase_conversion_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="blanketorder",
            name="auto_closed",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="purchasequotationline",
            name="blanket_order_line",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="quotation_lines",
                to="gsshop_purchase.blanketorderline",
            ),
        ),
        migrations.AddField(
            model_name="purchasesetting",
            name="validate_bill_behavior",
            field=models.CharField(
                choices=[
                    ("manual", "Create vendor bill in draft"),
                    ("auto", "Post and pay vendor bill automatically"),
                ],
                default="manual",
                max_length=12,
            ),
        ),
    ]
