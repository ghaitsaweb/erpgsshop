from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.operations import AddIndexConcurrently
from django.contrib.postgres.search import SearchVector
from django.db import migrations


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("erpshop_moduleai_assistant", "0015_ai_knowledge_business_flow_generation_job")]

    operations = [
        AddIndexConcurrently(
            model_name="aiknowledgechunk",
            index=GinIndex(SearchVector("content", config="simple"), name="ai_knowledge_chunk_fts"),
        ),
    ]
