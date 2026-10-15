import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_employee_supervisor_employee_user'),
        ('gsshop_hr', '0002_cashadvance_documenttype_employeechangerequest_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CareerTransitionType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=120)),
                ('category', models.CharField(choices=[('promotion', 'Promotion'), ('transfer', 'Transfer'), ('demotion', 'Demotion'), ('resignation', 'Resignation'), ('termination', 'Termination'), ('retirement', 'Retirement')], max_length=20)),
                ('requires_clearance', models.BooleanField(default=False)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='career_transition_types', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='CareerTransitionRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40, unique=True)),
                ('effective_date', models.DateField()),
                ('current_position', models.CharField(blank=True, max_length=120)),
                ('new_position', models.CharField(blank=True, max_length=120)),
                ('reason', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('approver', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='career_transition_approvals', to=settings.AUTH_USER_MODEL)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='career_transition_requests', to='core.employee')),
                ('transition_type', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='requests', to='gsshop_hr.careertransitiontype')),
            ],
            options={
                'ordering': ['-effective_date', 'number'],
            },
        ),
        migrations.CreateModel(
            name='CompensationBenefit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('effective_date', models.DateField(default=django.utils.timezone.localdate)),
                ('basic_salary', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('allowance_amount', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('deduction_amount', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('notes', models.TextField(blank=True)),
                ('approver', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='compensation_approvals', to=settings.AUTH_USER_MODEL)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='compensation_benefits', to='core.employee')),
            ],
            options={
                'ordering': ['-effective_date', 'employee__name'],
            },
        ),
        migrations.CreateModel(
            name='ExpenseProduct',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=120)),
                ('category', models.CharField(blank=True, max_length=100)),
                ('maximum_amount', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('receipt_required', models.BooleanField(default=True)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='employee_expense_products', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='HRSetting',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('standard_hours_per_day', models.DecimalField(decimal_places=2, default=8, max_digits=4)),
                ('attendance_tolerance_minutes', models.PositiveIntegerField(default=15)),
                ('leave_approval_levels', models.PositiveSmallIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(5)])),
                ('overtime_approval_required', models.BooleanField(default=True)),
                ('payroll_currency', models.CharField(default='IDR', max_length=3)),
                ('company', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='hr_setting', to='core.company')),
            ],
            options={
                'ordering': ['company__name'],
            },
        ),
        migrations.CreateModel(
            name='LoanType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=120)),
                ('interest_method', models.CharField(choices=[('flat', 'Flat'), ('declining', 'Declining Balance'), ('none', 'No Interest')], default='flat', max_length=16)),
                ('default_interest_rate', models.DecimalField(decimal_places=3, default=0, max_digits=6)),
                ('maximum_term_months', models.PositiveIntegerField(default=12)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='employee_loan_types', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='LoanPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('minimum_service_months', models.PositiveIntegerField(default=0)),
                ('maximum_amount', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('maximum_installment_ratio', models.DecimalField(decimal_places=2, default=30, help_text='Maximum percentage of salary.', max_digits=5)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='employee_loan_policies', to='core.company')),
                ('loan_type', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='policies', to='gsshop_hr.loantype')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='PayrollRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40, unique=True)),
                ('employee_count', models.PositiveIntegerField(default=0)),
                ('gross_amount', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('deduction_amount', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('net_amount', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('calculating', 'Calculating'), ('ready', 'Ready'), ('posted', 'Posted'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('notes', models.TextField(blank=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payroll_runs', to='core.company')),
                ('period', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payroll_runs', to='gsshop_hr.payrollperiod')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='TrainingCourse',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=140)),
                ('category', models.CharField(blank=True, max_length=100)),
                ('duration_hours', models.DecimalField(decimal_places=2, default=1, max_digits=7)),
                ('description', models.TextField(blank=True)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='hr_training_courses', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='TrainingTrainer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=140)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('phone', models.CharField(blank=True, max_length=40)),
                ('specialties', models.TextField(blank=True)),
                ('external', models.BooleanField(default=False)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='hr_training_trainers', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='TrainingConduct',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reference', models.CharField(max_length=40, unique=True)),
                ('date_start', models.DateField()),
                ('date_end', models.DateField()),
                ('location', models.CharField(blank=True, max_length=160)),
                ('participant_count', models.PositiveIntegerField(default=0)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('scheduled', 'Scheduled'), ('running', 'Running'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('course', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='conducts', to='gsshop_hr.trainingcourse')),
                ('trainer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='conducts', to='gsshop_hr.trainingtrainer')),
            ],
            options={
                'ordering': ['-date_start'],
            },
        ),
        migrations.CreateModel(
            name='TravelPackage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('destination_group', models.CharField(max_length=120)),
                ('daily_allowance', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('lodging_limit', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('transport_limit', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='travel_packages', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='CareerTransitionLetter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=120)),
                ('subject', models.CharField(max_length=180)),
                ('template_body', models.TextField(help_text='Use plain text placeholders such as employee_name and effective_date.')),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='career_transition_letters', to='core.company')),
            ],
            options={
                'ordering': ['name'],
                'constraints': [models.UniqueConstraint(fields=('company', 'code'), name='unique_transition_letter_code_per_company')],
            },
        ),
        migrations.AddConstraint(
            model_name='careertransitiontype',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_transition_type_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='expenseproduct',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_expense_product_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='loantype',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_loan_type_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='loanpolicy',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='unique_loan_policy_name_per_company'),
        ),
        migrations.AddConstraint(
            model_name='trainingcourse',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_training_course_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='travelpackage',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='unique_travel_package_name_per_company'),
        ),
    ]
