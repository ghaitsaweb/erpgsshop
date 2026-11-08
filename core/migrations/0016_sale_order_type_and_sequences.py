import django.db.models.deletion
from django.db import migrations, models


def create_default_order_type(apps, schema_editor):
    SaleOrderType = apps.get_model("core", "SaleOrderType")
    SaleOrderTypeSequence = apps.get_model("core", "SaleOrderTypeSequence")
    SaleOrder = apps.get_model("core", "SaleOrder")
    order_type, _created = SaleOrderType.objects.get_or_create(
        name="All",
        defaults={
            "prefix_sequence": "AL",
            "origin": "sale",
            "description": "Global Sales Order Type",
        },
    )
    if not order_type.prefix_sequence:
        order_type.prefix_sequence = "AL"
        order_type.save(update_fields=["prefix_sequence"])
    SaleOrderTypeSequence.objects.get_or_create(order_type=order_type, company=None)
    SaleOrder.objects.filter(order_type__isnull=True).update(order_type=order_type)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_company_sale_setting"),
    ]

    operations = [
        migrations.CreateModel(
            name="SaleOrderType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255, verbose_name="Order Type Name")),
                ("description", models.TextField(blank=True)),
                ("prefix_sequence", models.CharField(editable=False, max_length=10, unique=True)),
                (
                    "origin",
                    models.CharField(
                        choices=[("sale", "Sales"), ("pos", "POS"), ("mp", "Marketplace")],
                        default="sale",
                        max_length=10,
                    ),
                ),
                (
                    "companies",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Leave empty to make this Order Type global.",
                        related_name="sale_order_types",
                        to="core.company",
                    ),
                ),
            ],
            options={"ordering": ["name", "pk"]},
        ),
        migrations.CreateModel(
            name="SaleOrderTypeSequence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("quotation_period", models.CharField(blank=True, editable=False, max_length=7)),
                ("quotation_next", models.PositiveIntegerField(default=1, editable=False)),
                ("order_period", models.CharField(blank=True, editable=False, max_length=7)),
                ("order_next", models.PositiveIntegerField(default=1, editable=False)),
                (
                    "company",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sale_order_type_sequences",
                        to="core.company",
                    ),
                ),
                (
                    "order_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sequence_lines",
                        to="core.saleordertype",
                    ),
                ),
            ],
            options={"ordering": ["order_type__name", "company__name", "pk"]},
        ),
        migrations.AddConstraint(
            model_name="saleordertypesequence",
            constraint=models.UniqueConstraint(
                condition=models.Q(("company__isnull", False)),
                fields=("order_type", "company"),
                name="unique_sale_type_company_sequence",
            ),
        ),
        migrations.AddConstraint(
            model_name="saleordertypesequence",
            constraint=models.UniqueConstraint(
                condition=models.Q(("company__isnull", True)),
                fields=("order_type",),
                name="unique_global_sale_type_sequence",
            ),
        ),
        migrations.AddField(
            model_name="saleorder",
            name="order_type",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sale_orders",
                to="core.saleordertype",
            ),
        ),
        migrations.AlterField(
            model_name="saleorder",
            name="number",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.RunPython(create_default_order_type, migrations.RunPython.noop),
    ]
