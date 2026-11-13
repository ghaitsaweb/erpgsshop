from django.db import migrations


BATCH_SIZE = 500


def ensure_team_lead_memberships(apps, schema_editor):
    del schema_editor
    Team = apps.get_model("gsshop_purchase", "PurchaseTeam")
    Member = apps.get_model("gsshop_purchase", "PurchaseTeamMember")
    pending = []
    for team_id, manager_id in Team.objects.order_by("pk").values_list("pk", "manager_id").iterator(
        chunk_size=BATCH_SIZE
    ):
        if not manager_id:
            continue
        pending.append(
            Member(
                team_id=team_id,
                user_id=manager_id,
                role="Team Lead",
            )
        )
        if len(pending) == BATCH_SIZE:
            Member.objects.bulk_create(pending, ignore_conflicts=True, batch_size=BATCH_SIZE)
            pending = []
    if pending:
        Member.objects.bulk_create(pending, ignore_conflicts=True, batch_size=BATCH_SIZE)


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0038_purchase_team_auto_routing"),
    ]

    operations = [
        migrations.RunPython(ensure_team_lead_memberships, migrations.RunPython.noop),
    ]
