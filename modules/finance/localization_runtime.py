"""Country-aware statutory calculation, validation, and rendering.

The country packs expose catalog metadata.  This module owns the operational
contract: one bounded read of immutable tax source rows, deterministic country
calculations, authority-shaped payloads, and export content that can be audited
and reproduced later.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from xml.etree.ElementTree import Element, SubElement, tostring

from django.core.exceptions import ValidationError

from .models import LocalizedTaxTransactionLine


ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")


def _decimal(value):
    return Decimal(str(value or 0))


def _money(value):
    return _decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _text(value):
    return str(value or "").strip()


def _profile_config(report):
    config = report.profile.configuration
    return config if isinstance(config, dict) else {}


def _source_rows(report):
    """Read all report sources once; country calculators stay query-free."""
    return list(
        LocalizedTaxTransactionLine.objects.filter(
            profile_id=report.profile_id,
            transaction_date__range=(report.period_start, report.period_end),
        ).values(
            "id", "transaction_date", "document_number", "document_type", "supply_category",
            "quantity", "tax_base", "tax_amount", "gross_amount", "currency", "exchange_rate",
            "authority_fields", "tax_code__code", "tax_code__tax_type", "tax_code__rate",
            "tax_code__authority_code", "tax_code__metadata", "partner_id", "partner__code",
            "partner__name", "partner__email", "partner__phone",
        ).order_by("transaction_date", "document_number", "id")
    )


def _signed(row):
    return Decimal("-1") if row["document_type"] in {"sale_return", "purchase_return"} else Decimal("1")


def _row_amount(row, field):
    return _money(_decimal(row[field]) * _signed(row))


def _is_sale(row):
    return row["document_type"] in {"sale", "sale_return"}


def _is_purchase(row):
    return row["document_type"] in {"purchase", "purchase_return"}


def _tax_codes(row):
    metadata = row["tax_code__metadata"] if isinstance(row["tax_code__metadata"], dict) else {}
    return {
        _text(row["tax_code__code"]).upper(),
        _text(row["tax_code__authority_code"]).upper(),
        _text(metadata.get("bucket")).upper(),
        _text(metadata.get("category")).upper(),
    } - {""}


def _sum(rows, field="tax_base", predicate=None):
    return _money(sum((_row_amount(row, field) for row in rows if predicate is None or predicate(row)), ZERO))


def _line(code, label, value, *, tax_base=ZERO, tax_amount=ZERO, metadata=None):
    return {
        "field_code": code,
        "label": label,
        "value": str(value),
        "tax_base": _money(tax_base),
        "tax_amount": _money(tax_amount),
        "metadata": metadata or {},
    }


def _require(config, fields, label):
    missing = [field for field in fields if not _text(config.get(field))]
    if missing:
        raise ValidationError(f"{label} profile is incomplete: {', '.join(missing)}")


def _document_rows(rows):
    documents = defaultdict(lambda: {"tax_base": ZERO, "tax_amount": ZERO, "gross_amount": ZERO, "lines": []})
    for row in rows:
        target = documents[row["document_number"]]
        target.update({
            "document_number": row["document_number"],
            "document_date": row["transaction_date"].isoformat(),
            "document_type": row["document_type"],
            "partner_code": row["partner__code"],
            "partner_name": row["partner__name"],
            "currency": row["currency"],
        })
        target["tax_base"] += _row_amount(row, "tax_base")
        target["tax_amount"] += _row_amount(row, "tax_amount")
        target["gross_amount"] += _row_amount(row, "gross_amount")
        target["lines"].append({
            "source_id": row["id"], "tax_code": row["tax_code__authority_code"] or row["tax_code__code"],
            "quantity": str(row["quantity"]), "tax_base": str(_row_amount(row, "tax_base")),
            "tax_amount": str(_row_amount(row, "tax_amount")), "authority_fields": row["authority_fields"],
        })
    result = []
    for document in documents.values():
        for key in ("tax_base", "tax_amount", "gross_amount"):
            document[key] = str(_money(document[key]))
        result.append(document)
    return result


def _australia(report, rows, config):
    _require(config, ["legal_name", "abn", "gst_accounting_method", "reporting_frequency"], "Australia BAS")
    standard = lambda row: bool(_tax_codes(row) & {"GST", "GST10", "GST_STD"})
    export = lambda row: bool(_tax_codes(row) & {"GST_EXPORT", "EXPORT"})
    free = lambda row: bool(_tax_codes(row) & {"GST_FREE", "FREE"})
    capital = lambda row: _text((row["authority_fields"] or {}).get("purchase_type")).lower() == "capital"
    values = {
        "G1": _sum(rows, "gross_amount", _is_sale),
        "G2": _sum(rows, "tax_base", lambda row: _is_sale(row) and export(row)),
        "G3": _sum(rows, "tax_base", lambda row: _is_sale(row) and free(row)),
        "G10": _sum(rows, "tax_base", lambda row: _is_purchase(row) and capital(row)),
        "G11": _sum(rows, "tax_base", lambda row: _is_purchase(row) and not capital(row)),
        "1A": _sum(rows, "tax_amount", lambda row: _is_sale(row) and standard(row)),
        "1B": _sum(rows, "tax_amount", lambda row: _is_purchase(row) and standard(row)),
    }
    values["NET_GST"] = _money(values["1A"] - values["1B"])
    labels = {
        "G1": "Total sales", "G2": "Export sales", "G3": "Other GST-free sales",
        "G10": "Capital purchases", "G11": "Non-capital purchases",
        "1A": "GST on sales", "1B": "GST on purchases", "NET_GST": "Net GST payable/refundable",
    }
    payload = {
        "form": "BAS", "abn": config["abn"], "entityName": config["legal_name"],
        "accountingBasis": config["gst_accounting_method"], "reportingFrequency": config["reporting_frequency"],
        "period": {"from": report.period_start.isoformat(), "to": report.period_end.isoformat()},
        "labels": {key: str(value) for key, value in values.items()},
    }
    return values, payload, [_line(code, labels[code], value) for code, value in values.items()]


def _singapore(report, rows, config):
    _require(config, ["legal_name", "uen", "gst_registration_number"], "Singapore GST")
    standard = lambda row: bool(_tax_codes(row) & {"GST", "GST9", "SR", "STANDARD"})
    zero = lambda row: bool(_tax_codes(row) & {"ZR", "ZERO", "ZERO_RATED"})
    exempt = lambda row: bool(_tax_codes(row) & {"ES", "EXEMPT"})
    values = {
        "BOX1": _sum(rows, "tax_base", lambda row: _is_sale(row) and standard(row)),
        "BOX2": _sum(rows, "tax_base", lambda row: _is_sale(row) and zero(row)),
        "BOX3": _sum(rows, "tax_base", lambda row: _is_sale(row) and exempt(row)),
        "BOX5": _sum(rows, "tax_base", _is_purchase),
        "BOX6": _sum(rows, "tax_amount", lambda row: _is_sale(row) and standard(row)),
        "BOX7": _sum(rows, "tax_amount", lambda row: _is_purchase(row) and standard(row)),
    }
    values["BOX4"] = _money(values["BOX1"] + values["BOX2"] + values["BOX3"])
    values["BOX8"] = _money(values["BOX6"] - values["BOX7"])
    values["BOX9"] = _sum(rows, "tax_base", lambda row: (row["authority_fields"] or {}).get("import_scheme") is True)
    values["BOX10"] = _money(_decimal(config.get("tourist_refund_amount")))
    values["BOX11"] = _money(_decimal(config.get("bad_debt_relief_amount")))
    values["BOX12"] = _money(_decimal(config.get("pre_registration_claim_amount")))
    values["BOX13"] = _sum(rows, "gross_amount", _is_sale)
    payload = {
        "form": report.report_type, "uen": config["uen"], "gstRegistrationNumber": config["gst_registration_number"],
        "periodStart": report.period_start.isoformat(), "periodEnd": report.period_end.isoformat(),
        "boxes": {key.replace("BOX", ""): str(value) for key, value in values.items()},
        "documents": _document_rows(rows) if report.report_type in {"IAF", "PEPPOL"} else [],
    }
    labels = {f"BOX{number}": f"GST Box {number}" for number in range(1, 14)}
    return values, payload, [_line(code, labels[code], value) for code, value in values.items()]


def _thailand(report, rows, config):
    _require(config, ["legal_name", "tax_id", "branch_code"], "Thailand PP30")
    output_base = _sum(rows, "tax_base", _is_sale)
    output_tax = _sum(rows, "tax_amount", _is_sale)
    input_base = _sum(rows, "tax_base", _is_purchase)
    input_tax = _sum(rows, "tax_amount", _is_purchase)
    output_adjustment = _money(config.get("output_vat_adjustment"))
    input_adjustment = _money(config.get("input_vat_adjustment"))
    carry = _money(config.get("carry_forward_amount"))
    surcharge = _money(config.get("surcharge_amount"))
    penalty = _money(config.get("penalty_amount"))
    net = _money(output_tax + output_adjustment - input_tax - input_adjustment - carry)
    values = {
        "TOTAL_SALES": output_base, "OUTPUT_VAT": output_tax, "TOTAL_PURCHASES": input_base,
        "INPUT_VAT": input_tax, "OUTPUT_ADJUSTMENT": output_adjustment, "INPUT_ADJUSTMENT": input_adjustment,
        "CARRY_FORWARD": carry, "TAX_PAYABLE": max(net, ZERO), "EXCESS_TAX": abs(min(net, ZERO)),
        "SURCHARGE": surcharge, "PENALTY": penalty, "TOTAL_DUE": max(net, ZERO) + surcharge + penalty,
    }
    payload = {
        "form": "PP30", "taxId": re.sub(r"\D", "", config["tax_id"]), "branchCode": config["branch_code"],
        "filingType": config.get("filing_type", "normal"), "refundDecision": config.get("refund_decision", "carry_forward"),
        "period": {"month": report.period_end.month, "year": report.period_end.year, "buddhistYear": report.period_end.year + 543},
        "boxes": {key: str(value) for key, value in values.items()},
    }
    return values, payload, [_line(code, code.replace("_", " ").title(), value) for code, value in values.items()]


def _malaysia(report, rows, config):
    _require(config, ["legal_name", "tin", "registration_type", "registration_number"], "Malaysia SST/IRBM")
    sales_tax = lambda row: _text(row["tax_code__tax_type"]).lower() in {"sales_tax", "sst_sales"}
    service_tax = lambda row: _text(row["tax_code__tax_type"]).lower() in {"service_tax", "sst_service"}
    card_levy = lambda row: bool(_tax_codes(row) & {"CARD_LEVY", "CC_LEVY"})
    values = {
        "TAXABLE_SALES": _sum(rows, "tax_base", lambda row: _is_sale(row) and sales_tax(row)),
        "SALES_TAX": _sum(rows, "tax_amount", lambda row: _is_sale(row) and sales_tax(row)),
        "TAXABLE_SERVICES": _sum(rows, "tax_base", lambda row: _is_sale(row) and service_tax(row)),
        "SERVICE_TAX": _sum(rows, "tax_amount", lambda row: _is_sale(row) and service_tax(row)),
        "CARD_LEVY": _sum(rows, "tax_amount", card_levy),
        "BAD_DEBT_RELIEF": _money(config.get("bad_debt_relief")),
        "RECOVERED_BAD_DEBT": _money(config.get("recovered_bad_debt")),
    }
    values["NET_TAX_PAYABLE"] = _money(
        values["SALES_TAX"] + values["SERVICE_TAX"] + values["CARD_LEVY"]
        - values["BAD_DEBT_RELIEF"] + values["RECOVERED_BAD_DEBT"]
    )
    payload = {
        "form": report.report_type, "supplier": {
            "name": config["legal_name"], "tin": config["tin"], "idType": config["registration_type"],
            "idValue": config["registration_number"], "sstRegistration": config.get("sst_registration_number", ""),
        },
        "period": {"from": report.period_start.isoformat(), "to": report.period_end.isoformat()},
        "totals": {key: str(value) for key, value in values.items()},
        "invoices": _document_rows(rows) if report.report_type == "IRBM-INVOICE" else [],
    }
    return values, payload, [_line(code, code.replace("_", " ").title(), value) for code, value in values.items()]


def _philippines(report, rows, config):
    _require(config, ["registered_name", "tin", "branch_code", "rdo_code"], "Philippines BIR")
    tin = re.sub(r"\D", "", config["tin"])
    if len(tin) not in {9, 12}:
        raise ValidationError("Philippines TIN must contain 9 or 12 digits.")
    report_type = report.report_type.upper()
    withholding = [row for row in rows if _text(row["tax_code__tax_type"]).lower() == "withholding"]
    vat_rows = [row for row in rows if _text(row["tax_code__tax_type"]).lower() in {"vat", "gst"}]
    source = withholding if report_type in {"0619E", "0619F", "1601EQ", "1601FQ", "1604E", "2306", "2307", "SAWT"} else vat_rows
    values = {
        "TAXABLE_BASE": _sum(source, "tax_base"),
        "TAX_DUE": _sum(source, "tax_amount"),
        "OUTPUT_VAT": _sum(vat_rows, "tax_amount", _is_sale),
        "INPUT_VAT": _sum(vat_rows, "tax_amount", _is_purchase),
        "EXEMPT_SALES": _sum(rows, "tax_base", lambda row: _is_sale(row) and "EXEMPT" in _tax_codes(row)),
        "ZERO_RATED_SALES": _sum(rows, "tax_base", lambda row: _is_sale(row) and bool(_tax_codes(row) & {"ZERO", "ZERO_RATED"})),
    }
    values["NET_VAT"] = _money(values["OUTPUT_VAT"] - values["INPUT_VAT"])
    detail = []
    for sequence, row in enumerate(source, start=1):
        authority = row["authority_fields"] if isinstance(row["authority_fields"], dict) else {}
        detail.append({
            "sequence": sequence, "documentNumber": row["document_number"],
            "date": row["transaction_date"].isoformat(), "partnerTin": authority.get("partner_tin", ""),
            "partnerBranch": authority.get("partner_branch_code", "0000"), "partnerName": row["partner__name"],
            "atc": authority.get("atc", row["tax_code__authority_code"]),
            "taxBase": str(_row_amount(row, "tax_base")), "taxAmount": str(_row_amount(row, "tax_amount")),
        })
    payload = {
        "form": report_type, "taxpayer": {"tin": tin, "branchCode": config["branch_code"], "name": config["registered_name"], "rdo": config["rdo_code"]},
        "period": {"from": report.period_start.isoformat(), "to": report.period_end.isoformat()},
        "totals": {key: str(value) for key, value in values.items()}, "details": detail,
    }
    return values, payload, [_line(code, code.replace("_", " ").title(), value) for code, value in values.items()]


def _indonesia(report, rows, config):
    _require(config, ["legal_name", "npwp", "business_unit_code"], "Indonesia Coretax")
    npwp = re.sub(r"\D", "", config["npwp"])
    if len(npwp) not in {15, 16}:
        raise ValidationError("Indonesia NPWP must contain 15 or 16 digits.")
    report_type = report.report_type.upper()
    selected = rows
    if report_type == "VAT-IN":
        selected = [row for row in rows if _is_purchase(row)]
    elif report_type == "VAT-OUT":
        selected = [row for row in rows if _is_sale(row)]
    elif report_type == "EBUPOT":
        selected = [row for row in rows if _text(row["tax_code__tax_type"]).lower() == "withholding"]
    values = {
        "DPP": _sum(selected, "tax_base"), "PPN": _sum(selected, "tax_amount"),
        "PPNBM": _sum(selected, "tax_amount", lambda row: "PPNBM" in _tax_codes(row)),
        "WITHHOLDING_GROSS": _sum(selected, "tax_base", lambda row: _text(row["tax_code__tax_type"]).lower() == "withholding"),
        "WITHHOLDING_TAX": _sum(selected, "tax_amount", lambda row: _text(row["tax_code__tax_type"]).lower() == "withholding"),
    }
    detail = []
    for row in selected:
        authority = row["authority_fields"] if isinstance(row["authority_fields"], dict) else {}
        partner_npwp = re.sub(r"\D", "", _text(authority.get("partner_npwp")))
        partner_nik = re.sub(r"\D", "", _text(authority.get("partner_nik")))
        if partner_npwp and len(partner_npwp) not in {15, 16}:
            raise ValidationError(f"Invalid partner NPWP on {row['document_number']}.")
        if not partner_npwp and partner_nik and len(partner_nik) != 16:
            raise ValidationError(f"Partner NIK must contain 16 digits on {row['document_number']}.")
        detail.append({
            "taxInvoiceNumber": authority.get("tax_invoice_number", row["document_number"]),
            "taxInvoiceDate": row["transaction_date"].isoformat(), "counterpartyNpwp": partner_npwp,
            "counterpartyNik": partner_nik, "counterpartyName": row["partner__name"],
            "transactionCode": authority.get("transaction_code", "04"),
            "facilityStamp": authority.get("facility_stamp", ""), "taxObjectCode": authority.get("tax_object_code", ""),
            "uomCode": authority.get("uom_code", "UM.0018"), "dpp": str(_row_amount(row, "tax_base")),
            "vat": str(_row_amount(row, "tax_amount")), "taxRate": str(row["tax_code__rate"]),
        })
    payload = {
        "documentType": report_type, "seller": {"npwp": npwp, "name": config["legal_name"], "businessUnitCode": config["business_unit_code"]},
        "period": {"from": report.period_start.isoformat(), "to": report.period_end.isoformat()},
        "totals": {key: str(value) for key, value in values.items()}, "documents": detail,
    }
    return values, payload, [_line(code, code.replace("_", " ").title(), value) for code, value in values.items()]


COUNTRY_BUILDERS = {
    "AU": _australia, "SG": _singapore, "TH": _thailand,
    "MY": _malaysia, "PH": _philippines, "ID": _indonesia,
}


def _render_xml(payload):
    def append(parent, key, value):
        child = SubElement(parent, str(key))
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                append(child, nested_key, nested_value)
        elif isinstance(value, list):
            for item in value:
                append(child, "item", item)
        else:
            child.text = _text(value)

    root = Element("StatutoryReport")
    for key, value in payload.items():
        append(root, key, value)
    return tostring(root, encoding="unicode", xml_declaration=True)


def _render_dat(payload):
    output = io.StringIO()
    writer = csv.writer(output, delimiter="|", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    taxpayer = payload.get("taxpayer", {})
    period = payload.get("period", {})
    writer.writerow(["H", payload.get("form", ""), taxpayer.get("tin", ""), taxpayer.get("branchCode", ""), period.get("to", "")])
    for row in payload.get("details", []):
        writer.writerow([
            "D", row.get("sequence", ""), row.get("partnerTin", ""), row.get("partnerBranch", ""),
            row.get("partnerName", ""), row.get("atc", ""), row.get("taxBase", "0"), row.get("taxAmount", "0"),
        ])
    writer.writerow(["T", len(payload.get("details", [])), payload.get("totals", {}).get("TAXABLE_BASE", "0"), payload.get("totals", {}).get("TAX_DUE", "0")])
    return output.getvalue()


def _render_csv(payload):
    output = io.StringIO()
    rows = payload.get("documents") or payload.get("details") or []
    field_names = sorted({key for row in rows for key in row}) if rows else ["form", "period"]
    writer = csv.DictWriter(output, fieldnames=field_names, extrasaction="ignore")
    writer.writeheader()
    if rows:
        writer.writerows(rows)
    else:
        writer.writerow({"form": payload.get("form") or payload.get("documentType"), "period": json.dumps(payload.get("period", {}), sort_keys=True)})
    return output.getvalue()


def build_country_report(report, output_format):
    country_code = report.profile.country_code.strip().upper()
    builder = COUNTRY_BUILDERS.get(country_code)
    if builder is None:
        raise ValidationError(f"No operational statutory engine is installed for {country_code}.")
    rows = _source_rows(report)
    if not rows:
        raise ValidationError("No localized tax transaction rows exist for this report period.")
    totals, payload, lines = builder(report, rows, _profile_config(report))
    if output_format == "xml":
        content = _render_xml(payload)
    elif output_format == "dat":
        content = _render_dat(payload)
    elif output_format == "csv":
        content = _render_csv(payload)
    else:
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str)
    extension = "json" if output_format not in {"xml", "dat", "csv"} else output_format
    filename = f"{country_code}-{report.report_type}-{report.period_end:%Y%m%d}.{extension}".replace("/", "-")
    return {
        "totals": {key: str(value) for key, value in totals.items()},
        "payload": payload,
        "lines": lines,
        "rendered_content": content,
        "rendered_filename": filename,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def validate_country_report(report):
    payload = report.payload if isinstance(report.payload, dict) else {}
    if not report.content_sha256 or hashlib.sha256(report.rendered_content.encode("utf-8")).hexdigest() != report.content_sha256:
        raise ValidationError("Rendered statutory content is missing or its audit checksum no longer matches.")
    country_code = report.profile.country_code.strip().upper()
    expected_type = payload.get("form") or payload.get("documentType")
    if expected_type and _text(expected_type).upper() != report.report_type.upper():
        raise ValidationError("The rendered authority payload does not match the selected report type.")
    if country_code == "SG" and report.report_type == "GST-F7":
        error_description = _text(_profile_config(report).get("error_description"))
        if not error_description or len(error_description) > 200 or set(error_description) & set("<>{}[]^~|;"):
            raise ValidationError("GST F7 requires a valid error description of at most 200 characters.")
    if country_code == "TH" and report.report_type == "PP30":
        filing_type = payload.get("filingType")
        if filing_type not in {"normal", "additional"}:
            raise ValidationError("PP30 filing type must be normal or additional.")
    return True
