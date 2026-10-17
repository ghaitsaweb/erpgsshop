from decimal import Decimal

from . import CountryPack, ReportDefinition, TaxDefinition, register_country_pack


register_country_pack(CountryPack(
    country_code="ID",
    reports=tuple(
        ReportDefinition(
            code, name, output_format,
            field_mapping={field: field for field in ("DPP", "PPN", "PPNBM", "WITHHOLDING_GROSS", "WITHHOLDING_TAX")},
            validation_rules={
                "required_fields": ["DPP", "PPN", "PPNBM", "WITHHOLDING_GROSS", "WITHHOLDING_TAX"],
                "non_negative_fields": ["DPP", "PPN", "PPNBM", "WITHHOLDING_GROSS", "WITHHOLDING_TAX"],
                "registration_required": True,
            },
            menu_entity=menu_entity,
        )
        for code, name, output_format, menu_entity in (
            ("VAT-IN", "Indonesia Input VAT", "csv", "compliance-reports"),
            ("VAT-OUT", "Indonesia Output VAT", "csv", "compliance-reports"),
            ("CORETAX", "Coretax Tax Document", "xml", "tax-authority-documents"),
            ("EBUPOT", "e-Bupot Withholding", "xml", "tax-authority-documents"),
            ("BPPU", "BPPU Tax Document", "xml", "tax-authority-documents"),
        )
    ),
    taxes=(
        TaxDefinition("PPN11", "Indonesia VAT 11%", "vat", Decimal("11"), "PPN", {"bucket": "STANDARD"}),
        TaxDefinition("PPN12", "Indonesia VAT 12%", "vat", Decimal("12"), "PPN", {"bucket": "STANDARD"}),
        TaxDefinition("PPN0", "Indonesia Zero-Rated VAT", "vat", Decimal("0"), "PPN0", {"bucket": "ZERO_RATED"}),
        TaxDefinition("PPNBM", "Luxury Goods Sales Tax", "vat", Decimal("0"), "PPNBM"),
        TaxDefinition("PPH21", "PPh Article 21", "withholding", Decimal("5"), "21-100-01"),
        TaxDefinition("PPH22", "PPh Article 22", "withholding", Decimal("1.5"), "22-100-01"),
        TaxDefinition("PPH23", "PPh Article 23", "withholding", Decimal("2"), "23-100-01"),
        TaxDefinition("PPH4-2", "PPh Article 4(2)", "withholding", Decimal("10"), "28-409-01"),
    ),
))
