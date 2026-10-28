from django.db import migrations


def create_contract_workflow(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ApprovalWorkflow = apps.get_model("approval_workflow", "ApprovalWorkflow")
    ApprovalWorkflowPerimeter = apps.get_model("approval_workflow", "ApprovalWorkflowPerimeter")
    ApprovalRule = apps.get_model("approval_workflow", "ApprovalRule")
    ApprovalRuleLevel = apps.get_model("approval_workflow", "ApprovalRuleLevel")
    ApprovalRuleLevelAssignee = apps.get_model("approval_workflow", "ApprovalRuleLevelAssignee")

    content_type = ContentType.objects.filter(app_label="gsshop_hr", model="employmentcontract").first()
    if content_type is None:
        return
    workflow, _created = ApprovalWorkflow.objects.get_or_create(
        content_type=content_type,
        defaults={
            "description": "Employment-contract activation with a frozen approval route.",
            "status_field": "status",
            "primary_perimeter": ["employee", "contract_type", "start_date", "end_date"],
            "active": True,
        },
    )
    perimeter, _created = ApprovalWorkflowPerimeter.objects.get_or_create(
        workflow=workflow,
        name="Employee contract",
        defaults={
            "perimeter_type": "primary",
            "field_names": ["employee", "contract_type", "start_date", "end_date"],
            "priority": 10,
        },
    )
    rule, _created = ApprovalRule.objects.get_or_create(
        name="Employment Contract Default",
        defaults={
            "workflow": workflow,
            "perimeter": perimeter,
            "description": "Contracts require HR or direct-supervisor approval before activation.",
            "state": "associated",
            "priority": 100,
            "trigger_conditions": [],
            "approval_levels": [],
            "use_relational_conditions": True,
            "use_relational_levels": True,
            "notification_settings": {"submitted": True, "approved": True, "rejected": True, "cancelled": True},
        },
    )
    level, _created = ApprovalRuleLevel.objects.get_or_create(
        rule=rule,
        sequence=10,
        defaults={
            "name": "HR or Direct Supervisor",
            "approval_type": "access_profile",
            "minimum_approvals": 1,
            "active": True,
        },
    )
    for sequence, group_name in enumerate(("HR Super User", "HR Manager", "HR Administrator"), start=10):
        ApprovalRuleLevelAssignee.objects.get_or_create(
            level=level,
            role="approver",
            source="group",
            group_name=group_name,
            defaults={"sequence": sequence, "active": True},
        )
    ApprovalRuleLevelAssignee.objects.get_or_create(
        level=level,
        role="approver",
        source="record_supervisor",
        defaults={"sequence": 50, "active": True},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("approval_workflow", "0005_hr_document_workflows"),
        ("gsshop_hr", "0015_employmentcontract_approval_statuses"),
    ]

    operations = [
        migrations.RunPython(create_contract_workflow, migrations.RunPython.noop),
    ]
