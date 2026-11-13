from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0054_cost_center_mode_optional"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="vendorprofile",
            name="mobile_number",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="vendorprofile",
            name="bank_account",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="vendorprofile",
            name="parent_vendor",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="subsidiary_vendors",
                to="gsshop_purchase.vendorprofile",
            ),
        ),
        migrations.CreateModel(
            name="VendorChangeProposal",
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
                ("proposed_values", models.JSONField(default=dict)),
                ("original_snapshot", models.JSONField(default=dict, editable=False)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("waiting_approval", "Waiting Approval"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="draft",
                        max_length=24,
                    ),
                ),
                ("reviewed_at", models.DateTimeField(blank=True, editable=False, null=True)),
                ("review_reason", models.TextField(blank=True, editable=False)),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="requested_vendor_changes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reviewed_vendor_changes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "vendor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="change_proposals",
                        to="gsshop_purchase.vendorprofile",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-pk"],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(status__in=["draft", "waiting_approval"]),
                        fields=("vendor",),
                        name="unique_open_vendor_change_proposal",
                    )
                ],
            },
        ),
    ]
