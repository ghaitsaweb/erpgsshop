import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Company',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=20, unique=True)),
                ('name', models.CharField(max_length=160)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('phone', models.CharField(blank=True, max_length=40)),
                ('address', models.TextField(blank=True)),
                ('currency', models.CharField(default='IDR', max_length=3)),
                ('active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name_plural': 'Companies',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Contact',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=160)),
                ('contact_type', models.CharField(choices=[('customer', 'Customer'), ('vendor', 'Vendor'), ('both', 'Customer & Vendor')], default='customer', max_length=16)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('phone', models.CharField(blank=True, max_length=40)),
                ('tax_id', models.CharField(blank=True, max_length=64)),
                ('address', models.TextField(blank=True)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='contacts', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Employee',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('employee_id', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=160)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('phone', models.CharField(blank=True, max_length=40)),
                ('department', models.CharField(blank=True, max_length=100)),
                ('position', models.CharField(blank=True, max_length=100)),
                ('join_date', models.DateField(default=django.utils.timezone.localdate)),
                ('status', models.CharField(choices=[('active', 'Active'), ('inactive', 'Inactive')], default='active', max_length=16)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='employees', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Attendance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('attendance_date', models.DateField(default=django.utils.timezone.localdate)),
                ('check_in', models.TimeField(blank=True, null=True)),
                ('check_out', models.TimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('present', 'Present'), ('leave', 'Leave'), ('sick', 'Sick'), ('absent', 'Absent')], default='present', max_length=16)),
                ('note', models.CharField(blank=True, max_length=200)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='attendances', to='core.employee')),
            ],
            options={
                'ordering': ['-attendance_date', 'employee__name'],
            },
        ),
        migrations.CreateModel(
            name='Invoice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40)),
                ('invoice_type', models.CharField(choices=[('customer', 'Customer Invoice'), ('vendor', 'Vendor Bill')], default='customer', max_length=16)),
                ('invoice_date', models.DateField(default=django.utils.timezone.localdate)),
                ('due_date', models.DateField()),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('posted', 'Posted'), ('paid', 'Paid'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('total', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=18)),
                ('paid_amount', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('notes', models.TextField(blank=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='invoices', to='core.company')),
                ('partner', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='invoices', to='core.contact')),
            ],
            options={
                'ordering': ['-invoice_date', '-id'],
            },
        ),
        migrations.CreateModel(
            name='Product',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sku', models.CharField(max_length=40)),
                ('name', models.CharField(max_length=160)),
                ('product_type', models.CharField(choices=[('stockable', 'Stockable Product'), ('consumable', 'Consumable'), ('service', 'Service')], default='stockable', max_length=16)),
                ('unit', models.CharField(default='Unit', max_length=24)),
                ('sale_price', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('purchase_price', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('current_stock', models.DecimalField(decimal_places=3, default=0, editable=False, max_digits=18)),
                ('reorder_level', models.DecimalField(decimal_places=3, default=0, max_digits=18)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='products', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='InvoiceLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('description', models.CharField(max_length=200)),
                ('quantity', models.DecimalField(decimal_places=3, default=1, max_digits=18)),
                ('unit_price', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='core.invoice')),
                ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='core.product')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='PurchaseOrder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40)),
                ('order_date', models.DateField(default=django.utils.timezone.localdate)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('confirmed', 'Confirmed'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('total', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=18)),
                ('notes', models.TextField(blank=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.company')),
                ('vendor', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='purchase_orders', to='core.contact')),
            ],
            options={
                'ordering': ['-order_date', '-id'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='PurchaseOrderLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('description', models.CharField(blank=True, max_length=200)),
                ('quantity', models.DecimalField(decimal_places=3, default=1, max_digits=18)),
                ('unit_price', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='core.purchaseorder')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.product')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='SaleOrder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40)),
                ('order_date', models.DateField(default=django.utils.timezone.localdate)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('confirmed', 'Confirmed'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('total', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=18)),
                ('notes', models.TextField(blank=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.company')),
                ('customer', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sale_orders', to='core.contact')),
            ],
            options={
                'ordering': ['-order_date', '-id'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='SaleOrderLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('description', models.CharField(blank=True, max_length=200)),
                ('quantity', models.DecimalField(decimal_places=3, default=1, max_digits=18)),
                ('unit_price', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='core.saleorder')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.product')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='Warehouse',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=120)),
                ('address', models.TextField(blank=True)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='warehouses', to='core.company')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='StockMove',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('direction', models.CharField(choices=[('in', 'Stock In'), ('out', 'Stock Out')], max_length=3)),
                ('quantity', models.DecimalField(decimal_places=3, max_digits=18)),
                ('reference', models.CharField(blank=True, max_length=80)),
                ('movement_date', models.DateField(default=django.utils.timezone.localdate)),
                ('note', models.TextField(blank=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stock_moves', to='core.company')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stock_moves', to=settings.AUTH_USER_MODEL)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stock_moves', to='core.product')),
                ('warehouse', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='stock_moves', to='core.warehouse')),
            ],
            options={
                'ordering': ['-movement_date', '-id'],
            },
        ),
        migrations.AddConstraint(
            model_name='contact',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_contact_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='employee',
            constraint=models.UniqueConstraint(fields=('company', 'employee_id'), name='unique_employee_id_per_company'),
        ),
        migrations.AddConstraint(
            model_name='attendance',
            constraint=models.UniqueConstraint(fields=('employee', 'attendance_date'), name='unique_attendance_per_day'),
        ),
        migrations.AddConstraint(
            model_name='invoice',
            constraint=models.UniqueConstraint(fields=('company', 'number'), name='unique_invoice_number_per_company'),
        ),
        migrations.AddConstraint(
            model_name='product',
            constraint=models.UniqueConstraint(fields=('company', 'sku'), name='unique_product_sku_per_company'),
        ),
        migrations.AddConstraint(
            model_name='purchaseorder',
            constraint=models.UniqueConstraint(fields=('company', 'number'), name='unique_purchase_number_per_company'),
        ),
        migrations.AddConstraint(
            model_name='saleorder',
            constraint=models.UniqueConstraint(fields=('company', 'number'), name='unique_sale_number_per_company'),
        ),
        migrations.AddConstraint(
            model_name='warehouse',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_warehouse_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='stockmove',
            constraint=models.CheckConstraint(condition=models.Q(('quantity__gt', 0)), name='stock_move_quantity_positive'),
        ),
    ]
