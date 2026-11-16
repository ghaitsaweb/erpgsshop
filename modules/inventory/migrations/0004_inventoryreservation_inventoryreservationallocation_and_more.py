import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_company_logo'),
        ('gsshop_inventory', '0003_inventorystockcount_inventorystockcountline_and_more'),
        ('organization', '0002_recordorganizationscope'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='InventoryReservation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reference', models.CharField(max_length=80)),
                ('requested_quantity', models.DecimalField(decimal_places=3, max_digits=18)),
                ('reserved_quantity', models.DecimalField(decimal_places=3, default=0, editable=False, max_digits=18)),
                ('allow_partial', models.BooleanField(default=False)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('reserved', 'Reserved'), ('partially_reserved', 'Partially Reserved'), ('released', 'Released'), ('consumed', 'Consumed'), ('cancelled', 'Cancelled')], db_index=True, default='draft', max_length=24)),
                ('notes', models.TextField(blank=True)),
                ('reserved_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('released_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('consumed_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('cancelled_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('branch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='inventory_reservations', to='organization.branch')),
                ('cancelled_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='cancelled_inventory_reservations', to=settings.AUTH_USER_MODEL)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='inventory_reservations', to='core.company')),
                ('consumed_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='consumed_inventory_reservations', to=settings.AUTH_USER_MODEL)),
                ('location', models.ForeignKey(blank=True, help_text='Leave blank to allocate across active internal locations by priority.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='inventory_reservations', to='gsshop_inventory.inventorylocation')),
                ('lot', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='inventory_reservations', to='gsshop_inventory.inventorylotserial')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='inventory_reservations', to='core.product')),
                ('released_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='released_inventory_reservations', to=settings.AUTH_USER_MODEL)),
                ('reserved_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='reserved_inventory_reservations', to=settings.AUTH_USER_MODEL)),
                ('warehouse', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='inventory_reservations', to='core.warehouse')),
            ],
            options={
                'ordering': ['-created_at', '-pk'],
            },
        ),
        migrations.CreateModel(
            name='InventoryReservationAllocation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity', models.DecimalField(decimal_places=3, max_digits=18)),
                ('allocated_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('balance', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reservation_allocations', to='gsshop_inventory.inventorystockbalance')),
                ('reservation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='allocations', to='gsshop_inventory.inventoryreservation')),
                ('stock_move', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='inventory_reservation_allocation', to='core.stockmove')),
            ],
            options={
                'ordering': ['reservation_id', 'balance_id', 'pk'],
            },
        ),
        migrations.AddIndex(
            model_name='inventoryreservation',
            index=models.Index(fields=['company', 'branch', 'warehouse', 'status'], name='inv_reservation_scope_idx'),
        ),
        migrations.AddIndex(
            model_name='inventoryreservation',
            index=models.Index(fields=['company', 'product', 'status'], name='inv_reservation_product_idx'),
        ),
        migrations.AddConstraint(
            model_name='inventoryreservation',
            constraint=models.UniqueConstraint(fields=('company', 'reference'), name='unique_inventory_reservation_reference'),
        ),
        migrations.AddConstraint(
            model_name='inventoryreservation',
            constraint=models.CheckConstraint(condition=models.Q(('requested_quantity__gt', 0)), name='inventory_reservation_requested_positive'),
        ),
        migrations.AddConstraint(
            model_name='inventoryreservation',
            constraint=models.CheckConstraint(condition=models.Q(('reserved_quantity__gte', 0)), name='inventory_reservation_reserved_nonnegative'),
        ),
        migrations.AddConstraint(
            model_name='inventoryreservationallocation',
            constraint=models.UniqueConstraint(fields=('reservation', 'balance'), name='unique_inventory_reservation_balance'),
        ),
        migrations.AddConstraint(
            model_name='inventoryreservationallocation',
            constraint=models.CheckConstraint(condition=models.Q(('quantity__gt', 0)), name='inventory_reservation_allocation_positive'),
        ),
    ]
