import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='LocalizationPack',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('country_code', models.CharField(max_length=2, unique=True)),
                ('country_name', models.CharField(max_length=100)),
                ('currency', models.CharField(max_length=3)),
                ('tax_label', models.CharField(max_length=40)),
                ('language_code', models.CharField(default='en', max_length=12)),
                ('version', models.CharField(default='0.1.0', max_length=20)),
                ('enabled', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ['country_name'],
            },
        ),
        migrations.CreateModel(
            name='DocumentSequence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('document_type', models.CharField(max_length=80)),
                ('prefix', models.CharField(max_length=30)),
                ('padding', models.PositiveSmallIntegerField(default=5)),
                ('next_number', models.PositiveBigIntegerField(default=1)),
                ('reset_annually', models.BooleanField(default=True)),
                ('localization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='document_sequences', to='gsshop_localization.localizationpack')),
            ],
            options={
                'ordering': ['localization', 'document_type'],
                'constraints': [models.UniqueConstraint(fields=('localization', 'document_type'), name='unique_document_sequence_per_localization')],
            },
        ),
        migrations.CreateModel(
            name='PayrollParameter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=40)),
                ('name', models.CharField(max_length=140)),
                ('value', models.DecimalField(decimal_places=4, default=0, max_digits=18)),
                ('unit', models.CharField(default='percent', max_length=24)),
                ('effective_from', models.DateField(blank=True, null=True)),
                ('active', models.BooleanField(default=True)),
                ('localization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payroll_parameters', to='gsshop_localization.localizationpack')),
            ],
            options={
                'ordering': ['localization', 'code'],
                'constraints': [models.UniqueConstraint(fields=('localization', 'code'), name='unique_payroll_parameter_per_localization')],
            },
        ),
        migrations.CreateModel(
            name='TaxRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=40)),
                ('name', models.CharField(max_length=140)),
                ('tax_type', models.CharField(choices=[('sales', 'Sales Tax'), ('purchase', 'Purchase Tax'), ('withholding', 'Withholding Tax'), ('zero', 'Zero Rated'), ('exempt', 'Exempt')], max_length=16)),
                ('rate', models.DecimalField(decimal_places=4, default=0, max_digits=7, validators=[django.core.validators.MinValueValidator(0)])),
                ('effective_from', models.DateField(blank=True, null=True)),
                ('effective_until', models.DateField(blank=True, null=True)),
                ('active', models.BooleanField(default=True)),
                ('localization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tax_rules', to='gsshop_localization.localizationpack')),
            ],
            options={
                'ordering': ['localization', 'code'],
                'constraints': [models.UniqueConstraint(fields=('localization', 'code'), name='unique_tax_rule_per_localization')],
            },
        ),
    ]
