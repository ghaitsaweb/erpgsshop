from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("erpshop_moduleai_assistant", "0001_initial")]

    operations = [
        AddIndexConcurrently(
            model_name="assistantsession",
            index=models.Index(
                fields=["company", "user", "status", "-last_activity_at", "-id"],
                name="ai_session_owner_recent",
            ),
        ),
        AddIndexConcurrently(
            model_name="assistantsession",
            index=models.Index(
                fields=["company", "status", "-last_activity_at", "-id"],
                name="ai_session_company_recent",
            ),
        ),
        AddIndexConcurrently(
            model_name="assistantmessage",
            index=models.Index(fields=["session", "-id"], name="ai_message_session_recent"),
        ),
        AddIndexConcurrently(
            model_name="assistantmessage",
            index=models.Index(
                fields=["session", "state", "-created_at", "-id"],
                name="ai_message_context_recent",
            ),
        ),
    ]
