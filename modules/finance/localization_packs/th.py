from decimal import Decimal

from . import CountryPack, ReportDefinition, TaxDefinition, register_country_pack


register_country_pack(CountryPack(
    country_code="TH",
    reports=(ReportDefinition(
        "PP30", "Thailand PP30", "xml",
        field_mapping={code: code for code in (
            "TOTAL_SALES", "OUTPUT_VAT", "TOTAL_PURCHASES", "INPUT_VAT", "OUTPUT_ADJUSTMENT",
            "INPUT_ADJUSTMENT", "CARRY_FORWARD", "TAX_PAYABLE", "EXCESS_TAX", "SURCHARGE", "PENALTY", "TOTAL_DUE",
        )},
        validation_rules={
            "required_fields": ["TOTAL_SALES", "OUTPUT_VAT", "TOTAL_PURCHASES", "INPUT_VAT", "TAX_PAYABLE", "EXCESS_TAX", "TOTAL_DUE"],
            "non_negative_fields": ["TOTAL_SALES", "OUTPUT_VAT", "TOTAL_PURCHASES", "INPUT_VAT", "TAX_PAYABLE", "EXCESS_TAX", "SURCHARGE", "PENALTY", "TOTAL_DUE"],
            "registration_required": True,
        },
    ),),
    taxes=(
        TaxDefinition("VAT7", "Thailand VAT 7%", "vat", Decimal("7"), "VAT7", {"bucket": "STANDARD"}),
        TaxDefinition("VAT0", "Thailand VAT Zero Rate", "vat", Decimal("0"), "VAT0", {"bucket": "ZERO_RATED"}),
        TaxDefinition("VAT-EXEMPT", "Thailand VAT Exempt", "vat", Decimal("0"), "EXEMPT", {"bucket": "EXEMPT"}),
    ),
))
