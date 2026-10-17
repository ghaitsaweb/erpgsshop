from decimal import Decimal

from . import CountryPack, ReportDefinition, TaxDefinition, register_country_pack


register_country_pack(CountryPack(
    country_code="SG",
    reports=tuple(
        ReportDefinition(code, name, "xml", field_mapping={f"BOX{i}": f"BOX{i}" for i in range(1, 14)},
                         validation_rules={"required_fields": [f"BOX{i}" for i in range(1, 14)], "registration_required": True})
        for code, name in (("GST-F5", "Singapore GST F5"), ("GST-F7", "Singapore GST F7"),
                           ("IAF", "Singapore IAF"), ("PEPPOL", "Singapore Peppol Invoice"))
    ),
    taxes=(
        TaxDefinition("GST9", "Singapore GST Standard 9%", "gst", Decimal("9"), "SR", {"bucket": "STANDARD"}),
        TaxDefinition("GST-ZR", "Singapore Zero-Rated Supply", "gst", Decimal("0"), "ZR", {"bucket": "ZERO_RATED"}),
        TaxDefinition("GST-ES", "Singapore Exempt Supply", "gst", Decimal("0"), "ES", {"bucket": "EXEMPT"}),
        TaxDefinition("GST-OS", "Singapore Out-of-Scope Supply", "gst", Decimal("0"), "OS"),
    ),
))
