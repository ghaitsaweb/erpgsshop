import django.db.models.deletion
from django.db import migrations, models


EXCLUDED_LABELS = {
    "core.company",
    "organization.branch",
    "organization.recordorganizationscope",
    "organization.userorganizationaccess",
}


def backfill_record_scopes(apps, schema_editor):
    database = schema_editor.connection.alias
    ContentType = apps.get_model("contenttypes", "ContentType")
    Branch = apps.get_model("organization", "Branch")
    Scope = apps.get_model("organization", "RecordOrganizationScope")

    branch_by_company = {}
    branch_rows = (
        Branch.objects.using(database)
        .order_by("company_id", "-is_main", "-active", "sequence", "pk")
        .values_list("company_id", "pk")
    )
    for company_id, branch_id in branch_rows.iterator():
        branch_by_company.setdefault(company_id, branch_id)

    for model in apps.get_models():
        if model._meta.label_lower in EXCLUDED_LABELS or model._meta.proxy or not model._meta.managed:
            continue
        company_field = None
        has_direct_branch = False
        for field in model._meta.concrete_fields:
            if not field.is_relation or not (field.many_to_one or field.one_to_one) or not field.related_model:
                continue
            related_label = field.related_model._meta.label_lower
            if related_label == "core.company":
                company_field = field
            elif related_label == "organization.branch":
                has_direct_branch = True
        if company_field is None or has_direct_branch:
            continue

        content_type, _created = ContentType.objects.using(database).get_or_create(
            app_label=model._meta.app_label,
            model=model._meta.model_name,
        )
        batch = []
        values = model._default_manager.using(database).values_list("pk", company_field.attname)
        for object_id, company_id in values.iterator(chunk_size=2000):
            branch_id = branch_by_company.get(company_id)
            if not branch_id:
                continue
            batch.append(
                Scope(
                    content_type_id=content_type.pk,
                    object_id=str(object_id),
                    company_id=company_id,
                    branch_id=branch_id,
                )
            )
            if len(batch) >= 2000:
                Scope.objects.using(database).bulk_create(batch, ignore_conflicts=True)
                batch = []
        if batch:
            Scope.objects.using(database).bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("core", "0007_warehouse_branch"),
        ("gsshop_ai", "0001_initial"),
        ("gsshop_construction", "0006_constructionboqcostline_job_position_and_more"),
        ("gsshop_crm", "0001_initial"),
        ("gsshop_education", "0001_initial"),
        ("gsshop_finance", "0006_accountdeposit_deposit_type_and_more"),
        ("gsshop_hr", "0007_alter_facerecognitionprofile_options"),
        ("gsshop_operations", "0001_initial"),
        ("gsshop_pos", "0004_posorder_preorder_and_return_lifecycle"),
        ("gsshop_purchase", "0004_productbudgetmovement_approved_by"),
        ("organization", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="RecordOrganizationScope",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("object_id", models.CharField(max_length=255)),
                (
                    "branch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="record_organization_scopes",
                        to="organization.branch",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="record_organization_scopes",
                        to="core.company",
                    ),
                ),
                (
                    "content_type",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="contenttypes.contenttype"),
                ),
            ],
            options={
                "ordering": ["content_type_id", "object_id"],
                "indexes": [
                    models.Index(fields=["content_type", "object_id"], name="org_scope_record_idx"),
                    models.Index(fields=["company", "branch"], name="org_scope_company_branch_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("content_type", "object_id"),
                        name="unique_organization_scope_per_record",
                    ),
                ],
            },
        ),
        migrations.RunPython(backfill_record_scopes, migrations.RunPython.noop),
    ]
