from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_existing_main_branches(apps, schema_editor):
    Company = apps.get_model("core", "Company")
    Branch = apps.get_model("organization", "Branch")
    for company in Company.objects.all().iterator():
        base = (company.code or f"COMPANY-{company.pk}").strip().upper()
        Branch.objects.get_or_create(
            company_id=company.pk,
            is_main=True,
            defaults={
                "code": f"{base}-MAIN"[:30],
                "name": f"{company.name} Main Branch",
                "active": company.active,
                "sequence": 0,
            },
        )


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0006_alter_purchaseorder_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="Branch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.CharField(max_length=30)),
                ("name", models.CharField(max_length=160)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("phone", models.CharField(blank=True, max_length=40)),
                ("address", models.TextField(blank=True)),
                ("sequence", models.PositiveIntegerField(default=10)),
                ("is_main", models.BooleanField(default=False)),
                ("active", models.BooleanField(default=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="branches", to="core.company")),
                ("parent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="children", to="organization.branch")),
            ],
            options={"ordering": ["company__name", "sequence", "name"]},
        ),
        migrations.CreateModel(
            name="UserOrganizationAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("all_companies", models.BooleanField(default=False, help_text="Allow every active company, including companies created later.")),
                ("all_branches", models.BooleanField(default=False, help_text="Allow every active branch inside the allowed companies.")),
                ("branches", models.ManyToManyField(blank=True, related_name="organization_users", to="organization.branch")),
                ("companies", models.ManyToManyField(blank=True, related_name="organization_users", to="core.company")),
                ("default_branch", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="default_for_users", to="organization.branch")),
                ("default_company", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="default_for_users", to="core.company")),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="organization_access", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "User organization access", "verbose_name_plural": "User organization access"},
        ),
        migrations.AddConstraint(
            model_name="branch",
            constraint=models.UniqueConstraint(fields=("company", "code"), name="unique_branch_code_per_company"),
        ),
        migrations.AddConstraint(
            model_name="branch",
            constraint=models.UniqueConstraint(condition=models.Q(("is_main", True)), fields=("company",), name="one_main_branch_per_company"),
        ),
        migrations.RunPython(create_existing_main_branches, migrations.RunPython.noop),
    ]
