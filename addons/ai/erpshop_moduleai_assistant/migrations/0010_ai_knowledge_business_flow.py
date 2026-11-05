from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("erpshop_moduleai_assistant", "0009_ai_knowledge_base_profile_category")]

    operations = [
        migrations.CreateModel(
            name="AIKnowledgeBusinessFlow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=180)),
                ("trigger", models.CharField(blank=True, max_length=255)),
                ("description", models.TextField(blank=True)),
                ("steps", models.JSONField(blank=True, default=list)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("active", "Active"), ("archived", "Archived")], default="draft", max_length=16)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="ai_knowledge_business_flows", to="core.company")),
            ],
            options={"ordering": ["name", "id"]},
        ),
        migrations.AddConstraint(
            model_name="aiknowledgebusinessflow",
            constraint=models.UniqueConstraint(fields=("company", "name"), name="unique_ai_knowledge_business_flow"),
        ),
        migrations.AddIndex(
            model_name="aiknowledgebusinessflow",
            index=models.Index(fields=["company", "status", "-updated_at"], name="ai_knowledge_flow_scope"),
        ),
    ]
