from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def _existing_ids(model, values):
    values = {int(value) for value in values if value not in (None, "")}
    if not values:
        return set()
    return set(model.objects.filter(pk__in=values).values_list("pk", flat=True))


def _create_assignee(Assignee, level, *, role, source, sequence, user_id=None, group_id=None, group_name="", field_path=""):
    Assignee.objects.create(
        level=level,
        role=role,
        source=source,
        sequence=sequence,
        user_id=user_id,
        group_id=group_id,
        group_name=group_name or "",
        field_path=field_path or "",
    )


def _backfill_relational_configuration(apps, schema_editor):
    ApprovalRule = apps.get_model("approval_workflow", "ApprovalRule")
    ConditionGroup = apps.get_model("approval_workflow", "ApprovalRuleConditionGroup")
    ConditionLine = apps.get_model("approval_workflow", "ApprovalRuleConditionLine")
    Level = apps.get_model("approval_workflow", "ApprovalRuleLevel")
    Assignee = apps.get_model("approval_workflow", "ApprovalRuleLevelAssignee")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    Group = apps.get_model("auth", "Group")

    for rule in ApprovalRule.objects.order_by("pk").iterator():
        level_payloads = rule.approval_levels if isinstance(rule.approval_levels, list) else []
        levels_convertible = bool(level_payloads)
        for level_index, payload in enumerate(level_payloads, start=1):
            if not isinstance(payload, dict):
                levels_convertible = False
                break
            level = Level.objects.create(
                rule=rule,
                sequence=level_index * 10,
                name=payload.get("name") or payload.get("layer_name") or f"Level {level_index}",
                approval_type=payload.get("approval_type") or "user",
                minimum_approvals=int(payload.get("minimum_approvals", payload.get("min_approval", 1)) or 1),
                maximum_hierarchy=payload.get("maximum_hierarchy") or None,
                active=True,
            )
            sequence = 10
            direct_users = _existing_ids(
                User, payload.get("user_ids") or payload.get("selected_user_ids") or []
            )
            for user_id in sorted(direct_users):
                _create_assignee(Assignee, level, role="approver", source="user", sequence=sequence, user_id=user_id)
                sequence += 10
            group_ids = _existing_ids(
                Group, payload.get("group_ids") or payload.get("selected_role_ids") or []
            )
            for group_id in sorted(group_ids):
                _create_assignee(Assignee, level, role="approver", source="group", sequence=sequence, group_id=group_id)
                sequence += 10
            for group_name in payload.get("group_names") or []:
                _create_assignee(Assignee, level, role="approver", source="group", sequence=sequence, group_name=group_name)
                sequence += 10
            for source in payload.get("approvers") or []:
                if not isinstance(source, dict):
                    levels_convertible = False
                    continue
                source_type = source.get("type")
                if source_type == "user":
                    for user_id in sorted(_existing_ids(User, source.get("user_ids") or source.get("users") or [])):
                        _create_assignee(Assignee, level, role="approver", source="user", sequence=sequence, user_id=user_id)
                        sequence += 10
                elif source_type in {"group", "access_profile", "functional_hierarchy"}:
                    for group_id in sorted(_existing_ids(Group, source.get("group_ids") or [])):
                        _create_assignee(Assignee, level, role="approver", source="group", sequence=sequence, group_id=group_id)
                        sequence += 10
                    for group_name in source.get("group_names") or []:
                        _create_assignee(Assignee, level, role="approver", source="group", sequence=sequence, group_name=group_name)
                        sequence += 10
                elif source_type in {"record_field", "hierarchy"} and source.get("field"):
                    _create_assignee(Assignee, level, role="approver", source="record_field", sequence=sequence, field_path=source["field"])
                    sequence += 10
                elif source_type == "record_supervisor":
                    _create_assignee(Assignee, level, role="approver", source="record_supervisor", sequence=sequence)
                    sequence += 10
                else:
                    levels_convertible = False
            for user_id in sorted(_existing_ids(User, payload.get("delegate_user_ids") or payload.get("selected_delegate_ids") or [])):
                _create_assignee(Assignee, level, role="delegate", source="user", sequence=sequence, user_id=user_id)
                sequence += 10
            for group_id in sorted(_existing_ids(Group, payload.get("delegate_group_ids") or payload.get("selected_delegate_group_ids") or [])):
                _create_assignee(Assignee, level, role="delegate", source="group", sequence=sequence, group_id=group_id)
                sequence += 10
            for group_name in payload.get("delegate_group_names") or []:
                _create_assignee(Assignee, level, role="delegate", source="group", sequence=sequence, group_name=group_name)
                sequence += 10

        raw_conditions = rule.trigger_conditions
        raw_conditions = [raw_conditions] if isinstance(raw_conditions, dict) else raw_conditions
        conditions_convertible = isinstance(raw_conditions, list)
        for group_index, payload in enumerate(raw_conditions or [], start=1):
            if not isinstance(payload, dict):
                conditions_convertible = False
                break
            nested = payload.get("conditions")
            line_payloads = nested if isinstance(nested, list) else [payload]
            if any(not isinstance(line, dict) or isinstance(line.get("conditions"), list) or not (line.get("field") or line.get("field_name")) for line in line_payloads):
                conditions_convertible = False
                break
            group = ConditionGroup.objects.create(
                rule=rule,
                name=payload.get("name") or f"Condition group {group_index}",
                sequence=group_index * 10,
                operator=str(payload.get("operator", "and")).lower() if nested is not None else "and",
                active=True,
            )
            for line_index, line in enumerate(line_payloads, start=1):
                ConditionLine.objects.create(
                    group=group,
                    field_name=line.get("field") or line.get("field_name"),
                    operator=line.get("operator", "eq"),
                    value=line.get("value"),
                    sequence=line_index * 10,
                    active=True,
                )

        if levels_convertible:
            rule.use_relational_levels = True
        if conditions_convertible:
            rule.use_relational_conditions = True
        rule.save(update_fields=["use_relational_levels", "use_relational_conditions", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("approval_workflow", "0003_approvalnotification_and_more"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("gsshop_hr", "0014_employeeaddress_phone_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="approvalrule",
            name="use_relational_conditions",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="approvalrule",
            name="use_relational_levels",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="ApprovalRuleConditionGroup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(blank=True, max_length=120)),
                ("sequence", models.PositiveSmallIntegerField(default=10)),
                ("operator", models.CharField(choices=[("and", "All conditions"), ("or", "Any condition")], default="and", max_length=3)),
                ("active", models.BooleanField(default=True)),
                ("rule", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="condition_groups", to="approval_workflow.approvalrule")),
            ],
            options={"ordering": ["sequence", "pk"]},
        ),
        migrations.CreateModel(
            name="ApprovalRuleLevel",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("sequence", models.PositiveSmallIntegerField(default=10)),
                ("name", models.CharField(blank=True, max_length=120)),
                ("approval_type", models.CharField(choices=[("user", "Users"), ("access_profile", "Access Profile"), ("hierarchy", "Hierarchy"), ("functional_hierarchy", "Functional Hierarchy")], default="user", max_length=24)),
                ("minimum_approvals", models.PositiveSmallIntegerField(default=1)),
                ("maximum_hierarchy", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("active", models.BooleanField(default=True)),
                ("rule", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="levels", to="approval_workflow.approvalrule")),
            ],
            options={"ordering": ["sequence", "pk"]},
        ),
        migrations.CreateModel(
            name="ApprovalRuleConditionLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("field_name", models.CharField(max_length=160)),
                ("operator", models.CharField(default="eq", max_length=20)),
                ("value", models.JSONField(blank=True, null=True)),
                ("sequence", models.PositiveSmallIntegerField(default=10)),
                ("active", models.BooleanField(default=True)),
                ("group", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="approval_workflow.approvalruleconditiongroup")),
            ],
            options={"ordering": ["sequence", "pk"]},
        ),
        migrations.CreateModel(
            name="ApprovalRuleLevelAssignee",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("role", models.CharField(choices=[("approver", "Approver"), ("delegate", "Delegate")], default="approver", max_length=12)),
                ("source", models.CharField(choices=[("user", "Specific User"), ("group", "Access Profile / Group"), ("record_field", "Record User Field"), ("record_supervisor", "Employee Supervisor")], max_length=20)),
                ("group_name", models.CharField(blank=True, max_length=150)),
                ("field_path", models.CharField(blank=True, max_length=160)),
                ("sequence", models.PositiveSmallIntegerField(default=10)),
                ("active", models.BooleanField(default=True)),
                ("group", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="approval_rule_level_assignments", to="auth.group")),
                ("level", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="assignees", to="approval_workflow.approvalrulelevel")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="approval_rule_level_assignments", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["sequence", "pk"]},
        ),
        migrations.AddConstraint(
            model_name="approvalruleconditiongroup",
            constraint=models.UniqueConstraint(fields=("rule", "sequence"), name="approval_condition_group_sequence"),
        ),
        migrations.AddConstraint(
            model_name="approvalruleconditionline",
            constraint=models.UniqueConstraint(fields=("group", "sequence"), name="approval_condition_line_sequence"),
        ),
        migrations.AddConstraint(
            model_name="approvalrulelevel",
            constraint=models.UniqueConstraint(fields=("rule", "sequence"), name="approval_rule_level_sequence"),
        ),
        migrations.RunPython(_backfill_relational_configuration, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="approvalrule",
            name="use_relational_conditions",
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name="approvalrule",
            name="use_relational_levels",
            field=models.BooleanField(default=True),
        ),
    ]
