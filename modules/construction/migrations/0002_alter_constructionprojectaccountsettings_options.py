from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('gsshop_construction', '0001_initial'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='constructionprojectaccountsettings',
            options={'ordering': ['project__code'], 'verbose_name_plural': 'Construction project account settings'},
        ),
    ]
