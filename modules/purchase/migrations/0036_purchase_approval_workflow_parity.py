import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


WORKFLOW_SPECS = (
    {
        "model": "vendorprofile",
        "description": "HMX-compatible Purchase Vendor approval.",
        "status_field": "status",
        "perimeter": ["company", "partner", "category"],
        "perimeter_name": "Vendor company and category",
        "rule": "Purchase Vendor Default",
    },
    {
        "model": "vendorpricelist",
        "description": "HMX-compatible Vendor Pricelist approval.",
        "status_field": "status",
        "perimeter": ["company", "vendor"],
        "perimeter_name": "Pricelist company and vendor",
        "rule": "Vendor Pricelist Default",
    },
    {
        "model": "vendorpricelistamendment",
        "description": "HMX-compatible Vendor Pricelist Amendment approval.",
        "status_field": "status",
        "perimeter": ["pricelist.company", "pricelist.vendor"],
        "perimeter_name": "Amendment pricelist company and vendor",
        "rule": "Vendor Pricelist Amendment Default",
    },
    {
        "model": "blanketorder",
        "description": "HMX-compatible Blanket Order approval.",
        "status_field": "status",
        "perimeter": ["company", "vendor", "agreement_value"],
        "perimeter_name": "Blanket company, vendor, and value",
        "rule": "Blanket Order Default",
    },
    {
        "model": "purchaserequest",
        "description": "HMX-compatible Purchase Request approval with line decisions.",
        "status_field": "status",
        "perimeter": ["company", "requestor"],
        "perimeter_name": "Purchase Request company and requestor",
        "rule": "Purchase Request Default",
        "groups": ("Purchase Manager", "Purchase Requestor Manager"),
    },
    {
        "model": "vendorperformanceevaluation",
        "description": "HMX-compatible Vendor Performance Evaluation approval.",
        "status_field": "status",
        "perimeter": ["company", "vendor", "template"],
        "perimeter_name": "Evaluation company, vendor, and template",
        "rule": "Vendor Performance Evaluation Default",
    },
    {
        "model": "purchasekpitarget",
        "description": "HMX-compatible Purchase KPI Target approval.",
        "status_field": "status",
        "perimeter": ["company", "team", "buyer", "target_value"],
        "perimeter_name": "KPI company, scope, and value",
        "rule": "Purchase KPI Target Default",
    },
    {
        "model": "purchasetender",
        "description": "HMX-compatible Tender and Tender Decision approval.",
        "status_field": "status",
        "perimeter": ["company", "award_strategy", "budget_amount"],
        "perimeter_name": "Tender company, strategy, and value",
        "rule": "Tender Decision Default",
    },
)


def create_purchase_approval_workflows(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ApprovalWorkflow = apps.get_model("approval_workflow", "ApprovalWorkflow")
    ApprovalWorkflowPerimeter = apps.get_model(
        "approval_workflow",
        "ApprovalWorkflowPerimeter",
    )
    ApprovalRule = apps.get_model("approval_workflow", "ApprovalRule")
    ApprovalRuleLevel = apps.get_model("approval_workflow", "ApprovalRuleLevel")
    ApprovalRuleLevelAssignee = apps.get_model(
        "approval_workflow",
        "ApprovalRuleLevelAssignee",
    )

    for spec in WORKFLOW_SPECS:
        content_type = ContentType.objects.filter(
            app_label="gsshop_purchase",
            model=spec["model"],
        ).first()
        if content_type is None:
            continue
        workflow, _created = ApprovalWorkflow.objects.get_or_create(
            content_type=content_type,
            defaults={
                "description": spec["description"],
                "status_field": spec["status_field"],
                "primary_perimeter": spec["perimeter"],
                "active": True,
            },
        )
        perimeter = ApprovalWorkflowPerimeter.objects.filter(
            workflow=workflow,
            perimeter_type="primary",
        ).first()
        if perimeter is None:
            perimeter = ApprovalWorkflowPerimeter.objects.create(
                workflow=workflow,
                name=spec["perimeter_name"],
                perimeter_type="primary",
                field_names=spec["perimeter"],
                priority=10,
            )
        rule, _created = ApprovalRule.objects.get_or_create(
            name=spec["rule"],
            defaults={
                "workflow": workflow,
                "perimeter": perimeter,
                "description": spec["description"],
                "state": "associated",
                "priority": 100,
                "trigger_conditions": [],
                "approval_levels": [],
                "use_relational_conditions": True,
                "use_relational_levels": True,
                "require_approve_reason": False,
                "notification_settings": {
                    "submitted": True,
                    "approved": True,
                    "rejected": True,
                },
            },
        )
        level, _created = ApprovalRuleLevel.objects.get_or_create(
            rule=rule,
            sequence=10,
            defaults={
                "name": "Team Purchase",
                "approval_type": "access_profile",
                "minimum_approvals": 1,
                "active": True,
            },
        )
        for sequence, group_name in enumerate(
            spec.get("groups", ("Purchase Manager",)),
            start=1,
        ):
            ApprovalRuleLevelAssignee.objects.get_or_create(
                level=level,
                role="approver",
                source="group",
                group_name=group_name,
                defaults={"sequence": sequence * 10, "active": True},
            )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("gsshop_purchase", "0035_purchase_order_approval_workflow"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vendorpricelistamendment",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("submitted", "Submitted"),
                    ("waiting_approval", "Waiting For Approval"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("cancelled", "Cancelled"),
                ],
                default="draft",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="purchasekpitargetline",
            name="activated_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="purchasekpitargetline",
            name="activated_by",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="activated_purchase_kpi_target_lines",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(
            create_purchase_approval_workflows,
            migrations.RunPython.noop,
        ),
    ]
