import django.core.validators
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
            name='Asset',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=40)),
                ('name', models.CharField(max_length=160)),
                ('purchase_date', models.DateField(blank=True, null=True)),
                ('acquisition_value', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('location', models.CharField(blank=True, max_length=160)),
                ('status', models.CharField(choices=[('available', 'Available'), ('in_use', 'In Use'), ('maintenance', 'Maintenance'), ('retired', 'Retired')], default='available', max_length=16)),
                ('assigned_employee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_assets', to='core.employee')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assets', to='core.company')),
                ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assets', to='core.product')),
            ],
            options={
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='BillOfMaterial',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=40)),
                ('output_quantity', models.DecimalField(decimal_places=3, default=1, max_digits=18)),
                ('active', models.BooleanField(default=True)),
                ('notes', models.TextField(blank=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='bills_of_material', to='core.company')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='boms', to='core.product')),
            ],
            options={
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='BillOfMaterialLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('quantity', models.DecimalField(decimal_places=3, max_digits=18)),
                ('bom', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='components', to='gsshop_operations.billofmaterial')),
                ('component', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='used_in_boms', to='core.product')),
                ('warehouse', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='core.warehouse')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='ConstructionProject',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=40)),
                ('name', models.CharField(max_length=180)),
                ('site_address', models.TextField(blank=True)),
                ('contract_value', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField(blank=True, null=True)),
                ('progress', models.PositiveSmallIntegerField(default=0, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)])),
                ('status', models.CharField(choices=[('tender', 'Tender'), ('active', 'Active'), ('on_hold', 'On Hold'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], default='tender', max_length=16)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='construction_projects', to='core.company')),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='construction_projects', to='core.contact')),
            ],
            options={
                'ordering': ['-start_date', 'name'],
            },
        ),
        migrations.CreateModel(
            name='MaintenanceRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40, unique=True)),
                ('request_date', models.DateField(default=django.utils.timezone.localdate)),
                ('scheduled_date', models.DateField(blank=True, null=True)),
                ('description', models.TextField()),
                ('estimated_cost', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('actual_cost', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('status', models.CharField(choices=[('open', 'Open'), ('scheduled', 'Scheduled'), ('in_progress', 'In Progress'), ('done', 'Done'), ('cancelled', 'Cancelled')], default='open', max_length=16)),
                ('asset', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='maintenance_requests', to='gsshop_operations.asset')),
                ('assigned_to', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='maintenance_requests', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-request_date', '-id'],
            },
        ),
        migrations.CreateModel(
            name='ManufacturingOrder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40)),
                ('quantity', models.DecimalField(decimal_places=3, default=1, max_digits=18)),
                ('planned_start', models.DateTimeField()),
                ('planned_end', models.DateTimeField()),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('confirmed', 'Confirmed'), ('in_progress', 'In Progress'), ('done', 'Done'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('bom', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='manufacturing_orders', to='gsshop_operations.billofmaterial')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='manufacturing_orders', to='core.company')),
                ('responsible', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='manufacturing_orders', to=settings.AUTH_USER_MODEL)),
                ('warehouse', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='manufacturing_orders', to='core.warehouse')),
            ],
            options={
                'ordering': ['-planned_start'],
            },
        ),
        migrations.CreateModel(
            name='Project',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=40)),
                ('name', models.CharField(max_length=180)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField(blank=True, null=True)),
                ('budget', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('progress', models.PositiveSmallIntegerField(default=0, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)])),
                ('status', models.CharField(choices=[('planning', 'Planning'), ('active', 'Active'), ('on_hold', 'On Hold'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], default='planning', max_length=16)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='projects', to='core.company')),
                ('customer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='projects', to='core.contact')),
                ('manager', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='managed_projects', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-start_date', 'name'],
            },
        ),
        migrations.CreateModel(
            name='ProjectTask',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=180)),
                ('due_date', models.DateField(blank=True, null=True)),
                ('status', models.CharField(choices=[('todo', 'To Do'), ('in_progress', 'In Progress'), ('blocked', 'Blocked'), ('done', 'Done')], default='todo', max_length=16)),
                ('progress', models.PositiveSmallIntegerField(default=0, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)])),
                ('description', models.TextField(blank=True)),
                ('assignee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='project_tasks', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tasks', to='gsshop_operations.project')),
            ],
            options={
                'ordering': ['status', 'due_date', 'id'],
            },
        ),
        migrations.CreateModel(
            name='QualityInspection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40)),
                ('inspection_date', models.DateField(default=django.utils.timezone.localdate)),
                ('reference', models.CharField(blank=True, max_length=80)),
                ('result', models.CharField(choices=[('pending', 'Pending'), ('pass', 'Pass'), ('fail', 'Fail'), ('conditional', 'Conditional')], default='pending', max_length=16)),
                ('notes', models.TextField(blank=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='quality_inspections', to='core.company')),
                ('inspector', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='quality_inspections', to=settings.AUTH_USER_MODEL)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='quality_inspections', to='core.product')),
            ],
            options={
                'ordering': ['-inspection_date', '-id'],
            },
        ),
        migrations.CreateModel(
            name='WorkPackage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=40)),
                ('name', models.CharField(max_length=160)),
                ('budget', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('actual_cost', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('progress', models.PositiveSmallIntegerField(default=0, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)])),
                ('status', models.CharField(choices=[('planning', 'Planning'), ('active', 'Active'), ('on_hold', 'On Hold'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], default='planning', max_length=16)),
                ('construction_project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='work_packages', to='gsshop_operations.constructionproject')),
            ],
            options={
                'ordering': ['construction_project', 'code'],
            },
        ),
        migrations.AddConstraint(
            model_name='asset',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_asset_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='billofmaterial',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_bom_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='constructionproject',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_construction_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='manufacturingorder',
            constraint=models.UniqueConstraint(fields=('company', 'number'), name='unique_manufacturing_number_per_company'),
        ),
        migrations.AddConstraint(
            model_name='project',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_project_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='qualityinspection',
            constraint=models.UniqueConstraint(fields=('company', 'number'), name='unique_quality_number_per_company'),
        ),
        migrations.AddConstraint(
            model_name='workpackage',
            constraint=models.UniqueConstraint(fields=('construction_project', 'code'), name='unique_work_package_per_project'),
        ),
    ]
