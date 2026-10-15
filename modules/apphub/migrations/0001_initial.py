import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ERPApp',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(max_length=80, unique=True)),
                ('name', models.CharField(max_length=120)),
                ('category', models.CharField(max_length=80)),
                ('summary', models.CharField(max_length=240)),
                ('icon', models.CharField(default='◆', max_length=8)),
                ('version', models.CharField(default='0.1.0', max_length=20)),
                ('status', models.CharField(choices=[('stable', 'Stable'), ('beta', 'Beta'), ('planned', 'Planned')], default='planned', max_length=16)),
                ('enabled', models.BooleanField(default=False)),
                ('is_core', models.BooleanField(default=False)),
                ('sequence', models.PositiveIntegerField(default=100)),
                ('dependencies', models.JSONField(blank=True, default=list)),
                ('route', models.CharField(blank=True, max_length=120)),
                ('enabled_at', models.DateTimeField(blank=True, null=True)),
                ('enabled_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='enabled_erp_apps', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['category', 'sequence', 'name'],
            },
        ),
    ]
