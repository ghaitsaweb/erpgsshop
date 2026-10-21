import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gsshop_hr", "0010_attendance_change_raw_snapshot"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ContractLetterTemplate",
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
                ("code", models.CharField(max_length=30)),
                ("name", models.CharField(max_length=120)),
                ("letter_type", models.CharField(default="Employment Contract", max_length=80)),
                (
                    "content",
                    models.TextField(
                        help_text=(
                            "Supported variables: {{ company_name }}, {{ contract_number }}, {{ employee_id }}, "
                            "{{ employee_name }}, {{ start_date }}, {{ end_date }}, and {{ base_salary }}."
                        )
                    ),
                ),
                ("active", models.BooleanField(default=True)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="contract_letter_templates",
                        to="core.company",
                    ),
                ),
            ],
            options={
                "ordering": ["name"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("company", "code"),
                        name="unique_contract_letter_template_code",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="employmentcontract",
            name="letter_template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="contracts",
                to="gsshop_hr.contractlettertemplate",
            ),
        ),
        migrations.AddField(
            model_name="employmentcontract",
            name="salary_package",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="contracts",
                to="gsshop_hr.salarypackage",
            ),
        ),
        migrations.AddField(
            model_name="compensationbenefit",
            name="contract",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="compensation_snapshots",
                to="gsshop_hr.employmentcontract",
            ),
        ),
        migrations.AddField(
            model_name="contractletter",
            name="content",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="contractletter",
            name="generated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="generated_contract_letters",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="contractletter",
            name="template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="generated_letters",
                to="gsshop_hr.contractlettertemplate",
            ),
        ),
        migrations.AddConstraint(
            model_name="compensationbenefit",
            constraint=models.UniqueConstraint(
                fields=("contract", "effective_date"),
                name="unique_contract_compensation_effective_date",
            ),
        ),
        migrations.AddConstraint(
            model_name="contractallowance",
            constraint=models.UniqueConstraint(
                fields=("contract", "component", "effective_date"),
                name="unique_contract_allowance_effective_date",
            ),
        ),
        migrations.AddConstraint(
            model_name="contractallowance",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount__gte", 0)),
                name="contract_allowance_amount_nonnegative",
            ),
        ),
        migrations.AddIndex(
            model_name="contractallowance",
            index=models.Index(
                fields=["contract", "active", "effective_date"],
                name="hr_contract_allow_active_idx",
            ),
        ),
    ]
