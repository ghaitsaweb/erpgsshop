import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("erpshop_moduleai_assistant", "0008_ai_knowledge_chunk")]

    operations = [
        migrations.CreateModel(
            name="AIKnowledgeCompanyProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("summary", models.TextField(blank=True)),
                ("operating_context", models.TextField(blank=True)),
                ("active", models.BooleanField(default=True)),
                (
                    "company",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ai_knowledge_profile",
                        to="core.company",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="AIKnowledgeCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                ("description", models.CharField(blank=True, max_length=500)),
                ("active", models.BooleanField(default=True)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ai_knowledge_categories",
                        to="core.company",
                    ),
                ),
            ],
            options={"ordering": ["name", "id"]},
        ),
        migrations.AddConstraint(
            model_name="aiknowledgecategory",
            constraint=models.UniqueConstraint(fields=("company", "name"), name="unique_ai_knowledge_category"),
        ),
        migrations.AddIndex(
            model_name="aiknowledgecategory",
            index=models.Index(fields=["company", "active", "name"], name="ai_knowledge_category_scope"),
        ),
    ]
