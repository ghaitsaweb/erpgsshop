import django.db.models.deletion
from django.db import migrations, models


def link_unambiguous_existing_payslips(apps, schema_editor):
    """Backfill only a single, date-overlapping historical contract match."""
    Payslip = apps.get_model("gsshop_hr", "Payslip")
    EmploymentContract = apps.get_model("gsshop_hr", "EmploymentContract")
    for payslip in Payslip.objects.filter(contract__isnull=True).iterator():
        candidates = EmploymentContract.objects.filter(
            employee_id=payslip.employee_id,
            status__in=("active", "expired", "terminated"),
            start_date__lte=payslip.period_end,
        ).filter(models.Q(end_date__isnull=True) | models.Q(end_date__gte=payslip.period_start))
        candidate_ids = list(candidates.order_by("pk").values_list("pk", flat=True)[:2])
        if len(candidate_ids) == 1:
            Payslip.objects.filter(pk=payslip.pk).update(contract_id=candidate_ids[0])


def unlink_backfilled_payslips(apps, schema_editor):
    Payslip = apps.get_model("gsshop_hr", "Payslip")
    Payslip.objects.update(contract=None, payroll_run=None)


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_hr", "0016_contract_workspace_relations"),
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
        migrations.AddField(
            model_name="payslip",
            name="payroll_run",
            field=models.ForeignKey(
                blank=True,
                help_text="The payroll run that produced or includes this payslip.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="payslips",
                to="gsshop_hr.payrollrun",
            ),
        ),
        migrations.RunPython(link_unambiguous_existing_payslips, unlink_backfilled_payslips),
    ]
