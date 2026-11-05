import hashlib

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


CHUNK_CHARACTERS = 1200
CHUNK_OVERLAP = 160


def _split_knowledge_text(value):
    text = (value or "").strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARACTERS)
        if end < len(text):
            boundary_start = start + (CHUNK_CHARACTERS * 3 // 5)
            boundary = max(text.rfind("\n", boundary_start, end), text.rfind(" ", boundary_start, end))
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        next_start = max(end - CHUNK_OVERLAP, start + 1)
        while next_start < len(text) and not text[next_start].isspace():
            next_start += 1
        start = next_start if next_start < len(text) else end
    return chunks


def backfill_knowledge_chunks(apps, schema_editor):
    """Index existing ready documents in bounded bulk batches during upgrade."""

    Document = apps.get_model("erpshop_moduleai_assistant", "AIKnowledgeDocument")
    Chunk = apps.get_model("erpshop_moduleai_assistant", "AIKnowledgeChunk")
    pending = []
    now = timezone.now()
    documents = Document.objects.filter(active=True, status="ready").iterator(chunk_size=100)
    for document in documents:
        for ordinal, content in enumerate(_split_knowledge_text(document.extracted_text)):
            pending.append(
                Chunk(
                    company_id=document.company_id,
                    document_id=document.pk,
                    ordinal=ordinal,
                    content=content,
                    content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    created_at=now,
                    updated_at=now,
                )
            )
        if len(pending) >= 250:
            Chunk.objects.bulk_create(pending, batch_size=250)
            pending = []
    if pending:
        Chunk.objects.bulk_create(pending, batch_size=250)


class Migration(migrations.Migration):
    dependencies = [("erpshop_moduleai_assistant", "0007_assistantconfiguration_allow_knowledge_context")]

    operations = [
        migrations.CreateModel(
            name="AIKnowledgeChunk",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("ordinal", models.PositiveIntegerField()),
                ("content", models.TextField()),
                ("content_sha256", models.CharField(db_index=True, max_length=64)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ai_knowledge_chunks",
                        to="core.company",
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunks",
                        to="erpshop_moduleai_assistant.aiknowledgedocument",
                    ),
                ),
            ],
            options={"ordering": ["document_id", "ordinal", "id"]},
        ),
        migrations.AddConstraint(
            model_name="aiknowledgechunk",
            constraint=models.UniqueConstraint(fields=("document", "ordinal"), name="unique_ai_knowledge_chunk_ordinal"),
        ),
        migrations.AddIndex(
            model_name="aiknowledgechunk",
            index=models.Index(fields=["company", "document", "ordinal"], name="ai_knowledge_chunk_scope"),
        ),
        migrations.RunPython(backfill_knowledge_chunks, migrations.RunPython.noop),
    ]
