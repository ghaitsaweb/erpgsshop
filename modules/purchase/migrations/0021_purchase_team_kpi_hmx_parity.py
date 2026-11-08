import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


KPI_DEFINITIONS = {
    "savings_percent": ("Percent", "higher_is_better", "((Budget - Actual) / Budget) x 100"),
    "purchase_price_variance": ("%", "lower_is_better", "(Actual - Baseline) / Baseline"),
    "on_time_po_rate": ("%", "higher_is_better", "On-time PO / Total PO"),
    "pr_to_po_cycle_time": ("Days", "lower_is_better", "PO Date - PR Approved Date"),
    "cycle_time": ("Days", "lower_is_better", "Average confirmation cycle time"),
    "po_count": ("Count", "higher_is_better", "Confirmed purchase order count"),
    "supplier_compliance": ("%", "higher_is_better", "Compliant receipts / Total receipts"),
}


def migrate_legacy_kpi_data(apps, schema_editor):
    del schema_editor
    Team = apps.get_model("gsshop_purchase", "PurchaseTeam")
    Template = apps.get_model("gsshop_purchase", "PurchaseKPITemplate")
    TemplateLine = apps.get_model("gsshop_purchase", "PurchaseKPITemplateLine")
    Target = apps.get_model("gsshop_purchase", "PurchaseKPITarget")
    TargetLine = apps.get_model("gsshop_purchase", "PurchaseKPITargetLine")
    Control = apps.get_model("gsshop_purchase", "PurchaseOrderControl")

    teams = list(Team.objects.order_by("pk"))
    for team in teams:
        team.code = team.code or f"PT-{team.pk:08d}"
        team.status = "active" if team.active else "inactive"
    if teams:
        Team.objects.bulk_update(teams, ["code", "status"], batch_size=500)

    templates = list(Template.objects.order_by("pk"))
    template_lines = []
    for template in templates:
        metric = template.code if template.code in KPI_DEFINITIONS else "savings_percent"
        unit, direction, calculation = KPI_DEFINITIONS[metric]
        template_lines.append(
            TemplateLine(
                template_id=template.pk,
                sequence=10,
                metric=metric,
                weight=100,
                default_target=0,
                unit=template.unit or unit,
                direction=(template.direction if template.direction in {"higher_is_better", "lower_is_better"} else direction),
                calculation=calculation,
            )
        )
    if template_lines:
        TemplateLine.objects.bulk_create(template_lines, batch_size=500)

    template_metric = dict(TemplateLine.objects.order_by("pk").values_list("template_id", "metric"))
    status_map = {
        "draft": "draft",
        "submitted": "waiting_approval",
        "approved": "active",
        "rejected": "rejected",
        "cancelled": "closed",
    }
    targets = list(Target.objects.select_related("team", "template").order_by("pk"))
    target_lines = []
    for target in targets:
        target.company_id = target.team.company_id
        target.name = target.name or f"{target.template.name} {target.period_start:%b %Y}"
        target.template_version = target.template.version
        target.status = status_map.get(target.status, "draft")
        target_lines.append(
            TargetLine(
                target_id=target.pk,
                sequence=10,
                metric=template_metric.get(target.template_id, "savings_percent"),
                target_value=target.target_value,
                actual_value=target.actual_value,
                snapshot_weight=100,
                snapshot_target_value=target.target_value,
                snapshot_unit=target.template.unit or "Percent",
                snapshot_direction=target.template.direction or "higher_is_better",
                snapshot_calculation=KPI_DEFINITIONS[template_metric.get(target.template_id, "savings_percent")][2],
            )
        )
    if targets:
        Target.objects.bulk_update(
            targets,
            ["company", "name", "template_version", "status"],
            batch_size=500,
        )
    if target_lines:
        TargetLine.objects.bulk_create(target_lines, batch_size=500)

    controls = list(
        Control.objects.filter(order__status__in=["confirmed", "completed", "closed"]).select_related("order")
    )
    for control in controls:
        control.confirmed_at = control.order.updated_at
    if controls:
        Control.objects.bulk_update(controls, ["confirmed_at"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_purchase_receiving_fields"),
        ("gsshop_purchase", "0020_vendor_pricelist_import_hmx_parity"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PurchaseKPITargetLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("sequence", models.PositiveIntegerField(default=10)),
                ("metric", models.CharField(choices=[("savings_percent", "Savings %"), ("purchase_price_variance", "Purchase Price Variance"), ("on_time_po_rate", "On-Time Purchase Order"), ("pr_to_po_cycle_time", "PR to PO Cycle Time"), ("cycle_time", "Cycle Time (days)"), ("po_count", "PO Count"), ("supplier_compliance", "Supplier Compliance")], max_length=50)),
                ("target_value", models.DecimalField(decimal_places=4, default=0, max_digits=18)),
                ("actual_value", models.DecimalField(decimal_places=4, default=0, editable=False, max_digits=18)),
                ("snapshot_weight", models.DecimalField(decimal_places=2, default=0, max_digits=5)),
                ("snapshot_target_value", models.DecimalField(decimal_places=4, default=0, editable=False, max_digits=18)),
                ("snapshot_unit", models.CharField(blank=True, editable=False, max_length=64)),
                ("snapshot_direction", models.CharField(blank=True, editable=False, max_length=24)),
                ("snapshot_calculation", models.TextField(blank=True, editable=False)),
                ("calculation_snapshot", models.JSONField(blank=True, default=dict, editable=False)),
                ("last_calculated_at", models.DateTimeField(blank=True, editable=False, null=True)),
            ],
            options={"ordering": ["target", "sequence", "pk"]},
        ),
        migrations.CreateModel(
            name="PurchaseKPITemplateLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("sequence", models.PositiveIntegerField(default=10)),
                ("metric", models.CharField(choices=[("savings_percent", "Savings %"), ("purchase_price_variance", "Purchase Price Variance"), ("on_time_po_rate", "On-Time Purchase Order"), ("pr_to_po_cycle_time", "PR to PO Cycle Time"), ("cycle_time", "Cycle Time (days)"), ("po_count", "PO Count"), ("supplier_compliance", "Supplier Compliance")], max_length=50)),
                ("weight", models.DecimalField(decimal_places=2, default=0, max_digits=5)),
                ("default_target", models.DecimalField(decimal_places=4, default=0, max_digits=18)),
                ("unit", models.CharField(blank=True, max_length=64)),
                ("direction", models.CharField(blank=True, choices=[("higher_is_better", "Higher is Better"), ("lower_is_better", "Lower is Better")], max_length=24)),
                ("calculation", models.TextField(blank=True)),
            ],
            options={"ordering": ["template", "sequence", "pk"]},
        ),
        migrations.RemoveConstraint(model_name="purchasekpitarget", name="unique_purchase_kpi_target_period"),
        migrations.AddField(model_name="purchasekpitarget", name="activated_at", field=models.DateTimeField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="purchasekpitarget", name="activated_by", field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="activated_purchase_kpi_targets", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="purchasekpitarget", name="active", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="purchasekpitarget", name="buyer", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="purchase_kpi_targets", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="purchasekpitarget", name="closed_at", field=models.DateTimeField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="purchasekpitarget", name="closed_by", field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="closed_purchase_kpi_targets", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="purchasekpitarget", name="company", field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="purchase_kpi_targets", to="core.company")),
        migrations.AddField(model_name="purchasekpitarget", name="name", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="purchasekpitarget", name="notes", field=models.TextField(blank=True)),
        migrations.AddField(model_name="purchasekpitarget", name="period_type", field=models.CharField(choices=[("monthly", "Monthly"), ("quarterly", "Quarterly"), ("annual", "Annual")], default="monthly", max_length=20)),
        migrations.AddField(model_name="purchasekpitarget", name="target_scope", field=models.CharField(choices=[("team", "Team"), ("buyer", "Purchase Person")], default="team", max_length=20)),
        migrations.AddField(model_name="purchasekpitarget", name="template_batch", field=models.UUIDField(default=uuid.uuid4, editable=False)),
        migrations.AddField(model_name="purchasekpitarget", name="template_version", field=models.PositiveIntegerField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="purchasekpitemplate", name="version", field=models.PositiveIntegerField(default=1, editable=False)),
        migrations.AddField(model_name="purchaseordercontrol", name="assigned_to", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="assigned_purchase_order_controls", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="purchaseordercontrol", name="confirmed_at", field=models.DateTimeField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name="purchaseteam", name="code", field=models.CharField(blank=True, editable=False, max_length=100)),
        migrations.AddField(model_name="purchaseteam", name="order_types", field=models.ManyToManyField(blank=True, related_name="purchase_teams", to="gsshop_purchase.purchaseordertype")),
        migrations.AddField(model_name="purchaseteam", name="parameter", field=models.CharField(choices=[("product_category", "Product Category"), ("order_type", "Order Type"), ("vendor_category", "Vendor Category")], default="product_category", max_length=24)),
        migrations.AddField(model_name="purchaseteam", name="product_categories", field=models.ManyToManyField(blank=True, related_name="purchase_teams", to="gsshop_purchase.purchaseproductcategory")),
        migrations.AddField(model_name="purchaseteam", name="status", field=models.CharField(choices=[("active", "Active"), ("inactive", "Inactive")], default="active", max_length=16)),
        migrations.AddField(model_name="purchaseteam", name="vendor_categories", field=models.ManyToManyField(blank=True, related_name="purchase_teams", to="gsshop_purchase.vendorcategory")),
        migrations.AddField(model_name="purchaseteammember", name="categories", field=models.ManyToManyField(blank=True, related_name="purchase_team_member_assignments", to="gsshop_purchase.purchaseproductcategory")),
        migrations.AddField(model_name="purchaseteammember", name="is_primary", field=models.BooleanField(default=False)),
        migrations.AlterField(model_name="purchasekpitarget", name="status", field=models.CharField(choices=[("draft", "Draft"), ("waiting_approval", "Waiting Approval"), ("active", "Active"), ("rejected", "Rejected"), ("closed", "Closed")], default="draft", max_length=20)),
        migrations.AlterField(model_name="purchasekpitarget", name="target_value", field=models.DecimalField(decimal_places=4, default=0, max_digits=18)),
        migrations.AlterField(model_name="purchasekpitarget", name="team", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="kpi_targets", to="gsshop_purchase.purchaseteam")),
        migrations.AlterField(model_name="purchasekpitemplate", name="code", field=models.CharField(blank=True, max_length=30)),
        migrations.AlterField(model_name="purchasekpitemplate", name="direction", field=models.CharField(blank=True, default="higher_is_better", max_length=20)),
        migrations.AlterField(model_name="purchasekpitemplate", name="unit", field=models.CharField(blank=True, default="percent", max_length=40)),
        migrations.AddField(model_name="purchasekpitargetline", name="last_calculated_by", field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="calculated_purchase_kpi_lines", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="purchasekpitargetline", name="target", field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="gsshop_purchase.purchasekpitarget")),
        migrations.AddField(model_name="purchasekpitemplateline", name="template", field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="gsshop_purchase.purchasekpitemplate")),
        migrations.RunPython(migrate_legacy_kpi_data, migrations.RunPython.noop),
        migrations.AlterField(model_name="purchasekpitarget", name="company", field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchase_kpi_targets", to="core.company")),
        migrations.AlterField(model_name="purchasekpitarget", name="name", field=models.CharField(max_length=255)),
        migrations.AddConstraint(model_name="purchasekpitarget", constraint=models.UniqueConstraint(condition=models.Q(("team__isnull", False)), fields=("team", "template", "period_start"), name="unique_purchase_kpi_target_team_period")),
        migrations.AddConstraint(model_name="purchasekpitarget", constraint=models.UniqueConstraint(condition=models.Q(("buyer__isnull", False)), fields=("buyer", "template", "period_start"), name="unique_purchase_kpi_target_buyer_period")),
        migrations.AddConstraint(model_name="purchasekpitargetline", constraint=models.UniqueConstraint(fields=("target", "metric"), name="unique_purchase_kpi_metric_per_target")),
        migrations.AddConstraint(model_name="purchasekpitemplateline", constraint=models.UniqueConstraint(fields=("template", "metric"), name="unique_purchase_kpi_metric_per_template")),
    ]
