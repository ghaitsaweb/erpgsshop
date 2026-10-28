"""Link historical payslips only when one dated contract matches.

The migration is resumable: each small batch commits independently, and rows
with an existing contract link are never changed. Reversing it keeps the link.
"""

from collections import defaultdict

from django.db import migrations, models


BATCH_SIZE = 500
ELIGIBLE_STATUSES = ("active", "expired", "terminated")


def _backfill_portable(Payslip, EmploymentContract, alias, ids):
    rows = list(
        Payslip.objects.using(alias)
        .filter(pk__in=ids, contract__isnull=True)
        .values("pk", "employee_id", "period_start", "period_end")
    )
    if not rows:
        return
    contracts = EmploymentContract.objects.using(alias).filter(
        employee_id__in={row["employee_id"] for row in rows},
        status__in=ELIGIBLE_STATUSES,
        start_date__lte=max(row["period_end"] for row in rows),
    ).filter(models.Q(end_date__isnull=True) | models.Q(end_date__gte=min(row["period_start"] for row in rows)))
    by_employee = defaultdict(list)
    for contract in contracts.values("pk", "employee_id", "start_date", "end_date"):
        by_employee[contract["employee_id"]].append(contract)
    changes = []
    for row in rows:
        matches = [
            contract for contract in by_employee[row["employee_id"]]
            if contract["start_date"] <= row["period_end"]
            and (contract["end_date"] is None or contract["end_date"] >= row["period_start"])
        ]
        if len(matches) == 1:
            changes.append(Payslip(pk=row["pk"], contract_id=matches[0]["pk"]))
    if changes:
        Payslip.objects.using(alias).bulk_update(changes, ["contract"], batch_size=BATCH_SIZE)


def backfill_unambiguous_contracts(apps, schema_editor):
    Payslip = apps.get_model("gsshop_hr", "Payslip")
    EmploymentContract = apps.get_model("gsshop_hr", "EmploymentContract")
    connection = schema_editor.connection
    alias = connection.alias
    last_id = 0
    max_id = Payslip.objects.using(alias).order_by("-pk").values_list("pk", flat=True).first() or 0
    quote = connection.ops.quote_name
    payslip_table = quote(Payslip._meta.db_table)
    contract_table = quote(EmploymentContract._meta.db_table)

    while last_id < max_id:
        ids = list(
            Payslip.objects.using(alias)
            .filter(pk__gt=last_id, pk__lte=max_id)
            .order_by("pk")
            .values_list("pk", flat=True)[:BATCH_SIZE]
        )
        if not ids:
            break
        batch_end = ids[-1]
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    WITH candidates AS (
                        SELECT p.id AS payslip_id, MIN(c.id) AS contract_id
                        FROM {payslip_table} p
                        JOIN {contract_table} c
                          ON c.employee_id = p.employee_id
                         AND c.status IN (%s, %s, %s)
                         AND c.start_date <= p.period_end
                         AND (c.end_date IS NULL OR c.end_date >= p.period_start)
                        WHERE p.id > %s AND p.id <= %s AND p.contract_id IS NULL
                        GROUP BY p.id
                        HAVING COUNT(*) = 1
                    )
                    UPDATE {payslip_table} p
                       SET contract_id = candidates.contract_id
                      FROM candidates
                     WHERE p.id = candidates.payslip_id AND p.contract_id IS NULL
                    """,
                    [*ELIGIBLE_STATUSES, last_id, batch_end],
                )
        else:
            _backfill_portable(Payslip, EmploymentContract, alias, ids)
        last_id = batch_end


class Migration(migrations.Migration):
    atomic = False
    dependencies = [
        ("gsshop_hr", "0036_contract_workspace_schema"),
    ]
    operations = [migrations.RunPython(backfill_unambiguous_contracts, migrations.RunPython.noop)]
