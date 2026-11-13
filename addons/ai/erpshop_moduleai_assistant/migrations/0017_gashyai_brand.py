from django.db import migrations, models


def rename_default_display_name(apps, schema_editor):
    AssistantConfiguration = apps.get_model("erpshop_moduleai_assistant", "AssistantConfiguration")
    AssistantConfiguration.objects.filter(display_name="Hashy AI Assistant").update(display_name="GashyAI")


class Migration(migrations.Migration):
    dependencies = [("erpshop_moduleai_assistant", "0016_ai_knowledge_chunk_fts")]

    operations = [
        migrations.AlterField(
            model_name="assistantconfiguration",
            name="display_name",
            field=models.CharField(default="GashyAI", max_length=120),
        ),
        migrations.RunPython(rename_default_display_name, migrations.RunPython.noop),
    ]
