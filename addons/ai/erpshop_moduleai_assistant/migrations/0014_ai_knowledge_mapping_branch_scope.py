from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("erpshop_moduleai_assistant", "0013_ai_knowledge_branch_scope")]

    operations = [
        migrations.AddField(
            model_name="aiknowledgerecordmodelmapping",
            name="branch",
            field=models.ForeignKey(
                blank=True,
                help_text="Leave blank when the mapping applies to every branch in the company.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ai_knowledge_record_mappings",
                to="organization.branch",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="aiknowledgerecordmodelmapping",
            name="unique_ai_knowledge_record_model",
        ),
        migrations.AddConstraint(
            model_name="aiknowledgerecordmodelmapping",
            constraint=models.UniqueConstraint(
                condition=models.Q(("branch__isnull", True)),
                fields=("company", "record_model"),
                name="unique_ai_knowledge_record_model_companywide",
            ),
        ),
        migrations.AddConstraint(
            model_name="aiknowledgerecordmodelmapping",
            constraint=models.UniqueConstraint(
                fields=("company", "branch", "record_model"),
                name="unique_ai_knowledge_record_model_branch",
            ),
        ),
        migrations.AddIndex(
            model_name="aiknowledgerecordmodelmapping",
            index=models.Index(
                fields=["company", "branch", "active", "record_model"],
                name="ai_knowledge_mapping_branch",
            ),
        ),
    ]
