# Generated for relational approval workflow adoption.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gsshop_hr", "0014_employeeaddress_phone_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="employmentcontract",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("submitted", "Submitted for Approval"),
                    ("active", "Active"),
                    ("rejected", "Rejected"),
                    ("cancelled", "Cancelled"),
                    ("expired", "Expired"),
                    ("terminated", "Terminated"),
                ],
                default="draft",
                max_length=16,
            ),
        ),
    ]
