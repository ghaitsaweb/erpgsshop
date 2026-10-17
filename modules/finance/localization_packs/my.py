from decimal import Decimal

from . import CountryPack, ReportDefinition, TaxDefinition, register_country_pack


register_country_pack(CountryPack(
    country_code="MY",
    reports=tuple(
        ReportDefinition(code, name, output_format,
                         field_mapping={field: field for field in ("TAXABLE_SALES", "SALES_TAX", "TAXABLE_SERVICES", "SERVICE_TAX", "CARD_LEVY", "BAD_DEBT_RELIEF", "RECOVERED_BAD_DEBT", "NET_TAX_PAYABLE")},
                         validation_rules={"required_fields": ["TAXABLE_SALES", "SALES_TAX", "TAXABLE_SERVICES", "SERVICE_TAX", "NET_TAX_PAYABLE"], "registration_required": True},
                         menu_entity=menu_entity)
        for code, name, output_format, menu_entity in (
            ("SST-02", "Malaysia SST-02", "xml", "compliance-reports"),
            ("SST-02A", "Malaysia SST-02A", "xml", "compliance-reports"),
            ("IRBM-INVOICE", "IRBM e-Invoice", "json", "tax-authority-documents"),
        )
    ),
    taxes=(
        TaxDefinition("SST-SALES-5", "Malaysia Sales Tax 5%", "sales_tax", Decimal("5"), "01"),
        TaxDefinition("SST-SALES-10", "Malaysia Sales Tax 10%", "sales_tax", Decimal("10"), "01"),
        TaxDefinition("SST-SERVICE-6", "Malaysia Service Tax 6%", "service_tax", Decimal("6"), "02"),
        TaxDefinition("SST-SERVICE-8", "Malaysia Service Tax 8%", "service_tax", Decimal("8"), "02"),
        TaxDefinition("SST-CARD-LEVY", "Malaysia Credit Card Levy", "service_tax", Decimal("0"), "04", {"bucket": "CARD_LEVY"}),
        TaxDefinition("SST-EXEMPT", "Malaysia SST Exempt", "sales_tax", Decimal("0"), "06", {"bucket": "EXEMPT"}),
    ),
))
