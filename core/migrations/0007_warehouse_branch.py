from django.db import migrations, models
import django.db.models.deletion


def assign_warehouses_to_main_branch(apps, schema_editor):
    Warehouse = apps.get_model("core", "Warehouse")
    Branch = apps.get_model("organization", "Branch")
    main_by_company = dict(
        Branch.objects.filter(is_main=True).values_list("company_id", "pk")
    )
    for warehouse in Warehouse.objects.filter(branch__isnull=True).iterator():
        branch_id = main_by_company.get(warehouse.company_id)
        if branch_id:
            Warehouse.objects.filter(pk=warehouse.pk).update(branch_id=branch_id)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_alter_purchaseorder_status"),
        ("organization", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="warehouse",
            name="branch",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="warehouses",
                to="organization.branch",
            ),
        ),
        migrations.RunPython(assign_warehouses_to_main_branch, migrations.RunPython.noop),
    ]
