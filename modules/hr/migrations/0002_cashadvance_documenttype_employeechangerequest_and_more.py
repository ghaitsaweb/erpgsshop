import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import modules.hr.models
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('gsshop_hr', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CashAdvance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40, unique=True)),
                ('request_date', models.DateField(default=django.utils.timezone.localdate)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=18, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('purpose', models.TextField()),
                ('settlement_due_date', models.DateField(blank=True, null=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('approver', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cash_advance_approvals', to=settings.AUTH_USER_MODEL)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cash_advances', to='core.employee')),
            ],
            options={
                'ordering': ['-request_date'],
            },
        ),
        migrations.CreateModel(
            name='DocumentType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=100)),
                ('mandatory', models.BooleanField(default=False)),
                ('has_expiry', models.BooleanField(default=False)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='employee_document_types', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='EmployeeChangeRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('field_name', models.CharField(max_length=80)),
                ('current_value', models.TextField(blank=True)),
                ('requested_value', models.TextField()),
                ('effective_date', models.DateField(default=django.utils.timezone.localdate)),
                ('reason', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('approver', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='employee_change_approvals', to=settings.AUTH_USER_MODEL)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='change_requests', to='core.employee')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='EmployeeChecklist',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('lifecycle', models.CharField(choices=[('onboarding', 'Onboarding'), ('offboarding', 'Offboarding')], max_length=16)),
                ('description', models.TextField(blank=True)),
                ('responsible_role', models.CharField(blank=True, max_length=100)),
                ('sequence', models.PositiveIntegerField(default=10)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='employee_checklists', to='core.company')),
            ],
            options={
                'ordering': ['lifecycle', 'sequence', 'name'],
            },
        ),
        migrations.CreateModel(
            name='EmployeeDisciplinary',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reference', models.CharField(max_length=40)),
                ('incident_date', models.DateField()),
                ('violation', models.CharField(max_length=160)),
                ('action_taken', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('review', 'Under Review'), ('decided', 'Decision Issued'), ('closed', 'Closed')], default='draft', max_length=16)),
                ('notes', models.TextField(blank=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='disciplinary_cases', to='core.company')),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='disciplinary_cases', to='core.employee')),
            ],
            options={
                'ordering': ['-incident_date'],
            },
        ),
        migrations.CreateModel(
            name='EmployeeDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('document_number', models.CharField(blank=True, max_length=80)),
                ('issued_date', models.DateField(blank=True, null=True)),
                ('expiry_date', models.DateField(blank=True, null=True)),
                ('attachment_url', models.URLField(blank=True)),
                ('verified', models.BooleanField(default=False)),
                ('notes', models.TextField(blank=True)),
                ('document_type', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='employee_documents', to='gsshop_hr.documenttype')),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='documents', to='core.employee')),
            ],
            options={
                'ordering': ['employee__name', 'document_type__name'],
            },
        ),
        migrations.CreateModel(
            name='EmployeeGrade',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=100)),
                ('rank', models.PositiveIntegerField(default=1)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='employee_grades', to='core.company')),
            ],
            options={
                'ordering': ['rank', 'name'],
            },
        ),
        migrations.CreateModel(
            name='EmployeeOffboarding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('last_working_date', models.DateField()),
                ('reason', models.TextField(blank=True)),
                ('progress', models.PositiveSmallIntegerField(default=0, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)])),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('notes', models.TextField(blank=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='offboarding_processes', to='core.employee')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='owned_offboarding_processes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-last_working_date', 'employee__name'],
            },
        ),
        migrations.CreateModel(
            name='EmployeeOnboarding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('start_date', models.DateField(default=django.utils.timezone.localdate)),
                ('progress', models.PositiveSmallIntegerField(default=0, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)])),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('notes', models.TextField(blank=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='onboarding_processes', to='core.employee')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='owned_onboarding_processes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-start_date', 'employee__name'],
            },
        ),
        migrations.CreateModel(
            name='HRYear',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=80)),
                ('date_start', models.DateField()),
                ('date_end', models.DateField()),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='hr_years', to='core.company')),
            ],
            options={
                'ordering': ['-date_start'],
            },
        ),
        migrations.CreateModel(
            name='JobPosition',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=120)),
                ('planned_headcount', models.PositiveIntegerField(default=1)),
                ('description', models.TextField(blank=True)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='job_positions', to='core.company')),
                ('department', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='job_positions', to='gsshop_hr.department')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='LeaveType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=100)),
                ('annual_entitlement', models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ('paid', models.BooleanField(default=True)),
                ('requires_attachment', models.BooleanField(default=False)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='leave_types', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='LeaveAllocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('year', models.PositiveIntegerField(default=modules.hr.models.current_year)),
                ('allocated_days', models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ('used_days', models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='leave_allocations', to='core.employee')),
                ('leave_type', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='allocations', to='gsshop_hr.leavetype')),
            ],
            options={
                'ordering': ['-year', 'employee__name'],
            },
        ),
        migrations.CreateModel(
            name='OvertimeRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('weekday_multiplier', models.DecimalField(decimal_places=2, default=1.5, max_digits=5)),
                ('weekend_multiplier', models.DecimalField(decimal_places=2, default=2, max_digits=5)),
                ('maximum_hours', models.DecimalField(decimal_places=2, default=4, max_digits=5)),
                ('minimum_minutes', models.PositiveIntegerField(default=30)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='overtime_rules', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='PayrollPeriod',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=100)),
                ('date_start', models.DateField()),
                ('date_end', models.DateField()),
                ('cutoff_date', models.DateField()),
                ('status', models.CharField(choices=[('open', 'Open'), ('processing', 'Processing'), ('closed', 'Closed')], default='open', max_length=16)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payroll_periods', to='core.company')),
            ],
            options={
                'ordering': ['-date_start'],
            },
        ),
        migrations.CreateModel(
            name='RawAttendance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('captured_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('event_type', models.CharField(choices=[('check_in', 'Check In'), ('check_out', 'Check Out')], max_length=16)),
                ('source', models.CharField(default='manual', max_length=40)),
                ('device_identifier', models.CharField(blank=True, max_length=100)),
                ('processed', models.BooleanField(default=False)),
                ('note', models.CharField(blank=True, max_length=200)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='raw_attendance_events', to='core.employee')),
            ],
            options={
                'ordering': ['-captured_at'],
            },
        ),
        migrations.CreateModel(
            name='SalaryComponent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=100)),
                ('component_type', models.CharField(choices=[('earning', 'Earning'), ('deduction', 'Deduction')], max_length=16)),
                ('default_amount', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('taxable', models.BooleanField(default=True)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='salary_components', to='core.company')),
            ],
            options={
                'ordering': ['component_type', 'name'],
            },
        ),
        migrations.CreateModel(
            name='Shift',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=100)),
                ('start_time', models.TimeField()),
                ('end_time', models.TimeField()),
                ('break_minutes', models.PositiveIntegerField(default=60)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='hr_shifts', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Skill',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('category', models.CharField(blank=True, max_length=80)),
                ('description', models.TextField(blank=True)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='hr_skills', to='core.company')),
            ],
            options={
                'ordering': ['category', 'name'],
            },
        ),
        migrations.CreateModel(
            name='TrainingProgram',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=140)),
                ('trainer', models.CharField(blank=True, max_length=140)),
                ('date_start', models.DateField()),
                ('date_end', models.DateField()),
                ('capacity', models.PositiveIntegerField(default=1)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('scheduled', 'Scheduled'), ('running', 'Running'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('description', models.TextField(blank=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='training_programs', to='core.company')),
            ],
            options={
                'ordering': ['-date_start'],
            },
        ),
        migrations.CreateModel(
            name='TravelRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40, unique=True)),
                ('destination', models.CharField(max_length=160)),
                ('date_start', models.DateField()),
                ('date_end', models.DateField()),
                ('purpose', models.TextField()),
                ('estimated_amount', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('approver', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='travel_approvals', to=settings.AUTH_USER_MODEL)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='travel_requests', to='core.employee')),
            ],
            options={
                'ordering': ['-date_start'],
            },
        ),
        migrations.CreateModel(
            name='WorkLocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=120)),
                ('address', models.TextField(blank=True)),
                ('timezone', models.CharField(default='Asia/Jakarta', max_length=60)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='work_locations', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='WorkSchedule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=100)),
                ('working_days', models.CharField(default='Mon,Tue,Wed,Thu,Fri', max_length=80)),
                ('hours_per_day', models.DecimalField(decimal_places=2, default=8, max_digits=4)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='work_schedules', to='core.company')),
                ('shift', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='work_schedules', to='gsshop_hr.shift')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='ScheduleAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('date_start', models.DateField()),
                ('date_end', models.DateField(blank=True, null=True)),
                ('active', models.BooleanField(default=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='schedule_assignments', to='core.employee')),
                ('schedule', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assignments', to='gsshop_hr.workschedule')),
            ],
            options={
                'ordering': ['-date_start', 'employee__name'],
            },
        ),
        migrations.CreateModel(
            name='BusinessUnit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=120)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='business_units', to='core.company')),
                ('manager', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='managed_business_units', to='core.employee')),
            ],
            options={
                'ordering': ['name'],
                'constraints': [models.UniqueConstraint(fields=('company', 'code'), name='unique_business_unit_code_per_company')],
            },
        ),
        migrations.CreateModel(
            name='CompanyHoliday',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('holiday_date', models.DateField()),
                ('recurring', models.BooleanField(default=False)),
                ('paid', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='company_holidays', to='core.company')),
            ],
            options={
                'ordering': ['-holiday_date', 'name'],
                'constraints': [models.UniqueConstraint(fields=('company', 'holiday_date', 'name'), name='unique_company_holiday')],
            },
        ),
        migrations.AddConstraint(
            model_name='documenttype',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_document_type_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='employeedisciplinary',
            constraint=models.UniqueConstraint(fields=('company', 'reference'), name='unique_disciplinary_reference_per_company'),
        ),
        migrations.AddConstraint(
            model_name='employeegrade',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_employee_grade_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='hryear',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='unique_hr_year_name_per_company'),
        ),
        migrations.AddConstraint(
            model_name='jobposition',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_job_position_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='leavetype',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_leave_type_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='leaveallocation',
            constraint=models.UniqueConstraint(fields=('employee', 'leave_type', 'year'), name='unique_leave_allocation_per_year'),
        ),
        migrations.AddConstraint(
            model_name='overtimerule',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='unique_overtime_rule_name_per_company'),
        ),
        migrations.AddConstraint(
            model_name='payrollperiod',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='unique_payroll_period_name_per_company'),
        ),
        migrations.AddConstraint(
            model_name='salarycomponent',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_salary_component_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='shift',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_shift_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='skill',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='unique_hr_skill_name_per_company'),
        ),
        migrations.AddConstraint(
            model_name='trainingprogram',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_training_program_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='worklocation',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_work_location_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='workschedule',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_work_schedule_code_per_company'),
        ),
    ]
