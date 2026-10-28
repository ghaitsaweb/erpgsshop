"""Apply the remote contract schema after the local payroll migration branch."""

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gsshop_hr", "0035_integrate_contract_workspace"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="payslip",
            name="contract",
            field=models.ForeignKey(
                blank=True,
                help_text="The effective employment contract used for this payslip.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="payslips",
                to="gsshop_hr.employmentcontract",
            ),
        ),
        migrations.AlterField(
            model_name="employmentcontract",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("submitted", "Submitted for Approval"),
                    ("active", "Active"),
                    ("rejected", "Rejected"),
                    ("cancelled", "Cancelled"),
                    ("expired", "Expired"),
                    ("terminated", "Terminated"),
                ],
                default="draft",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="ContractAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=160)),
                ("category", models.CharField(choices=[
                    ("offer", "Offer"), ("amendment", "Amendment"),
                    ("compliance", "Compliance"), ("identity", "Identity"),
                    ("other", "Other"),
                ], default="other", max_length=16)),
                ("file_url", models.URLField(blank=True)),
                ("attachment_name", models.CharField(blank=True, editable=False, max_length=255)),
                ("attachment_content_type", models.CharField(blank=True, editable=False, max_length=120)),
                ("attachment_content", models.BinaryField(blank=True, null=True)),
                ("issued_date", models.DateField(blank=True, null=True)),
                ("expiry_date", models.DateField(blank=True, null=True)),
                ("verified", models.BooleanField(default=False)),
                ("notes", models.TextField(blank=True)),
                ("contract", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="attachments", to="gsshop_hr.employmentcontract",
                )),
                ("uploaded_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="contract_attachments_uploaded", to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"ordering": ["-expiry_date", "title"]},
        ),
        migrations.CreateModel(
            name="CompensationBenefitLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category", models.CharField(choices=[
                    ("basic", "Basic Salary"), ("allowance", "Allowance"),
                    ("deduction", "Deduction"), ("overtime", "Overtime Component"),
                ], max_length=16)),
                ("label", models.CharField(max_length=120)),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ("sequence", models.PositiveSmallIntegerField(default=10)),
                ("benefit", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="component_lines", to="gsshop_hr.compensationbenefit",
                )),
                ("component", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="compensation_benefit_lines", to="gsshop_hr.salarycomponent",
                )),
            ],
            options={
                "ordering": ["sequence", "pk"],
                "indexes": [models.Index(fields=["benefit", "sequence"], name="hr_comp_benefit_line_idx")],
            },
        ),
        migrations.CreateModel(
            name="ContractOvertimeComponent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("rate_multiplier", models.DecimalField(decimal_places=3, default=1, max_digits=7)),
                ("effective_date", models.DateField(default=django.utils.timezone.localdate)),
                ("end_date", models.DateField(blank=True, null=True)),
                ("active", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True)),
                ("component", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="contract_overtime_components", to="gsshop_hr.salarycomponent",
                )),
                ("contract", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="overtime_components", to="gsshop_hr.employmentcontract",
                )),
            ],
            options={
                "ordering": ["-effective_date", "component__name"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("contract", "component", "effective_date"),
                        name="unique_contract_overtime_effective_date",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("rate_multiplier__gt", 0)),
                        name="contract_overtime_multiplier_positive",
                    ),
                ],
            },
        ),
    ]
