from django.db import migrations, models
import django.db.models.deletion
from django.db.models import OuterRef, Subquery


def backfill_chunk_branch(apps, schema_editor):
    document_model = apps.get_model("erpshop_moduleai_assistant", "AIKnowledgeDocument")
    chunk_model = apps.get_model("erpshop_moduleai_assistant", "AIKnowledgeChunk")
    document_branch = document_model.objects.filter(pk=OuterRef("document_id")).values("branch_id")[:1]
    chunk_model.objects.filter(branch_id__isnull=True).update(branch_id=Subquery(document_branch))


class Migration(migrations.Migration):
    dependencies = [("erpshop_moduleai_assistant", "0012_ai_knowledge_record_model_mapping")]

    operations = [
        migrations.AddField(
            model_name="assistantsession",
            name="branch",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="hashy_assistant_sessions", to="organization.branch"),
        ),
        migrations.AddField(
            model_name="aiknowledgedocument",
            name="branch",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ai_knowledge_documents", to="organization.branch"),
        ),
        migrations.AddField(
            model_name="aiknowledgechunk",
            name="branch",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ai_knowledge_chunks", to="organization.branch"),
        ),
        migrations.RunPython(backfill_chunk_branch, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="assistantsession",
            index=models.Index(fields=["company", "branch", "status", "-last_activity_at", "-id"], name="ai_session_branch_recent"),
        ),
        migrations.AddIndex(
            model_name="aiknowledgedocument",
            index=models.Index(fields=["company", "branch", "active", "status", "id"], name="ai_knowledge_doc_branch"),
        ),
        migrations.AddIndex(
            model_name="aiknowledgechunk",
            index=models.Index(fields=["company", "branch", "document", "ordinal"], name="ai_knowledge_chunk_branch"),
        ),
    ]
