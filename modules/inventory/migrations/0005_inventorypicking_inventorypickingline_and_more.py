import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_company_logo'),
        ('gsshop_inventory', '0004_inventoryreservation_inventoryreservationallocation_and_more'),
        ('organization', '0002_recordorganizationscope'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='InventoryPicking',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reference', models.CharField(max_length=80)),
                ('scheduled_date', models.DateField(db_index=True, default=django.utils.timezone.localdate)),
                ('destination_name', models.CharField(blank=True, max_length=160)),
                ('destination_address', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('confirmed', 'Waiting Availability'), ('partially_available', 'Partially Available'), ('ready', 'Ready'), ('done', 'Done'), ('cancelled', 'Cancelled')], db_index=True, default='draft', max_length=24)),
                ('notes', models.TextField(blank=True)),
                ('confirmed_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('availability_checked_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('completed_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('cancelled_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('availability_checked_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='availability_checked_inventory_pickings', to=settings.AUTH_USER_MODEL)),
                ('backorder_of', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='backorders', to='gsshop_inventory.inventorypicking')),
                ('branch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='inventory_pickings', to='organization.branch')),
                ('cancelled_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='cancelled_inventory_pickings', to=settings.AUTH_USER_MODEL)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='inventory_pickings', to='core.company')),
                ('completed_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='completed_inventory_pickings', to=settings.AUTH_USER_MODEL)),
                ('confirmed_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='confirmed_inventory_pickings', to=settings.AUTH_USER_MODEL)),
                ('responsible', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='responsible_inventory_pickings', to=settings.AUTH_USER_MODEL)),
                ('warehouse', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='inventory_pickings', to='core.warehouse')),
            ],
            options={
                'ordering': ['-scheduled_date', '-created_at', '-pk'],
            },
        ),
        migrations.CreateModel(
            name='InventoryPickingLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('requested_quantity', models.DecimalField(decimal_places=3, max_digits=18)),
                ('reserved_quantity', models.DecimalField(decimal_places=3, default=0, editable=False, max_digits=18)),
                ('done_quantity', models.DecimalField(decimal_places=3, default=0, max_digits=18)),
                ('current_reservation', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='current_picking_lines', to='gsshop_inventory.inventoryreservation')),
                ('location', models.ForeignKey(blank=True, help_text='Optional exact source location; blank uses location priority.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='inventory_picking_lines', to='gsshop_inventory.inventorylocation')),
                ('lot', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='inventory_picking_lines', to='gsshop_inventory.inventorylotserial')),
                ('picking', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='gsshop_inventory.inventorypicking')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='inventory_picking_lines', to='core.product')),
            ],
            options={
                'ordering': ['picking_id', 'pk'],
            },
        ),
        migrations.AddIndex(
            model_name='inventorypicking',
            index=models.Index(fields=['company', 'branch', 'warehouse', 'status'], name='inv_picking_scope_idx'),
        ),
        migrations.AddIndex(
            model_name='inventorypicking',
            index=models.Index(fields=['company', 'scheduled_date', 'status'], name='inv_picking_schedule_idx'),
        ),
        migrations.AddConstraint(
            model_name='inventorypicking',
            constraint=models.UniqueConstraint(fields=('company', 'reference'), name='unique_inventory_picking_reference'),
        ),
        migrations.AddConstraint(
            model_name='inventorypickingline',
            constraint=models.UniqueConstraint(fields=('picking', 'product', 'location', 'lot'), name='unique_inventory_picking_position', nulls_distinct=False),
        ),
        migrations.AddConstraint(
            model_name='inventorypickingline',
            constraint=models.CheckConstraint(condition=models.Q(('requested_quantity__gt', 0)), name='inventory_picking_requested_positive'),
        ),
        migrations.AddConstraint(
            model_name='inventorypickingline',
            constraint=models.CheckConstraint(condition=models.Q(('reserved_quantity__gte', 0)), name='inventory_picking_reserved_nonnegative'),
        ),
        migrations.AddConstraint(
            model_name='inventorypickingline',
            constraint=models.CheckConstraint(condition=models.Q(('done_quantity__gte', 0)), name='inventory_picking_done_nonnegative'),
        ),
    ]
