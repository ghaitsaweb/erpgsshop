import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_sale_quotation_email"),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanySaleSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "quotation_expiry_days",
                    models.PositiveIntegerField(
                        default=7,
                        help_text="Default number of days before a new Sales quotation expires.",
                        validators=[django.core.validators.MinValueValidator(1)],
                        verbose_name="Quotation Expiry Period",
                    ),
                ),
                (
                    "default_delivery_date",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Number of days added to the order date for new Sales Orders.",
                        verbose_name="Default Delivery Date",
                    ),
                ),
                ("allow_order_revision", models.BooleanField(default=True)),
                (
                    "revised_sequence_rule",
                    models.CharField(
                        choices=[
                            ("append_suffix", "Append Revision Suffix"),
                            ("new_sequence", "Follow Sequence Master Data"),
                        ],
                        default="append_suffix",
                        max_length=50,
                    ),
                ),
                (
                    "company",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sale_setting",
                        to="core.company",
                    ),
                ),
            ],
            options={"ordering": ["company__name"]},
        ),
    ]
