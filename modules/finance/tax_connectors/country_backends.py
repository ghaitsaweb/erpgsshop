"""Authority-specific middleware contracts used by the HMX country packs."""

from django.core.exceptions import ValidationError

from . import register_connector_backend
from .http_json import JsonHttpConnectorBackend


class CountryJsonBackend(JsonHttpConnectorBackend):
    default_paths = {}
    required_country = ""

    def __init__(self, connector):
        super().__init__(connector)
        for key, path in self.default_paths.items():
            self.configuration.setdefault(key, path)

    def validate_configuration(self):
        super().validate_configuration()
        if self.required_country and self.connector.profile.country_code.upper() != self.required_country:
            raise ValidationError(
                f"Connector {self.connector.connector_type} requires a {self.required_country} country profile."
            )
        return True


class PajakExpressCoretaxBackend(CountryJsonBackend):
    required_country = "ID"
    default_paths = {
        "document_path": "/api/coretax/invoices",
        "lookup_path": "/api/coretax/vendor/mst_kop_bpu",
        "report_path": "/api/coretax/invoices",
    }

    def send_document(self, document):
        document_type = document.document_type.upper()
        path_by_type = {
            "VAT-OUT": "/api/coretax/vendor/IF_TXR_001/create",
            "VAT-IN": "/api/coretax/vendor/IF_TXR_015/upload",
            "VAT-OUT-RETURN": "/api/coretax/vendor/IF_TXR_005/rpk",
            "VAT-IN-RETURN": "/api/coretax/vendor/IF_TXR_017/create",
            "EBUPOT": "/api/coretax/vendor/IF_TXR_028/bpu",
            "BPPU": "/api/coretax/vendor/IF_TXR_028/bpu",
        }
        self.configuration["document_path"] = path_by_type.get(document_type, "/api/coretax/invoices")
        return super().send_document(document)


class MalaysiaIRBMBackend(CountryJsonBackend):
    required_country = "MY"
    default_paths = {
        "document_path": "/api/irbm/invoices",
        "lookup_path": "/api/irbm/platform/validate-tin",
        "report_path": "/api/irbm/invoices",
        "received_path": "/api/irbm/invoices/received",
    }


class SingaporePeppolBackend(CountryJsonBackend):
    required_country = "SG"
    default_paths = {
        "document_path": "/api/peppol/invoices/",
        "lookup_path": "/api/peppol/registrations/buyer",
        "report_path": "/api/peppol/invoices/",
        "received_path": "/api/peppol/invoices/received",
    }


class PhilippinesBIRBackend(CountryJsonBackend):
    required_country = "PH"
    default_paths = {
        "document_path": "/api/bir/documents",
        "lookup_path": "/api/bir/tin/validate",
        "report_path": "/api/bir/returns",
    }


class ThailandRevenueBackend(CountryJsonBackend):
    required_country = "TH"
    default_paths = {
        "document_path": "/api/th-rd/documents",
        "lookup_path": "/api/th-rd/taxpayer/validate",
        "report_path": "/api/th-rd/pp30",
    }


class AustraliaATOBackend(CountryJsonBackend):
    required_country = "AU"
    default_paths = {
        "document_path": "/api/ato/documents",
        "lookup_path": "/api/ato/abn/lookup",
        "report_path": "/api/ato/bas",
    }


register_connector_backend("pajak_express", PajakExpressCoretaxBackend)
register_connector_backend("coretax", PajakExpressCoretaxBackend)
register_connector_backend("irbm", MalaysiaIRBMBackend)
register_connector_backend("peppol", SingaporePeppolBackend)
register_connector_backend("bir", PhilippinesBIRBackend)
register_connector_backend("thai_revenue", ThailandRevenueBackend)
register_connector_backend("ato", AustraliaATOBackend)
