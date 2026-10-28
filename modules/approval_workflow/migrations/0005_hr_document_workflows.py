from django.db import migrations


def create_hr_document_workflows(apps, schema_editor):
    ContentType = apps.get_model("contenttypes", "ContentType")
    ApprovalWorkflow = apps.get_model("approval_workflow", "ApprovalWorkflow")
    ApprovalWorkflowPerimeter = apps.get_model("approval_workflow", "ApprovalWorkflowPerimeter")
    ApprovalRule = apps.get_model("approval_workflow", "ApprovalRule")
    ApprovalRuleLevel = apps.get_model("approval_workflow", "ApprovalRuleLevel")
    ApprovalRuleLevelAssignee = apps.get_model("approval_workflow", "ApprovalRuleLevelAssignee")

    specifications = (
        (
            "leaverequest",
            "Leave Request Default",
            "Leave requests approved by HR authorities or the employee supervisor.",
            ["employee", "leave_type", "start_date", "end_date"],
        ),
        (
            "overtimerequest",
            "Overtime Request Default",
            "Overtime requests approved by HR authorities or the employee supervisor.",
            ["employee", "overtime_date", "hours"],
        ),
        (
            "attendancechangerequest",
            "Attendance Change Request Default",
            "Attendance corrections approved by HR authorities or the employee supervisor.",
            ["employee", "attendance_date"],
        ),
        (
            "careertransitionrequest",
            "Career Transition Request Default",
            "Career transitions approved by HR authorities or the employee supervisor.",
            ["employee", "transition_type", "effective_date"],
        ),
        (
            "employeeoffboarding",
            "Employee Offboarding Default",
            "Offboarding requests approved by HR authorities or the employee supervisor.",
            ["employee", "last_working_date"],
        ),
        (
            "employeeonboarding",
            "Employee Onboarding Default",
            "Onboarding requests approved by HR authorities or the employee supervisor.",
            ["employee", "start_date"],
        ),
        (
            "leaveallocation",
            "Leave Allocation Default",
            "Leave allocations approved by HR authorities or the employee supervisor.",
            ["employee", "leave_type", "year"],
        ),
    )
    group_names = ["HR Super User", "HR Manager", "HR Administrator"]
    for model, rule_name, description, primary_perimeter in specifications:
        content_type = ContentType.objects.filter(app_label="gsshop_hr", model=model).first()
        if content_type is None:
            continue
        workflow, _created = ApprovalWorkflow.objects.get_or_create(
            content_type=content_type,
            defaults={
                "description": description,
                "status_field": "status",
                "primary_perimeter": primary_perimeter,
                "active": True,
            },
        )
        perimeter, _created = ApprovalWorkflowPerimeter.objects.get_or_create(
            workflow=workflow,
            name="Employee request",
            defaults={
                "perimeter_type": "primary",
                "field_names": primary_perimeter,
                "priority": 10,
            },
        )
        rule, _created = ApprovalRule.objects.get_or_create(
            name=rule_name,
            defaults={
                "workflow": workflow,
                "perimeter": perimeter,
                "description": description,
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
        for sequence, group_name in enumerate(group_names, start=10):
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
        ("approval_workflow", "0004_relational_rule_lines"),
    ]

    operations = [
        migrations.RunPython(create_hr_document_workflows, migrations.RunPython.noop),
    ]
