from django.db import migrations


def create_purchase_order_approval_workflow(apps, schema_editor):
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

    content_type = ContentType.objects.filter(
        app_label="core",
        model="purchaseorder",
    ).first()
    if content_type is None:
        return

    perimeter_fields = ["company", "vendor", "total"]
    workflow, _created = ApprovalWorkflow.objects.get_or_create(
        content_type=content_type,
        defaults={
            "description": "HMX-compatible RFQ and Purchase Order approval.",
            "status_field": "status",
            "primary_perimeter": perimeter_fields,
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
            name="Purchase company, vendor, and value",
            perimeter_type="primary",
            field_names=perimeter_fields,
            priority=10,
        )
    rule, _created = ApprovalRule.objects.get_or_create(
        name="Purchase Order Default",
        defaults={
            "workflow": workflow,
            "perimeter": perimeter,
            "description": "RFQs and Purchase Orders approved by Purchase management.",
            "state": "associated",
            "priority": 100,
            "trigger_conditions": [],
            "approval_levels": [],
            "use_relational_conditions": True,
            "use_relational_levels": True,
            "require_approve_reason": True,
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
            "name": "Purchase Management",
            "approval_type": "access_profile",
            "minimum_approvals": 1,
            "active": True,
        },
    )
    ApprovalRuleLevelAssignee.objects.get_or_create(
        level=level,
        role="approver",
        source="group",
        group_name="Purchase Manager",
        defaults={"sequence": 10, "active": True},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("approval_workflow", "0009_sale_order_workflow"),
        ("gsshop_purchase", "0034_remove_blanketorderline_unique_blanket_order_product"),
    ]

    operations = [
        migrations.RunPython(
            create_purchase_order_approval_workflow,
            migrations.RunPython.noop,
        ),
    ]
