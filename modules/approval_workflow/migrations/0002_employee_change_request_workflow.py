from django.db import migrations


def create_employee_change_request_workflow(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ApprovalWorkflow = apps.get_model("approval_workflow", "ApprovalWorkflow")
    ApprovalWorkflowPerimeter = apps.get_model("approval_workflow", "ApprovalWorkflowPerimeter")
    ApprovalRule = apps.get_model("approval_workflow", "ApprovalRule")

    content_type = ContentType.objects.filter(
        app_label="gsshop_hr",
        model="employeechangerequest",
    ).first()
    if content_type is None:
        return
    workflow, _created = ApprovalWorkflow.objects.get_or_create(
        content_type=content_type,
        defaults={
            "description": "Employee profile changes with immutable submission snapshots.",
            "status_field": "status",
            "primary_perimeter": ["employee", "effective_date"],
            "optional_perimeters": [
                {"name": "Changed field", "field_names": ["field_name"], "is_override": True},
            ],
            "active": True,
        },
    )
    perimeter, _created = ApprovalWorkflowPerimeter.objects.get_or_create(
        workflow=workflow,
        name="Employee and effective date",
        defaults={
            "perimeter_type": "primary",
            "field_names": ["employee", "effective_date"],
            "priority": 10,
        },
    )
    ApprovalRule.objects.get_or_create(
        name="Employee Change Request Default",
        defaults={
            "workflow": workflow,
            "perimeter": perimeter,
            "description": "One active layer for HR authorities or the employee's direct supervisor.",
            "state": "associated",
            "priority": 100,
            "trigger_conditions": [],
            "approval_levels": [
                {
                    "name": "HR or Direct Supervisor",
                    "minimum_approvals": 1,
                    "approvers": [
                        {
                            "type": "group",
                            "group_names": ["HR Super User", "HR Manager", "HR Administrator"],
                        },
                        {"type": "record_supervisor"},
                    ],
                }
            ],
            "require_approve_reason": False,
            "notification_settings": {
                "submitted": True,
                "approved": True,
                "rejected": True,
            },
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("approval_workflow", "0001_initial"),
        ("gsshop_hr", "0013_employeechangerequesthistory_and_more"),
    ]

    operations = [
        migrations.RunPython(create_employee_change_request_workflow, migrations.RunPython.noop),
    ]
