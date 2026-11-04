from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("erpshop_moduleai_assistant", "0006_ai_knowledge_document")]

    operations = [
        migrations.AddField(
            model_name="assistantconfiguration",
            name="allow_knowledge_context",
            field=models.BooleanField(default=True, help_text="Include bounded active company knowledge documents in assistant context."),
        )
    ]
