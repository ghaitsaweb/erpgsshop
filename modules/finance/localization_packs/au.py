from decimal import Decimal

from . import CountryPack, ReportDefinition, TaxDefinition, register_country_pack


register_country_pack(CountryPack(
    country_code="AU",
    reports=(ReportDefinition(
        "BAS", "Australia Business Activity Statement", "json",
        field_mapping={code: code for code in ("G1", "G2", "G3", "G10", "G11", "1A", "1B", "NET_GST")},
        validation_rules={
            "required_fields": ["G1", "G2", "G3", "G10", "G11", "1A", "1B", "NET_GST"],
            "non_negative_fields": ["G1", "G2", "G3", "G10", "G11", "1A", "1B"],
            "registration_required": True,
        },
    ),),
    taxes=(
        TaxDefinition("GST10", "GST Standard 10%", "gst", Decimal("10"), "GST", {"bucket": "GST_STD"}),
        TaxDefinition("GST-FREE", "GST Free", "gst", Decimal("0"), "GST_FREE", {"bucket": "GST_FREE"}),
        TaxDefinition("GST-EXPORT", "GST Export", "gst", Decimal("0"), "GST_EXPORT", {"bucket": "EXPORT"}),
        TaxDefinition("INPUT-TAXED", "Input Taxed", "gst", Decimal("0"), "INPUT_TAXED"),
        TaxDefinition("WHT-NO-ABN", "Withholding - No ABN", "withholding", Decimal("47"), "WHT_NO_ABN"),
    ),
))
