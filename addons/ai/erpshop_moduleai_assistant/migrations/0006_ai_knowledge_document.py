import addons.ai.erpshop_moduleai_assistant.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("erpshop_moduleai_assistant", "0005_ai_digest_foundation")]

    operations = [
        migrations.CreateModel(
            name="AIKnowledgeDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255)),
                ("category", models.CharField(blank=True, max_length=100)),
                ("file", models.FileField(blank=True, upload_to=addons.ai.erpshop_moduleai_assistant.models.knowledge_document_path)),
                ("original_name", models.CharField(blank=True, max_length=255)),
                ("content_type", models.CharField(blank=True, max_length=120)),
                ("byte_size", models.PositiveBigIntegerField(default=0)),
                ("extracted_text", models.TextField(blank=True)),
                ("content_sha256", models.CharField(blank=True, db_index=True, max_length=64)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("ready", "Ready"), ("pending", "Pending extraction"), ("failed", "Extraction failed"), ("archived", "Archived")], db_index=True, default="pending", max_length=16)),
                ("error_message", models.CharField(blank=True, max_length=500)),
                ("active", models.BooleanField(db_index=True, default=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="ai_knowledge_documents", to="core.company")),
            ],
            options={"ordering": ["category", "title", "id"]},
        ),
        migrations.AddConstraint(model_name="aiknowledgedocument", constraint=models.UniqueConstraint(fields=("company", "title"), name="unique_ai_knowledge_title")),
        migrations.AddIndex(model_name="aiknowledgedocument", index=models.Index(fields=["company", "active", "status", "id"], name="ai_knowledge_context")),
    ]
