from decimal import Decimal

from . import CountryPack, ReportDefinition, TaxDefinition, register_country_pack


register_country_pack(CountryPack(
    country_code="PH",
    reports=tuple(
        ReportDefinition(
            code, name, output_format,
            field_mapping={field: field for field in (
                "TAXABLE_BASE", "TAX_DUE", "OUTPUT_VAT", "INPUT_VAT", "EXEMPT_SALES", "ZERO_RATED_SALES", "NET_VAT",
            )},
            validation_rules={
                "required_fields": ["TAXABLE_BASE", "TAX_DUE", "OUTPUT_VAT", "INPUT_VAT", "NET_VAT"],
                "non_negative_fields": ["TAXABLE_BASE", "TAX_DUE", "OUTPUT_VAT", "INPUT_VAT", "EXEMPT_SALES", "ZERO_RATED_SALES"],
                "registration_required": True,
            },
        )
        for code, name, output_format in (
            ("0619E", "BIR 0619E", "dat"), ("0619F", "BIR 0619F", "dat"),
            ("1601EQ", "BIR 1601EQ", "dat"), ("1601FQ", "BIR 1601FQ", "dat"),
            ("1604E", "BIR 1604E", "dat"), ("2306", "BIR 2306", "pdf"),
            ("2307", "BIR 2307", "pdf"), ("2550M", "BIR 2550M", "dat"),
            ("2550Q", "BIR 2550Q", "dat"),
            ("SAWT", "Summary Alphalist of Withholding Taxes", "dat"),
            ("RELIEF", "RELIEF VAT Schedule", "dat"),
        )
    ),
    taxes=(
        TaxDefinition("VAT12", "Philippines VAT 12%", "vat", Decimal("12"), "VAT12", {"bucket": "STANDARD"}),
        TaxDefinition("VAT0", "Philippines Zero-Rated VAT", "vat", Decimal("0"), "ZERO_RATED", {"bucket": "ZERO_RATED"}),
        TaxDefinition("VAT-EXEMPT", "Philippines VAT Exempt", "vat", Decimal("0"), "EXEMPT", {"bucket": "EXEMPT"}),
        TaxDefinition("EWT-SERVICE-2", "Expanded Withholding - Services", "withholding", Decimal("2"), "WI010"),
        TaxDefinition("EWT-GOODS-1", "Expanded Withholding - Goods", "withholding", Decimal("1"), "WI158"),
        TaxDefinition("FWT-INTEREST-20", "Final Withholding - Interest", "withholding", Decimal("20"), "WF320"),
        TaxDefinition("GOV-VAT-5", "Government Creditable VAT", "withholding", Decimal("5"), "WV010"),
    ),
))
