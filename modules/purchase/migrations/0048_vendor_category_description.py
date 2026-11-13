from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_purchase", "0047_purchase_remark_snapshots"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vendorcategory",
            name="code",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="vendorcategory",
            name="description",
            field=models.TextField(blank=True),
        ),
    ]
