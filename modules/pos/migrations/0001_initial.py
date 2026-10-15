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
            name='POSOrder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40, unique=True)),
                ('order_type', models.CharField(choices=[('retail', 'Retail'), ('dine_in', 'Dine In'), ('takeaway', 'Takeaway'), ('delivery', 'Delivery')], default='retail', max_length=16)),
                ('total', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=18)),
                ('paid_amount', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('payment_method', models.CharField(blank=True, max_length=40)),
                ('status', models.CharField(choices=[('open', 'Open'), ('paid', 'Paid'), ('cancelled', 'Cancelled'), ('refunded', 'Refunded')], default='open', max_length=16)),
                ('customer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='pos_orders', to='core.contact')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='KitchenTicket',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('station', models.CharField(default='Main Kitchen', max_length=80)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('preparing', 'Preparing'), ('ready', 'Ready'), ('served', 'Served'), ('cancelled', 'Cancelled')], default='queued', max_length=16)),
                ('notes', models.TextField(blank=True)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='kitchen_tickets', to='gsshop_pos.posorder')),
            ],
            options={
                'ordering': ['status', 'created_at'],
            },
        ),
        migrations.CreateModel(
            name='POSOrderLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('quantity', models.DecimalField(decimal_places=3, default=1, max_digits=18)),
                ('unit_price', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('notes', models.CharField(blank=True, max_length=200)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='gsshop_pos.posorder')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pos_order_lines', to='core.product')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='POSRegister',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=120)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pos_registers', to='core.company')),
                ('warehouse', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pos_registers', to='core.warehouse')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='POSSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('opened_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('closed_at', models.DateTimeField(blank=True, null=True)),
                ('opening_cash', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('closing_cash', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('status', models.CharField(choices=[('open', 'Open'), ('closed', 'Closed')], default='open', max_length=16)),
                ('opened_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='opened_pos_sessions', to=settings.AUTH_USER_MODEL)),
                ('register', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sessions', to='gsshop_pos.posregister')),
            ],
            options={
                'ordering': ['-opened_at'],
            },
        ),
        migrations.AddField(
            model_name='posorder',
            name='session',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='orders', to='gsshop_pos.possession'),
        ),
        migrations.CreateModel(
            name='RestaurantTable',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=60)),
                ('area', models.CharField(blank=True, max_length=80)),
                ('seats', models.PositiveSmallIntegerField(default=2)),
                ('status', models.CharField(choices=[('available', 'Available'), ('occupied', 'Occupied'), ('reserved', 'Reserved'), ('cleaning', 'Cleaning')], default='available', max_length=16)),
                ('register', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='restaurant_tables', to='gsshop_pos.posregister')),
            ],
            options={
                'ordering': ['area', 'name'],
            },
        ),
        migrations.AddField(
            model_name='posorder',
            name='restaurant_table',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='orders', to='gsshop_pos.restauranttable'),
        ),
        migrations.AddConstraint(
            model_name='posregister',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_pos_register_per_company'),
        ),
        migrations.AddConstraint(
            model_name='restauranttable',
            constraint=models.UniqueConstraint(fields=('register', 'name'), name='unique_restaurant_table_per_register'),
        ),
    ]
