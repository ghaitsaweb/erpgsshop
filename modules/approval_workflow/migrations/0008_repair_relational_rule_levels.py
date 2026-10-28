from django.conf import settings
from django.db import migrations


def _existing_ids(model, values, database):
    values = {int(value) for value in values if value not in (None, "")}
    if not values:
        return set()
    return set(model.objects.using(database).filter(pk__in=values).values_list("pk", flat=True))


def _assignee(Assignee, database, level, *, source, sequence, user_id=None, group_id=None, group_name="", field_path=""):
    Assignee.objects.using(database).create(
        level=level,
        role="approver",
        source=source,
        sequence=sequence,
        user_id=user_id,
        group_id=group_id,
        group_name=group_name,
        field_path=field_path,
    )


def repair_missing_relational_levels(apps, schema_editor):
    """Repair rules created after the original relational backfill branch ran."""
    database = schema_editor.connection.alias
    ApprovalRule = apps.get_model("approval_workflow", "ApprovalRule")
    Level = apps.get_model("approval_workflow", "ApprovalRuleLevel")
    Assignee = apps.get_model("approval_workflow", "ApprovalRuleLevelAssignee")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    Group = apps.get_model("auth", "Group")

    rules = ApprovalRule.objects.using(database).filter(use_relational_levels=True, levels__isnull=True).iterator()
    for rule in rules:
        payloads = rule.approval_levels if isinstance(rule.approval_levels, list) else []
        if not payloads:
            continue
        # scale-guard: allow SG003 — repair iterates configured approval levels only, never transaction rows.
        for level_index, payload in enumerate(payloads, start=1):
            if not isinstance(payload, dict):
                break
            level = Level.objects.using(database).create(
                rule=rule,
                sequence=level_index * 10,
                name=payload.get("name") or payload.get("layer_name") or f"Level {level_index}",
                approval_type=payload.get("approval_type") or "user",
                minimum_approvals=int(payload.get("minimum_approvals", payload.get("min_approval", 1)) or 1),
                maximum_hierarchy=payload.get("maximum_hierarchy") or None,
                active=True,
            )
            sequence = 10
            for user_id in sorted(_existing_ids(User, payload.get("user_ids") or [], database)):
                _assignee(Assignee, database, level, source="user", sequence=sequence, user_id=user_id)
                sequence += 10
            for group_id in sorted(_existing_ids(Group, payload.get("group_ids") or [], database)):
                _assignee(Assignee, database, level, source="group", sequence=sequence, group_id=group_id)
                sequence += 10
            for group_name in payload.get("group_names") or []:
                _assignee(Assignee, database, level, source="group", sequence=sequence, group_name=group_name)
                sequence += 10
            for source in payload.get("approvers") or []:
                if not isinstance(source, dict):
                    continue
                source_type = source.get("type")
                if source_type == "group":
                    for group_id in sorted(_existing_ids(Group, source.get("group_ids") or [], database)):
                        _assignee(Assignee, database, level, source="group", sequence=sequence, group_id=group_id)
                        sequence += 10
                    for group_name in source.get("group_names") or []:
                        _assignee(Assignee, database, level, source="group", sequence=sequence, group_name=group_name)
                        sequence += 10
                elif source_type == "user":
                    for user_id in sorted(_existing_ids(User, source.get("user_ids") or [], database)):
                        _assignee(Assignee, database, level, source="user", sequence=sequence, user_id=user_id)
                        sequence += 10
                elif source_type in {"record_field", "hierarchy"} and source.get("field"):
                    _assignee(
                        Assignee,
                        database,
                        level,
                        source="record_field",
                        sequence=sequence,
                        field_path=source["field"],
                    )
                    sequence += 10
                elif source_type == "record_supervisor":
                    _assignee(Assignee, database, level, source="record_supervisor", sequence=sequence)
                    sequence += 10


class Migration(migrations.Migration):
    dependencies = [
        ("approval_workflow", "0007_merge_local_and_relational"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(repair_missing_relational_levels, migrations.RunPython.noop),
    ]
