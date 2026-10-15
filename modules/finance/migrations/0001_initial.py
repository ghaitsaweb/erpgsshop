import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Account',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=30)),
                ('name', models.CharField(max_length=160)),
                ('account_type', models.CharField(choices=[('asset', 'Asset'), ('liability', 'Liability'), ('equity', 'Equity'), ('income', 'Income'), ('expense', 'Expense')], max_length=16)),
                ('active', models.BooleanField(default=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='accounts', to='core.company')),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='children', to='gsshop_finance.account')),
            ],
            options={
                'ordering': ['code'],
            },
        ),
        migrations.CreateModel(
            name='Budget',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('amount', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('committed', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('actual', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('approved', 'Approved'), ('closed', 'Closed')], default='draft', max_length=16)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='budgets', to='core.company')),
            ],
            options={
                'ordering': ['-start_date', 'name'],
            },
        ),
        migrations.CreateModel(
            name='ExpenseClaim',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reference', models.CharField(max_length=40)),
                ('claim_date', models.DateField(default=django.utils.timezone.localdate)),
                ('category', models.CharField(max_length=80)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=18, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('description', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('paid', 'Paid'), ('rejected', 'Rejected')], default='draft', max_length=16)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='expense_claims', to='core.company')),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='expense_claims', to='core.employee')),
            ],
            options={
                'ordering': ['-claim_date', '-id'],
            },
        ),
        migrations.CreateModel(
            name='JournalEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40)),
                ('entry_date', models.DateField(default=django.utils.timezone.localdate)),
                ('reference', models.CharField(blank=True, max_length=80)),
                ('description', models.CharField(blank=True, max_length=240)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('posted', 'Posted'), ('reversed', 'Reversed')], default='draft', max_length=16)),
                ('total_debit', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=18)),
                ('total_credit', models.DecimalField(decimal_places=2, default=0, editable=False, max_digits=18)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='journal_entries', to='core.company')),
            ],
            options={
                'ordering': ['-entry_date', '-id'],
            },
        ),
        migrations.CreateModel(
            name='JournalLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('description', models.CharField(blank=True, max_length=200)),
                ('debit', models.DecimalField(decimal_places=2, default=0, max_digits=18, validators=[django.core.validators.MinValueValidator(Decimal('0'))])),
                ('credit', models.DecimalField(decimal_places=2, default=0, max_digits=18, validators=[django.core.validators.MinValueValidator(Decimal('0'))])),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='journal_lines', to='gsshop_finance.account')),
                ('entry', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='gsshop_finance.journalentry')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='Loan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('number', models.CharField(max_length=40, unique=True)),
                ('principal', models.DecimalField(decimal_places=2, max_digits=18, validators=[django.core.validators.MinValueValidator(Decimal('0.01'))])),
                ('balance', models.DecimalField(decimal_places=2, max_digits=18)),
                ('term_months', models.PositiveIntegerField(default=12)),
                ('start_date', models.DateField(default=django.utils.timezone.localdate)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('approved', 'Approved'), ('active', 'Active'), ('paid', 'Paid'), ('cancelled', 'Cancelled')], default='draft', max_length=16)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='loans', to='core.employee')),
            ],
            options={
                'ordering': ['-start_date', 'number'],
            },
        ),
        migrations.AddConstraint(
            model_name='account',
            constraint=models.UniqueConstraint(fields=('company', 'code'), name='unique_account_code_per_company'),
        ),
        migrations.AddConstraint(
            model_name='expenseclaim',
            constraint=models.UniqueConstraint(fields=('company', 'reference'), name='unique_expense_ref_per_company'),
        ),
        migrations.AddConstraint(
            model_name='journalentry',
            constraint=models.UniqueConstraint(fields=('company', 'number'), name='unique_journal_number_per_company'),
        ),
    ]
