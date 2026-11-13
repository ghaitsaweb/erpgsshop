from django.db import migrations, models
import django.db.models.deletion


DURATION_CHOICES = [
    ("day", "Day"),
    ("week", "Week"),
    ("month", "Month"),
    ("year", "Year"),
]


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_purchase_order_generated_number"),
        ("gsshop_purchase", "0045_po_vendor_moq_trace"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="duration_unit",
            field=models.CharField(choices=DURATION_CHOICES, default="day", max_length=8),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="expected_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="expected_return_date",
            field=models.DateField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="is_multiple_expected_date",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="is_multiple_rental_duration",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="rental_duration",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="total_rental_duration",
            field=models.PositiveIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="purchaseorderlinepricingprovenance",
            name="cost_center",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="purchaseorderlinepricingprovenance",
            name="expected_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="purchaseorderlinepricingprovenance",
            name="is_rental",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="purchaseorderlinepricingprovenance",
            name="rent_duration",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="purchaseorderlinepricingprovenance",
            name="rental_end_date",
            field=models.DateField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="purchaseorderlinepricingprovenance",
            name="rental_period",
            field=models.CharField(choices=DURATION_CHOICES, default="day", max_length=8),
        ),
        migrations.AddField(
            model_name="purchaseorderlinepricingprovenance",
            name="total_rental_duration",
            field=models.PositiveIntegerField(default=0, editable=False),
        ),
        migrations.CreateModel(
            name="PurchaseProductRentalProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_rental", models.BooleanField(default=False)),
                (
                    "product",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="purchase_rental_profile",
                        to="core.product",
                    ),
                ),
            ],
            options={"ordering": ["product__name"]},
        ),
    ]
