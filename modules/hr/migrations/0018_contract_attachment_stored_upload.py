from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_hr", "0017_payslip_contract_and_payroll_run"),
    ]

    operations = [
        migrations.AddField(
            model_name="contractattachment",
            name="attachment_content",
            field=models.BinaryField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="contractattachment",
            name="attachment_content_type",
            field=models.CharField(blank=True, editable=False, max_length=120),
        ),
        migrations.AddField(
            model_name="contractattachment",
            name="attachment_name",
            field=models.CharField(blank=True, editable=False, max_length=255),
        ),
        migrations.AlterField(
            model_name="contractattachment",
            name="file_url",
            field=models.URLField(blank=True),
        ),
    ]
