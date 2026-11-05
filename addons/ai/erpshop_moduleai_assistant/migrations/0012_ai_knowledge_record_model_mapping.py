from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("erpshop_moduleai_assistant", "0011_ai_knowledge_business_flow_edges")]

    operations = [
        migrations.CreateModel(
            name="AIKnowledgeRecordModelMapping",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("record_model", models.CharField(max_length=100)),
                ("display_name", models.CharField(max_length=180)),
                ("category", models.CharField(blank=True, max_length=100)),
                ("module_name", models.CharField(blank=True, max_length=128)),
                ("feature_tags", models.JSONField(blank=True, default=list)),
                ("active", models.BooleanField(default=True)),
                ("business_flow", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="record_model_mappings", to="erpshop_moduleai_assistant.aiknowledgebusinessflow")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="ai_knowledge_record_mappings", to="core.company")),
            ],
            options={"ordering": ["display_name", "record_model", "id"]},
        ),
        migrations.AddConstraint(
            model_name="aiknowledgerecordmodelmapping",
            constraint=models.UniqueConstraint(fields=("company", "record_model"), name="unique_ai_knowledge_record_model"),
        ),
        migrations.AddIndex(
            model_name="aiknowledgerecordmodelmapping",
            index=models.Index(fields=["company", "active", "record_model"], name="ai_knowledge_record_scope"),
        ),
    ]
