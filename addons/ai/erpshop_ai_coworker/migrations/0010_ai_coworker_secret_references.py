from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("gsshop_ai", "0009_ai_model_catalog_audit")]

    operations = [
        migrations.CreateModel(
            name="AICoworkerSecret",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("scope", models.CharField(choices=[("participant", "Participant"), ("system", "System")], default="participant", max_length=16)),
                ("key", models.CharField(max_length=128)),
                ("credential_env_var", models.CharField(help_text="Environment variable holding the secret value; its value is never stored in ERP GS Shop.", max_length=128)),
                ("active", models.BooleanField(default=True)),
                ("agent", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="secret_references", to="gsshop_ai.aiagent")),
                ("participant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="secret_references", to="gsshop_ai.aicoworkerparticipant")),
            ],
            options={"ordering": ["scope", "key", "id"]},
        ),
        migrations.AddConstraint(
            model_name="aicoworkersecret",
            constraint=models.UniqueConstraint(condition=models.Q(scope="system"), fields=("agent", "key"), name="unique_ai_system_secret_key"),
        ),
        migrations.AddConstraint(
            model_name="aicoworkersecret",
            constraint=models.UniqueConstraint(condition=models.Q(scope="participant"), fields=("agent", "participant", "key"), name="unique_ai_participant_secret_key"),
        ),
        migrations.AddIndex(
            model_name="aicoworkersecret",
            index=models.Index(fields=["agent", "scope", "active", "id"], name="ai_secret_resolution"),
        ),
    ]
