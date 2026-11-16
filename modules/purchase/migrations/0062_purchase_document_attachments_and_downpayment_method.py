from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import modules.purchase.models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("gsshop_purchase", "0061_rfq_tender_purchase_team_assignment"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchaseordercontrol",
            name="downpayment_method",
            field=models.CharField(
                choices=[("with_vat", "With VAT"), ("without_vat", "Without VAT")],
                default="with_vat",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="PurchaseDocumentAttachment",
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
                (
                    "file",
                    models.FileField(
                        upload_to=modules.purchase.models.purchase_document_attachment_upload_to,
                        validators=[modules.purchase.models.validate_purchase_document_attachment],
                    ),
                ),
                ("original_name", models.CharField(max_length=255)),
                ("content_type", models.CharField(blank=True, max_length=100)),
                ("file_size", models.PositiveBigIntegerField()),
                (
                    "purchase_order",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="purchase_document_attachments",
                        to="core.purchaseorder",
                    ),
                ),
                (
                    "purchase_request",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="document_attachments",
                        to="gsshop_purchase.purchaserequest",
                    ),
                ),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="uploaded_purchase_document_attachments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-pk"],
                "constraints": [
                    models.CheckConstraint(
                        condition=(
                            models.Q(
                                ("purchase_order__isnull", True),
                                ("purchase_request__isnull", False),
                            )
                            | models.Q(
                                ("purchase_order__isnull", False),
                                ("purchase_request__isnull", True),
                            )
                        ),
                        name="purchase_attachment_exactly_one_document",
                    )
                ],
            },
        ),
    ]
