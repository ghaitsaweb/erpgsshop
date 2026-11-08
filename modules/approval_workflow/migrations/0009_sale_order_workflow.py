from django.db import migrations


def create_sale_order_workflow(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ApprovalWorkflow = apps.get_model("approval_workflow", "ApprovalWorkflow")
    ApprovalWorkflowPerimeter = apps.get_model("approval_workflow", "ApprovalWorkflowPerimeter")
    ApprovalRule = apps.get_model("approval_workflow", "ApprovalRule")
    ApprovalRuleLevel = apps.get_model("approval_workflow", "ApprovalRuleLevel")
    ApprovalRuleLevelAssignee = apps.get_model(
        "approval_workflow",
        "ApprovalRuleLevelAssignee",
    )

    content_type = ContentType.objects.filter(app_label="core", model="saleorder").first()
    if content_type is None:
        return
    perimeter_fields = ["company", "customer", "total"]
    workflow, _created = ApprovalWorkflow.objects.get_or_create(
        content_type=content_type,
        defaults={
            "description": "HMX-compatible Sales quotation approval.",
            "status_field": "status",
            "primary_perimeter": perimeter_fields,
            "active": True,
        },
    )
    perimeter, _created = ApprovalWorkflowPerimeter.objects.get_or_create(
        workflow=workflow,
        name="Sales company, customer, and value",
        defaults={
            "perimeter_type": "primary",
            "field_names": perimeter_fields,
            "priority": 10,
        },
    )
    rule, _created = ApprovalRule.objects.get_or_create(
        name="Sales Order Default",
        defaults={
            "workflow": workflow,
            "perimeter": perimeter,
            "description": "Sales quotations approved by Sales management.",
            "state": "associated",
            "priority": 100,
            "trigger_conditions": [],
            "approval_levels": [],
            "use_relational_conditions": True,
            "use_relational_levels": True,
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
            "name": "Sales Management",
            "approval_type": "access_profile",
            "minimum_approvals": 1,
            "active": True,
        },
    )
    for sequence, group_name in enumerate(
        ("Sales Super User", "Sales Manager", "Sales Administrator"),
        start=10,
    ):
        ApprovalRuleLevelAssignee.objects.get_or_create(
            level=level,
            role="approver",
            source="group",
            group_name=group_name,
            defaults={"sequence": sequence, "active": True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("approval_workflow", "0008_repair_relational_rule_levels"),
        ("core", "0013_sale_order_hmx_workflow"),
    ]

    operations = [
        migrations.RunPython(create_sale_order_workflow, migrations.RunPython.noop),
    ]
