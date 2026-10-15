import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AIAgent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('role', models.CharField(max_length=160)),
                ('provider', models.CharField(choices=[('openai_compatible', 'OpenAI-compatible'), ('anthropic', 'Anthropic'), ('local', 'Local Model')], default='openai_compatible', max_length=24)),
                ('model_name', models.CharField(blank=True, max_length=100)),
                ('system_prompt', models.TextField(blank=True)),
                ('allowed_tools', models.JSONField(blank=True, default=list)),
                ('active', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['workspace', 'name'],
            },
        ),
        migrations.CreateModel(
            name='AIConversation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=180)),
                ('status', models.CharField(choices=[('active', 'Active'), ('archived', 'Archived')], default='active', max_length=16)),
                ('last_activity_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('agent', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='conversations', to='gsshop_ai.aiagent')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ai_conversations', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-last_activity_at'],
            },
        ),
        migrations.CreateModel(
            name='AIMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('role', models.CharField(choices=[('system', 'System'), ('user', 'User'), ('assistant', 'Assistant'), ('tool', 'Tool')], max_length=16)),
                ('content', models.TextField()),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('conversation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='gsshop_ai.aiconversation')),
            ],
            options={
                'ordering': ['created_at', 'id'],
            },
        ),
        migrations.CreateModel(
            name='AITask',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=180)),
                ('task_type', models.CharField(max_length=80)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('running', 'Running'), ('completed', 'Completed'), ('failed', 'Failed'), ('cancelled', 'Cancelled')], default='queued', max_length=16)),
                ('input_data', models.JSONField(blank=True, default=dict)),
                ('result_data', models.JSONField(blank=True, default=dict)),
                ('error_message', models.TextField(blank=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('agent', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='tasks', to='gsshop_ai.aiagent')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='AIWorkspace',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=140)),
                ('description', models.TextField(blank=True)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ai_workspaces', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.AddField(
            model_name='aiagent',
            name='workspace',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='agents', to='gsshop_ai.aiworkspace'),
        ),
        migrations.AddConstraint(
            model_name='aiworkspace',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='unique_ai_workspace_per_company'),
        ),
        migrations.AddConstraint(
            model_name='aiagent',
            constraint=models.UniqueConstraint(fields=('workspace', 'name'), name='unique_ai_agent_per_workspace'),
        ),
    ]
