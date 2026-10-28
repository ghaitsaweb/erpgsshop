from django.db import migrations


class Migration(migrations.Migration):
    replaces = [
        ("gsshop_hr", "0015_employmentcontract_approval_statuses"),
        ("gsshop_hr", "0016_contract_workspace_relations"),
        ("gsshop_hr", "0017_payslip_contract_and_payroll_run"),
        ("gsshop_hr", "0018_contract_attachment_stored_upload"),
    ]

    dependencies = [
        ("gsshop_hr", "0034_cash_advance_lines"),
    ]

    operations = []
