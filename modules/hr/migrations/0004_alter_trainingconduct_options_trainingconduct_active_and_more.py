import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_employee_supervisor_employee_user'),
        ('gsshop_hr', '0003_careertransitiontype_careertransitionrequest_and_more'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='trainingconduct',
            options={'ordering': ['-date_start', 'name']},
        ),
        migrations.AddField(
            model_name='trainingconduct',
            name='active',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='trainingconduct',
            name='company',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='training_conducts', to='core.company'),
        ),
        migrations.AddField(
            model_name='trainingconduct',
            name='maximum_participants',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trainingconduct',
            name='minimum_participants',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trainingconduct',
            name='name',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='trainingconduct',
            name='pic',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='managed_training_conducts', to='core.employee'),
        ),
        migrations.AddField(
            model_name='trainingconduct',
            name='program',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='conducts', to='gsshop_hr.trainingprogram'),
        ),
        migrations.AddField(
            model_name='trainingconduct',
            name='request_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='trainingconduct',
            name='training_source',
            field=models.CharField(choices=[('course', 'Course'), ('program', 'Program')], default='course', max_length=20),
        ),
        migrations.AddField(
            model_name='trainingconduct',
            name='training_type',
            field=models.CharField(choices=[('internal', 'Internal Training'), ('public', 'Public Training')], default='internal', max_length=20),
        ),
        migrations.AddField(
            model_name='trainingconduct',
            name='use_maximum_participant',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='trainingconduct',
            name='use_minimum_participant',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='trainingconduct',
            name='course',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='conducts', to='gsshop_hr.trainingcourse'),
        ),
        migrations.AlterField(
            model_name='trainingconduct',
            name='date_end',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='trainingconduct',
            name='date_start',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='trainingconduct',
            name='reference',
            field=models.CharField(blank=True, max_length=40, unique=True),
        ),
        migrations.AlterField(
            model_name='trainingconduct',
            name='status',
            field=models.CharField(choices=[('draft', 'Draft'), ('open', 'Open'), ('upcoming', 'Upcoming'), ('on_going', 'On Going'), ('cancelled', 'Cancelled')], default='draft', max_length=16),
        ),
        migrations.CreateModel(
            name='TrainingConductSchedule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sequence', models.PositiveIntegerField(default=1)),
                ('notes', models.CharField(blank=True, max_length=240)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('start_time', models.TimeField()),
                ('end_time', models.TimeField()),
                ('conduct', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='schedule_lines', to='gsshop_hr.trainingconduct')),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='conduct_schedule_lines', to='gsshop_hr.trainingcourse')),
                ('trainer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='conduct_schedule_lines', to='gsshop_hr.trainingtrainer')),
            ],
            options={
                'ordering': ['sequence', 'start_date', 'start_time'],
            },
        ),
        migrations.CreateModel(
            name='TrainingConductParticipant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('enrolled', 'Enrolled'), ('attended', 'Attended'), ('absent', 'Absent'), ('cancelled', 'Cancelled')], default='enrolled', max_length=16)),
                ('notes', models.CharField(blank=True, max_length=240)),
                ('conduct', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='participant_lines', to='gsshop_hr.trainingconduct')),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='training_participations', to='core.employee')),
            ],
            options={
                'ordering': ['employee__name'],
                'constraints': [models.UniqueConstraint(fields=('conduct', 'employee'), name='unique_training_conduct_participant')],
            },
        ),
    ]
