from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("gsshop_ai", "0007_ai_tool_approval")]

    operations = [
        migrations.AddField(
            model_name="aitask",
            name="feedback",
            field=models.CharField(blank=True, choices=[("helpful", "Helpful"), ("unhelpful", "Needs improvement")], max_length=16),
        ),
        migrations.AddField(
            model_name="aitask",
            name="feedback_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="aitask",
            name="feedback_note",
            field=models.TextField(blank=True),
        ),
    ]
