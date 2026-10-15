import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_employee_supervisor_employee_user'),
    ]

    operations = [
        migrations.AddField(
            model_name='saleorder',
            name='customer_reference',
            field=models.CharField(blank=True, max_length=120, verbose_name='Customer Reference'),
        ),
        migrations.AddField(
            model_name='saleorder',
            name='odoo_source_id',
            field=models.PositiveIntegerField(blank=True, editable=False, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='saleorder',
            name='payment_terms',
            field=models.CharField(blank=True, max_length=120, verbose_name='Payment Terms'),
        ),
        migrations.AddField(
            model_name='saleorder',
            name='price_list',
            field=models.CharField(blank=True, default='IDR', max_length=120, verbose_name='Pricelist'),
        ),
        migrations.AddField(
            model_name='saleorder',
            name='salesperson_name',
            field=models.CharField(blank=True, max_length=160, verbose_name='Salesperson'),
        ),
        migrations.AddField(
            model_name='saleorder',
            name='validity_date',
            field=models.DateField(blank=True, null=True, verbose_name='Expiration Date'),
        ),
        migrations.AddField(
            model_name='saleorderline',
            name='discount_percent',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)], verbose_name='Discount (%)'),
        ),
        migrations.AddField(
            model_name='saleorderline',
            name='odoo_source_id',
            field=models.PositiveIntegerField(blank=True, editable=False, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='saleorderline',
            name='rental_return_date',
            field=models.DateField(blank=True, null=True, verbose_name='Tanggal Kembali'),
        ),
        migrations.AddField(
            model_name='saleorderline',
            name='rental_start_date',
            field=models.DateField(blank=True, null=True, verbose_name='Tanggal Sewa'),
        ),
        migrations.AddField(
            model_name='saleorderline',
            name='rental_status',
            field=models.CharField(blank=True, choices=[('returned', 'Sudah Kembali'), ('at_customer', 'Di Customer')], max_length=20, verbose_name='Status Barang'),
        ),
    ]
