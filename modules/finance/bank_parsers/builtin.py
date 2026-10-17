import csv
import io
import re
from datetime import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

from . import BankParseResult, register_bank_parser


class BuiltinBankParser:
    def __init__(self, job):
        self.job = job
        self.mapping = job.column_mapping if isinstance(job.column_mapping, dict) else {}

    def _source_bytes(self):
        if self.job.source_file:
            maximum = int(self.job.provider_options.get("max_file_bytes", 10 * 1024 * 1024))
            if self.job.source_file.size > maximum:
                raise ValidationError(f"Bank statement file exceeds the configured {maximum}-byte limit.")
            self.job.source_file.open("rb")
            try:
                return self.job.source_file.read()
            finally:
                self.job.source_file.close()
        return self.job.source_text.encode("utf-8")

    def _mapped(self, source, key, fallback):
        column = str(self.mapping.get(key) or fallback)
        return str(source.get(column, "") or "").strip()

    def _csv_rows(self, content):
        text = content.decode(self.job.provider_options.get("encoding", "utf-8-sig"))
        delimiter = str(self.job.provider_options.get("delimiter", ","))[:1] or ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        return [{
            "date": self._mapped(row, "date", "date"),
            "reference": self._mapped(row, "reference", "reference"),
            "description": self._mapped(row, "description", "description"),
            "amount": self._mapped(row, "amount", "amount"),
        } for row in reader]

    @staticmethod
    def _ofx_rows(content):
        text = content.decode("utf-8", errors="replace")
        blocks = re.findall(r"<STMTTRN>(.*?)(?=<STMTTRN>|</BANKTRANLIST>|$)", text, flags=re.I | re.S)
        rows = []
        for block in blocks:
            def value(tag):
                match = re.search(rf"<{tag}>([^<\r\n]+)", block, flags=re.I)
                return match.group(1).strip() if match else ""
            raw_date = value("DTPOSTED")[:8]
            rows.append({
                "date": datetime.strptime(raw_date, "%Y%m%d").date().isoformat() if raw_date else "",
                "reference": value("FITID") or value("CHECKNUM"),
                "description": value("MEMO") or value("NAME"),
                "amount": value("TRNAMT") or "0",
            })
        return rows

    @staticmethod
    def _text_rows(content):
        text = content.decode("utf-8", errors="replace")
        pattern = re.compile(
            r"^\s*(?P<date>\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4})\s+"
            r"(?P<description>.*?)\s+(?P<amount>\(?-?[\d.,]+\)?)\s*$"
        )
        rows = []
        for source_line in text.splitlines():
            match = pattern.match(source_line)
            if not match:
                continue
            raw_date = match.group("date").replace("-", "/")
            parsed_date = datetime.strptime(raw_date, "%d/%m/%Y").date().isoformat() if raw_date[2] == "/" else raw_date.replace("/", "-")
            amount = match.group("amount")
            negative = amount.startswith("(") and amount.endswith(")")
            normalized = amount.strip("()").replace(",", "")
            rows.append({
                "date": parsed_date, "reference": "", "description": match.group("description").strip(),
                "amount": f"-{normalized}" if negative else normalized,
            })
        return rows

    def parse(self):
        content = self._source_bytes()
        if not content:
            raise ValidationError("Upload a bank file or paste source text before parsing.")
        if self.job.parser_type == "ofx":
            rows, confidence = self._ofx_rows(content), Decimal("100")
        elif self.job.parser_type == "csv":
            rows, confidence = self._csv_rows(content), Decimal("100")
        else:
            rows, confidence = self._text_rows(content), Decimal("80")
        return BankParseResult(rows=rows, confidence=confidence, raw_result={"provider": "builtin"})


register_bank_parser("builtin", BuiltinBankParser)
