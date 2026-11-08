from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def backfill_purchase_configuration(apps, schema_editor):
    del schema_editor
    OrderType = apps.get_model("gsshop_purchase", "PurchaseOrderType")
    MilestoneTemplate = apps.get_model("gsshop_purchase", "PurchaseMilestoneTemplate")
    MilestoneLine = apps.get_model("gsshop_purchase", "PurchaseMilestoneTemplateLine")

    order_types = list(OrderType.objects.order_by("pk"))
    used_prefixes = set()
    through_rows = []
    Through = OrderType.companies.through
    for order_type in order_types:
        words = [word for word in (order_type.name or "").split() if word]
        base = ("".join(word[0] for word in words).upper() or "POT")[:10]
        candidate = base
        index = 2
        while candidate in used_prefixes:
            suffix = str(index)
            candidate = f"{base[: 10 - len(suffix)]}{suffix}"
            index += 1
        used_prefixes.add(candidate)
        order_type.prefix_sequence = candidate
        order_type.sequence_prefix = candidate
        order_type.code = order_type.code or f"POT-{order_type.pk:08d}"
        through_rows.append(Through(purchaseordertype_id=order_type.pk, company_id=order_type.company_id))
    if order_types:
        OrderType.objects.bulk_update(
            order_types,
            ["prefix_sequence", "sequence_prefix", "code"],
            batch_size=500,
        )
        Through.objects.bulk_create(through_rows, batch_size=500, ignore_conflicts=True)

    templates = list(MilestoneTemplate.objects.select_related("order_template").order_by("pk"))
    lines = []
    for template in templates:
        template.company_id = template.order_template.company_id
        lines.append(
            MilestoneLine(
                template_id=template.pk,
                sequence=template.sequence,
                milestone_name=template.name,
                contract_term_percentage=template.percentage,
                due_days=template.due_days,
            )
        )
    if templates:
        MilestoneTemplate.objects.bulk_update(templates, ["company"], batch_size=500)
        MilestoneLine.objects.bulk_create(lines, batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_purchase_order_generated_number"),
        ("gsshop_purchase", "0021_purchase_team_kpi_hmx_parity"),
    ]

    operations = [
        migrations.CreateModel(
            name="PurchaseMilestoneTemplateLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("sequence", models.PositiveIntegerField(default=10)),
                ("milestone_name", models.CharField(max_length=255)),
                (
                    "contract_term_percentage",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        max_digits=5,
                        validators=[django.core.validators.MaxValueValidator(100)],
                    ),
                ),
                ("due_days", models.PositiveIntegerField(default=0)),
            ],
            options={"ordering": ["template", "sequence", "pk"]},
        ),
        migrations.CreateModel(
            name="PurchaseOrderTemplateLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("sequence", models.PositiveIntegerField(default=10)),
                ("description", models.CharField(blank=True, max_length=255)),
                (
                    "quantity",
                    models.DecimalField(
                        decimal_places=3,
                        default=1,
                        max_digits=18,
                        validators=[django.core.validators.MinValueValidator(Decimal("0.001"))],
                    ),
                ),
                ("uom", models.CharField(blank=True, max_length=24)),
                ("price", models.DecimalField(decimal_places=2, default=0, max_digits=18)),
            ],
            options={"ordering": ["order_template", "sequence", "pk"]},
        ),
        migrations.CreateModel(
            name="PurchaseOrderTypeSequenceLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("period", models.CharField(blank=True, editable=False, max_length=7)),
                ("purchase_request_next", models.PositiveIntegerField(default=1, editable=False)),
                ("purchase_tender_next", models.PositiveIntegerField(default=1, editable=False)),
                ("blanket_order_next", models.PositiveIntegerField(default=1, editable=False)),
                ("rfq_next", models.PositiveIntegerField(default=1, editable=False)),
                ("purchase_order_draft_next", models.PositiveIntegerField(default=1, editable=False)),
                ("direct_purchase_order_next", models.PositiveIntegerField(default=1, editable=False)),
                ("purchase_order_next", models.PositiveIntegerField(default=1, editable=False)),
            ],
            options={"ordering": ["order_type__name", "company__name", "pk"]},
        ),
        migrations.AlterModelOptions(
            name="purchasemilestonetemplate",
            options={"ordering": ["company", "name", "pk"]},
        ),
        migrations.RemoveConstraint(
            model_name="purchasemilestonetemplate",
            name="unique_purchase_milestone_template_sequence",
        ),
        migrations.AddField(
            model_name="blanketorder",
            name="order_template",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="blanket_orders", to="gsshop_purchase.purchaseordertemplate"),
        ),
        migrations.AddField(
            model_name="blanketorder",
            name="order_type",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="blanket_orders", to="gsshop_purchase.purchaseordertype"),
        ),
        migrations.AddField(
            model_name="directpurchase",
            name="order_template",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="direct_purchases", to="gsshop_purchase.purchaseordertemplate"),
        ),
        migrations.AddField(
            model_name="directpurchase",
            name="order_type",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="direct_purchases", to="gsshop_purchase.purchaseordertype"),
        ),
        migrations.AddField(
            model_name="purchasemilestonetemplate",
            name="company",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="purchase_milestone_templates", to="core.company"),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="milestone_template",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="orders", to="gsshop_purchase.purchasemilestonetemplate"),
        ),
        migrations.AddField(
            model_name="purchaseordertemplate",
            name="vendor",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="purchase_order_templates", to="core.contact"),
        ),
        migrations.AddField(
            model_name="purchaseordertype",
            name="companies",
            field=models.ManyToManyField(blank=True, related_name="available_purchase_order_types", to="core.company"),
        ),
        migrations.AddField(model_name="purchaseordertype", name="description", field=models.TextField(blank=True)),
        migrations.AddField(model_name="purchaseordertype", name="icon", field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name="purchaseordertype", name="prefix_sequence", field=models.CharField(blank=True, editable=False, max_length=10)),
        migrations.AddField(
            model_name="purchaseordertype",
            name="product_categories",
            field=models.ManyToManyField(blank=True, related_name="purchase_order_types", to="gsshop_purchase.purchaseproductcategory"),
        ),
        migrations.AddField(
            model_name="purchasequotation",
            name="order_template",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="purchase_quotations", to="gsshop_purchase.purchaseordertemplate"),
        ),
        migrations.AddField(
            model_name="purchasequotation",
            name="order_type",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="purchase_quotations", to="gsshop_purchase.purchaseordertype"),
        ),
        migrations.AddField(
            model_name="purchaserequest",
            name="order_type",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="purchase_requests", to="gsshop_purchase.purchaseordertype"),
        ),
        migrations.AddField(
            model_name="purchasetender",
            name="order_template",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="purchase_tenders", to="gsshop_purchase.purchaseordertemplate"),
        ),
        migrations.AddField(
            model_name="purchasetender",
            name="order_type",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="purchase_tenders", to="gsshop_purchase.purchaseordertype"),
        ),
        migrations.AlterField(
            model_name="purchasemilestonetemplate",
            name="order_template",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="milestone_templates", to="gsshop_purchase.purchaseordertemplate"),
        ),
        migrations.AlterField(
            model_name="purchaseordertemplate",
            name="order_type",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="templates", to="gsshop_purchase.purchaseordertype"),
        ),
        migrations.AlterField(model_name="purchaseordertype", name="code", field=models.CharField(blank=True, max_length=30)),
        migrations.AddField(
            model_name="purchasemilestonetemplateline",
            name="template",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="gsshop_purchase.purchasemilestonetemplate"),
        ),
        migrations.AddField(
            model_name="purchaseordertemplateline",
            name="order_template",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="gsshop_purchase.purchaseordertemplate"),
        ),
        migrations.AddField(
            model_name="purchaseordertemplateline",
            name="product",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_order_template_lines", to="core.product"),
        ),
        migrations.AddField(
            model_name="purchaseordertypesequenceline",
            name="company",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="purchase_order_type_sequences", to="core.company"),
        ),
        migrations.AddField(
            model_name="purchaseordertypesequenceline",
            name="order_type",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sequence_lines", to="gsshop_purchase.purchaseordertype"),
        ),
        migrations.RunPython(backfill_purchase_configuration, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="purchasemilestonetemplate",
            name="company",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_milestone_templates", to="core.company"),
        ),
        migrations.AddConstraint(
            model_name="purchasemilestonetemplate",
            constraint=models.UniqueConstraint(condition=models.Q(("order_template__isnull", False)), fields=("order_template", "sequence"), name="unique_purchase_milestone_template_sequence"),
        ),
        migrations.AddConstraint(
            model_name="purchasemilestonetemplateline",
            constraint=models.UniqueConstraint(fields=("template", "sequence"), name="unique_purchase_milestone_template_line_sequence"),
        ),
        migrations.AddConstraint(
            model_name="purchaseordertypesequenceline",
            constraint=models.UniqueConstraint(fields=("order_type", "company"), name="unique_purchase_order_type_company_sequence"),
        ),
    ]
