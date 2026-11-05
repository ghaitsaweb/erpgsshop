from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("erpshop_moduleai_assistant", "0010_ai_knowledge_business_flow")]

    operations = [
        migrations.AddField(
            model_name="aiknowledgebusinessflow",
            name="edges",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
