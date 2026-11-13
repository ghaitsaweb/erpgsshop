from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_purchase_order_generated_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="logo",
            field=models.ImageField(
                blank=True,
                help_text="PNG, JPEG, or WebP. Maximum file size 5 MB.",
                upload_to="company-logos/%Y/%m/",
            ),
        ),
    ]
