import django.db.models.deletion
from django.db import migrations, models


PRICE_SOURCE_CHOICES = [
    ("pricelist", "Pricelist"),
    ("manual", "Manual"),
    ("no_match", "No Match"),
]


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0039_purchase_team_lead_memberships"),
    ]

    operations = [
        migrations.AddField(
            model_name="blanketorderline",
            name="applied_pricelist",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="blanket_order_lines",
                to="gsshop_purchase.vendorpricelist",
            ),
        ),
        migrations.AddField(
            model_name="blanketorderline",
            name="applied_pricelist_tier",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="blanket_order_lines",
                to="gsshop_purchase.vendorpricelisttier",
            ),
        ),
        migrations.AddField(
            model_name="blanketorderline",
            name="discount_formula",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="blanketorderline",
            name="free_product",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="blanketorderline",
            name="free_product_source_line",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="free_product_benefit_lines",
                to="gsshop_purchase.blanketorderline",
            ),
        ),
        migrations.AddField(
            model_name="blanketorderline",
            name="price_source",
            field=models.CharField(blank=True, choices=PRICE_SOURCE_CHOICES, max_length=20),
        ),
        migrations.AddField(
            model_name="blanketorderline",
            name="price_source_note",
            field=models.CharField(blank=True, editable=False, max_length=255),
        ),
        migrations.AddField(
            model_name="purchasequotationline",
            name="applied_pricelist",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="purchase_quotation_lines",
                to="gsshop_purchase.vendorpricelist",
            ),
        ),
        migrations.AddField(
            model_name="purchasequotationline",
            name="applied_pricelist_tier",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="purchase_quotation_lines",
                to="gsshop_purchase.vendorpricelisttier",
            ),
        ),
        migrations.AddField(
            model_name="purchasequotationline",
            name="discount_formula",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="purchasequotationline",
            name="free_product",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="purchasequotationline",
            name="free_product_source_line",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="free_product_benefit_lines",
                to="gsshop_purchase.purchasequotationline",
            ),
        ),
        migrations.AddField(
            model_name="purchasequotationline",
            name="price_source",
            field=models.CharField(blank=True, choices=PRICE_SOURCE_CHOICES, max_length=20),
        ),
        migrations.AddField(
            model_name="purchasequotationline",
            name="price_source_note",
            field=models.CharField(blank=True, editable=False, max_length=255),
        ),
        migrations.AddConstraint(
            model_name="blanketorderline",
            constraint=models.UniqueConstraint(
                condition=models.Q(("free_product_source_line__isnull", False)),
                fields=("free_product_source_line",),
                name="unique_blanket_free_benefit_source",
            ),
        ),
    ]
