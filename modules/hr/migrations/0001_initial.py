import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal
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
            name='Appraisal',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('review_date', models.DateField(default=django.utils.timezone.localdate)),
                ('score', models.DecimalField(decimal_places=2, default=0, max_digits=4, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(5)])),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('in_review', 'In Review'), ('completed', 'Completed')], default='draft', max_length=16)),
                ('strengths', models.TextField(blank=True)),
                ('development_plan', models.TextField(blank=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='appraisals', to='core.employee')),
                ('reviewer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='employee_appraisals', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-review_date', 'employee__name'],
            },
        ),
        migrations.CreateModel(
            name='Candidate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('job_title', models.CharField(max_length=140)),
                ('name', models.CharField(max_length=160)),
                ('email', models.EmailField(max_length=254)),
                ('phone', models.CharField(blank=True, max_length=40)),
                ('source', models.CharField(blank=True, max_length=80)),
                ('status', models.CharField(choices=[('applied', 'Applied'), ('screening', 'Screening'), ('interview', 'Interview'), ('offer', 'Offer'), ('hired', 'Hired'), ('rejected', 'Rejected')], default='applied', max_length=16)),
                ('notes', models.TextField(blank=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='candidates', to='core.company')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='EmploymentContract',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40, unique=True)),
                ('contract_type', models.CharField(choices=[('permanent', 'Permanent'), ('fixed_term', 'Fixed Term'), ('contractor', 'Contractor'), ('intern', 'Intern')], max_length=20)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField(blank=True, null=True)),
                ('base_salary', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('active', 'Active'), ('expired', 'Expired'), ('terminated', 'Terminated')], default='draft', max_length=16)),
                ('notes', models.TextField(blank=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='contracts', to='core.employee')),
            ],
            options={
                'ordering': ['-start_date', 'number'],
            },
        ),
        migrations.CreateModel(
            name='LeaveRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('leave_type', models.CharField(default='Annual Leave', max_length=60)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('days', models.DecimalField(decimal_places=2, default=1, max_digits=6)),
                ('reason', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('approver', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='leave_approvals', to=settings.AUTH_USER_MODEL)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='leave_requests', to='core.employee')),
            ],
            options={
                'ordering': ['-start_date', 'employee__name'],
            },
        ),
        migrations.CreateModel(
            name='OvertimeRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('overtime_date', models.DateField(default=django.utils.timezone.localdate)),
                ('hours', models.DecimalField(decimal_places=2, max_digits=5, validators=[django.core.validators.MinValueValidator(Decimal('0.25'))])),
                ('reason', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('approver', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='overtime_approvals', to=settings.AUTH_USER_MODEL)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='overtime_requests', to='core.employee')),
            ],
            options={
                'ordering': ['-overtime_date', 'employee__name'],
            },
        ),
        migrations.CreateModel(
            name='Payslip',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40, unique=True)),
                ('period_start', models.DateField()),
                ('period_end', models.DateField()),
                ('basic_salary', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('allowances', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('deductions', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('net_salary', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=18)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('confirmed', 'Confirmed'), ('paid', 'Paid'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payslips', to='core.employee')),
            ],
            options={
                'ordering': ['-period_end', 'employee__name'],
            },
        ),
        migrations.CreateModel(
            name='Department',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=120)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='departments', to='core.company')),
                ('manager', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='managed_departments', to='core.employee')),
            ],
            options={
                'ordering': ['name'],
                'constraints': [models.UniqueConstraint(fields=('company', 'code'), name='unique_department_code_per_company')],
            },
        ),
    ]
